# -*- coding: utf-8 -*-
"""
Texturas dos fundos gerados, desenhadas no tamanho final da imagem.

As medidas são em mm (convertidas pela altura física do fundo), para o grão ficar
igual na prévia, a 150 DPI e a 300 DPI. A faixa central da lâmina (dobra) recebe
menos textura e menos enfeites, para não competir com as fotos nem com o vinco.
"""

import math

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter

from comum import cor_rgb

ALTURA_PADRAO_MM = 220.0
# Lado máximo das camadas suaves (veios, bokeh, sombras): ampliadas depois, sem perder qualidade.
LADO_CAMADA_SUAVE = 1600


# ------------------------------------------------------------
# Ruído e máscaras
# ------------------------------------------------------------

def _ruido(W, H, ppm, mm, rng, estica_x=1.0):
    """Ruído com grão de `mm` milímetros (desvio 1); some quando o grão fica menor que 1 px."""
    passo = mm * ppm
    peso = min(1.0, passo)
    passo = max(1.0, passo)
    if passo * estica_x <= 1.01:
        return rng.standard_normal((H, W), dtype=np.float32) * peso
    gw = max(2, int(math.ceil(W / (passo * estica_x))) + 1)
    gh = max(2, int(math.ceil(H / passo)) + 1)
    base = rng.standard_normal((gh, gw), dtype=np.float32)
    arr = np.asarray(Image.fromarray(base, "F").resize((W, H), Image.BICUBIC), dtype=np.float32)
    desvio = float(arr.std()) or 1.0
    return arr * (peso / desvio)


def _suave(t):
    t = np.clip(t, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _calma_x(W):
    """Peso da textura por coluna: 45% na dobra, 100% nas laterais."""
    x = np.abs(np.arange(W, dtype=np.float32) / max(1, W - 1) - 0.5)
    return (0.45 + 0.55 * _suave(x / 0.22)).astype(np.float32)


def _peso_borda(u, v):
    """0 no centro da lâmina e na dobra, 1 perto das bordas (para enfeites)."""
    lateral = abs(u - 0.5) / 0.5
    vertical = abs(v - 0.5) / 0.5
    return float(min(1.0, max(lateral ** 1.6, vertical ** 2.2)))


def _tamanho_suave(W, H):
    escala = min(1.0, LADO_CAMADA_SUAVE / float(max(W, H)))
    return max(8, int(round(W * escala))), max(8, int(round(H * escala))), escala


def _aplicar_relevo(img, relevo):
    """Soma o relevo (em níveis de 0 a 255) nos três canais sem converter a imagem para float."""
    deslocado = np.clip(np.rint(relevo + 128.0), 0, 255).astype(np.uint8)
    canal = Image.fromarray(deslocado, "L")
    return ImageChops.add(img, Image.merge("RGB", (canal, canal, canal)), scale=1.0, offset=-128)


def _compor_cor(img, cor, mascara):
    return Image.composite(Image.new("RGB", img.size, cor), img, mascara)


# ------------------------------------------------------------
# Superfícies (relevo)
# ------------------------------------------------------------

def _concreto(W, H, ppm, rng, _padrao_cor):
    rel = (
        _ruido(W, H, ppm, 0.15, rng) * 2.4
        + _ruido(W, H, ppm, 0.8, rng) * 3.2
        + _ruido(W, H, ppm, 5.0, rng) * 4.6
        + _ruido(W, H, ppm, 30.0, rng) * 5.5
    )
    poros = _ruido(W, H, ppm, 0.3, rng) > 2.5
    rel[poros] -= 9.0
    return rel


def _lona(W, H, ppm, rng, _padrao_cor):
    periodo = 0.7 * ppm
    rel = _ruido(W, H, ppm, 0.12, rng) * 2.0 + _ruido(W, H, ppm, 12.0, rng) * 4.0
    nitidez_trama = float(np.clip((periodo - 2.0) / 2.0, 0.0, 1.0))
    if nitidez_trama > 0:
        sx = np.sin(np.arange(W, dtype=np.float32) * (2.0 * math.pi / periodo))
        sy = np.sin(np.arange(H, dtype=np.float32) * (2.0 * math.pi / periodo))
        rel += np.outer(sy, sx) * (3.4 * nitidez_trama)
    return rel


def _gesso(W, H, ppm, rng, padrao_cor):
    rel = (
        _ruido(W, H, ppm, 0.2, rng) * 1.6
        + _ruido(W, H, ppm, 2.0, rng) * 1.8
        + _ruido(W, H, ppm, 25.0, rng) * 4.2
    )
    # Veios de mármore: faixas diagonais longas, levemente onduladas, mais fortes em alguns trechos.
    sw, sh, escala = _tamanho_suave(W, H)
    ppm_s = ppm * escala
    turb = _ruido(sw, sh, ppm_s, 60.0, rng) * 0.7 + _ruido(sw, sh, ppm_s, 15.0, rng) * 0.18
    presenca = _suave(_ruido(sw, sh, ppm_s, 80.0, rng) * 0.6 + 0.55)
    x_mm = np.arange(sw, dtype=np.float32)[None, :] / ppm_s
    y_mm = np.arange(sh, dtype=np.float32)[:, None] / ppm_s
    veios = np.zeros((sh, sw), dtype=np.float32)
    for freq_x, freq_y, forca, largura in ((0.0045, 0.0105, 1.0, 0.035), (0.007, 0.004, 0.55, 0.025)):
        campo = np.sin((x_mm * freq_x + y_mm * freq_y) * 2.0 * math.pi + turb * 1.6)
        veios += np.exp(-np.abs(campo) / largura) * forca
    veios *= presenca
    veios_full = np.asarray(Image.fromarray(veios, "F").resize((W, H), Image.BICUBIC), dtype=np.float32)
    rel += veios_full * (16.0 if padrao_cor == "preto" else -22.0)
    return rel


def _papel(W, H, ppm, rng, _padrao_cor):
    return (
        _ruido(W, H, ppm, 0.12, rng) * 1.8
        + _ruido(W, H, ppm, 0.5, rng) * 1.6
        + _ruido(W, H, ppm, 1.2, rng, estica_x=6.0) * 2.4
        + _ruido(W, H, ppm, 10.0, rng) * 2.8
    )


def _veludo(W, H, ppm, rng, _padrao_cor):
    return (
        _ruido(W, H, ppm, 0.25, rng, estica_x=40.0) * 3.6
        + _ruido(W, H, ppm, 0.12, rng) * 1.4
        + _ruido(W, H, ppm, 45.0, rng) * 7.5
    )


# ------------------------------------------------------------
# Enfeites (máscaras de cor por cima)
# ------------------------------------------------------------

def _posicoes_na_borda(rng, quantidade):
    pontos = []
    tentativas = 0
    while len(pontos) < quantidade and tentativas < quantidade * 20:
        tentativas += 1
        u, v = float(rng.random()), float(rng.random())
        if rng.random() < 0.08 + 0.92 * _peso_borda(u, v):
            pontos.append((u, v))
    return pontos


def _po_dourado(W, H, ppm, rng):
    """Máscara L com partículas douradas finas, concentradas nas bordas."""
    area_cm2 = (W / ppm) * (H / ppm) / 100.0
    quantidade = int(min(7000, max(60, area_cm2 * 1.1)))
    mascara = Image.new("L", (W, H), 0)
    desenho = ImageDraw.Draw(mascara)
    sw, sh, escala = _tamanho_suave(W, H)
    brilho = Image.new("L", (sw, sh), 0)
    desenho_b = ImageDraw.Draw(brilho)
    for u, v in _posicoes_na_borda(rng, quantidade):
        x, y = u * W, v * H
        grande = rng.random() < 0.05
        raio = (rng.uniform(0.7, 1.3) if grande else rng.uniform(0.1, 0.45)) * ppm
        raio = max(0.6, raio)
        alfa = int(rng.uniform(150, 255))
        desenho.ellipse((x - raio, y - raio, x + raio, y + raio), fill=alfa)
        if grande:
            rb = raio * escala * 3.5
            xb, yb = x * escala, y * escala
            desenho_b.ellipse((xb - rb, yb - rb, xb + rb, yb + rb), fill=110)
    if max(sw, sh) > 8:
        brilho = brilho.filter(ImageFilter.GaussianBlur(max(1.0, 1.2 * ppm * escala)))
    brilho = brilho.resize((W, H), Image.BILINEAR)
    return ImageChops.lighter(mascara, brilho)


def _bokeh(W, H, ppm, rng, quantidade=(14, 24), raio_mm=(5.0, 22.0)):
    """Máscara L de círculos desfocados de luz nas bordas."""
    sw, sh, escala = _tamanho_suave(W, H)
    ppm_s = ppm * escala
    mascara = Image.new("L", (sw, sh), 0)
    desenho = ImageDraw.Draw(mascara)
    qtd = int(rng.integers(quantidade[0], quantidade[1] + 1))
    for u, v in _posicoes_na_borda(rng, qtd):
        r = rng.uniform(*raio_mm) * ppm_s
        x, y = u * sw, v * sh
        desenho.ellipse((x - r, y - r, x + r, y + r), fill=int(rng.uniform(45, 95)))
        desenho.ellipse((x - r, y - r, x + r, y + r), outline=int(rng.uniform(90, 140)), width=max(1, int(r * 0.06)))
    mascara = mascara.filter(ImageFilter.GaussianBlur(max(0.8, 0.9 * ppm_s)))
    return mascara.resize((W, H), Image.BILINEAR)


def _folha(desenho, cx, cy, comprimento, largura, angulo, alfa):
    pontos = []
    for i in range(25):
        t = i / 24.0
        meia = math.sin(math.pi * t) * largura / 2.0
        pontos.append((t * comprimento, meia))
    for i in range(24, -1, -1):
        t = i / 24.0
        pontos.append((t * comprimento, -math.sin(math.pi * t) * largura / 2.0))
    ca, sa = math.cos(angulo), math.sin(angulo)
    desenho.polygon([(cx + px * ca - py * sa, cy + px * sa + py * ca) for px, py in pontos], fill=alfa)


def _sombras_folhas(W, H, ppm, rng):
    """Máscara L de sombras suaves de galhos com folhas entrando pelos cantos e laterais."""
    sw, sh, escala = _tamanho_suave(W, H)
    ppm_s = ppm * escala
    mascara = Image.new("L", (sw, sh), 0)
    desenho = ImageDraw.Draw(mascara)
    origens = [(0.0, rng.uniform(0.0, 0.2)), (1.0, rng.uniform(0.0, 0.25)), (float(rng.choice([0.0, 1.0])), rng.uniform(0.75, 1.0))]
    for ox, oy in origens:
        x, y = ox * sw, oy * sh
        para_dentro = 1.0 if ox < 0.5 else -1.0
        direcao = math.atan2((0.45 if oy < 0.5 else -0.45), para_dentro) + rng.uniform(-0.25, 0.25)
        comp_passo = rng.uniform(11.0, 16.0) * ppm_s
        for passo in range(int(rng.integers(6, 10))):
            nx = x + math.cos(direcao) * comp_passo
            ny = y + math.sin(direcao) * comp_passo
            if not (-0.05 <= nx / sw <= 0.34 or 0.66 <= nx / sw <= 1.05):
                break
            desenho.line((x, y, nx, ny), fill=150, width=max(1, int(0.9 * ppm_s)))
            lado = 1 if passo % 2 else -1
            for repeticao in range(2):
                _folha(
                    desenho, nx, ny,
                    rng.uniform(30.0, 46.0) * ppm_s * (0.8 if repeticao else 1.0),
                    rng.uniform(11.0, 16.0) * ppm_s * (0.8 if repeticao else 1.0),
                    direcao + lado * rng.uniform(0.55, 0.95) * (1 if repeticao == 0 else -1), 215,
                )
            x, y = nx, ny
            direcao += rng.uniform(-0.18, 0.18)
    return mascara.filter(ImageFilter.GaussianBlur(max(0.8, 1.3 * ppm_s))).resize((W, H), Image.BILINEAR)


def _vinheta(img, padrao_cor):
    W, H = img.size
    sw, sh, _ = _tamanho_suave(W, H)
    u = np.linspace(-1.0, 1.0, sw, dtype=np.float32)[None, :]
    v = np.linspace(-1.0, 1.0, sh, dtype=np.float32)[:, None]
    dist = np.sqrt(u * u * 0.8 + v * v)
    perda = 0.14 if padrao_cor == "preto" else 0.06
    fator = 1.0 - perda * _suave((dist - 0.55) / 0.75)
    mascara = Image.fromarray(np.clip(fator * 255.0, 0, 255).astype(np.uint8), "L").resize((W, H), Image.BILINEAR)
    return ImageChops.multiply(img, Image.merge("RGB", (mascara, mascara, mascara)))


def _escala_mascara(mascara, fator):
    return mascara.point(lambda p: int(p * fator))


# ------------------------------------------------------------
# Receitas
# ------------------------------------------------------------

def _cor_luz(cores, padrao_cor):
    cores = [cor_rgb(c) for c in (cores or [])]
    acento = max(cores, key=lambda c: max(c) - min(c)) if cores else (255, 236, 200)
    if padrao_cor == "preto":
        return tuple(min(255, int(c * 0.55 + 255 * 0.45)) for c in acento)
    return (255, 255, 255)


SUPERFICIES = {
    "concreto": _concreto,
    "lona": _lona,
    "gesso": _gesso,
    "papel": _papel,
    "veludo": _veludo,
}

TEXTURAS = {
    "concreto": ("concreto", ()),
    "concreto_royal": ("concreto", ("vinheta",)),
    "lona": ("lona", ()),
    "lona_bokeh": ("lona", ("bokeh", "vinheta")),
    "gesso": ("gesso", ()),
    "gesso_marmore": ("gesso", ()),
    "papel": ("papel", ()),
    "papel_botanico": ("papel", ("folhas",)),
    "linho": ("papel", ()),
    "veludo": ("veludo", ("vinheta",)),
    "veludo_ouro": ("veludo", ("ouro", "bokeh_ouro", "vinheta")),
}


def tem_textura(nome):
    return str(nome or "").lower() in TEXTURAS


def aplicar_textura(img, nome, seed=0, altura_mm=None, padrao_cor="preto", cores=None, intensidade=1.0):
    """Devolve `img` (RGB) com a textura `nome` desenhada no tamanho da própria imagem."""
    nome = str(nome or "").lower()
    if nome not in TEXTURAS:
        return img
    img = img.convert("RGB")
    W, H = img.size
    ppm = H / float(altura_mm or ALTURA_PADRAO_MM)
    rng = np.random.default_rng(int(seed) & 0xFFFFFFFF)
    superficie, enfeites = TEXTURAS[nome]
    padrao_cor = "preto" if str(padrao_cor).lower() == "preto" else "branco"

    relevo = SUPERFICIES[superficie](W, H, ppm, rng, padrao_cor)
    relevo *= _calma_x(W)[None, :] * float(intensidade)
    saida = _aplicar_relevo(img, relevo)
    del relevo

    for enfeite in enfeites:
        if enfeite == "vinheta":
            saida = _vinheta(saida, padrao_cor)
        elif enfeite == "bokeh":
            saida = _compor_cor(saida, _cor_luz(cores, padrao_cor), _escala_mascara(_bokeh(W, H, ppm, rng), 0.55))
        elif enfeite == "bokeh_ouro":
            mascara = _bokeh(W, H, ppm, rng, quantidade=(10, 16), raio_mm=(3.0, 12.0))
            saida = _compor_cor(saida, (240, 200, 110), _escala_mascara(mascara, 0.5))
        elif enfeite == "ouro":
            saida = _compor_cor(saida, (236, 196, 98), _po_dourado(W, H, ppm, rng))
        elif enfeite == "folhas":
            sombra = (40, 36, 28) if padrao_cor == "branco" else (0, 0, 0)
            saida = _compor_cor(saida, sombra, _escala_mascara(_sombras_folhas(W, H, ppm, rng), 0.24))
    return saida
