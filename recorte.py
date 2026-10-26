# -*- coding: utf-8 -*-
"""
Corte da foto para o espaço e tratamento estilo Lightroom.

A edição de cada espaço fica no projeto, em frações da foto (não em pixels),
para servir tanto na miniatura da prévia quanto na foto inteira:
  corte: {"cx", "cy", "zoom"} centro do corte e zoom (1 = o maior corte que
         cabe na foto; menor que 1 = foto inteira com borda). Sem corte = automático pelo rosto.
  rot (0, 90, 180, 270, no sentido horário), espelhar, angulo (endireitar, graus)
  Luz: exposicao, brilho, contraste, realces, sombras (-100 a 100)
  Cor: temperatura, matiz, vibracao, saturacao (-100 a 100) e pb (preto e branco)
  Efeitos: nitidez (0 a 100), vinheta (-100 a 100)
"""

import math

from PIL import Image, ImageChops, ImageEnhance, ImageOps

import analise
from comum import cor_rgb

OLHOS_NO_CORTE = 0.38
ZOOM_MAXIMO = 6.0

CHAVES_AJUSTES = (
    "exposicao", "brilho", "contraste", "realces", "sombras",
    "temperatura", "matiz", "vibracao", "saturacao",
    "nitidez", "vinheta",
)

_CACHE_VINHETA_BASE = None


def edicao_padrao():
    return {
        "rot": 0, "espelhar": False, "angulo": 0.0,
        "exposicao": 0, "brilho": 0, "contraste": 0,
        "realces": 0, "sombras": 0,
        "temperatura": 0, "matiz": 0,
        "vibracao": 0, "saturacao": 0,
        "nitidez": 0, "vinheta": 0,
        "pb": False,
    }


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


def corte_automatico(W, H, aspecto, rosto_t):
    """
    Preenche o espaço (zoom = 1.0) centralizando no rosto se houver.
    A proteção de proporção máxima (22%) já é feita nos quadros do diagramar.py,
    evitando que arredondamentos de 1px na prévia façam a imagem piscar entre
    cortada (zoom 1.0) e encaixada inteira (zoom_encaixar).
    """
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


def corte_do_espaco(W, H, aspecto, edicao, rosto, rosto_ja_transformado=False):
    """Corte que vale para o espaço: o manual (limitado) ou o automático."""
    ed = edicao or {}
    if ed.get("corte"):
        return limitar_corte(W, H, aspecto, ed["corte"])
    if ed.get("ignorar_rosto"):
        rosto_t = None
    elif rosto_ja_transformado:
        rosto_t = rosto
    else:
        rosto_t = rosto_transformado(rosto, ed)
    return corte_automatico(W, H, aspecto, rosto_t)


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


# ------------------------------------------------------------
# Tratamento de cor e luz estilo Lightroom
# ------------------------------------------------------------

def _lut_tonal_e_balanco(exposicao, brilho, contraste, realces, sombras, temperatura, matiz):
    """
    Gera tabela LUT de 768 entradas (R, G, B) combinando Exposição, Brilho,
    Contraste, Realces, Sombras, Temperatura e Matiz em uma única passagem C.
    """
    fator_exp = 2.0 ** (exposicao / 55.0) if exposicao else 1.0
    fator_bri = 1.0 + (brilho / 100.0) * 0.65 if brilho else 1.0
    fator_con = (1.0 + (contraste / 100.0) * 0.85) if contraste >= 0 else (1.0 + (contraste / 100.0) * 0.55)
    forca_sombras = (sombras / 100.0) * 0.55
    forca_realces = (realces / 100.0) * 0.55

    # Balanço de branco (Temperatura: azul <-> âmbar, Matiz: verde <-> magenta)
    t_norm = temperatura / 100.0
    m_norm = matiz / 100.0
    dr = t_norm * 0.22 + m_norm * 0.10
    dg = t_norm * 0.03 - m_norm * 0.18
    db = -t_norm * 0.24 + m_norm * 0.10

    lut_r, lut_g, lut_b = [], [], []
    for i in range(256):
        x = i / 255.0
        # 1. Exposição fotográfica com compressão suave no topo
        if exposicao > 0:
            x = 1.0 - math.exp(-x * fator_exp * 1.45) / (1.0 - math.exp(-1.45) + 1e-9) * (1.0 - x) + (x * fator_exp - x) * (1.0 - x * 0.65)
            x = min(1.0, max(0.0, (i / 255.0) * fator_exp / (1.0 + ((i / 255.0) * (fator_exp - 1.0) * 0.35))))
        elif exposicao < 0:
            x = x * fator_exp

        # 2. Sombras (atua nos tons escuros preservando o preto absoluto)
        if sombras:
            peso_s = ((1.0 - x) ** 1.8) * (0.25 + 0.75 * math.sin(math.pi * min(1.0, x * 1.4)))
            x = x + forca_sombras * peso_s

        # 3. Realces (atua nos tons claros preservando o branco)
        if realces:
            peso_r = (x ** 1.8) * (0.25 + 0.75 * math.sin(math.pi * max(0.0, (x - 0.2) / 0.8)))
            x = x + forca_realces * peso_r

        # 4. Brilho e Contraste centrados no meio-tom
        if brilho:
            x = x * fator_bri + (brilho / 100.0) * 0.12
        if contraste:
            x = (x - 0.5) * fator_con + 0.5

        x = min(1.0, max(0.0, x))
        # Peso para balanço de branco (protege preto e branco puros)
        peso_wb = 0.2 + 0.8 * math.sin(math.pi * x)
        r = min(255, max(0, int(round((x + dr * peso_wb) * 255.0))))
        g = min(255, max(0, int(round((x + dg * peso_wb) * 255.0))))
        b = min(255, max(0, int(round((x + db * peso_wb) * 255.0))))
        lut_r.append(r)
        lut_g.append(g)
        lut_b.append(b)
    return lut_r + lut_g + lut_b


def _mascara_vinheta_base():
    """Gera uma máscara radial 128x128 em escala de cinza (0 no centro, 255 nos cantos)."""
    global _CACHE_VINHETA_BASE
    if _CACHE_VINHETA_BASE is not None:
        return _CACHE_VINHETA_BASE
    tamanho = 128
    mascara = Image.new("L", (tamanho, tamanho))
    px = mascara.load()
    centro = (tamanho - 1) / 2.0
    for y in range(tamanho):
        dy = (y - centro) / centro
        for x in range(tamanho):
            dx = (x - centro) / centro
            dist = math.sqrt(dx * dx + dy * dy)
            # Começa suave a partir de r=0.35 até r=1.35
            t = min(1.0, max(0.0, (dist - 0.32) / 0.98))
            curva = t * t * (3.0 - 2.0 * t)
            px[x, y] = int(round(curva * 255.0))
    _CACHE_VINHETA_BASE = mascara
    return mascara


def aplicar_ajustes(img, edicao):
    ed = _ed(edicao)
    if img.mode != "RGB":
        img = img.convert("RGB")

    exposicao = float(ed.get("exposicao") or 0)
    brilho = float(ed.get("brilho") or 0)
    contraste = float(ed.get("contraste") or 0)
    realces = float(ed.get("realces") or 0)
    sombras = float(ed.get("sombras") or 0)
    temperatura = float(ed.get("temperatura") or 0)
    matiz = float(ed.get("matiz") or 0)
    vibracao = float(ed.get("vibracao") or 0)
    saturacao = float(ed.get("saturacao") or 0)
    nitidez = float(ed.get("nitidez") or 0)
    vinheta = float(ed.get("vinheta") or 0)

    # 1. Luz + Balanço de branco (LUT único de alta velocidade)
    if any((exposicao, brilho, contraste, realces, sombras, temperatura, matiz)):
        lut = _lut_tonal_e_balanco(exposicao, brilho, contraste, realces, sombras, temperatura, matiz)
        img = img.point(lut)

    # 2. Cor: P&B, Vibração seletiva e Saturação
    if ed.get("pb"):
        img = ImageOps.grayscale(img).convert("RGB")
    else:
        if vibracao:
            cinza = ImageOps.grayscale(img).convert("RGB")
            diff = ImageOps.grayscale(ImageChops.difference(img, cinza))
            forca = abs(vibracao) / 100.0
            if vibracao > 0:
                # Realça mais os tons pouco saturados sem estourar pele/cores já vivas
                saturada = ImageEnhance.Color(img).enhance(1.0 + forca * 1.35)
                mascara = diff.point(lambda d: int(round(((1.0 - (d / 255.0)) ** 0.85) * forca * 255)))
                img = Image.composite(saturada, img, mascara)
            else:
                img = ImageEnhance.Color(img).enhance(max(0.0, 1.0 - forca * 0.75))
        if saturacao:
            img = ImageEnhance.Color(img).enhance(max(0.0, 1.0 + saturacao / 100.0))

    # 3. Nitidez
    if nitidez > 0:
        img = ImageEnhance.Sharpness(img).enhance(1.0 + (nitidez / 100.0) * 2.4)

    # 4. Vinheta (positiva ou negativa: escurece ou clareia bordas suavemente)
    if abs(vinheta) >= 1:
        forca_v = min(1.0, abs(vinheta) / 100.0) * 0.82
        mascara = _mascara_vinheta_base().resize(img.size, Image.BILINEAR)
        mascara = mascara.point(lambda v: int(round(v * forca_v)))
        cor_borda = (0, 0, 0) if vinheta > 0 else (255, 255, 255)
        camada_v = Image.new("RGB", img.size, cor_borda)
        img = Image.composite(camada_v, img, mascara)

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
