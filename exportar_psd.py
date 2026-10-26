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
from composicao import aplicar_moldura, imagem_de_fundo, rosto_de
from exportar import itens_exportacao, opcoes_padrao, perfil_icc, tamanho_lamina
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


def _preparar_fotos(espacos, tamanho, fundo, info, pasta, prefixo, produto, com_icc=True):
    """Corta cada foto no tamanho exato do espaÃ§o e grava na pasta temporÃ¡ria."""
    icc = perfil_icc(produto) if com_icc else None
    fotos = []
    for indice, espaco in enumerate(espacos, start=1):
        if not espaco.get("foto"):
            continue
        x, y, w, h = caixa_px(espaco, tamanho)
        pronta = foto_no_espaco(espaco["foto"], espaco.get("edicao"), rosto_de(info, espaco["foto"]), (w, h), fundo)
        aplicar_moldura(pronta, (espaco.get("edicao") or {}).get("moldura"),
                         tamanho[0] / float(lamina_mm(produto)[0]))
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
    """O PNG do molde no tamanho da Ã¡rea. dx/dy: onde comeÃ§a o conteÃºdo visÃ­vel do PNG."""
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


def _preparar_fundo(fundo_imagem, tamanho, pasta, prefixo, aviso):
    """A imagem de fundo jÃ¡ no tamanho do documento, com opacidade e desfoque, num PNG."""
    if not fundo_imagem or not fundo_imagem.get("caminho"):
        return []
    try:
        camada = imagem_de_fundo(fundo_imagem, tamanho)
    except (OSError, ValueError) as e:
        aviso("  imagem de fundo ignorada (%s): %s" % (fundo_imagem["caminho"], e))
        return []
    caixa = camada.getchannel("A").getbbox()
    if not caixa:
        return []
    arquivo = pasta / ("%s_fundo.png" % prefixo)
    camada.save(arquivo, "PNG", compress_level=1)
    camada.close()
    return [{
        "arquivo": str(arquivo), "x": 0, "y": 0, "dx": caixa[0], "dy": caixa[1],
        "nome": "Fundo - %s" % Path(fundo_imagem["caminho"]).stem,
    }]


def _job_item(projeto, produto, info, item, nome, pasta, aviso=print, com_icc=True):
    lamina = projeto["laminas"][item]
    tamanho = tamanho_lamina(produto)
    fundo = cor_rgb(lamina.get("fundo", "#ffffff"))
    fotos = _preparar_fotos(lamina.get("espacos", []), tamanho, fundo, info, pasta, nome, produto, com_icc)
    moldes = _preparar_moldes(lamina.get("sobreposicoes"), tamanho, pasta, nome)
    textos = []
    fundos = _preparar_fundo(lamina.get("fundo_imagem"), tamanho, pasta, nome, aviso)
    return {
        "largura": tamanho[0], "altura": tamanho[1], "dpi": produto["dpi"], "nome": nome,
        "fundo": list(fundo), "fundos": fundos, "fotos": fotos, "moldes": moldes, "textos": textos,
        "saida": str(pasta / (nome + ".psd")),
    }


def exportar_psd(projeto, produto, info, pasta_saida, aviso=print, parar=lambda: False, ao_progresso=None, opcoes=None):
    """Um PSD por lÃ¢mina. Devolve os arquivos gravados. opcoes como em exportar.exportar_jpg."""
    op = opcoes_padrao()
    op.update(opcoes or {})
    pasta_saida = Path(pasta_saida)
    pasta_saida.mkdir(parents=True, exist_ok=True)
    pasta_temp = Path(tempfile.mkdtemp(prefix="album_psd_"))
    itens = itens_exportacao(projeto, op.get("indices"), op["padrao"], produto)
    gravados = []
    try:
        for n, (nome, item) in enumerate(itens, start=1):
            if parar():
                aviso("Parado.")
                break
            if op["existentes"] == "pular" and (pasta_saida / (nome + ".psd")).exists():
                aviso("Pulou %s.psd (jÃ¡ existe)" % nome)
                if ao_progresso:
                    ao_progresso(n, len(itens))
                continue
            aviso("Montando %s no Photoshop..." % nome)
            job = _job_item(projeto, produto, info, item, nome, pasta_temp, aviso, op["icc"])
            rodar_jsx(job, JSX_LAMINA, pasta_temp)
            local = Path(job["saida"])
            if not local.is_file() or local.stat().st_size == 0:
                raise RuntimeError("O Photoshop nÃ£o gravou %s" % local.name)
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
