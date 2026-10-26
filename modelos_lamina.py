# -*- coding: utf-8 -*-
"""
Biblioteca de modelos de lâmina (modelos/*.json).

Os espaços das fotos ficam em fração da área do modelo, então o mesmo
modelo serve em qualquer produto:
  escopo "lamina": a lâmina inteira, com sangria;
  escopo "pagina": uma página, com a sangria dos lados de fora.
Um modelo pode ter um PNG por cima (molde com os espaços transparentes).
"""

import re
import shutil
import uuid
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from comum import chave_natural, gravar_json, ler_json, pasta_recurso
from produtos import area_pagina_mm, lamina_mm

LIMIAR_ALPHA = 128
PROPORCAO_MAXIMA = 6.0


def pasta_modelos():
    pasta = pasta_recurso("modelos")
    pasta.mkdir(parents=True, exist_ok=True)
    return pasta


def listar_modelos():
    modelos = []
    for arquivo in sorted(pasta_modelos().glob("*.json"), key=chave_natural):
        dados = ler_json(arquivo, None)
        if isinstance(dados, dict) and dados.get("espacos") is not None:
            dados.setdefault("id", arquivo.stem)
            dados.setdefault("nome", arquivo.stem)
            dados.setdefault("escopo", "lamina")
            modelos.append(dados)
    return modelos


def carregar_modelo(id_modelo):
    for modelo in listar_modelos():
        if modelo["id"] == id_modelo:
            return modelo
    return None


def _id_livre(nome):
    base = re.sub(r"[^a-z0-9]+", "_", nome.casefold()).strip("_") or "modelo"
    existentes = {m["id"] for m in listar_modelos()}
    if base not in existentes:
        return base
    return "%s_%s" % (base, uuid.uuid4().hex[:6])


def gravar_modelo(modelo):
    modelo = dict(modelo)
    modelo["id"] = modelo.get("id") or _id_livre(modelo.get("nome", "modelo"))
    gravar_json(pasta_modelos() / (modelo["id"] + ".json"), modelo)
    return modelo


def apagar_modelo(id_modelo):
    arquivo = pasta_modelos() / (id_modelo + ".json")
    if arquivo.is_file():
        arquivo.unlink()


def caminho_png(modelo):
    if not modelo.get("molde_png"):
        return None
    caminho = Path(modelo["molde_png"])
    if not caminho.is_absolute():
        caminho = pasta_modelos() / caminho
    return caminho if caminho.is_file() else None


# ------------------------------------------------------------
# Modelo para a lâmina
# ------------------------------------------------------------

def area_do_modelo(produto, escopo, pagina=0):
    """Área (x, y, w, h) em fração da lâmina onde o modelo entra."""
    lw, lh = lamina_mm(produto)
    if escopo == "pagina" and produto["paginas_por_lamina"] > 1:
        x, y, w, h = area_pagina_mm(produto, pagina)
        return (x / lw, y / lh, w / lw, h / lh)
    return (0.0, 0.0, 1.0, 1.0)


def espacos_do_modelo(modelo, produto, pagina=0):
    """
    (espaços em fração da lâmina, sobreposição ou None).
    A sobreposição é o PNG do molde com a área da lâmina onde ele entra.
    """
    ax, ay, aw, ah = area_do_modelo(produto, modelo.get("escopo", "lamina"), pagina)
    espacos = [
        {"x": ax + e["x"] * aw, "y": ay + e["y"] * ah, "w": e["w"] * aw, "h": e["h"] * ah}
        for e in modelo["espacos"]
    ]
    png = caminho_png(modelo)
    sobreposicao = {"png": str(png), "x": ax, "y": ay, "w": aw, "h": ah} if png else None
    return espacos, sobreposicao


def modelo_da_lamina(lamina, nome):
    """Guarda as posições da lâmina atual como um modelo novo de lâmina inteira."""
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


# ------------------------------------------------------------
# Importar PNG com espaços transparentes
# ------------------------------------------------------------

def _ordenar_buracos(buracos):
    """De cima para baixo e, na mesma faixa, da esquerda para a direita."""
    if not buracos:
        return []
    alturas = sorted(b[3] for b in buracos)
    tolerancia = max(20, alturas[len(alturas) // 2] * 0.25)
    linhas = []
    for buraco in sorted(buracos, key=lambda b: (b[1], b[0])):
        if linhas and abs(buraco[1] - linhas[-1][0][1]) <= tolerancia:
            linhas[-1].append(buraco)
        else:
            linhas.append([buraco])
    saida = []
    for linha in linhas:
        saida.extend(sorted(linha, key=lambda b: b[0]))
    return saida


def achar_buracos_alpha(img):
    """Áreas transparentes do PNG, como caixas (x, y, w, h) em pixels."""
    alpha = np.asarray(img.getchannel("A"))
    altura, largura = alpha.shape[:2]
    vazio = (alpha < LIMIAR_ALPHA).astype(np.uint8)
    n, _labels, stats, _ = cv2.connectedComponentsWithStats(vazio, connectivity=4)
    min_lado = max(20, int(min(largura, altura) * 0.03))
    folga = max(1, int(min(largura, altura) * 0.0007))
    buracos = []
    for i in range(1, n):
        x, y, w, h, area = (int(v) for v in stats[i])
        if w < min_lado or h < min_lado or area < min_lado * min_lado:
            continue
        if max(w, h) / float(min(w, h)) > PROPORCAO_MAXIMA:
            continue
        x = max(0, x - folga)
        y = max(0, y - folga)
        w = min(largura - x, w + 2 * folga)
        h = min(altura - y, h + 2 * folga)
        buracos.append((x, y, w, h))
    return _ordenar_buracos(buracos)


def importar_png(caminho, produto, nome=None):
    """
    Lê um PNG com os espaços transparentes e grava como modelo. Se a
    proporção do PNG for mais parecida com a de uma página, vira modelo de página.
    """
    caminho = Path(caminho)
    with Image.open(caminho) as original:
        img = original.convert("RGBA")
    try:
        largura, altura = img.size
        buracos = achar_buracos_alpha(img)
    finally:
        img.close()
    if not buracos:
        raise ValueError("Nenhum espaço transparente encontrado em %s" % caminho.name)
    proporcao = largura / float(altura)
    lw, lh = lamina_mm(produto)
    escopo = "lamina"
    if produto["paginas_por_lamina"] > 1:
        _x, _y, pw, ph = area_pagina_mm(produto, 0)
        if abs(proporcao - pw / ph) < abs(proporcao - lw / lh):
            escopo = "pagina"
    destino_png = pasta_modelos() / "png" / caminho.name
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
    modelos = []
    erros = []
    for arquivo in sorted(Path(pasta).glob("*.png"), key=chave_natural):
        try:
            modelos.append(importar_png(arquivo, produto))
        except Exception as e:
            erros.append("%s: %s" % (arquivo.name, e))
    return modelos, erros
