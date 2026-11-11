# -*- coding: utf-8 -*-
"""
Reconhecimento de cena (Places365 / ResNet18, MIT CSAIL) para o álbum de formatura.

As 365 categorias viram cenas do álbum (Colação/Palco, Baile/Festa, Culto/Igreja, Externa, Estúdio,
Interna). Estúdio também sai do fundo uniforme; Retrato individual / Grupo vem dos rostos do YuNet.
Jaleco não existe no Places365: fica pelo nome da pasta, com só uma pista fraca de roupa branca.
"""

import numpy as np
from PIL import Image

import ia_modelos

LADO_ENTRADA = 224
MEDIA = np.array([0.485, 0.456, 0.406], dtype=np.float32)
DESVIO = np.array([0.229, 0.224, 0.225], dtype=np.float32)
CONFIANCA_MINIMA = 0.30

COLACAO = "Colação/Palco"
BAILE = "Baile/Festa"
CULTO = "Culto/Igreja"
EXTERNA = "Externa"
ESTUDIO = "Estúdio"
INTERNA = "Interna"

MAPA_CENAS = {
    COLACAO: (
        "auditorium", "stage/indoor", "stage/outdoor", "lecture_room", "conference_center", "conference_room",
        "orchestra_pit", "legislative_chamber", "arena/performance", "amphitheater", "movie_theater/indoor",
        "gymnasium/indoor", "basketball_court/indoor",
    ),
    BAILE: (
        "ballroom", "banquet_hall", "discotheque", "bar", "restaurant", "dining_hall", "beer_hall", "pub/indoor",
        "wet_bar", "dining_room", "restaurant_patio", "beer_garden",
    ),
    CULTO: ("church/indoor", "church/outdoor", "synagogue/outdoor", "mosque/outdoor", "temple/asia"),
    ESTUDIO: ("television_studio",),
}
_CATEGORIA_PARA_CENA = {cat: cena for cena, cats in MAPA_CENAS.items() for cat in cats}


def preprocessar(img):
    """Foto -> (1, 3, 224, 224): RGB redimensionado (como trn.Resize((224, 224)) do Places365), média/desvio ImageNet."""
    img = img.convert("RGB").resize((LADO_ENTRADA, LADO_ENTRADA), Image.BILINEAR)
    arr = np.asarray(img, dtype=np.float32) / np.float32(255.0)
    arr = (arr - MEDIA) / DESVIO
    return np.ascontiguousarray(arr.transpose(2, 0, 1)[None])


def categorias():
    dados = ia_modelos.carregar_json_modelo("cena", "categorias")
    if not isinstance(dados, dict) or len(dados.get("categorias") or []) != 365:
        return None
    return dados


def probabilidades(img):
    """Softmax das 365 categorias, ou None sem o modelo."""
    net = ia_modelos.rede("cena")
    if net is None:
        return None
    net.setInput(preprocessar(img))
    logits = np.asarray(net.forward(), dtype=np.float64).reshape(-1)
    if logits.size != 365 or not np.all(np.isfinite(logits)):
        return None
    logits -= logits.max()
    p = np.exp(logits)
    return p / p.sum()


def massas_por_cena(probs, nomes, externa):
    """Soma das probabilidades por cena do álbum; categorias externas sem cena própria contam como Externa."""
    massas = {COLACAO: 0.0, BAILE: 0.0, CULTO: 0.0, EXTERNA: 0.0, ESTUDIO: 0.0, INTERNA: 0.0}
    for i, p in enumerate(probs):
        cena = _CATEGORIA_PARA_CENA.get(nomes[i])
        if cena is None:
            cena = EXTERNA if externa[i] else INTERNA
        massas[cena] += float(p)
    return massas


def fundo_uniforme(img):
    """0..1: quanto o anel externo da foto (fundo atrás das pessoas) é liso, como fundo de estúdio."""
    pequena = np.asarray(img.convert("RGB").resize((96, 96), Image.BILINEAR), dtype=np.float32)
    mascara = np.ones((96, 96), dtype=bool)
    mascara[22:78, 22:74] = False
    anel = pequena[mascara]
    lum = anel @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    desvio = float(lum.std())
    faixas = [pequena[:20, :, :], pequena[:, :16, :], pequena[:, -16:, :]]
    medias = [float(f.mean()) for f in faixas]
    diferenca = max(medias) - min(medias)
    return round(max(0.0, min(1.0, (24.0 - desvio) / 16.0)) * max(0.0, min(1.0, (40.0 - diferenca) / 25.0)), 3)


def roupa_branca(img, rosto):
    """Fração de branco no tronco abaixo do rosto principal (pista fraca de jaleco/beca branca)."""
    if not isinstance(rosto, dict):
        return 0.0
    lista = rosto.get("rostos") or []
    if not lista:
        return 0.0
    principal = max(lista, key=lambda r: float(r.get("w") or 0) * float(r.get("h") or 0))
    cx, cy = float(principal.get("cx", 0.5)), float(principal.get("cy", 0.4))
    w, h = float(principal.get("w") or 0.1), float(principal.get("h") or 0.12)
    hsv = np.asarray(img.convert("HSV"), dtype=np.float32)
    alt, larg = hsv.shape[:2]
    x0, x1 = int(max(0.0, cx - w * 1.1) * larg), int(min(1.0, cx + w * 1.1) * larg)
    y0, y1 = int(min(1.0, cy + h * 0.9) * alt), int(min(1.0, cy + h * 2.8) * alt)
    regiao = hsv[y0:y1, x0:x1]
    if regiao.size < 30:
        return 0.0
    branco = (regiao[..., 2] > 190) & (regiao[..., 1] < 40)
    return round(float(branco.mean()), 3)


def enquadramento(rosto):
    try:
        qtd = int((rosto or {}).get("qtd") or 0)
    except (TypeError, ValueError, AttributeError):
        qtd = 0
    if qtd >= 2:
        return "Grupo"
    if qtd == 1:
        return "Retrato individual"
    return "Sem rosto"


def decidir(massas, uniforme):
    """(rótulo, confiança) a partir das massas por cena e do fundo uniforme."""
    especificas = {c: massas[c] for c in (COLACAO, BAILE, CULTO, EXTERNA)}
    melhor = max(especificas, key=especificas.get)
    estudio = max(massas[ESTUDIO], uniforme * 0.8 if massas[EXTERNA] < 0.5 else 0.0)
    if estudio >= CONFIANCA_MINIMA and estudio > especificas[melhor]:
        return ESTUDIO, round(estudio, 3)
    if especificas[melhor] >= CONFIANCA_MINIMA:
        return melhor, round(especificas[melhor], 3)
    return INTERNA, round(massas[INTERNA], 3)


def reconhecer(img, rosto=None):
    """
    {"rotulo", "confianca", "top5": [(categoria, prob)], "io": "interna"/"externa", "enquadramento",
     "fundo_uniforme", "roupa_branca"} ou None sem o modelo.
    """
    probs = probabilidades(img)
    cats = categorias()
    if probs is None or cats is None:
        return None
    nomes = cats["categorias"]
    externa = cats.get("externa") or [0] * 365
    massas = massas_por_cena(probs, nomes, externa)
    uniforme = fundo_uniforme(img)
    rotulo, confianca = decidir(massas, uniforme)
    ordem = np.argsort(probs)[::-1][:5]
    io_externa = float(sum(float(probs[i]) for i in range(365) if externa[i]))
    return {
        "rotulo": rotulo,
        "confianca": confianca,
        "top5": [(nomes[int(i)], round(float(probs[int(i)]), 4)) for i in ordem],
        "io": "externa" if io_externa >= 0.5 else "interna",
        "enquadramento": enquadramento(rosto),
        "fundo_uniforme": uniforme,
        "roupa_branca": roupa_branca(img, rosto),
    }


def texto_curto(cena):
    """'Baile/Festa (72%) · Grupo' para dicas e diagnóstico."""
    if not isinstance(cena, dict) or not cena.get("rotulo"):
        return ""
    texto = "%s (%d%%)" % (cena["rotulo"], int(round(float(cena.get("confianca") or 0) * 100)))
    if cena.get("enquadramento") and cena["enquadramento"] != "Sem rosto":
        texto += " · %s" % cena["enquadramento"]
    return texto
