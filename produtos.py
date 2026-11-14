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


PRODUTOS_PADRAO_EMBUTIDOS = (
    {
        "nome": "20X20",
        "descricao": "Álbum 20x20 cm (lâmina aberta 40x20)",
        "pagina_mm": [200, 200],
        "paginas_por_lamina": 2,
        "dpi": 300,
        "sangria_mm": 3,
        "seguranca_mm": 6,
        "dobra_mm": 8,
        "margem_mm": 6,
        "espacamento_mm": 3,
        "fotos_por_lamina": 4,
        "max_fotos_lamina": 10,
        "laminas_min": 10,
        "laminas_max": 30,
        "icc": "",
        "capa": {
            "frente_mm": [205, 205],
            "sangria_mm": 15,
            "lombada_base_mm": 4,
            "lombada_por_lamina_mm": 0.6,
        },
    },
    {
        "nome": "24X34",
        "descricao": "Álbum 24x34 cm, vertical (lâmina aberta 48x34)",
        "pagina_mm": [240, 340],
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
            "frente_mm": [245, 345],
            "sangria_mm": 15,
            "lombada_base_mm": 4,
            "lombada_por_lamina_mm": 0.6,
        },
    },
    {
        "nome": "30X20",
        "descricao": "Álbum 30x20 cm (lâmina aberta 60x20)",
        "pagina_mm": [300, 200],
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
            "frente_mm": [305, 205],
            "sangria_mm": 15,
            "lombada_base_mm": 4,
            "lombada_por_lamina_mm": 0.6,
        },
    },
    {
        "nome": "30X22",
        "descricao": "Álbum 30x22 cm (lâmina 3508x2646 px a 300 dpi)",
        "pagina_mm": [291.01, 218.03],
        "paginas_por_lamina": 1,
        "dpi": 300,
        "sangria_mm": 3,
        "seguranca_mm": 8,
        "dobra_mm": 10,
        "margem_mm": 8,
        "espacamento_mm": 4,
        "fotos_por_lamina": 2,
        "max_fotos_lamina": 6,
        "laminas_min": 10,
        "laminas_max": 40,
        "icc": "",
        "capa": {
            "frente_mm": [305, 225],
            "sangria_mm": 15,
            "lombada_base_mm": 4,
            "lombada_por_lamina_mm": 0.6,
        },
    },
)


def listar_produtos():
    pasta = pasta_produtos()
    produtos = []
    if pasta.is_dir():
        for arquivo in sorted(pasta.glob("*.json")):
            dados = ler_json(arquivo, None)
            if isinstance(dados, dict):
                produtos.append(_completar(dados, arquivo.stem))
    if not produtos:
        for padrao_item in PRODUTOS_PADRAO_EMBUTIDOS:
            comp = _completar(padrao_item, padrao_item["nome"])
            produtos.append(comp)
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


def area_util_pagina_mm(produto, indice, margem=None, respeitar_seguranca=True):
    """
    Área onde as fotos entram respeitando as linhas imaginárias da Safe Zone:
    a página sem sangria, menos a margem (no mínimo seguranca_mm quando respeitar_seguranca=True)
    e a proteção da dobra central.
    """
    pw, ph = produto["pagina_mm"]
    b = float(produto["sangria_mm"])
    seg = float(produto.get("seguranca_mm", 8.0))
    n = produto["paginas_por_lamina"]
    margem_cfg = float(produto["margem_mm"] if margem is None else margem)
    if not respeitar_seguranca and margem_cfg <= 0:
        return area_pagina_mm(produto, indice)
    margem_efetiva = max(seg, margem_cfg) if respeitar_seguranca else max(0.0, margem_cfg)
    meia_dobra = float(produto.get("dobra_mm", 10.0)) / 2.0 if n > 1 else 0.0
    margem_dobra = max(margem_efetiva, meia_dobra)
    x0 = b + pw * indice + margem_efetiva
    x1 = b + pw * (indice + 1) - margem_efetiva
    if n > 1 and indice > 0:
        x0 = b + pw * indice + margem_dobra
    if n > 1 and indice < n - 1:
        x1 = b + pw * (indice + 1) - margem_dobra
    return (x0, b + margem_efetiva, max(10.0, x1 - x0), max(10.0, ph - 2 * margem_efetiva))


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


# ------------------------------------------------------------
# Modelos salvos pelo usuário e moldes PNG (guardados em %LOCALAPPDATA%\smart_album\modelos)
# ------------------------------------------------------------

def pasta_modelos(criar=False):
    from comum import pasta_dados
    pasta = pasta_dados() / "modelos"
    if criar:
        pasta.mkdir(parents=True, exist_ok=True)
    return pasta


_CACHE_MODELOS = {}


def invalidar_cache_modelos():
    _CACHE_MODELOS.clear()


def listar_modelos():
    from comum import chave_natural
    pasta = pasta_modelos(criar=False)
    try:
        marca = pasta.stat().st_mtime_ns
    except OSError:
        return []
    guardado = _CACHE_MODELOS.get(str(pasta))
    if guardado is not None and guardado[0] == marca:
        return [dict(m) for m in guardado[1]]
    modelos = []
    for arquivo in sorted(pasta.glob("*.json"), key=chave_natural):
        dados = ler_json(arquivo, None)
        if isinstance(dados, dict) and dados.get("espacos") is not None:
            dados.setdefault("id", arquivo.stem)
            dados.setdefault("nome", arquivo.stem)
            dados.setdefault("escopo", "lamina")
            modelos.append(dados)
    _CACHE_MODELOS[str(pasta)] = (marca, modelos)
    return [dict(m) for m in modelos]


def carregar_modelo(id_modelo):
    for modelo in listar_modelos():
        if modelo["id"] == id_modelo:
            return modelo
    return None


def gravar_modelo(modelo):
    import uuid
    modelo = dict(modelo)
    base = re.sub(r"[^a-z0-9]+", "_", str(modelo.get("nome", "modelo")).casefold()).strip("_") or "modelo"
    existentes = {m["id"] for m in listar_modelos()}
    if not modelo.get("id"):
        modelo["id"] = base if base not in existentes else "%s_%s" % (base, uuid.uuid4().hex[:6])
    gravar_json(pasta_modelos(criar=True) / (modelo["id"] + ".json"), modelo)
    invalidar_cache_modelos()
    return modelo


def apagar_modelo(id_modelo):
    arquivo = pasta_modelos(criar=False) / (str(id_modelo) + ".json")
    if arquivo.is_file():
        arquivo.unlink()
    invalidar_cache_modelos()


def caminho_png(modelo):
    if not modelo.get("molde_png"):
        return None
    caminho = Path(modelo["molde_png"])
    if not caminho.is_absolute():
        caminho = pasta_modelos(criar=False) / caminho
    return caminho if caminho.is_file() else None


def area_do_modelo(produto, escopo, pagina=0):
    lw, lh = lamina_mm(produto)
    if escopo == "pagina" and produto["paginas_por_lamina"] > 1:
        x, y, w, h = area_pagina_mm(produto, pagina)
        return (x / lw, y / lh, w / lw, h / lh)
    return (0.0, 0.0, 1.0, 1.0)


def espacos_do_modelo(modelo, produto, pagina=0):
    ax, ay, aw, ah = area_do_modelo(produto, modelo.get("escopo", "lamina"), pagina)
    espacos = [
        {"x": ax + e["x"] * aw, "y": ay + e["y"] * ah, "w": e["w"] * aw, "h": e["h"] * ah}
        for e in modelo["espacos"]
    ]
    png = caminho_png(modelo)
    sobreposicao = {"png": str(png), "x": ax, "y": ay, "w": aw, "h": ah} if png else None
    return espacos, sobreposicao


def modelo_da_lamina(lamina, nome):
    modelo = {
        "nome": nome,
        "escopo": "lamina",
        "espacos": [
            {k: round(float(e[k]), 6) for k in ("x", "y", "w", "h")} for e in lamina.get("espacos", [])
        ],
        "molde_png": None,
    }
    sobre = lamina.get("sobreposicoes") or []
    if len(sobre) == 1 and abs(sobre[0]["w"] - 1) < 1e-6 and abs(sobre[0]["h"] - 1) < 1e-6:
        modelo["molde_png"] = sobre[0]["png"]
    return gravar_modelo(modelo)


def importar_png(caminho, produto, nome=None):
    import shutil
    import uuid
    import cv2
    import numpy as np
    from PIL import Image

    caminho = Path(caminho)
    with Image.open(caminho) as original:
        img = original.convert("RGBA")
    try:
        largura, altura = img.size
        alpha = np.asarray(img.getchannel("A"))
    finally:
        img.close()
    vazio = (alpha < 128).astype(np.uint8)
    n, _labels, stats, _ = cv2.connectedComponentsWithStats(vazio, connectivity=4)
    min_lado = max(20, int(min(largura, altura) * 0.03))
    folga = max(1, int(min(largura, altura) * 0.0007))
    buracos = []
    for i in range(1, n):
        x, y, w, h, area = (int(v) for v in stats[i])
        if w < min_lado or h < min_lado or area < min_lado * min_lado:
            continue
        if max(w, h) / float(min(w, h)) > 6.0:
            continue
        x = max(0, x - folga)
        y = max(0, y - folga)
        w = min(largura - x, w + 2 * folga)
        h = min(altura - y, h + 2 * folga)
        buracos.append((x, y, w, h))
    buracos.sort(key=lambda b: (b[1], b[0]))
    if not buracos:
        raise ValueError("Nenhum espaço transparente encontrado em %s" % caminho.name)
    proporcao = largura / float(altura)
    lw, lh = lamina_mm(produto)
    escopo = "lamina"
    if produto["paginas_por_lamina"] > 1:
        _x, _y, pw, ph = area_pagina_mm(produto, 0)
        if abs(proporcao - pw / ph) < abs(proporcao - lw / lh):
            escopo = "pagina"
    destino_png = pasta_modelos(criar=True) / "png" / caminho.name
    destino_png.parent.mkdir(parents=True, exist_ok=True)
    if destino_png.resolve() != caminho.resolve():
        if destino_png.exists():
            destino_png = destino_png.with_name("%s_%s%s" % (caminho.stem, uuid.uuid4().hex[:6], caminho.suffix))
        shutil.copy2(caminho, destino_png)
    modelo = {
        "nome": nome or caminho.stem,
        "escopo": escopo,
        "espacos": [
            {"x": round(x / largura, 6), "y": round(y / altura, 6),
             "w": round(w / largura, 6), "h": round(h / altura, 6)}
            for x, y, w, h in buracos
        ],
        "molde_png": "png/" + destino_png.name,
    }
    return gravar_modelo(modelo)


def importar_pasta_png(pasta, produto):
    from comum import chave_natural
    modelos = []
    erros = []
    for arquivo in sorted(Path(pasta).glob("*.png"), key=chave_natural):
        try:
            modelos.append(importar_png(arquivo, produto))
        except Exception as e:
            erros.append("%s: %s" % (arquivo.name, e))
    return modelos, erros

