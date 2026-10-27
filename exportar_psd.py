# -*- coding: utf-8 -*-
"""
ExportaÃ§Ã£o em PSD com camadas, pelo Photoshop.

Baseado no gerar_script_jsx: selecionar o espaÃ§o e colar dentro
(doc.paste(true)), que cria a mÃ¡scara da foto, e salvar com camadas.
DiferenÃ§as:
  - as unidades vÃ£o para pixels antes de criar o documento (e voltam no fim);
  - o Python jÃ¡ corta cada foto no tamanho exato do espaÃ§o, entÃ£o o
    "colar dentro" encaixa sem ajuste;
  - caminhos e nomes vÃ£o como JSON (aspas, acentos e barras nÃ£o quebram o script);
  - camadas com nome, fundo na cor da lÃ¢mina, imagem de fundo logo acima dele,
    molde PNG por cima e textos da capa;
  - status de volta por arquivo; tudo em pasta temporÃ¡ria local e depois
    copiado para a saÃ­da com nova tentativa.
"""

import tempfile
from pathlib import Path

from PIL import Image

from comum import apagar_pasta, cor_rgb, substituir_arquivo
from composicao import aplicar_moldura, compor_fundo_completo, rosto_de
from exportar import converter_para_perfil, itens_exportacao, opcoes_padrao, perfil_icc, produto_com_dpi, tamanho_lamina
from photoshop import fechar_documentos_da_pasta, rodar_jsx
from produtos import caixa_px, lamina_mm
from recorte import foto_no_espaco

JSX_LAMINA = r"""
function colarDentro(doc, f) {
    var arq = app.open(new File(f.arquivo));
    arq.selection.selectAll();
    arq.selection.copy();
    arq.close(SaveOptions.DONOTSAVECHANGES);
    app.activeDocument = doc;
    var x1 = f.x, y1 = f.y, x2 = f.x + f.w, y2 = f.y + f.h;
    doc.selection.select([[x1, y1], [x2, y1], [x2, y2], [x1, y2]]);
    doc.paste(true);
    doc.activeLayer.name = f.nome;
    doc.selection.deselect();
}
function colarMolde(doc, s) {
    var arq = app.open(new File(s.arquivo));
    var camada = arq.activeLayer;
    if (camada.isBackgroundLayer) { camada.isBackgroundLayer = false; }
    var copia = camada.duplicate(doc, ElementPlacement.PLACEATBEGINNING);
    arq.close(SaveOptions.DONOTSAVECHANGES);
    app.activeDocument = doc;
    doc.activeLayer = copia;
    var b = copia.bounds;
    copia.translate(s.x + s.dx - b[0].as("px"), s.y + s.dy - b[1].as("px"));
    copia.name = s.nome;
}
function adicionarTexto(doc, t) {
    var camada = doc.artLayers.add();
    camada.kind = LayerKind.TEXT;
    camada.name = t.nome;
    var ti = camada.textItem;
    ti.contents = t.texto;
    try { ti.font = t.fonte; } catch (e) { ti.font = "ArialMT"; }
    ti.size = new UnitValue(t.tamanho_pt, "pt");
    ti.color = corSolida(t.cor);
    ti.justification = Justification.CENTER;
    ti.position = [t.cx, t.cy];
    if (t.giro) { camada.rotate(t.giro, AnchorPosition.MIDDLECENTER); }
    var b = camada.bounds;
    var cx = (b[0].as("px") + b[2].as("px")) / 2;
    var cy = (b[1].as("px") + b[3].as("px")) / 2;
    camada.translate(t.cx - cx, t.cy - cy);
}
(function () {
    var prefs = guardarPreferencias();
    var etapa = "inicio";
    var doc = null;
    try {
        prepararPreferencias();
        etapa = "criar documento";
        doc = app.documents.add(JOB.largura, JOB.altura, JOB.dpi, JOB.nome, NewDocumentMode.RGB, DocumentFill.WHITE);
        doc.selection.selectAll();
        doc.selection.fill(corSolida(JOB.fundo));
        doc.selection.deselect();
        for (var m = 0; m < JOB.fundos.length; m++) {
            etapa = "fundo " + JOB.fundos[m].nome;
            colarMolde(doc, JOB.fundos[m]);
        }
        for (var i = 0; i < JOB.fotos.length; i++) {
            etapa = "foto " + JOB.fotos[i].nome;
            colarDentro(doc, JOB.fotos[i]);
        }
        for (var j = 0; j < JOB.moldes.length; j++) {
            etapa = "molde " + JOB.moldes[j].nome;
            colarMolde(doc, JOB.moldes[j]);
        }
        for (var k = 0; k < JOB.textos.length; k++) {
            etapa = "texto " + JOB.textos[k].nome;
            adicionarTexto(doc, JOB.textos[k]);
        }
        etapa = "salvar PSD";
        salvarComo(doc, JOB.saida, "psd");
        doc.close(SaveOptions.DONOTSAVECHANGES);
        doc = null;
        gravarStatus("OK|" + JOB.saida);
    } catch (e) {
        gravarStatus("ERRO|" + etapa + "|" + e.message + " (linha " + e.line + ")");
        try { if (doc) { doc.close(SaveOptions.DONOTSAVECHANGES); } } catch (e2) {}
    } finally {
        restaurarPreferencias(prefs);
    }
})();
"""


def _preparar_fotos(espacos, tamanho, fundo, info, pasta, prefixo, produto, com_icc=True, perfil_cor="sRGB"):
    """Corta cada foto no tamanho exato do espaço e grava na pasta temporária."""
    icc = perfil_icc(produto, perfil_cor) if com_icc else None
    fotos = []
    px_por_mm = tamanho[0] / float(lamina_mm(produto)[0])
    for indice, espaco in enumerate(espacos, start=1):
        if not espaco.get("foto"):
            continue
        x, y, w, h = caixa_px(espaco, tamanho)
        ex = float(espaco.get("x", 0.0))
        ew = float(espaco.get("w", 0.0))
        dobra_x = (0.5 - ex) / ew if (ew > 0.05 and ex < 0.495 and ex + ew > 0.505) else None
        pronta = foto_no_espaco(
            espaco["foto"], espaco.get("edicao"), rosto_de(info, espaco["foto"]), (w, h), fundo, dobra_x=dobra_x
        )
        aplicar_moldura(pronta, (espaco.get("edicao") or {}).get("moldura"), px_por_mm)
        if com_icc:
            pronta_conv = converter_para_perfil(pronta, perfil_cor)
            pronta.close()
            pronta = pronta_conv
        arquivo = pasta / ("%s_foto_%02d.jpg" % (prefixo, indice))
        opcoes = {"quality": 100, "subsampling": 0, "dpi": (produto["dpi"], produto["dpi"])}
        if icc:
            opcoes["icc_profile"] = icc
        pronta.save(arquivo, "JPEG", **opcoes)
        pronta.close()
        fotos.append({
            "arquivo": str(arquivo), "x": x, "y": y, "w": w, "h": h,
            "nome": "Foto %02d - %s" % (indice, Path(espaco["foto"]).stem),
        })
    return fotos


def _preparar_moldes(sobreposicoes, tamanho, pasta, prefixo):
    """O PNG do molde no tamanho da área. dx/dy: onde começa o conteúdo visível do PNG."""
    moldes = []
    for indice, sobre in enumerate(sobreposicoes or [], start=1):
        try:
            with Image.open(sobre["png"]) as png:
                png = png.convert("RGBA")
        except OSError:
            continue
        x, y, w, h = caixa_px(sobre, tamanho)
        if png.size != (w, h):
            png = png.resize((w, h), Image.LANCZOS)
        caixa = png.getchannel("A").getbbox() or (0, 0, w, h)
        arquivo = pasta / ("%s_molde_%02d.png" % (prefixo, indice))
        png.save(arquivo, "PNG")
        moldes.append({
            "arquivo": str(arquivo), "x": x, "y": y, "dx": caixa[0], "dy": caixa[1],
            "nome": "Molde - %s" % Path(sobre["png"]).stem,
        })
    return moldes


def _preparar_fundo(lamina, tamanho, pasta, prefixo, produto, com_icc=True, perfil_cor="sRGB", aviso=print):
    """
    Gera uma camada única de fundo já mesclada com todos os efeitos e configurações da lâmina:
    cor base, imagem de fundo (lâmina inteira e/ou páginas esquerda/direita), brilho, contraste,
    saturação, matiz, tingir com cor (Colorize), preto e branco, desfoque, opacidade, degradê e fusão.
    """
    try:
        camada_rgba = compor_fundo_completo(
            tamanho,
            lamina.get("fundo", "#ffffff"),
            lamina.get("fundo_imagem"),
            rapido=False,
            opcoes_pagina=lamina,
        )
        camada_rgb = camada_rgba.convert("RGB")
        camada_rgba.close()
        if com_icc:
            conv = converter_para_perfil(camada_rgb, perfil_cor)
            camada_rgb.close()
            camada_rgb = conv
        arquivo = pasta / ("%s_fundo.png" % prefixo)
        icc = perfil_icc(produto, perfil_cor) if com_icc else None
        op_png = {"compress_level": 1, "dpi": (produto["dpi"], produto["dpi"])}
        if icc:
            op_png["icc_profile"] = icc
        camada_rgb.save(arquivo, "PNG", **op_png)
        camada_rgb.close()

        partes_nome = []
        for ch in ("fundo_imagem", "fundo_imagem_esquerda", "fundo_imagem_direita"):
            dados_f = lamina.get(ch) or {}
            if dados_f.get("caminho"):
                partes_nome.append(Path(dados_f["caminho"]).stem)
        nome_camada = ("Fundo - " + " + ".join(partes_nome)) if partes_nome else "Fundo da Lâmina"
        return [{
            "arquivo": str(arquivo), "x": 0, "y": 0, "dx": 0, "dy": 0,
            "nome": nome_camada,
        }]
    except Exception as e:
        aviso("  aviso ao preparar camada de fundo (%s): %s" % (prefixo, e))
        return []


def _job_item(projeto, produto, info, item, nome, pasta, aviso=print, com_icc=True, perfil_cor="sRGB"):
    lamina = projeto["laminas"][item]
    tamanho = tamanho_lamina(produto)
    fundo = cor_rgb(lamina.get("fundo", "#ffffff"))
    fotos = _preparar_fotos(lamina.get("espacos", []), tamanho, fundo, info, pasta, nome, produto, com_icc, perfil_cor)
    moldes = _preparar_moldes(lamina.get("sobreposicoes"), tamanho, pasta, nome)
    textos = []
    fundos = _preparar_fundo(lamina, tamanho, pasta, nome, produto, com_icc, perfil_cor, aviso)
    return {
        "largura": tamanho[0], "altura": tamanho[1], "dpi": produto["dpi"], "nome": nome,
        "fundo": list(fundo), "fundos": fundos, "fotos": fotos, "moldes": moldes, "textos": textos,
        "saida": str(pasta / (nome + ".psd")),
    }


def exportar_psd(projeto, produto, info, pasta_saida, aviso=print, parar=lambda: False, ao_progresso=None, opcoes=None):
    """Um PSD por lâmina com a camada única de fundo já mesclada com todos os efeitos e configurações."""
    op = opcoes_padrao()
    op.update(opcoes or {})
    prod_dpi = produto_com_dpi(produto, op.get("dpi"))
    perfil_cor = op.get("perfil_cor", "sRGB")
    pasta_saida = Path(pasta_saida)
    pasta_saida.mkdir(parents=True, exist_ok=True)
    pasta_temp = Path(tempfile.mkdtemp(prefix="album_psd_"))
    itens = itens_exportacao(projeto, op.get("indices"), op["padrao"], prod_dpi)
    gravados = []
    try:
        for n, (nome, item) in enumerate(itens, start=1):
            if parar():
                aviso("Parado.")
                break
            if op["existentes"] == "pular" and (pasta_saida / (nome + ".psd")).exists():
                aviso("Pulou %s.psd (já existe)" % nome)
                if ao_progresso:
                    ao_progresso(n, len(itens))
                continue
            aviso("Montando %s no Photoshop..." % nome)
            job = _job_item(projeto, prod_dpi, info, item, nome, pasta_temp, aviso, op["icc"], perfil_cor)
            rodar_jsx(job, JSX_LAMINA, pasta_temp)
            local = Path(job["saida"])
            if not local.is_file() or local.stat().st_size == 0:
                raise RuntimeError("O Photoshop não gravou %s" % local.name)
            destino = pasta_saida / local.name
            substituir_arquivo(local, destino)
            gravados.append(destino)
            aviso("Gravou %s" % destino.name)
            for arquivo in pasta_temp.glob(nome + "_*"):
                try:
                    arquivo.unlink()
                except OSError:
                    pass
            try:
                local.unlink()
            except OSError:
                pass
            if ao_progresso:
                ao_progresso(n, len(itens))
    finally:
        fechar_documentos_da_pasta(pasta_temp)
        apagar_pasta(pasta_temp)
    return gravados
