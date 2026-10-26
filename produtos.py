# -*- coding: utf-8 -*-
"""
Produtos (tamanhos de álbum). Cada produto é um JSON em produtos/.

Medidas em mm. A lâmina é o arquivo que vai para o laboratório: as páginas
lado a lado (paginas_por_lamina) mais a sangria em volta.
"""

import re
from pathlib import Path

from comum import gravar_json, ler_json, mm_para_px, pasta_recurso

PADRAO = {
    "nome": "",
    "descricao": "",
    "pagina_mm": [300, 300],
    "paginas_por_lamina": 2,
    "dpi": 300,
    "sangria_mm": 3,
    "seguranca_mm": 8,
    "dobra_mm": 10,
    "margem_mm": 8,
    "espacamento_mm": 4,
    "fotos_por_lamina": 4,
    "max_fotos_lamina": 12,
    "laminas_min": 10,
    "laminas_max": 40,
    "icc": "",
    "capa": {
        "frente_mm": None,
        "sangria_mm": 15,
        "lombada_base_mm": 4,
        "lombada_por_lamina_mm": 0.6,
    },
}


def pasta_produtos():
    return pasta_recurso("produtos")


def _completar(dados, nome):
    produto = dict(PADRAO)
    produto.update(dados or {})
    capa = dict(PADRAO["capa"])
    capa.update((dados or {}).get("capa") or {})
    produto["capa"] = capa
    produto["nome"] = produto.get("nome") or nome
    return produto


def listar_produtos():
    pasta = pasta_produtos()
    produtos = []
    if pasta.is_dir():
        for arquivo in sorted(pasta.glob("*.json")):
            dados = ler_json(arquivo, None)
            if isinstance(dados, dict):
                produtos.append(_completar(dados, arquivo.stem))
    return produtos


def carregar_produto(nome):
    for produto in listar_produtos():
        if produto["nome"].casefold() == str(nome).casefold():
            return produto
    raise KeyError("Produto não encontrado: %s" % nome)


def gravar_produto(produto):
    gravar_json(pasta_produtos() / (produto["nome"] + ".json"), produto)


# ------------------------------------------------------------
# Medidas da lâmina
# ------------------------------------------------------------

def lamina_mm(produto):
    """(largura, altura) da lâmina inteira em mm, com sangria."""
    pw, ph = produto["pagina_mm"]
    b = produto["sangria_mm"]
    return (pw * produto["paginas_por_lamina"] + 2 * b, ph + 2 * b)


def lamina_px(produto):
    w, h = lamina_mm(produto)
    dpi = produto["dpi"]
    return (int(round(mm_para_px(w, dpi))), int(round(mm_para_px(h, dpi))))


def area_pagina_mm(produto, indice):
    """
    Retângulo (x, y, w, h) em mm da página dentro da lâmina, incluindo a
    sangria dos lados de fora. indice 0 = esquerda.
    """
    pw, ph = produto["pagina_mm"]
    b = produto["sangria_mm"]
    n = produto["paginas_por_lamina"]
    x0 = 0 if indice == 0 else b + pw * indice
    x1 = b + pw * (indice + 1) + (b if indice == n - 1 else 0)
    return (x0, 0, x1 - x0, ph + 2 * b)


def area_util_pagina_mm(produto, indice, margem=None):
    """Área onde as fotos entram: a página sem sangria, menos a margem e a dobra."""
    pw, ph = produto["pagina_mm"]
    b = produto["sangria_mm"]
    n = produto["paginas_por_lamina"]
    margem = produto["margem_mm"] if margem is None else margem
    if margem <= 0:
        return area_pagina_mm(produto, indice)
    meia_dobra = produto["dobra_mm"] / 2.0 if n > 1 else 0
    x0 = b + pw * indice + margem
    x1 = b + pw * (indice + 1) - margem
    if n > 1 and indice > 0:
        x0 = b + pw * indice + max(margem, meia_dobra)
    if n > 1 and indice < n - 1:
        x1 = b + pw * (indice + 1) - max(margem, meia_dobra)
    return (x0, b + margem, x1 - x0, ph - 2 * margem)


def normalizar_mm(produto, caixa):
    """Caixa em mm para fração da lâmina (0 a 1)."""
    lw, lh = lamina_mm(produto)
    x, y, w, h = caixa
    return {"x": x / lw, "y": y / lh, "w": w / lw, "h": h / lh}


def desnormalizar_mm(produto, espaco):
    lw, lh = lamina_mm(produto)
    return (espaco["x"] * lw, espaco["y"] * lh, espaco["w"] * lw, espaco["h"] * lh)


def caixa_px(espaco, tamanho):
    """Espaço normalizado para pixels inteiros (x, y, w, h) numa imagem de tamanho dado."""
    W, H = tamanho
    x0 = int(round(espaco["x"] * W))
    y0 = int(round(espaco["y"] * H))
    x1 = int(round((espaco["x"] + espaco["w"]) * W))
    y1 = int(round((espaco["y"] + espaco["h"]) * H))
    return (x0, y0, max(1, x1 - x0), max(1, y1 - y0))


def tamanhos_no_nome(texto):
    """Tamanhos como 30X22 escritos no nome (aceita 30x22, 30 x 22, 30X22CM)."""
    compacto = re.sub(r"\s+", "", str(texto).upper())
    return {a + "X" + b for a, b in re.findall(r"(?<!\d)(\d{2})X(\d{2})(?!\d)", compacto)}


def adivinhar_produto(textos):
    """Primeiro produto cujo nome aparece nos textos (nome da pasta, nomes das fotos)."""
    nomes = {p["nome"].upper(): p["nome"] for p in listar_produtos()}
    for texto in textos:
        for tamanho in tamanhos_no_nome(Path(str(texto)).name if texto else ""):
            if tamanho in nomes:
                return nomes[tamanho]
        for parte in Path(str(texto)).parts[::-1]:
            for tamanho in tamanhos_no_nome(parte):
                if tamanho in nomes:
                    return nomes[tamanho]
    return None
