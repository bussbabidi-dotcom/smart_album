# -*- coding: utf-8 -*-
"""
Capa: verso (esquerda), lombada (meio) e frente (direita), com a sangria de
dobra em volta. A lombada cresce com a quantidade de lâminas.
"""

import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from comum import cor_rgb, mm_para_px
from composicao import compor, desenhar_guias

FONTES = {
    "Arial": ("arial.ttf", "arialbd.ttf", "ArialMT", "Arial-BoldMT"),
    "Times New Roman": ("times.ttf", "timesbd.ttf", "TimesNewRomanPSMT", "TimesNewRomanPS-BoldMT"),
    "Georgia": ("georgia.ttf", "georgiab.ttf", "Georgia", "Georgia-Bold"),
    "Segoe UI": ("segoeui.ttf", "segoeuib.ttf", "SegoeUI", "SegoeUI-Bold"),
    "Calibri": ("calibri.ttf", "calibrib.ttf", "Calibri", "Calibri-Bold"),
    "Verdana": ("verdana.ttf", "verdanab.ttf", "Verdana", "Verdana-Bold"),
    "Trebuchet MS": ("trebuc.ttf", "trebucbd.ttf", "TrebuchetMS", "TrebuchetMS-Bold"),
}
AREAS = ("verso", "lombada", "frente")


def capa_padrao():
    return {
        "ativa": False,
        "fundo": "#212b4a",
        "foto": None,
        "foto_area": "frente",
        "edicao": {},
        "textos": [
            {"texto": "", "area": "frente", "x": 0.5, "y": 0.80, "tamanho_mm": 9, "cor": "#ffffff",
             "fonte": "Arial", "negrito": True},
            {"texto": "", "area": "frente", "x": 0.5, "y": 0.88, "tamanho_mm": 5, "cor": "#ffffff",
             "fonte": "Arial", "negrito": False},
            {"texto": "", "area": "lombada", "x": 0.5, "y": 0.5, "tamanho_mm": 4, "cor": "#ffffff",
             "fonte": "Arial", "negrito": True},
        ],
    }


def completar(capa):
    dados = capa_padrao()
    dados.update(capa or {})
    return dados


def lombada_mm(produto, n_laminas):
    c = produto["capa"]
    return float(c["lombada_base_mm"]) + float(c["lombada_por_lamina_mm"]) * max(0, n_laminas)


def frente_mm(produto):
    return tuple(produto["capa"].get("frente_mm") or produto["pagina_mm"])


def capa_mm(produto, n_laminas):
    fw, fh = frente_mm(produto)
    s = float(produto["capa"]["sangria_mm"])
    return (2 * fw + lombada_mm(produto, n_laminas) + 2 * s, fh + 2 * s)


def capa_px(produto, n_laminas):
    w, h = capa_mm(produto, n_laminas)
    dpi = produto["dpi"]
    return (int(round(mm_para_px(w, dpi))), int(round(mm_para_px(h, dpi))))


def areas_capa(produto, n_laminas):
    """Áreas em fração da capa, com a sangria dos lados de fora."""
    fw, _fh = frente_mm(produto)
    s = float(produto["capa"]["sangria_mm"])
    lomb = lombada_mm(produto, n_laminas)
    W, _H = capa_mm(produto, n_laminas)
    x1 = (s + fw) / W
    x2 = (s + fw + lomb) / W
    return {
        "verso": {"x": 0.0, "y": 0.0, "w": x1, "h": 1.0},
        "lombada": {"x": x1, "y": 0.0, "w": x2 - x1, "h": 1.0},
        "frente": {"x": x2, "y": 0.0, "w": 1.0 - x2, "h": 1.0},
        "inteira": {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0},
    }


def espaco_foto(capa, produto, n_laminas):
    capa = completar(capa)
    if not capa.get("foto") or capa.get("foto_area") not in ("frente", "verso", "inteira"):
        return None
    espaco = dict(areas_capa(produto, n_laminas)[capa["foto_area"]])
    espaco["foto"] = capa["foto"]
    espaco["edicao"] = capa.get("edicao") or {}
    return espaco


def arquivo_fonte(nome, negrito=False):
    normal, grosso, _ps, _ps_b = FONTES.get(nome, FONTES["Arial"])
    pasta = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    for arquivo in ((grosso, normal) if negrito else (normal,)):
        if (pasta / arquivo).is_file():
            return str(pasta / arquivo)
    return None


def nome_postscript(nome, negrito=False):
    _n, _g, ps, ps_b = FONTES.get(nome, FONTES["Arial"])
    return ps_b if negrito else ps


def textos_posicionados(capa, produto, n_laminas, tamanho):
    """Textos com posição em pixels da imagem de tamanho dado: centro, tamanho e giro."""
    capa = completar(capa)
    areas = areas_capa(produto, n_laminas)
    W, H = tamanho
    W_mm, _H_mm = capa_mm(produto, n_laminas)
    px_por_mm = W / W_mm
    saida = []
    for texto in capa.get("textos") or []:
        if not str(texto.get("texto") or "").strip():
            continue
        area = areas.get(texto.get("area", "frente"), areas["frente"])
        saida.append({
            "texto": str(texto["texto"]),
            "cx": (area["x"] + float(texto.get("x", 0.5)) * area["w"]) * W,
            "cy": (area["y"] + float(texto.get("y", 0.5)) * area["h"]) * H,
            "tamanho_px": max(4.0, float(texto.get("tamanho_mm", 6)) * px_por_mm),
            "tamanho_mm": float(texto.get("tamanho_mm", 6)),
            "cor": texto.get("cor", "#ffffff"),
            "fonte": texto.get("fonte", "Arial"),
            "negrito": bool(texto.get("negrito")),
            "giro": 90 if texto.get("area") == "lombada" else 0,
        })
    return saida


def _desenhar_texto(base, t):
    caminho = arquivo_fonte(t["fonte"], t["negrito"])
    tamanho = int(round(t["tamanho_px"]))
    try:
        fonte = ImageFont.truetype(caminho, tamanho) if caminho else ImageFont.load_default()
    except OSError:
        fonte = ImageFont.load_default()
    medida = ImageDraw.Draw(base).textbbox((0, 0), t["texto"], font=fonte)
    largura = medida[2] - medida[0] + 4
    altura = medida[3] - medida[1] + 4
    camada = Image.new("RGBA", (largura, altura), (0, 0, 0, 0))
    ImageDraw.Draw(camada).text((2 - medida[0], 2 - medida[1]), t["texto"], font=fonte, fill=cor_rgb(t["cor"]) + (255,))
    if t["giro"]:
        camada = camada.rotate(-t["giro"], expand=True)
    x = int(round(t["cx"] - camada.width / 2))
    y = int(round(t["cy"] - camada.height / 2))
    folha = Image.new("RGBA", base.size, (0, 0, 0, 0))
    folha.paste(camada, (x, y))
    base.alpha_composite(folha)


def renderizar_capa(capa, produto, n_laminas, info, largura=None, guias=False, rapido=False, sem_fundo=False):
    """sem_fundo: só foto e textos em RGBA transparente (para a prévia rápida do fundo)."""
    capa = completar(capa)
    tamanho = capa_px(produto, n_laminas)
    if largura:
        escala = largura / float(tamanho[0])
        tamanho = (int(largura), max(1, int(round(tamanho[1] * escala))))
    espaco = espaco_foto(capa, produto, n_laminas)
    img = compor(tamanho, None if sem_fundo else capa["fundo"], [espaco] if espaco else [], [], info, rapido,
                 fundo_imagem=None if sem_fundo else capa.get("fundo_imagem"))
    base = img.convert("RGBA")
    for t in textos_posicionados(capa, produto, n_laminas, tamanho):
        _desenhar_texto(base, t)
    if sem_fundo:
        return base
    img = base.convert("RGB")
    if guias:
        W_mm, H_mm = capa_mm(produto, n_laminas)
        k = tamanho[0] / W_mm
        s = float(produto["capa"]["sangria_mm"]) * k
        areas = areas_capa(produto, n_laminas)
        dobras = [areas["lombada"]["x"] * tamanho[0], (areas["lombada"]["x"] + areas["lombada"]["w"]) * tamanho[0]]
        desenhar_guias(img, [(s, s, tamanho[0] - s, tamanho[1] - s)], [], dobras)
    return img
