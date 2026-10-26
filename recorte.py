# -*- coding: utf-8 -*-
"""
Corte da foto para o espaço.

A edição de cada espaço fica no projeto, em frações da foto (não em pixels),
para servir tanto na miniatura da prévia quanto na foto inteira:
  corte: {"cx", "cy", "zoom"} centro do corte e zoom (1 = o maior corte que
         cabe na foto; menor que 1 = foto inteira com borda). Sem corte = automático pelo rosto.
  rot (0, 90, 180, 270, no sentido horário), espelhar, angulo (endireitar, graus)
  brilho, contraste, saturacao (-100 a 100) e pb (preto e branco)
"""

import math

from PIL import Image, ImageEnhance, ImageOps

import analise
from comum import cor_rgb

OLHOS_NO_CORTE = 0.38
ZOOM_MAXIMO = 6.0


def edicao_padrao():
    return {"rot": 0, "espelhar": False, "angulo": 0.0, "brilho": 0, "contraste": 0, "saturacao": 0, "pb": False}


def _ed(edicao):
    ed = edicao_padrao()
    ed.update(edicao or {})
    return ed


# ------------------------------------------------------------
# Giro, espelho e endireitar
# ------------------------------------------------------------

def _fator_inscrito(w, h, angulo):
    """Quanto reduzir para o retângulo girado não mostrar cantos vazios."""
    a = math.radians(abs(angulo))
    if a < 1e-6:
        return 1.0
    return 1.0 / (math.cos(a) + math.sin(a) * max(w / float(h), h / float(w)))


def transformar(img, edicao):
    ed = _ed(edicao)
    rot = int(ed["rot"]) % 360
    if rot == 90:
        img = img.transpose(Image.ROTATE_270)
    elif rot == 180:
        img = img.transpose(Image.ROTATE_180)
    elif rot == 270:
        img = img.transpose(Image.ROTATE_90)
    if ed["espelhar"]:
        img = ImageOps.mirror(img)
    angulo = float(ed["angulo"] or 0)
    if abs(angulo) > 0.01:
        w, h = img.size
        girada = img.rotate(-angulo, resample=Image.BICUBIC, expand=False)
        f = _fator_inscrito(w, h, angulo)
        cw, ch = w * f, h * f
        l, t = (w - cw) / 2, (h - ch) / 2
        img = girada.crop((int(round(l)), int(round(t)), int(round(l + cw)), int(round(t + ch))))
    return img


def tamanho_transformado(w, h, edicao):
    ed = _ed(edicao)
    if int(ed["rot"]) % 180 == 90:
        w, h = h, w
    f = _fator_inscrito(w, h, float(ed["angulo"] or 0))
    return w * f, h * f


def rosto_transformado(rosto, edicao):
    """Posição do rosto (fração) depois do giro e do espelho."""
    if not rosto:
        return None
    ed = _ed(edicao)
    x, y = rosto["cx"], rosto["cy"]
    rot = int(ed["rot"]) % 360
    if rot == 90:
        x, y = 1 - y, x
    elif rot == 180:
        x, y = 1 - x, 1 - y
    elif rot == 270:
        x, y = y, 1 - x
    if ed["espelhar"]:
        x = 1 - x
    return (x, y)


# ------------------------------------------------------------
# Caixa de corte
# ------------------------------------------------------------

def caixa_base(W, H, aspecto):
    """Maior caixa com a proporção do espaço que cabe na foto."""
    if W / float(H) > aspecto:
        return H * aspecto, float(H)
    return float(W), W / aspecto


def zoom_encaixar(W, H, aspecto):
    """Zoom em que a foto inteira aparece dentro do espaço (com borda)."""
    bw, bh = caixa_base(W, H, aspecto)
    return min(bw / float(W), bh / float(H))


CORTE_MAXIMO_AUTOMATICO = 1.22


def corte_automatico(W, H, aspecto, rosto_t):
    """
    Se o quadro distorcer/cortar a foto além de CORTE_MAXIMO_AUTOMATICO (22%),
    usa a proporção padrão da imagem (zoom_encaixar) sem mutilar a foto.
    """
    if W > 0 and H > 0 and aspecto > 0:
        prop_foto = W / float(H)
        diferenca = max(prop_foto / aspecto, aspecto / prop_foto)
        if diferenca > CORTE_MAXIMO_AUTOMATICO:
            return {"cx": 0.5, "cy": 0.5, "zoom": zoom_encaixar(W, H, aspecto)}
    bw, bh = caixa_base(W, H, aspecto)
    if rosto_t:
        cx = rosto_t[0] * W
        cy = rosto_t[1] * H + (0.5 - OLHOS_NO_CORTE) * bh
    else:
        cx, cy = W / 2.0, H / 2.0
    cx = min(max(cx, bw / 2), W - bw / 2)
    cy = min(max(cy, bh / 2), H - bh / 2)
    return {"cx": cx / W, "cy": cy / H, "zoom": 1.0}


def limitar_corte(W, H, aspecto, corte):
    """Mantém o zoom no intervalo e, com zoom >= 1, a caixa dentro da foto."""
    zoom = min(max(float(corte.get("zoom", 1.0)), zoom_encaixar(W, H, aspecto)), ZOOM_MAXIMO)
    bw, bh = caixa_base(W, H, aspecto)
    bw, bh = bw / zoom, bh / zoom
    cx, cy = corte["cx"] * W, corte["cy"] * H
    if bw <= W:
        cx = min(max(cx, bw / 2), W - bw / 2)
    else:
        cx = W / 2.0
    if bh <= H:
        cy = min(max(cy, bh / 2), H - bh / 2)
    else:
        cy = H / 2.0
    return {"cx": cx / W, "cy": cy / H, "zoom": zoom}


def corte_do_espaco(W, H, aspecto, edicao, rosto):
    """Corte que vale para o espaço: o manual (limitado) ou o automático."""
    ed = edicao or {}
    if ed.get("corte"):
        return limitar_corte(W, H, aspecto, ed["corte"])
    if ed.get("ignorar_rosto"):
        rosto = None
    return corte_automatico(W, H, aspecto, rosto_transformado(rosto, ed))


def caixa_corte(W, H, aspecto, corte):
    """(left, top, right, bottom) em pixels da foto transformada. Pode passar da foto quando zoom < 1."""
    bw, bh = caixa_base(W, H, aspecto)
    zoom = float(corte.get("zoom", 1.0))
    bw, bh = bw / zoom, bh / zoom
    cx, cy = corte["cx"] * W, corte["cy"] * H
    return (cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2)


def recortar(img, caixa, tamanho, fundo=(255, 255, 255), rapido=False):
    """Recorta a caixa (que pode passar da foto) e redimensiona para o tamanho exato."""
    tw, th = int(tamanho[0]), int(tamanho[1])
    l, t, r, b = caixa
    escala_x = tw / float(r - l)
    escala_y = th / float(b - t)
    filtro = Image.BILINEAR if rapido else Image.LANCZOS
    il, it = max(0.0, l), max(0.0, t)
    ir, ib = min(float(img.width), r), min(float(img.height), b)
    if il <= l and it <= t and ir >= r and ib >= b:
        return img.resize((tw, th), filtro, box=(l, t, r, b))
    saida = Image.new("RGB", (tw, th), cor_rgb(fundo))
    if ir <= il or ib <= it:
        return saida
    destino_w = max(1, int(round((ir - il) * escala_x)))
    destino_h = max(1, int(round((ib - it) * escala_y)))
    parte = img.resize((destino_w, destino_h), filtro, box=(il, it, ir, ib))
    saida.paste(parte, (int(round((il - l) * escala_x)), int(round((it - t) * escala_y))))
    return saida


def aplicar_ajustes(img, edicao):
    ed = _ed(edicao)
    if ed["brilho"]:
        img = ImageEnhance.Brightness(img).enhance(1 + float(ed["brilho"]) / 100.0)
    if ed["contraste"]:
        img = ImageEnhance.Contrast(img).enhance(1 + float(ed["contraste"]) / 100.0)
    if ed["pb"]:
        img = ImageOps.grayscale(img).convert("RGB")
    elif ed["saturacao"]:
        img = ImageEnhance.Color(img).enhance(1 + float(ed["saturacao"]) / 100.0)
    return img


def foto_no_espaco(caminho, edicao, rosto, tamanho, fundo=(255, 255, 255), rapido=False):
    """
    A foto pronta para o espaço, com o tamanho exato em pixels.
    rapido usa a miniatura (prévia); senão abre a foto inteira (exportação).
    """
    tw, th = max(1, int(tamanho[0])), max(1, int(tamanho[1]))
    if rapido:
        fonte = analise.imagem_para(caminho, max(tw, th) * 1.5)
    else:
        fonte, _icc, _dpi = analise.abrir_imagem(caminho)
    img = transformar(fonte, edicao)
    W, H = img.size
    aspecto = tw / float(th)
    corte = corte_do_espaco(W, H, aspecto, edicao, rosto)
    saida = recortar(img, caixa_corte(W, H, aspecto, corte), (tw, th), fundo, rapido)
    return aplicar_ajustes(saida, edicao)
