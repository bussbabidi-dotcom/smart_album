# -*- coding: utf-8 -*-
"""
Exportação em JPG e PDF Industrial: uma imagem por lâmina aberta ou PDF multipágina,
com escolha de DPI (150/300), perfil de cor (sRGB/AdobeRGB) e marcas de registro
para corte industrial (marcas de guilhotina, dobra central, alvos ⊕ e tira técnica).
"""

import io
import os
import re
import struct
from pathlib import Path

from PIL import Image, ImageCms, ImageDraw, ImageFont

import capa as capa_mod
from composicao import compor, desenhar_guias
from comum import mm_para_px
from produtos import lamina_mm, lamina_px

QUALIDADE_JPG = 95
PADRAO_NOME = "{n:02d} - lamina"
CARACTERES_PROIBIDOS = r'[<>:"/\\|?*\x00-\x1f]'
DPIS_DISPONIVEIS = (150, 300)
PERFIS_COR = ("sRGB", "AdobeRGB")
MARGEM_REGISTRO_MM = 10.0


def opcoes_padrao():
    """Como a exportação funciona sem escolher nada: compatível com o jeito antigo + opções de PDF industrial."""
    return {
        "formato": "jpg",
        "qualidade": QUALIDADE_JPG,
        "icc": True,
        "dpi": 300,
        "perfil_cor": "sRGB",
        "marcas_registro": False,
        "pdf_unico": True,
        "pasta": "",
        "subpasta": False,
        "laminas": "todas",
        "intervalo": "",
        "padrao": PADRAO_NOME,
        "existentes": "substituir",
    }


def produto_com_dpi(produto, dpi=None):
    """Retorna cópia do produto ajustada para o DPI escolhido (150 ou 300)."""
    if not produto:
        return produto
    copia = dict(produto)
    if dpi in DPIS_DISPONIVEIS:
        copia["dpi"] = int(dpi)
    elif dpi is not None:
        try:
            d = int(dpi)
            copia["dpi"] = 150 if abs(d - 150) < abs(d - 300) else 300
        except (TypeError, ValueError):
            pass
    return copia


def laminas_do_texto(texto, total):
    """'1-5, 8' em índices de lâmina (0..total-1), em ordem. Levanta ValueError se não entender."""
    indices = set()
    for parte in re.split(r"[,;\s]+", str(texto or "").strip()):
        if not parte:
            continue
        achado = re.fullmatch(r"(\d+)(?:-(\d+))?", parte)
        if not achado:
            raise ValueError("Não entendi '%s'. Use números e intervalos, como 1-5, 8." % parte)
        a = int(achado.group(1))
        b = int(achado.group(2) or a)
        if a > b:
            a, b = b, a
        if a < 1 or b > total:
            raise ValueError("As lâminas vão de 1 a %s (pediu %s)." % (total, parte))
        indices.update(range(a - 1, b))
    if not indices:
        raise ValueError("Escreva quais lâminas, como 1-5, 8.")
    return sorted(indices)


def nome_arquivo(padrao, n, projeto, produto, total=None):
    """Nome sem extensão pelo padrão ({n}, {n:02d}, {album}, {produto}, {total})."""
    dados = {
        "n": n,
        "album": projeto.get("nome") or "album",
        "produto": (produto or {}).get("nome", ""),
        "total": total if total is not None else len(projeto.get("laminas", [])),
    }
    try:
        nome = str(padrao or PADRAO_NOME).format(**dados)
    except (KeyError, ValueError, IndexError) as e:
        raise ValueError("Padrão de nome inválido (%s). Use {n}, {n:02d}, {album}, {produto} ou {total}." % e)
    nome = re.sub(CARACTERES_PROIBIDOS, "_", nome).strip().rstrip(".")
    if not nome:
        raise ValueError("O padrão de nome ficou vazio.")
    return nome


def nome_pdf_unico(projeto, produto):
    """Nome do arquivo PDF único do álbum."""
    base = nome_arquivo("{album}", 1, projeto, produto)
    return base + ".pdf"


def tamanho_lamina(produto, largura=None):
    tamanho = lamina_px(produto)
    if largura:
        escala = largura / float(tamanho[0])
        return (int(largura), max(1, int(round(tamanho[1] * escala))))
    return tamanho


def tamanho_final_px(produto, dpi=None, marcas_registro=False):
    """(largura_px, altura_px) finais considerando DPI (150/300) e margem de registro industrial."""
    prod = produto_com_dpi(produto, dpi)
    w, h = lamina_px(prod)
    if marcas_registro:
        m = int(round(mm_para_px(MARGEM_REGISTRO_MM, prod["dpi"])))
        return (w + 2 * m, h + 2 * m)
    return (w, h)


def _guias_lamina(img, produto, margem=None):
    from produtos import area_util_pagina_mm
    W_mm, H_mm = lamina_mm(produto)
    k = img.width / W_mm
    b = produto["sangria_mm"] * k
    pw = produto["pagina_mm"][0] * k
    n = produto["paginas_por_lamina"]
    sangria = [(b, b, img.width - b, img.height - b)]
    seguranca = []
    for i in range(n):
        ux, uy, uw, uh = area_util_pagina_mm(produto, i, margem, respeitar_seguranca=True)
        seguranca.append((ux * k, uy * k, (ux + uw) * k, (uy + uh) * k))
    dobras = [b + pw * i for i in range(1, n)]
    return desenhar_guias(img, sangria, seguranca, dobras)


def renderizar_lamina(lamina, produto, info, largura=None, guias=False, rapido=False, ao_espaco=None, sem_fundo=False):
    """sem_fundo: só fotos e moldes em RGBA transparente (para a prévia rápida do fundo)."""
    tamanho = tamanho_lamina(produto, largura)
    img = compor(
        tamanho, None if sem_fundo else lamina.get("fundo", "#ffffff"), lamina.get("espacos", []),
        lamina.get("sobreposicoes", []), info, rapido, ao_espaco, None if sem_fundo else lamina.get("fundo_imagem"),
        tamanho[0] / float(lamina_mm(produto)[0]), opcoes_pagina=None if sem_fundo else lamina,
    )
    if guias and not sem_fundo:
        _guias_lamina(img, produto)
    return img


# ------------------------------------------------------------------
# Perfis de cor ICC (sRGB e AdobeRGB 1998)
# ------------------------------------------------------------------

def _s15Fixed16(val):
    return struct.pack(">i", int(round(val * 65536.0)))


def _xyz_tag(x, y, z):
    return b"XYZ \x00\x00\x00\x00" + _s15Fixed16(x) + _s15Fixed16(y) + _s15Fixed16(z)


def _curv_gamma_tag(gamma):
    g_u8fixed8 = int(round(gamma * 256.0))
    return struct.pack(">4sIIH2x", b"curv", 0, 1, g_u8fixed8)


def _desc_tag(texto):
    ascii_bytes = texto.encode("ascii", errors="replace") + b"\x00"
    n = len(ascii_bytes)
    bloco = struct.pack(">4sII", b"desc", 0, n) + ascii_bytes + (b"\x00" * 70)
    if len(bloco) % 4:
        bloco += b"\x00" * (4 - (len(bloco) % 4))
    return bloco


def _text_tag(texto):
    b = b"text\x00\x00\x00\x00" + texto.encode("ascii", errors="replace") + b"\x00"
    if len(b) % 4:
        b += b"\x00" * (4 - (len(b) % 4))
    return b


def _criar_icc_adobe_rgb():
    """
    Constrói um perfil ICC v2.1 padrão para Adobe RGB (1998) (D50 PCS, gama 2.19921875).
    Compatível com Photoshop, Acrobat, indústrias gráficas e Pillow ImageCms.
    """
    tags = [
        (b"desc", _desc_tag("Adobe RGB (1998)")),
        (b"cprt", _text_tag("Copyright Adobe Systems Incorporated")),
        (b"wtpt", _xyz_tag(0.95045, 1.00000, 1.08905)),
        (b"bkpt", _xyz_tag(0.0, 0.0, 0.0)),
        (b"rXYZ", _xyz_tag(0.60974, 0.31111, 0.01947)),
        (b"gXYZ", _xyz_tag(0.20528, 0.62567, 0.06087)),
        (b"bXYZ", _xyz_tag(0.14919, 0.06322, 0.74457)),
        (b"rTRC", _curv_gamma_tag(2.19921875)),
        (b"gTRC", _curv_gamma_tag(2.19921875)),
        (b"bTRC", _curv_gamma_tag(2.19921875)),
    ]
    offset_inicial = 128 + 4 + 12 * len(tags)
    tabela = [struct.pack(">I", len(tags))]
    corpo = bytearray()
    for sig, dados in tags:
        pos = offset_inicial + len(corpo)
        tam = len(dados)
        tabela.append(struct.pack(">4sII", sig, pos, tam))
        corpo.extend(dados)
        if len(corpo) % 4:
            corpo.extend(b"\x00" * (4 - (len(corpo) % 4)))
    tamanho_total = offset_inicial + len(corpo)
    cabecalho = bytearray(128)
    struct.pack_into(">I", cabecalho, 0, tamanho_total)
    cabecalho[4:8] = b"ADBE"
    struct.pack_into(">I", cabecalho, 8, 0x02100000)
    cabecalho[12:16] = b"mntr"
    cabecalho[16:20] = b"RGB "
    cabecalho[20:24] = b"XYZ "
    struct.pack_into(">6H", cabecalho, 24, 2024, 1, 1, 0, 0, 0)
    cabecalho[36:40] = b"acsp"
    cabecalho[40:44] = b"APPL"
    struct.pack_into(">i", cabecalho, 68, int(round(0.9642 * 65536)))
    struct.pack_into(">i", cabecalho, 72, int(round(1.0000 * 65536)))
    struct.pack_into(">i", cabecalho, 76, int(round(0.8249 * 65536)))
    cabecalho[80:84] = b"ADBE"
    return bytes(cabecalho) + b"".join(tabela) + bytes(corpo)


def perfil_icc(produto=None, perfil_cor=None):
    """
    Retorna os bytes do perfil ICC escolhido ('sRGB' ou 'AdobeRGB'),
    ou o arquivo .icc/.icm configurado no produto quando não especificado.
    """
    escolha = str(perfil_cor or "").strip()
    if escolha == "AdobeRGB":
        return _criar_icc_adobe_rgb()
    if escolha != "sRGB" and produto:
        caminho = produto.get("icc")
        if caminho and Path(caminho).is_file():
            return Path(caminho).read_bytes()
    try:
        return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    except Exception:
        return None


def converter_para_perfil(img, perfil_cor="sRGB"):
    """
    Converte os pixels RGB de sRGB para AdobeRGB (1998) se 'AdobeRGB' for escolhido,
    garantindo fidelidade cromática real em impressão industrial.
    """
    if img.mode != "RGB":
        img = img.convert("RGB")
    if str(perfil_cor or "sRGB") != "AdobeRGB":
        return img
    try:
        p_srgb = ImageCms.createProfile("sRGB")
        p_adobe = ImageCms.ImageCmsProfile(io.BytesIO(_criar_icc_adobe_rgb()))
        return ImageCms.profileToProfile(img, p_srgb, p_adobe, outputMode="RGB")
    except Exception:
        return img


# ------------------------------------------------------------------
# Marcas de registro para corte industrial
# ------------------------------------------------------------------

def _desenhar_alvo_registro(draw, cx, cy, raio, espessura=1):
    """Desenha um alvo de registro industrial ⊕ (círculos concêntricos + mira de precisão)."""
    r_ext = max(4, int(round(raio)))
    r_int = max(2, int(round(raio * 0.45)))
    ext = max(6, int(round(raio * 1.45)))
    draw.ellipse((cx - r_ext, cy - r_ext, cx + r_ext, cy + r_ext), outline=(0, 0, 0), width=espessura)
    draw.ellipse((cx - r_int, cy - r_int, cx + r_int, cy + r_int), fill=(0, 0, 0))
    draw.line((cx - ext, cy, cx + ext, cy), fill=(0, 0, 0), width=espessura)
    draw.line((cx, cy - ext, cx, cy + ext), fill=(0, 0, 0), width=espessura)
    # Cruz branca fina dentro do núcleo escuro para contraste óptico de leitura
    draw.line((cx - r_int + 1, cy, cx + r_int - 1, cy), fill=(255, 255, 255), width=max(1, espessura // 2))
    draw.line((cx, cy - r_int + 1, cx, cy + r_int - 1), fill=(255, 255, 255), width=max(1, espessura // 2))


def aplicar_marcas_registro(
    img,
    produto,
    dpi=None,
    perfil_cor="sRGB",
    indice_lamina=1,
    total_laminas=1,
    nome_album="album",
):
    """
    Adiciona margem técnica externa (slug de 10 mm) ao redor da lâmina com sangria e desenha:
    - Marcas de corte de guilhotina nos 4 cantos (alinhadas exatamente na linha de refilo final);
    - Marcas de dobra central (vinco) no topo e na base;
    - 4 alvos ópticos de registro industrial (⊕) nos centros das margens;
    - Tira de controle de cor/densidade (CMYK/RGB + escala de cinza) e rodapé técnico.
    """
    prod = produto_com_dpi(produto, dpi)
    dpi_efetivo = int(prod.get("dpi", 300))
    W_mm, H_mm = lamina_mm(prod)
    k = img.width / float(W_mm)
    m = max(16, int(round(MARGEM_REGISTRO_MM * k)))
    b = int(round(prod.get("sangria_mm", 3) * k))
    pw = prod["pagina_mm"][0] * k
    n_pags = int(prod.get("paginas_por_lamina", 2))

    # Tela expandida com área de slug branca para corte industrial
    nova_w = img.width + 2 * m
    nova_h = img.height + 2 * m
    base = Image.new("RGB", (nova_w, nova_h), (255, 255, 255))
    base.paste(img.convert("RGB"), (m, m))

    draw = ImageDraw.Draw(base)
    esp = max(1, int(round(k * 0.18)))
    esp_halo = esp + 2

    # Coordenadas exatas da linha de refilo (corte final da guilhotina, descontando a sangria)
    tx0 = m + b
    ty0 = m + b
    tx1 = m + img.width - b
    ty1 = m + img.height - b

    # Borda sutil delimitando o fim da sangria
    draw.rectangle((m, m, m + img.width - 1, m + img.height - 1), outline=(215, 215, 215), width=1)

    # 1. Marcas de corte nos 4 cantos (param na borda da sangria para não invadir a foto refilada)
    afastamento = max(2, int(round(k * 0.8)))
    comp_marca = max(8, m - afastamento - max(2, int(round(k * 1.2))))

    cantos_linhas = [
        # Superior esquerdo
        ((tx0 - afastamento - comp_marca, ty0, tx0 - afastamento, ty0)),
        ((tx0, ty0 - afastamento - comp_marca, tx0, ty0 - afastamento)),
        # Superior direito
        ((tx1 + afastamento, ty0, tx1 + afastamento + comp_marca, ty0)),
        ((tx1, ty0 - afastamento - comp_marca, tx1, ty0 - afastamento)),
        # Inferior esquerdo
        ((tx0 - afastamento - comp_marca, ty1, tx0 - afastamento, ty1)),
        ((tx0, ty1 + afastamento, tx0, ty1 + afastamento + comp_marca)),
        # Inferior direito
        ((tx1 + afastamento, ty1, tx1 + afastamento + comp_marca, ty1)),
        ((tx1, ty1 + afastamento, tx1, ty1 + afastamento + comp_marca)),
    ]
    for coords in cantos_linhas:
        draw.line(coords, fill=(255, 255, 255), width=esp_halo)
        draw.line(coords, fill=(0, 0, 0), width=esp)

    # 2. Marcas de dobra central (vinco da lâmina panorâmica)
    for i in range(1, n_pags):
        dx = int(round(m + b + pw * i))
        # Topo e base: traço tracejado de dobra
        y_top_0 = max(2, ty0 - afastamento - comp_marca)
        y_top_1 = ty0 - afastamento
        y_bot_0 = ty1 + afastamento
        y_bot_1 = min(nova_h - 2, ty1 + afastamento + comp_marca)
        passo = max(3, int(round(k * 1.1)))
        for y in range(y_top_0, y_top_1, passo * 2):
            draw.line((dx, y, dx, min(y + passo, y_top_1)), fill=(0, 0, 0), width=esp)
        for y in range(y_bot_0, y_bot_1, passo * 2):
            draw.line((dx, y, dx, min(y + passo, y_bot_1)), fill=(0, 0, 0), width=esp)

    # 3. Alvos de registro óptico (⊕) nos 4 lados
    raio_alvo = max(4, int(round(m * 0.26)))
    meio_x = nova_w // 2
    meio_y = nova_h // 2
    # Desloca levemente o alvo superior/inferior do centro exato se houver linha de dobra no meio
    offset_alvo_x = int(round(k * 12)) if n_pags > 1 else 0
    _desenhar_alvo_registro(draw, meio_x - offset_alvo_x, m // 2, raio_alvo, esp)
    _desenhar_alvo_registro(draw, meio_x + offset_alvo_x, nova_h - m // 2, raio_alvo, esp)
    _desenhar_alvo_registro(draw, m // 2, meio_y, raio_alvo, esp)
    _desenhar_alvo_registro(draw, nova_w - m // 2, meio_y, raio_alvo, esp)

    # 4. Tira de controle de cor/densidade no topo direito do slug
    quad_l = max(6, int(round(m * 0.42)))
    quad_y0 = max(2, (m - quad_l) // 2)
    cores_controle = [
        (0, 174, 239),    # Cyan
        (236, 0, 140),    # Magenta
        (255, 242, 0),    # Yellow
        (35, 31, 32),     # Black
        (237, 28, 36),    # Red
        (0, 166, 81),     # Green
        (46, 49, 146),    # Blue
        (64, 64, 64),     # 75% K
        (128, 128, 128),  # 50% K
        (192, 192, 192),  # 25% K
    ]
    x_barra = tx1 - len(cores_controle) * quad_l - int(round(k * 4))
    if x_barra > tx0 + 40:
        for idx_c, cor_c in enumerate(cores_controle):
            cx0 = x_barra + idx_c * quad_l
            draw.rectangle((cx0, quad_y0, cx0 + quad_l, quad_y0 + quad_l), fill=cor_c, outline=(120, 120, 120), width=1)

    # 5. Slug técnico de identificação no rodapé esquerdo
    trim_w_mm = int(round(prod["pagina_mm"][0] * n_pags))
    trim_h_mm = int(round(prod["pagina_mm"][1]))
    sangria_mm = prod.get("sangria_mm", 3)
    texto_slug = (
        "SMART ALBUM · %s · LAMINA %02d/%02d · REFILO %dx%dmm (SANGRIA %smm) · %d DPI · %s"
        % (
            str(nome_album or "ALBUM").upper(),
            int(indice_lamina or 1),
            int(total_laminas or 1),
            trim_w_mm,
            trim_h_mm,
            sangria_mm,
            dpi_efetivo,
            str(perfil_cor or "sRGB"),
        )
    )
    try:
        fonte = ImageFont.load_default()
        ty_texto = nova_h - m + max(2, (m - 10) // 2)
        draw.text((tx0 + int(round(k * 3)), ty_texto), texto_slug, fill=(40, 40, 40), font=fonte)
    except Exception:
        pass

    return base


# ------------------------------------------------------------------
# Gravação em disco (JPG e PDF)
# ------------------------------------------------------------------

def salvar_jpg(img, destino, produto, qualidade=QUALIDADE_JPG, icc=True, dpi=None, perfil_cor="sRGB"):
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    prod = produto_com_dpi(produto, dpi)
    dpi_val = int(prod.get("dpi", 300))
    img_final = converter_para_perfil(img, perfil_cor) if icc else img.convert("RGB")
    opcoes = {
        "quality": int(qualidade),
        "subsampling": 0,
        "optimize": True,
        "dpi": (dpi_val, dpi_val),
    }
    perfil = perfil_icc(prod, perfil_cor) if icc else None
    if perfil:
        opcoes["icc_profile"] = perfil
    temp = destino.with_name(destino.stem + ".album_tmp.jpg")
    img_final.save(temp, "JPEG", **opcoes)
    os.replace(temp, destino)


def salvar_pdf(imagens, destino, produto, icc=True, dpi=None, perfil_cor="sRGB", nome_album="Album"):
    """
    Salva uma ou várias imagens (PIL.Image) em um arquivo PDF industrial com resolução física
    exata (150 ou 300 DPI) e perfil de cor convertido (sRGB ou AdobeRGB).
    """
    if not imagens:
        return
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    prod = produto_com_dpi(produto, dpi)
    dpi_val = float(prod.get("dpi", 300))
    convertidas = [converter_para_perfil(im, perfil_cor) if icc else im.convert("RGB") for im in imagens]
    primeira, restantes = convertidas[0], convertidas[1:]
    opcoes = {
        "resolution": dpi_val,
        "title": str(nome_album or "Smart Album"),
        "author": "Smart Album CC",
        "creator": "Smart Album Industrial (%d DPI · %s)" % (int(dpi_val), str(perfil_cor or "sRGB")),
    }
    perfil = perfil_icc(prod, perfil_cor) if icc else None
    if perfil:
        opcoes["icc_profile"] = perfil
    if restantes:
        opcoes["save_all"] = True
        opcoes["append_images"] = restantes
    temp = destino.with_name(destino.stem + ".album_tmp.pdf")
    primeira.save(temp, "PDF", **opcoes)
    os.replace(temp, destino)


def itens_exportacao(projeto, laminas=None, padrao=PADRAO_NOME, produto=None):
    """
    [(nome do arquivo sem extensão, índice da lâmina)] na ordem de impressão.
    laminas: índices escolhidos (None = todas). A capa é feita no programa de capas.
    """
    total = len(projeto.get("laminas", []))
    indices = range(total) if laminas is None else [i for i in laminas if 0 <= i < total]
    return [(nome_arquivo(padrao, i + 1, projeto, produto, total), i) for i in indices]


def fundo_imagem_do_item(projeto, item):
    if item == "capa":
        return capa_mod.completar(projeto.get("capa")).get("fundo_imagem")
    return projeto["laminas"][item].get("fundo_imagem")


def renderizar_item(projeto, produto, info, item, largura=None, guias=False, rapido=False, ao_espaco=None, sem_fundo=False):
    if item == "capa":
        return capa_mod.renderizar_capa(
            projeto.get("capa"), produto, len(projeto.get("laminas", [])), info, largura, guias, rapido, sem_fundo,
        )
    return renderizar_lamina(projeto["laminas"][item], produto, info, largura, guias, rapido, ao_espaco, sem_fundo)


def _renderizar_item_exportacao(projeto, prod_dpi, info, item, numero_lamina, total_laminas, op, problemas):
    """Renderiza a lâmina na resolução do DPI escolhido e aplica marcas de registro industrial se ativado."""
    def ao_espaco(indice, espaco, erro):
        if erro is not None:
            problemas.append("  espaço %s: erro ao abrir %s (%s)" % (indice + 1, Path(espaco["foto"]).name, erro))

    for ch_f in ("fundo_imagem", "fundo_imagem_esquerda", "fundo_imagem_direita"):
        fundo_img = projeto["laminas"][item].get(ch_f) if isinstance(item, int) else fundo_imagem_do_item(projeto, item)
        if fundo_img and fundo_img.get("caminho") and not Path(fundo_img["caminho"]).is_file():
            problemas.append("  imagem de fundo não encontrada: %s" % fundo_img["caminho"])

    img = renderizar_item(projeto, prod_dpi, info, item, ao_espaco=ao_espaco)
    if op.get("marcas_registro"):
        com_marcas = aplicar_marcas_registro(
            img,
            prod_dpi,
            dpi=prod_dpi["dpi"],
            perfil_cor=op.get("perfil_cor", "sRGB"),
            indice_lamina=numero_lamina,
            total_laminas=total_laminas,
            nome_album=projeto.get("nome") or "album",
        )
        img.close()
        return com_marcas
    return img


def exportar_jpg(projeto, produto, info, pasta_saida, aviso=print, parar=lambda: False, ao_progresso=None, opcoes=None):
    """
    Grava os JPGs respeitando DPI (150/300), Perfil de Cor (sRGB/AdobeRGB) e Marcas de Registro.
    Devolve a lista de arquivos gravados (vazia se parou no começo).
    """
    op = opcoes_padrao()
    op.update(opcoes or {})
    prod_dpi = produto_com_dpi(produto, op.get("dpi"))
    perfil_cor = op.get("perfil_cor", "sRGB")
    pasta_saida = Path(pasta_saida)
    pasta_saida.mkdir(parents=True, exist_ok=True)
    itens = itens_exportacao(projeto, op.get("indices"), op["padrao"], prod_dpi)
    total_album = len(projeto.get("laminas", []))
    gravados = []
    for n, (nome, item) in enumerate(itens, start=1):
        if parar():
            aviso("Parado.")
            break
        destino = pasta_saida / (nome + ".jpg")
        if op["existentes"] == "pular" and destino.exists():
            aviso("Pulou %s (já existe)" % destino.name)
            if ao_progresso:
                ao_progresso(n, len(itens))
            continue
        problemas = []
        img = _renderizar_item_exportacao(
            projeto, prod_dpi, info, item,
            (item + 1) if isinstance(item, int) else n,
            total_album, op, problemas,
        )
        try:
            salvar_jpg(img, destino, prod_dpi, op["qualidade"], op["icc"], prod_dpi["dpi"], perfil_cor)
        finally:
            img.close()
        for linha in problemas:
            aviso(linha)
        gravados.append(destino)
        aviso("Gravou %s (%d DPI · %s%s)" % (
            destino.name,
            prod_dpi["dpi"],
            perfil_cor,
            " · c/ marcas de corte" if op.get("marcas_registro") else "",
        ))
        if ao_progresso:
            ao_progresso(n, len(itens))
    return gravados


def exportar_pdf(projeto, produto, info, pasta_saida, aviso=print, parar=lambda: False, ao_progresso=None, opcoes=None):
    """
    Exporta o álbum em PDF de alta precisão (150 ou 300 DPI), com perfil de cor (sRGB ou AdobeRGB)
    e opção de marcas de registro para corte industrial.
    Por padrão gera um arquivo PDF único multipágina ({album}.pdf); se pdf_unico=False, gera 1 PDF por lâmina.
    """
    op = opcoes_padrao()
    op.update(opcoes or {})
    prod_dpi = produto_com_dpi(produto, op.get("dpi"))
    perfil_cor = op.get("perfil_cor", "sRGB")
    pasta_saida = Path(pasta_saida)
    pasta_saida.mkdir(parents=True, exist_ok=True)
    itens = itens_exportacao(projeto, op.get("indices"), op["padrao"], prod_dpi)
    if not itens:
        return []
    total_album = len(projeto.get("laminas", []))
    gravados = []

    if op.get("pdf_unico", True):
        destino = pasta_saida / nome_pdf_unico(projeto, prod_dpi)
        if op["existentes"] == "pular" and destino.exists():
            aviso("Pulou %s (já existe)" % destino.name)
            if ao_progresso:
                ao_progresso(len(itens), len(itens))
            return []
        paginas_pil = []
        try:
            for n, (_nome, item) in enumerate(itens, start=1):
                if parar():
                    aviso("Parado.")
                    return []
                problemas = []
                img = _renderizar_item_exportacao(
                    projeto, prod_dpi, info, item,
                    (item + 1) if isinstance(item, int) else n,
                    total_album, op, problemas,
                )
                paginas_pil.append(img)
                for linha in problemas:
                    aviso(linha)
                aviso("Renderizou lâmina %s/%s para PDF (%d DPI · %s)" % (n, len(itens), prod_dpi["dpi"], perfil_cor))
                if ao_progresso:
                    ao_progresso(n, len(itens))
            salvar_pdf(
                paginas_pil,
                destino,
                prod_dpi,
                icc=op["icc"],
                dpi=prod_dpi["dpi"],
                perfil_cor=perfil_cor,
                nome_album=projeto.get("nome") or "Album",
            )
            gravados.append(destino)
            aviso("Gravou PDF industrial: %s (%s lâmina(s) · %d DPI · %s%s)" % (
                destino.name,
                len(paginas_pil),
                prod_dpi["dpi"],
                perfil_cor,
                " · c/ marcas de corte" if op.get("marcas_registro") else "",
            ))
        finally:
            for im in paginas_pil:
                try:
                    im.close()
                except Exception:
                    pass
        return gravados

    # Modo 1 PDF por lâmina
    for n, (nome, item) in enumerate(itens, start=1):
        if parar():
            aviso("Parado.")
            break
        destino = pasta_saida / (nome + ".pdf")
        if op["existentes"] == "pular" and destino.exists():
            aviso("Pulou %s (já existe)" % destino.name)
            if ao_progresso:
                ao_progresso(n, len(itens))
            continue
        problemas = []
        img = _renderizar_item_exportacao(
            projeto, prod_dpi, info, item,
            (item + 1) if isinstance(item, int) else n,
            total_album, op, problemas,
        )
        try:
            salvar_pdf(
                [img],
                destino,
                prod_dpi,
                icc=op["icc"],
                dpi=prod_dpi["dpi"],
                perfil_cor=perfil_cor,
                nome_album=projeto.get("nome") or "Album",
            )
        finally:
            img.close()
        for linha in problemas:
            aviso(linha)
        gravados.append(destino)
        aviso("Gravou %s (%d DPI · %s%s)" % (
            destino.name,
            prod_dpi["dpi"],
            perfil_cor,
            " · c/ marcas de corte" if op.get("marcas_registro") else "",
        ))
        if ao_progresso:
            ao_progresso(n, len(itens))
    return gravados
