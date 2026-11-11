# -*- coding: utf-8 -*-
"""
Edição automática por IA: Image-Adaptive 3D LUT (Zeng et al., pré-treinado no MIT-Adobe FiveK, sRGB).

Uma rede pequena olha a foto reduzida (256x256) e devolve 3 pesos; a LUT final é a soma ponderada das
3 LUTs base (33x33x33), aplicada com a mesma interpolação trilinear do código original (trilinear.c,
LUT[c][b][g][r]). Só a miniatura passa pela rede; a foto inteira recebe apenas a consulta à LUT, em blocos.
"""

import functools

import numpy as np
from PIL import Image

import ia_modelos

LADO_REDE = 256
LADO_REFERENCIA = 480
DIM = 33
FATOR_BIN = 1.0001
PIXELS_POR_BLOCO = 1 << 18


def preparar_entrada(img):
    """Foto -> (1, 3, 256, 256) em [0, 1] RGB; o redimensionamento final é o do nn.Upsample(bilinear) original."""
    import cv2

    img = img.convert("RGB")
    if max(img.size) > LADO_REFERENCIA:
        escala = LADO_REFERENCIA / float(max(img.size))
        img = img.resize((max(1, round(img.width * escala)), max(1, round(img.height * escala))), Image.BILINEAR)
    arr = np.asarray(img, dtype=np.float32) / np.float32(255.0)
    arr = cv2.resize(arr, (LADO_REDE, LADO_REDE), interpolation=cv2.INTER_LINEAR)
    return np.ascontiguousarray(arr.transpose(2, 0, 1)[None])


def pesos_lut(img):
    """Os 3 pesos das LUTs base para esta foto, ou None sem o modelo."""
    net = ia_modelos.rede("edicao")
    if net is None:
        return None
    net.setInput(preparar_entrada(img))
    saida = np.asarray(net.forward(), dtype=np.float64).reshape(-1)
    if saida.size < 3 or not np.all(np.isfinite(saida[:3])):
        return None
    return [round(float(v), 6) for v in saida[:3]]


def luts_base():
    dados = ia_modelos.carregar_npz_modelo("edicao", "luts")
    if not dados or "luts" not in dados:
        return None
    luts = np.asarray(dados["luts"], dtype=np.float32)
    if luts.shape != (3, 3, DIM, DIM, DIM):
        return None
    return luts


@functools.lru_cache(maxsize=4)
def _tabelas(dim):
    binsize = np.float32(FATOR_BIN / (dim - 1))
    x = np.arange(256, dtype=np.float32) / np.float32(255.0)
    ids = np.floor(x / binsize).astype(np.int32)
    d = (np.fmod(x, binsize) / binsize).astype(np.float32)
    return ids, d


def preparar_lut(lut):
    """
    LUT (3, dim, dim, dim) [c][b][g][r] já interpolada em R e G para os 256 valores de 8 bits, em 0..255 + 0.5:
    (dim * 256 * 256, 3), linha = (nó_b * 256 + g) * 256 + r. A trilinear vira 2 consultas + 1 mistura em B.
    """
    lut = np.asarray(lut, dtype=np.float32)
    dim = lut.shape[-1]
    pesos = _matriz_interpolacao(dim)
    em_r = (lut.reshape(-1, dim) @ pesos.T).reshape(3 * dim, dim, 256)
    em_rg = np.matmul(pesos[None], em_r).reshape(3, -1)
    preparada = np.empty((em_rg.shape[1], 3), dtype=np.float32)
    for c in range(3):
        np.multiply(em_rg[c], np.float32(255.0), out=preparada[:, c])
    preparada += np.float32(0.5)
    return preparada


@functools.lru_cache(maxsize=4)
def _matriz_interpolacao(dim):
    """(256, dim): linha v = pesos (1 - d, d) nos nós ids[v] e ids[v] + 1, como no trilinear.c."""
    ids, d = _tabelas(dim)
    linhas = np.arange(256)
    pesos = np.zeros((256, dim), dtype=np.float32)
    pesos[linhas, ids] = 1.0 - d
    pesos[linhas, ids + 1] += d
    return pesos


@functools.lru_cache(maxsize=3)
def _lut_combinada_cache(pesos, geracao):
    luts = luts_base()
    if luts is None:
        return None
    p = np.asarray(pesos, dtype=np.float32)
    return preparar_lut(p[0] * luts[0] + p[1] * luts[1] + p[2] * luts[2])


def lut_combinada(pesos):
    if not pesos or len(pesos) < 3:
        return None
    return _lut_combinada_cache(tuple(round(float(p), 6) for p in pesos[:3]), ia_modelos._geracao)


def aplicar_preparada(arr, preparada, forca=100.0, bloco=PIXELS_POR_BLOCO):
    """arr uint8 (h, w, 3) RGB com a LUT de preparar_lut; forca 0..100 mistura com o original."""
    dim = preparada.shape[0] // (256 * 256)
    ids, tab_d = _tabelas(dim)
    t_b = ids * (256 * 256)
    mistura = np.float32(max(0.0, min(1.0, float(forca) / 100.0)))
    resto = (np.arange(256, dtype=np.float32) + np.float32(0.5)) * (np.float32(1.0) - mistura)
    plano = np.ascontiguousarray(arr).reshape(-1, 3)
    saida = np.empty_like(plano)
    passo_b = 256 * 256
    for ini in range(0, plano.shape[0], bloco):
        parte = plano[ini:ini + bloco]
        b = parte[:, 2]
        linha = t_b[b] + (parte[:, 1].astype(np.int32) << 8) + parte[:, 0]
        res = preparada.take(linha, axis=0)
        cima = preparada.take(linha + passo_b, axis=0)
        cima -= res
        cima *= tab_d[b][:, None]
        res += cima
        np.clip(res, 0.5, 255.5, out=res)
        if mistura < 1.0:
            res *= mistura
            res += resto[parte]
        saida[ini:ini + bloco] = res
    return saida.reshape(arr.shape)


def aplicar_lut_array(arr, lut, forca=100.0, bloco=PIXELS_POR_BLOCO):
    """arr uint8 (h, w, 3) RGB com a LUT (3, dim, dim, dim)."""
    return aplicar_preparada(arr, preparar_lut(lut), forca, bloco)


def aplicar_lut(img, pesos, forca=100.0):
    """A foto com o estilo da IA na força pedida; sem modelo ou pesos devolve a foto como veio."""
    if not pesos or float(forca or 0) <= 0:
        return img
    preparada = lut_combinada(pesos)
    if preparada is None:
        return img
    if img.mode != "RGB":
        img = img.convert("RGB")
    return Image.fromarray(aplicar_preparada(np.asarray(img), preparada, forca), "RGB")


def luminancia_rostos(img, rosto):
    """Luminância média (0..255) dentro das caixas dos rostos detectados, ou None."""
    if not isinstance(rosto, dict):
        return None
    caixas = rosto.get("rostos") or []
    if not caixas and rosto.get("cx") is not None:
        h_r = float(rosto.get("h") or 0.15)
        caixas = [{"cx": rosto["cx"], "cy": rosto["cy"], "w": h_r * 0.8, "h": h_r}]
    cinza = np.asarray(img.convert("L"), dtype=np.float32)
    alt, larg = cinza.shape
    soma, qtd = 0.0, 0
    for c in caixas:
        try:
            cx, cy = float(c["cx"]), float(c["cy"])
            w, h = float(c.get("w") or 0.08), float(c.get("h") or 0.1)
        except (KeyError, TypeError, ValueError):
            continue
        x0, x1 = int((cx - w * 0.35) * larg), int(round((cx + w * 0.35) * larg))
        y0, y1 = int((cy - h * 0.15) * alt), int(round((cy + h * 0.45) * alt))
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(larg, max(x0 + 1, x1)), min(alt, max(y0 + 1, y1))
        regiao = cinza[y0:y1, x0:x1]
        if regiao.size:
            soma += float(regiao.sum())
            qtd += int(regiao.size)
    return round(soma / qtd, 2) if qtd else None
