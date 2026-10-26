# -*- coding: utf-8 -*-
"""
Exportação em JPG: uma imagem por lâmina aberta, com sangria, DPI e perfil
de cor do produto. As guias aparecem só na prévia.
"""

import os
import re
from pathlib import Path

from PIL import ImageCms

import capa as capa_mod
from composicao import compor, desenhar_guias
from produtos import lamina_mm, lamina_px

QUALIDADE_JPG = 95
PADRAO_NOME = "{n:02d} - lamina"
CARACTERES_PROIBIDOS = r'[<>:"/\\|?*\x00-\x1f]'


def opcoes_padrao():
    """Como a exportação funciona sem escolher nada: tudo igual ao jeito antigo."""
    return {"formato": "jpg", "qualidade": QUALIDADE_JPG, "icc": True, "pasta": "", "subpasta": False,
            "laminas": "todas", "intervalo": "", "padrao": PADRAO_NOME, "existentes": "substituir"}


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
    dados = {"n": n, "album": projeto.get("nome") or "album", "produto": (produto or {}).get("nome", ""),
             "total": total if total is not None else len(projeto.get("laminas", []))}
    try:
        nome = str(padrao or PADRAO_NOME).format(**dados)
    except (KeyError, ValueError, IndexError) as e:
        raise ValueError("Padrão de nome inválido (%s). Use {n}, {n:02d}, {album}, {produto} ou {total}." % e)
    nome = re.sub(CARACTERES_PROIBIDOS, "_", nome).strip().rstrip(".")
    if not nome:
        raise ValueError("O padrão de nome ficou vazio.")
    return nome


def tamanho_lamina(produto, largura=None):
    tamanho = lamina_px(produto)
    if largura:
        escala = largura / float(tamanho[0])
        return (int(largura), max(1, int(round(tamanho[1] * escala))))
    return tamanho


def _guias_lamina(img, produto):
    W_mm, H_mm = lamina_mm(produto)
    k = img.width / W_mm
    b = produto["sangria_mm"] * k
    seg = (produto["sangria_mm"] + produto["seguranca_mm"]) * k
    pw = produto["pagina_mm"][0] * k
    n = produto["paginas_por_lamina"]
    sangria = [(b, b, img.width - b, img.height - b)]
    seguranca = []
    for i in range(n):
        x0 = b + pw * i
        seguranca.append((x0 + seg - b, seg, x0 + pw - (seg - b), img.height - seg))
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


def perfil_icc(produto):
    """Perfil do produto (arquivo .icc/.icm) ou sRGB."""
    caminho = produto.get("icc")
    if caminho and Path(caminho).is_file():
        return Path(caminho).read_bytes()
    try:
        return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    except Exception:
        return None


def salvar_jpg(img, destino, produto, qualidade=QUALIDADE_JPG, icc=True):
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    opcoes = {"quality": int(qualidade), "subsampling": 0, "optimize": True, "dpi": (produto["dpi"], produto["dpi"])}
    perfil = perfil_icc(produto) if icc else None
    if perfil:
        opcoes["icc_profile"] = perfil
    temp = destino.with_name(destino.stem + ".album_tmp.jpg")
    img.save(temp, "JPEG", **opcoes)
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


def exportar_jpg(projeto, produto, info, pasta_saida, aviso=print, parar=lambda: False, ao_progresso=None, opcoes=None):
    """
    Grava os JPGs. Devolve a lista de arquivos gravados (vazia se parou no começo).
    opcoes: laminas (índices ou None), padrao, qualidade, icc, existentes ("substituir" ou "pular").
    """
    op = opcoes_padrao()
    op.update(opcoes or {})
    pasta_saida = Path(pasta_saida)
    pasta_saida.mkdir(parents=True, exist_ok=True)
    itens = itens_exportacao(projeto, op.get("indices"), op["padrao"], produto)
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

        def ao_espaco(indice, espaco, erro):
            if erro is not None:
                problemas.append("  espaço %s: erro ao abrir %s (%s)" % (indice + 1, Path(espaco["foto"]).name, erro))

        fundo_img = fundo_imagem_do_item(projeto, item)
        if fundo_img and fundo_img.get("caminho") and not Path(fundo_img["caminho"]).is_file():
            problemas.append("  imagem de fundo não encontrada: %s" % fundo_img["caminho"])
        img = renderizar_item(projeto, produto, info, item, ao_espaco=ao_espaco)
        try:
            salvar_jpg(img, destino, produto, op["qualidade"], op["icc"])
        finally:
            img.close()
        for linha in problemas:
            aviso(linha)
        gravados.append(destino)
        aviso("Gravou %s" % destino.name)
        if ao_progresso:
            ao_progresso(n, len(itens))
    return gravados
