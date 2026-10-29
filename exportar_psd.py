# -*- coding: utf-8 -*-
"""
Exportação em PSD (Photoshop) em camadas editáveis:
- Gravação binária nativa do formato Adobe Photoshop (.psd) em Python (rápida, sem depender de janela COM do Photoshop aberta e sem falhas de ExtendScript)
- Camada de base: "Fundo da Lamina" já mesclada com todas as configurações (cor, foto de fundo, giro 90°, espelhamento, preenchimento/distorção, desfoque, tingimento, opacidade, degradê e fusão entre páginas)
- Cada foto da lâmina em sua própria camada individual posicionada nas coordenadas exatas (x, y, w, h), já com ajustes de cor, moldura e degradê de opacidade aplicados
- Cada molde PNG decorativo em sua própria camada transparente acima das fotos
- Imagem composta mesclada embutida no final do PSD (Maximize Compatibility) + resolução em DPI (0x03ED) + perfil de cor ICC (0x040F: sRGB ou Adobe RGB 1998)
"""

import struct
from pathlib import Path

from PIL import Image, ImageChops

from comum import cor_rgb, mm_para_px
from composicao import (
    aplicar_moldura,
    compor_fundo_completo,
    gerar_mascara_degrade,
    rosto_de,
)
from produtos import caixa_px, lamina_mm, lamina_px
from recorte import foto_no_espaco
import exportar
import photoshop


JSX_LAMINA = r"""
var regua = app.preferences.rulerUnits;
app.preferences.rulerUnits = Units.PIXELS;

function pxVal(u) {
    if (typeof u === "number") return u;
    try { return Number(u.as("px")); } catch (e) {}
    try { return Number(u.value); } catch (e2) {}
    return Number(u) || 0;
}

function pintarFundo(doc, rgb) {
    var cor = new SolidColor();
    cor.rgb.red = rgb[0];
    cor.rgb.green = rgb[1];
    cor.rgb.blue = rgb[2];
    doc.selection.selectAll();
    doc.selection.fill(cor);
    doc.selection.deselect();
}

function colarCamadaPos(doc, s) {
    var arq = app.open(new File(s.arquivo));
    try {
        var camada = arq.activeLayer;
        if (camada.isBackgroundLayer) {
            camada.name = s.nome || "Camada";
        }
        var copia = camada.duplicate(doc, ElementPlacement.PLACEATBEGINNING);
    } finally {
        arq.close(SaveOptions.DONOTSAVECHANGES);
    }
    doc.activeLayer = copia;
    copia.name = s.nome || "Camada";
    var b = copia.bounds;
    var bx = pxVal(b[0]);
    var by = pxVal(b[1]);
    var dx = (s.x || 0) + (s.dx || 0) - bx;
    var dy = (s.y || 0) + (s.dy || 0) - by;
    if (Math.abs(dx) > 0.01 || Math.abs(dy) > 0.01) {
        copia.translate(dx, dy);
    }
    if (s.opacidade !== undefined && s.opacidade < 100) {
        copia.opacity = s.opacidade;
    }
}

try {
    var doc = app.documents.add(
        UnitValue(JOB.largura, "px"),
        UnitValue(JOB.altura, "px"),
        JOB.dpi,
        JOB.nome,
        NewDocumentMode.RGB,
        DocumentFill.WHITE
    );
    try { doc.colorProfileName = JOB.perfil_nome || "sRGB IEC61966-2.1"; } catch (e) {}
    pintarFundo(doc, JOB.fundo);
    for (var m = 0; m < JOB.fundos.length; m++) {
        colarCamadaPos(doc, JOB.fundos[m]);
    }
    for (var i = 0; i < JOB.fotos.length; i++) {
        colarCamadaPos(doc, JOB.fotos[i]);
    }
    for (var j = 0; j < JOB.moldes.length; j++) {
        colarCamadaPos(doc, JOB.moldes[j]);
    }
    try {
        if (doc.artLayers.length > 1 && doc.artLayers[doc.artLayers.length - 1].isBackgroundLayer) {
            doc.artLayers[doc.artLayers.length - 1].remove();
        }
    } catch (eBg) {}
    if (JOB.acao && JOB.acao.conjunto && JOB.acao.acao) {
        app.doAction(JOB.acao.acao, JOB.acao.conjunto);
    }
    salvarComo(doc, new File(JOB.saida), "psd", 12);
    doc.close(SaveOptions.DONOTSAVECHANGES);
} finally {
    app.preferences.rulerUnits = regua;
}
"""


def _packbits_linha(raw_bytes):
    """
    Comprime uma linha de pixels (scanline) usando o algoritmo PackBits RLE padrão do Adobe Photoshop.
    Retorna bytes comprimidos garantidamente compatíveis com qualquer leitor PSD.
    """
    n = len(raw_bytes)
    if n == 0:
        return b""
    out = bytearray()
    i = 0
    while i < n:
        run_len = 1
        b0 = raw_bytes[i]
        max_run = min(128, n - i)
        while run_len < max_run and raw_bytes[i + run_len] == b0:
            run_len += 1
        if run_len >= 3:
            out.append((257 - run_len) & 0xFF)
            out.append(b0)
            i += run_len
            continue

        lit_start = i
        lit_len = 0
        max_lit = min(128, n - i)
        while lit_len < max_lit:
            rem = n - (lit_start + lit_len)
            if rem >= 3:
                c0 = raw_bytes[lit_start + lit_len]
                if raw_bytes[lit_start + lit_len + 1] == c0 and raw_bytes[lit_start + lit_len + 2] == c0:
                    break
            lit_len += 1
        out.append(lit_len - 1)
        out.extend(raw_bytes[lit_start : lit_start + lit_len])
        i += lit_len
    return bytes(out)


def _comprimir_canal_rle(canal_img):
    """
    Recebe uma imagem PIL modo 'L' (8 bits) de tamanho (w, h) e retorna o bloco
    de dados do canal PSD comprimido com PackBits (compression = 1):
    [uint16 compression=1] + [h * uint16 byte_counts] + [linhas comprimidas].
    """
    w, h = canal_img.size
    if w <= 0 or h <= 0:
        return struct.pack(">H", 1)
    raw = canal_img.tobytes()
    tamanhos = []
    linhas_comp = []
    for y in range(h):
        ini = y * w
        linha = _packbits_linha(raw[ini : ini + w])
        tamanhos.append(len(linha))
        linhas_comp.append(linha)
    cabecalho = struct.pack(">H" + "H" * h, 1, *tamanhos)
    return cabecalho + b"".join(linhas_comp)


def _bloco_recurso_psd(res_id, dados_bytes):
    """Monta um Image Resource Block (8BIM) com padding par."""
    if len(dados_bytes) % 2 == 1:
        dados_pad = dados_bytes + b"\x00"
    else:
        dados_pad = dados_bytes
    return struct.pack(">4sHHI", b"8BIM", res_id, 0, len(dados_bytes)) + dados_pad


def _montar_image_resources(dpi, icc_bytes=None):
    """
    Monta a seção Image Resources do PSD:
    - 0x03ED (1005): ResolutionInfo (DPI horizontal e vertical em ponto fixo 16.16)
    - 0x040F (1039): ICC Profile (sRGB ou Adobe RGB 1998), quando presente
    """
    blocos = []
    dpi_val = max(72, int(round(float(dpi or 300))))
    res_fixed = int(round(dpi_val * 65536))
    dados_res = struct.pack(">IHHIHH", res_fixed, 1, 1, res_fixed, 1, 1)
    blocos.append(_bloco_recurso_psd(0x03ED, dados_res))

    if icc_bytes:
        blocos.append(_bloco_recurso_psd(0x040F, bytes(icc_bytes)))

    conteudo = b"".join(blocos)
    return struct.pack(">I", len(conteudo)) + conteudo


def _montar_extra_data_camada(nome_str):
    """
    Monta o Extra Data Field de uma camada PSD:
    - Layer Mask Data (4 bytes = 0)
    - Layer Blending Ranges (4 bytes = 0)
    - Layer Name em Pascal String (padded para múltiplo de 4 bytes)
    - Additional Layer Information 'luni' (nome Unicode UTF-16BE completo para acentos)
    """
    nome_limpo = (nome_str or "Camada").strip()[:250]
    nome_latin = nome_limpo.encode("latin-1", errors="replace")[:255]
    tam_pascal = 1 + len(nome_latin)
    pad_pascal = (4 - (tam_pascal % 4)) % 4
    pascal_bytes = struct.pack(">B", len(nome_latin)) + nome_latin + (b"\x00" * pad_pascal)

    utf16 = nome_limpo.encode("utf-16-be")
    num_chars = len(utf16) // 2
    luni_payload = struct.pack(">I", num_chars) + utf16
    if len(luni_payload) % 2 == 1:
        luni_payload += b"\x00"
    if len(luni_payload) % 4 != 0:
        luni_payload += b"\x00" * ((4 - (len(luni_payload) % 4)) % 4)
    bloco_luni = struct.pack(">4s4sI", b"8BIM", b"luni", len(luni_payload)) + luni_payload

    corpo = struct.pack(">II", 0, 0) + pascal_bytes + bloco_luni
    return struct.pack(">I", len(corpo)) + corpo


def gravar_psd_em_camadas(caminho_psd, largura, altura, dpi, camadas, composta_rgb, icc_bytes=None):
    """
    Grava um arquivo Adobe Photoshop (.psd) multicamada 100% válido:
    - `camadas`: lista de dicts {"nome": str, "img": PIL.Image (RGBA ou RGB), "x": int, "y": int, "opacidade": int(0..100)}
      na ordem de baixo para cima (a primeira é o Fundo da Lâmina, seguidas pelas Fotos e Moldes).
    - `composta_rgb`: PIL.Image (RGB) de tamanho (largura, altura) contendo a imagem mesclada final.
    """
    caminho_psd = Path(caminho_psd)
    caminho_psd.parent.mkdir(parents=True, exist_ok=True)
    largura = int(largura)
    altura = int(altura)

    if composta_rgb.mode != "RGB":
        composta_rgb = composta_rgb.convert("RGB")
    if composta_rgb.size != (largura, altura):
        composta_rgb = composta_rgb.resize((largura, altura), Image.LANCZOS)

    # 1. Cabeçalho PSD (26 bytes): 8BPS, v1, 3 canais no composite (RGB), 8 bits por canal, modo RGB (3)
    header = struct.pack(">4sH6sHIIHH", b"8BPS", 1, b"\x00" * 6, 3, altura, largura, 8, 3)

    # 2. Color Mode Data (4 bytes = 0)
    color_mode_data = struct.pack(">I", 0)

    # 3. Image Resources (DPI + Perfil ICC)
    image_resources = _montar_image_resources(dpi, icc_bytes)

    # 4. Layer and Mask Information
    registros_camadas = []
    dados_canais_todas = []

    for cam in camadas:
        img_c = cam["img"]
        if img_c.mode != "RGBA":
            img_c = img_c.convert("RGBA")
        cw, ch = img_c.size
        x0 = int(round(cam.get("x", 0)))
        y0 = int(round(cam.get("y", 0)))
        x1 = x0 + cw
        y1 = y0 + ch

        if x0 < 0 or y0 < 0 or x1 > largura or y1 > altura:
            nx0 = max(0, x0)
            ny0 = max(0, y0)
            nx1 = min(largura, x1)
            ny1 = min(altura, y1)
            if nx1 > nx0 and ny1 > ny0:
                img_c = img_c.crop((nx0 - x0, ny0 - y0, nx1 - x0, ny1 - y0))
                x0, y0, x1, y1 = nx0, ny0, nx1, ny1
            else:
                continue

        r_ch, g_ch, b_ch, a_ch = img_c.split()
        canais_comp = [
            (-1, _comprimir_canal_rle(a_ch)),
            (0, _comprimir_canal_rle(r_ch)),
            (1, _comprimir_canal_rle(g_ch)),
            (2, _comprimir_canal_rle(b_ch)),
        ]

        opac_pct = float(cam.get("opacidade", 100.0))
        opac_255 = max(0, min(255, int(round(opac_pct * 255.0 / 100.0))))

        cab_bounds = struct.pack(">iiiiH", y0, x0, y1, x1, len(canais_comp))
        info_canais = b"".join(struct.pack(">hI", cid, len(cdata)) for cid, cdata in canais_comp)
        cab_blend = struct.pack(">4s4sBBBB", b"8BIM", b"norm", opac_255, 0, 0x08, 0)
        extra_data = _montar_extra_data_camada(cam.get("nome") or "Camada")

        registros_camadas.append(cab_bounds + info_canais + cab_blend + extra_data)
        for _cid, cdata in canais_comp:
            dados_canais_todas.append(cdata)

    if registros_camadas:
        layer_info_body = (
            struct.pack(">h", len(registros_camadas))
            + b"".join(registros_camadas)
            + b"".join(dados_canais_todas)
        )
        if len(layer_info_body) % 2 == 1:
            layer_info_body += b"\x00"
        layer_info_section = struct.pack(">I", len(layer_info_body)) + layer_info_body
        global_mask_info = struct.pack(">I", 0)
        layer_and_mask_body = layer_info_section + global_mask_info
        layer_and_mask_section = struct.pack(">I", len(layer_and_mask_body)) + layer_and_mask_body
    else:
        layer_and_mask_section = struct.pack(">I", 0)

    # 5. Composite Image Data Section (Maximize Compatibility: R, G, B comprimidos em PackBits)
    r_comp, g_comp, b_comp = composta_rgb.split()
    linhas_tamanhos = []
    linhas_dados = []
    for ch_comp in (r_comp, g_comp, b_comp):
        raw_c = ch_comp.tobytes()
        for y in range(altura):
            ini = y * largura
            linha_rle = _packbits_linha(raw_c[ini : ini + largura])
            linhas_tamanhos.append(len(linha_rle))
            linhas_dados.append(linha_rle)

    composite_header = struct.pack(">H" + ("H" * (3 * altura)), 1, *linhas_tamanhos)
    composite_section = composite_header + b"".join(linhas_dados)

    temp = caminho_psd.with_name(caminho_psd.stem + ".psd_tmp")
    with open(temp, "wb") as fp:
        fp.write(header)
        fp.write(color_mode_data)
        fp.write(image_resources)
        fp.write(layer_and_mask_section)
        fp.write(composite_section)
    temp.replace(caminho_psd)

    return caminho_psd


def _renderizar_camadas_lamina(lamina, prod_dpi, info, W, H, px_mm, off_x=0, off_y=0, problemas=None):
    """
    Constrói a lista ordenada de camadas (de baixo para cima) para gravar no PSD:
    1) Camada única "Fundo da Lamina" já mesclada com todos os efeitos e configurações
    2) Uma camada para cada foto posicionada em (x + off_x, y + off_y)
    3) Uma camada para cada molde PNG posicionado em (x + off_x, y + off_y)
    """
    camadas = []
    fundo_cor = lamina.get("fundo") or "#ffffff"
    fundo_rgb = cor_rgb(fundo_cor)

    # 1. Camada única de fundo mesclada com todas as configurações (cor, imagem, giro, espelho, corte/distorção, desfoque, tingimento, degradê, fusão)
    tela_fundo = compor_fundo_completo(
        (W, H),
        fundo_cor,
        lamina.get("fundo_imagem"),
        rapido=False,
        opcoes_pagina=lamina,
    )
    camadas.append({
        "nome": "Fundo da Lamina",
        "img": tela_fundo.convert("RGBA"),
        "x": int(off_x),
        "y": int(off_y),
        "opacidade": 100,
    })

    # 2. Camadas individuais de cada foto
    for idx, espaco in enumerate(lamina.get("espacos", []), start=1):
        caminho = espaco.get("foto")
        if not caminho:
            continue
        if not Path(caminho).is_file():
            if problemas is not None:
                problemas.append("  espaço %d: arquivo não encontrado (%s)" % (idx, caminho))
            continue

        x, y, w, h = caixa_px(espaco, (W, H))
        edicao = espaco.get("edicao") or {}
        ex = float(espaco.get("x", 0.0))
        ew = float(espaco.get("w", 0.0))
        dobra_x = (0.5 - ex) / ew if (ew > 0.05 and ex < 0.495 and ex + ew > 0.505) else None

        try:
            pronta = foto_no_espaco(
                caminho,
                edicao,
                rosto_de(info, caminho),
                (w, h),
                fundo_rgb,
                rapido=False,
                dobra_x=dobra_x,
            ).convert("RGBA")
            aplicar_moldura(pronta, edicao.get("moldura"), px_mm)

            # Aplica máscara de degradê de opacidade da foto diretamente no canal alfa da camada
            mascara_deg = gerar_mascara_degrade((w, h), edicao.get("degrade"), 100)
            if mascara_deg is not None:
                pronta.putalpha(ImageChops.multiply(pronta.getchannel("A"), mascara_deg))

            opac_foto = max(5, min(100, int(round(float(edicao.get("opacidade", 100))))))
            nome_camada = "Foto %02d - %s" % (idx, Path(caminho).stem)
            camadas.append({
                "nome": nome_camada,
                "img": pronta,
                "x": int(off_x + x),
                "y": int(off_y + y),
                "opacidade": opac_foto,
            })
        except Exception as e:
            if problemas is not None:
                problemas.append("  espaço %d: erro ao processar %s (%s)" % (idx, Path(caminho).name, e))

    # 3. Camadas de moldes / sobreposições PNG
    for idx_m, s in enumerate(lamina.get("sobreposicoes") or [], start=1):
        caminho_m = s.get("png")
        if not caminho_m or not Path(caminho_m).is_file():
            continue
        try:
            x, y, w, h = caixa_px(s, (W, H))
            with Image.open(caminho_m) as png:
                png = png.convert("RGBA")
                if png.size != (w, h):
                    png = png.resize((w, h), Image.LANCZOS)
            caixa_alfa = png.getchannel("A").getbbox()
            if not caixa_alfa:
                continue
            rec_img = png.crop(caixa_alfa)
            dx, dy = caixa_alfa[0], caixa_alfa[1]
            opac_m = s.get("opacidade", 100)
            if isinstance(opac_m, float) and opac_m <= 1.0:
                opac_m = int(round(opac_m * 100))
            camadas.append({
                "nome": "Molde %02d - %s" % (idx_m, Path(caminho_m).stem),
                "img": rec_img,
                "x": int(off_x + x + dx),
                "y": int(off_y + y + dy),
                "opacidade": max(5, min(100, int(round(float(opac_m))))),
            })
        except Exception:
            continue

    return camadas


def exportar_psd(projeto, produto, info, pasta_saida, aviso=print, parar=lambda: False, ao_progresso=None, opcoes=None):
    """
    Exporta as lâminas escolhidas em arquivos .psd multicamadas na pasta `pasta_saida`.
    Grava diretamente o binário PSD em camadas (sem depender de COM/JSX do Photoshop, evitando erros ao gravar).
    """
    op = exportar.opcoes_padrao()
    op.update(opcoes or {})
    prod_dpi = exportar.produto_com_dpi(produto, op.get("dpi"))
    dpi_escolhido = int(prod_dpi.get("dpi", 300))
    perfil_cor = op.get("perfil_cor", "sRGB")
    icc = exportar.perfil_icc(prod_dpi, perfil_cor) if op.get("icc", True) else None
    marcas_registro = bool(op.get("marcas_registro", False))

    W, H = lamina_px(prod_dpi)
    w_mm, _h_mm = lamina_mm(prod_dpi)
    px_mm = W / float(max(1.0, w_mm))

    pasta_saida = Path(pasta_saida)
    pasta_saida.mkdir(parents=True, exist_ok=True)

    itens = exportar.itens_exportacao(projeto, op.get("indices"), op["padrao"], prod_dpi)
    total_album = len(projeto.get("laminas", []))
    gravados = []

    for n, (nome, item) in enumerate(itens, start=1):
        if parar and parar():
            if aviso:
                aviso("Parado.")
            break
        destino = pasta_saida / (nome + ".psd")
        if op.get("existentes") == "pular" and destino.exists():
            if aviso:
                aviso("Pulou %s (já existe)" % destino.name)
            if ao_progresso:
                ao_progresso(n, len(itens))
            continue

        lamina = projeto["laminas"][item] if isinstance(item, int) else None
        if lamina is None:
            continue

        problemas = []
        composta = exportar._renderizar_item_exportacao(
            projeto,
            prod_dpi,
            info,
            item,
            (item + 1) if isinstance(item, int) else n,
            total_album,
            op,
            problemas,
        )
        try:
            if op.get("icc", True):
                composta_final = exportar.converter_para_perfil(composta, perfil_cor)
            else:
                composta_final = composta.convert("RGB")

            larg_doc, alt_doc = composta_final.size
            off_x = max(0, (larg_doc - W) // 2) if marcas_registro else 0
            off_y = max(0, (alt_doc - H) // 2) if marcas_registro else 0

            camadas_psd = []
            if marcas_registro and (off_x > 0 or off_y > 0):
                camadas_psd.append({
                    "nome": "Folha e Marcas de Registro Industrial",
                    "img": composta_final.convert("RGBA"),
                    "x": 0,
                    "y": 0,
                    "opacidade": 100,
                })

            camadas_lamina = _renderizar_camadas_lamina(
                lamina, prod_dpi, info, W, H, px_mm, off_x=off_x, off_y=off_y, problemas=problemas
            )
            camadas_psd.extend(camadas_lamina)

            gravar_psd_em_camadas(
                destino,
                larg_doc,
                alt_doc,
                dpi_escolhido,
                camadas_psd,
                composta_final,
                icc_bytes=icc,
            )

            # Caso uma ação opcional do Photoshop tenha sido configurada
            acao_cfg = op.get("acao")
            if isinstance(acao_cfg, dict) and acao_cfg.get("conjunto") and acao_cfg.get("acao"):
                try:
                    photoshop.aplicar_acao_em_arquivo(destino, acao_cfg["conjunto"], acao_cfg["acao"])
                except Exception as e_acao:
                    if aviso:
                        aviso("PSD gravado, mas a ação do Photoshop não pôde rodar: %s" % e_acao)

            for linha in problemas:
                if aviso:
                    aviso(linha)
            gravados.append(destino)
            if aviso:
                aviso(
                    "Gravou %s (%d camadas · %d DPI · %s%s)"
                    % (
                        destino.name,
                        len(camadas_psd),
                        dpi_escolhido,
                        perfil_cor,
                        " · c/ marcas de corte" if marcas_registro else "",
                    )
                )
        finally:
            try:
                composta.close()
            except Exception:
                pass

        if ao_progresso:
            ao_progresso(n, len(itens))

    return gravados
