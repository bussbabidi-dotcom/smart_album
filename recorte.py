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

import functools
import math
import threading
from collections import OrderedDict

from PIL import Image, ImageChops, ImageEnhance, ImageOps

import analise
from comum import cor_rgb, normalizar_caminho

OLHOS_NO_CORTE = 0.38
ZOOM_MAXIMO = 6.0

CHAVES_AJUSTES = (
    "exposicao", "brilho", "contraste", "realces", "sombras",
    "temperatura", "matiz", "vibracao", "saturacao",
    "nitidez", "vinheta",
)

_CACHE_VINHETA_BASE = None
_CACHE_ESPACO_RAPIDO = OrderedDict()
_TRAVA_CACHE_ESPACO = threading.Lock()
_LIMITE_CACHE_ESPACO = 240


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


def _chave_edicao_cache(edicao, rosto, dobra_x):
    if not edicao and not rosto and dobra_x is None:
        return (0, False, 0.0, None, None, None, False)
    ed = edicao or {}
    corte = ed.get("corte")
    corte_t = (
        round(float(corte.get("cx", 0.5)), 4),
        round(float(corte.get("cy", 0.5)), 4),
        round(float(corte.get("zoom", 1.0)), 4),
    ) if isinstance(corte, dict) else None
    rosto_t = (
        round(float(rosto.get("cx", 0.5)), 4),
        round(float(rosto.get("cy", 0.5)), 4),
    ) if isinstance(rosto, dict) else None
    ajustes_t = tuple(round(float(ed.get(k) or 0), 1) for k in CHAVES_AJUSTES)
    return (
        int(ed.get("rot") or 0) % 360,
        bool(ed.get("espelhar")),
        round(float(ed.get("angulo") or 0), 2),
        corte_t,
        rosto_t,
        round(float(dobra_x), 4) if dobra_x is not None else None,
        bool(ed.get("pb")),
        bool(ed.get("ignorar_rosto")),
        ajustes_t,
    )


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


class RostoTransformado(dict):
    """Dicionário de rosto transformado compatível tanto com indexação [0]/[1] quanto com chaves ('x0'..'y1', 'qtd', 'rostos')."""

    def __getitem__(self, chave):
        if chave == 0:
            return self["cx"]
        if chave == 1:
            return self["cy"]
        return super().__getitem__(chave)


def _transformar_ponto(x, y, rot, espelhar):
    if rot == 90:
        x, y = 1.0 - y, x
    elif rot == 180:
        x, y = 1.0 - x, 1.0 - y
    elif rot == 270:
        x, y = y, 1.0 - x
    if espelhar:
        x = 1.0 - x
    return x, y


def _transformar_caixa(x0, y0, x1, y1, rot, espelhar):
    p1 = _transformar_ponto(x0, y0, rot, espelhar)
    p2 = _transformar_ponto(x1, y0, rot, espelhar)
    p3 = _transformar_ponto(x0, y1, rot, espelhar)
    p4 = _transformar_ponto(x1, y1, rot, espelhar)
    xs = (p1[0], p2[0], p3[0], p4[0])
    ys = (p1[1], p2[1], p3[1], p4[1])
    return min(xs), min(ys), max(xs), max(ys)


def rosto_transformado(rosto, edicao):
    """
    Posição do(s) rosto(s) e caixa de segurança do grupo (fração 0..1)
    depois do giro e do espelhamento.
    """
    if not rosto:
        return None
    ed = _ed(edicao)
    rot = int(ed["rot"]) % 360
    espelhar = bool(ed["espelhar"])

    if isinstance(rosto, (tuple, list)):
        cx, cy = _transformar_ponto(float(rosto[0]), float(rosto[1]), rot, espelhar)
        return RostoTransformado(cx=cx, cy=cy, qtd=1, x0=max(0.0, cx - 0.12), y0=max(0.0, cy - 0.14), x1=min(1.0, cx + 0.12), y1=min(1.0, cy + 0.18), rostos=[])

    cx, cy = _transformar_ponto(float(rosto.get("cx", 0.5)), float(rosto.get("cy", 0.45)), rot, espelhar)
    h_r = float(rosto.get("h", 0.15))
    x0_orig = float(rosto.get("x0", max(0.0, float(rosto.get("cx", 0.5)) - max(0.08, h_r * 0.65))))
    y0_orig = float(rosto.get("y0", max(0.0, float(rosto.get("cy", 0.45)) - max(0.10, h_r * 0.75))))
    x1_orig = float(rosto.get("x1", min(1.0, float(rosto.get("cx", 0.5)) + max(0.08, h_r * 0.65))))
    y1_orig = float(rosto.get("y1", min(1.0, float(rosto.get("cy", 0.45)) + max(0.12, h_r * 0.95))))
    gx0, gy0, gx1, gy1 = _transformar_caixa(x0_orig, y0_orig, x1_orig, y1_orig, rot, espelhar)

    rostos_t = []
    for item in rosto.get("rostos") or []:
        icx, icy = _transformar_ponto(float(item.get("cx", 0.5)), float(item.get("cy", 0.5)), rot, espelhar)
        ix0, iy0, ix1, iy1 = _transformar_caixa(
            float(item.get("x0", icx - 0.06)),
            float(item.get("y0", icy - 0.08)),
            float(item.get("x1", icx + 0.06)),
            float(item.get("y1", icy + 0.10)),
            rot,
            espelhar,
        )
        rostos_t.append({"cx": icx, "cy": icy, "x0": ix0, "y0": iy0, "x1": ix1, "y1": iy1})

    qtd = max(1, int(rosto.get("qtd") or len(rostos_t) or 1))
    return RostoTransformado(
        cx=cx,
        cy=cy,
        h=h_r,
        qtd=qtd,
        x0=gx0,
        y0=gy0,
        x1=gx1,
        y1=gy1,
        rostos=rostos_t,
    )


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


def corte_automatico(W, H, aspecto, rosto_t, dobra_x=None):
    """
    Preenche o espaço (zoom = 1.0) protegendo TODOS os rostos/pessoas detectados na imagem
    para que nenhuma pessoa seja cortada nas bordas do quadro.
    Quando o quadro atravessa o meio da lâmina (dobra_x entre 0.05 e 0.95),
    desloca o corte horizontalmente dentro da margem segura para afastar rostos da dobra.
    """
    bw, bh = caixa_base(W, H, aspecto)
    if rosto_t:
        rx = float(rosto_t[0])
        ry = float(rosto_t[1])
        if isinstance(rosto_t, dict):
            gx0 = float(rosto_t.get("x0", max(0.0, rx - 0.10))) * W
            gy0 = float(rosto_t.get("y0", max(0.0, ry - 0.12))) * H
            gx1 = float(rosto_t.get("x1", min(1.0, rx + 0.10))) * W
            gy1 = float(rosto_t.get("y1", min(1.0, ry + 0.15))) * H
            qtd = int(rosto_t.get("qtd", 1))
            lista_r = rosto_t.get("rostos") or [{"cx": rx, "cy": ry}]
        else:
            gx0, gy0 = max(0.0, rx - 0.10) * W, max(0.0, ry - 0.12) * H
            gx1, gy1 = min(1.0, rx + 0.10) * W, min(1.0, ry + 0.15) * H
            qtd = 1
            lista_r = [{"cx": rx, "cy": ry}]

        # Posição ideal inicial: para grupos (2+ pessoas) centraliza no grupo inteiro;
        # para 1 pessoa posiciona os olhos na regra dos terços superior (OLHOS_NO_CORTE)
        if qtd >= 2:
            cx = (gx0 + gx1) * 0.5
            cy = (gy0 + gy1) * 0.48 + (ry * H) * 0.04
        else:
            cx = rx * W
            cy = ry * H + (0.5 - OLHOS_NO_CORTE) * bh

        # Intervalo seguro [cx_min, cx_max] e [cy_min, cy_max] que mantém TODAS as pessoas
        # 100% dentro da janela de corte [cx - bw/2 .. cx + bw/2, cy - bh/2 .. cy + bh/2]
        if (gx1 - gx0) <= bw:
            cx_min = max(bw / 2.0, gx1 - bw / 2.0)
            cx_max = min(W - bw / 2.0, gx0 + bw / 2.0)
            if cx_min <= cx_max:
                cx = min(max(cx, cx_min), cx_max)
            else:
                cx = (gx0 + gx1) * 0.5
        else:
            # Se o grupo de pessoas for mais largo que a janela, centraliza exatamente no grupo
            cx = (gx0 + gx1) * 0.5

        if (gy1 - gy0) <= bh:
            cy_min = max(bh / 2.0, gy1 - bh / 2.0)
            cy_max = min(H - bh / 2.0, gy0 + bh / 2.0)
            if cy_min <= cy_max:
                cy = min(max(cy, cy_min), cy_max)
            else:
                cy = (gy0 + gy1) * 0.5
        else:
            # Prioriza nunca cortar o topo da cabeça (gy0) quando o quadro é muito baixo
            cy = max(bh / 2.0, min(H - bh / 2.0, gy0 + bh * 0.48))

        # Desvio da linha de dobra central (sem nunca violar cx_min..cx_max que protege as pessoas das pontas)
        if dobra_x is not None and 0.05 < float(dobra_x) < 0.95 and bw < W - 1.0:
            dobra = float(dobra_x)
            margem_dobra = 0.13
            lim_esq = max(bw / 2.0, gx1 - bw / 2.0) if (gx1 - gx0) <= bw else bw / 2.0
            lim_dir = min(W - bw / 2.0, gx0 + bw / 2.0) if (gx1 - gx0) <= bw else W - bw / 2.0
            if lim_esq > lim_dir:
                lim_esq, lim_dir = bw / 2.0, W - bw / 2.0
            cx_clamped = min(max(cx, lim_esq), lim_dir)

            def _menor_dist_dobra(cand_cx):
                x_ini = cand_cx - bw / 2.0
                return min(abs((( float(r.get("cx", rx)) * W - x_ini ) / max(1.0, bw)) - dobra) for r in lista_r)

            if _menor_dist_dobra(cx_clamped) < margem_dobra and lim_dir > lim_esq + 1.0:
                # Testa deslocamentos seguros dentro de [lim_esq, lim_dir] para afastar todos os rostos da dobra
                passos = [lim_esq + (lim_dir - lim_esq) * (i / 16.0) for i in range(17)]
                melhor_cx = max(
                    passos,
                    key=lambda c_cand: (min(_menor_dist_dobra(c_cand), margem_dobra), -abs(c_cand - cx_clamped)),
                )
                cx = melhor_cx
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


def corte_do_espaco(W, H, aspecto, edicao, rosto, rosto_ja_transformado=False, dobra_x=None):
    """Corte que vale para o espaço: o manual (limitado) ou o automático (com desvio de dobra)."""
    ed = edicao or {}
    if ed.get("corte"):
        return limitar_corte(W, H, aspecto, ed["corte"])
    if ed.get("ignorar_rosto"):
        rosto_t = None
    elif rosto_ja_transformado:
        rosto_t = rosto
    else:
        rosto_t = rosto_transformado(rosto, ed)
    if dobra_x is None:
        dobra_x = ed.get("dobra_x")
    return corte_automatico(W, H, aspecto, rosto_t, dobra_x=dobra_x)


def caixa_corte(W, H, aspecto, corte):
    """(left, top, right, bottom) em pixels da foto transformada. Pode passar da foto quando zoom < 1."""
    bw, bh = caixa_base(W, H, aspecto)
    zoom = float(corte.get("zoom", 1.0))
    bw, bh = bw / zoom, bh / zoom
    cx, cy = corte["cx"] * W, corte["cy"] * H
    return (cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2)


def recortar(img, caixa, tamanho, fundo=(255, 255, 255), rapido=False):
    """Recorta a caixa (que pode passar da foto) e redimensiona para o tamanho exato."""
    tw, th = max(1, int(tamanho[0])), max(1, int(tamanho[1]))
    l, t, r, b = caixa
    escala_x = tw / float(max(1e-6, r - l))
    escala_y = th / float(max(1e-6, b - t))
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

@functools.lru_cache(maxsize=256)
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
            t = min(1.0, max(0.0, (dist - 0.32) / 0.98))
            curva = t * t * (3.0 - 2.0 * t)
            px[x, y] = int(round(curva * 255.0))
    _CACHE_VINHETA_BASE = mascara
    return mascara


def aplicar_ajustes(img, edicao):
    ed = _ed(edicao)
    if img.mode != "RGB":
        img = img.convert("RGB")

    exposicao = round(float(ed.get("exposicao") or 0), 1)
    brilho = round(float(ed.get("brilho") or 0), 1)
    contraste = round(float(ed.get("contraste") or 0), 1)
    realces = round(float(ed.get("realces") or 0), 1)
    sombras = round(float(ed.get("sombras") or 0), 1)
    temperatura = round(float(ed.get("temperatura") or 0), 1)
    matiz = round(float(ed.get("matiz") or 0), 1)
    vibracao = round(float(ed.get("vibracao") or 0), 1)
    saturacao = round(float(ed.get("saturacao") or 0), 1)
    nitidez = round(float(ed.get("nitidez") or 0), 1)
    vinheta = round(float(ed.get("vinheta") or 0), 1)

    # 1. Luz + Balanço de branco (LUT único de alta velocidade com cache)
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


def foto_no_espaco(caminho, edicao, rosto, tamanho, fundo=(255, 255, 255), rapido=False, dobra_x=None):
    """
    A foto pronta para o espaço, com o tamanho exato em pixels.
    rapido usa a miniatura (prévia) com cache em memória; senão abre a foto inteira (exportação).
    """
    tw, th = max(1, int(tamanho[0])), max(1, int(tamanho[1]))
    if rapido:
        fonte = analise.imagem_para(caminho, max(tw, th) * 1.5)
        cor_f = cor_rgb(fundo)
        chave_cache = (
            normalizar_caminho(caminho),
            id(fonte),
            tw,
            th,
            cor_f,
            _chave_edicao_cache(edicao, rosto, dobra_x),
        )
        with _TRAVA_CACHE_ESPACO:
            guardada = _CACHE_ESPACO_RAPIDO.get(chave_cache)
            if guardada is not None:
                _CACHE_ESPACO_RAPIDO.move_to_end(chave_cache)
                return guardada.copy()
    else:
        fonte, _icc, _dpi = analise.abrir_imagem(caminho)

    img = transformar(fonte, edicao)
    W, H = img.size
    aspecto = tw / float(th)
    corte = corte_do_espaco(W, H, aspecto, edicao, rosto, dobra_x=dobra_x)
    saida = recortar(img, caixa_corte(W, H, aspecto, corte), (tw, th), fundo, rapido)
    pronta = aplicar_ajustes(saida, edicao)

    if rapido:
        with _TRAVA_CACHE_ESPACO:
            _CACHE_ESPACO_RAPIDO[chave_cache] = pronta
            _CACHE_ESPACO_RAPIDO.move_to_end(chave_cache)
            while len(_CACHE_ESPACO_RAPIDO) > _LIMITE_CACHE_ESPACO:
                _CACHE_ESPACO_RAPIDO.popitem(last=False)
        return pronta.copy()
    return pronta
