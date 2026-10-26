# -*- coding: utf-8 -*-
"""Monta a imagem de uma lâmina: fundo, fotos nos espaços e moldes PNG por cima."""

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps

import analise
from comum import cor_rgb, normalizar_caminho
from produtos import caixa_px
from recorte import foto_no_espaco

MODOS_FUNDO = ("preencher", "ajustar", "repetir")
LADRILHOS_NA_ALTURA = 2
AJUSTES_FUNDO_PADRAO = {"brilho": 0, "contraste": 0, "saturacao": 0, "matiz": 0, "tingir_cor": "#c08a4a", "tingir_forca": 0}


def rosto_de(info, caminho):
    dados = (info or {}).get(normalizar_caminho(caminho)) if caminho else None
    return dados.get("rosto") if dados else None


def fundo_imagem_padrao(caminho=None):
    return {"caminho": caminho, "modo": "preencher", "opacidade": 100, "desfoque": 0, "pb": False}


def ajustes_fundo(fundo_imagem):
    ajustes = dict(AJUSTES_FUNDO_PADRAO)
    ajustes.update((fundo_imagem or {}).get("ajustes") or {})
    return ajustes


def ajustar_cores(img, ajustes):
    """
    Brilho, contraste, saturação (-100 a 100), matiz (-180 a 180 graus) e tingir com uma cor
    (força 0 a 100) numa imagem RGB ou RGBA. Não mexe no arquivo: vale só para o desenho.
    """
    a = dict(AJUSTES_FUNDO_PADRAO)
    a.update(ajustes or {})
    if not any(float(a[k] or 0) for k in ("brilho", "contraste", "saturacao", "matiz", "tingir_forca")):
        return img
    alfa = img.getchannel("A") if img.mode == "RGBA" else None
    rgb = img.convert("RGB")
    matiz = float(a["matiz"] or 0)
    if abs(matiz) >= 0.5:
        h, s, v = rgb.convert("HSV").split()
        passo = int(round(matiz * 256 / 360.0))
        h = h.point(lambda x: (x + passo) % 256)
        rgb = Image.merge("HSV", (h, s, v)).convert("RGB")
    if float(a["saturacao"] or 0):
        rgb = ImageEnhance.Color(rgb).enhance(max(0.0, 1 + float(a["saturacao"]) / 100.0))
    if float(a["brilho"] or 0):
        rgb = ImageEnhance.Brightness(rgb).enhance(max(0.0, 1 + float(a["brilho"]) / 100.0))
    if float(a["contraste"] or 0):
        rgb = ImageEnhance.Contrast(rgb).enhance(max(0.0, 1 + float(a["contraste"]) / 100.0))
    forca = max(0.0, min(100.0, float(a["tingir_forca"] or 0))) / 100.0
    if forca > 0:
        tingida = ImageOps.colorize(ImageOps.grayscale(rgb), black=(0, 0, 0), mid=cor_rgb(a["tingir_cor"]),
                                    white=(255, 255, 255))
        rgb = Image.blend(rgb, tingida, forca)
    if alfa is not None:
        rgb = rgb.convert("RGBA")
        rgb.putalpha(alfa)
    return rgb


def fundo_posicionado(fundo_imagem, tamanho, rapido=False):
    """A imagem de fundo já no tamanho da lâmina e no modo escolhido, antes de ajustes, desfoque e opacidade."""
    dados = fundo_imagem_padrao()
    dados.update(fundo_imagem or {})
    if not dados.get("caminho"):
        return None
    W, H = int(tamanho[0]), int(tamanho[1])
    if rapido:
        fonte = analise.imagem_para(dados["caminho"], max(W, H))
    else:
        fonte = analise.abrir_imagem(dados["caminho"])[0]
    if dados.get("pb"):
        fonte = ImageOps.grayscale(fonte).convert("RGB")
    filtro = Image.BILINEAR if rapido else Image.LANCZOS
    camada = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    modo = dados.get("modo") if dados.get("modo") in MODOS_FUNDO else "preencher"
    if modo == "ajustar":
        escala = min(W / float(fonte.width), H / float(fonte.height))
        w, h = max(1, int(round(fonte.width * escala))), max(1, int(round(fonte.height * escala)))
        camada.paste(fonte.resize((w, h), filtro), ((W - w) // 2, (H - h) // 2))
    elif modo == "repetir":
        h = max(1, int(round(H / float(LADRILHOS_NA_ALTURA))))
        w = max(1, int(round(fonte.width * h / float(fonte.height))))
        ladrilho = fonte.resize((w, h), filtro)
        for y in range(0, H, h):
            for x in range(0, W, w):
                camada.paste(ladrilho, (x, y))
    else:
        camada.paste(ImageOps.fit(fonte, (W, H), filtro), (0, 0))
    return camada


def acabar_fundo(camada, fundo_imagem):
    """Ajustes de cor, desfoque (milésimos da largura) e opacidade sobre a camada posicionada."""
    dados = fundo_imagem_padrao()
    dados.update(fundo_imagem or {})
    entrada = camada
    camada = ajustar_cores(camada, ajustes_fundo(dados))
    raio = float(dados.get("desfoque") or 0) / 1000.0 * camada.width
    if raio >= 0.5:
        camada = camada.filter(ImageFilter.GaussianBlur(raio))
    opacidade = max(0, min(100, int(dados.get("opacidade", 100))))
    if opacidade < 100:
        if camada.mode != "RGBA":
            camada = camada.convert("RGBA")
        elif camada is entrada:
            camada = camada.copy()
        camada.putalpha(camada.getchannel("A").point(lambda v: v * opacidade // 100))
    return camada


def imagem_de_fundo(fundo_imagem, tamanho, rapido=False):
    """
    Camada RGBA do tamanho da lâmina com a imagem de fundo, ou None sem imagem.
    desfoque é em milésimos da largura, para a prévia e a exportação ficarem iguais.
    """
    camada = fundo_posicionado(fundo_imagem, tamanho, rapido)
    if camada is None:
        return None
    return acabar_fundo(camada, fundo_imagem)


def colar_sobreposicoes(base, sobreposicoes):
    """PNGs (molde) por cima das fotos, cada um na sua área da lâmina."""
    for sobre in sobreposicoes or []:
        try:
            with Image.open(sobre["png"]) as png:
                png = png.convert("RGBA")
        except OSError:
            continue
        x, y, w, h = caixa_px(sobre, base.size)
        if png.size != (w, h):
            png = png.resize((w, h), Image.LANCZOS)
        camada = Image.new("RGBA", base.size, (0, 0, 0, 0))
        camada.paste(png, (x, y))
        base.alpha_composite(camada)
    return base


def compor(tamanho, fundo, espacos, sobreposicoes, info, rapido=False, ao_espaco=None, fundo_imagem=None,
           px_por_mm=None):
    """
    Imagem RGB com as fotos nos espaços. ao_espaco(indice, espaco, erro) é
    chamado para cada espaço, com o erro quando a foto não abre. A imagem de
    fundo que não abre é ignorada. Com fundo None devolve RGBA transparente
    onde não há foto (a camada da frente, para a prévia rápida do fundo).
    """
    cor = cor_rgb(fundo)
    base = Image.new("RGBA", tamanho, (0, 0, 0, 0) if fundo is None else cor + (255,))
    try:
        camada = imagem_de_fundo(fundo_imagem, tamanho, rapido)
        if camada is not None:
            base.alpha_composite(camada)
    except (OSError, ValueError):
        pass
    for indice, espaco in enumerate(espacos):
        foto = espaco.get("foto")
        if not foto:
            continue
        x, y, w, h = caixa_px(espaco, tamanho)
        try:
            pronta = foto_no_espaco(foto, espaco.get("edicao"), rosto_de(info, foto), (w, h), cor, rapido)
            aplicar_moldura(pronta, (espaco.get("edicao") or {}).get("moldura"), px_por_mm)
            base.paste(pronta, (x, y))
            if ao_espaco:
                ao_espaco(indice, espaco, None)
        except (OSError, ValueError) as e:
            if ao_espaco:
                ao_espaco(indice, espaco, e)
    colar_sobreposicoes(base, sobreposicoes)
    return base if fundo is None else base.convert("RGB")


def aplicar_moldura(imagem, moldura, px_por_mm=None):
    """Desenha um traçado com espessura fixa em milímetros na prévia e na exportação."""
    if not moldura:
        return imagem
    from PIL import ImageDraw

    if moldura.get("espessura_mm") is not None and px_por_mm:
        largura = max(1, round(float(moldura["espessura_mm"]) * px_por_mm))
    else:
        espessura = max(0.1, min(12.0, float(moldura.get("espessura", 1.5))))
        largura = max(1, round(min(imagem.size) * espessura / 100.0))
    cor = cor_rgb(moldura.get("cor", "#ffffff"))
    estilo = moldura.get("estilo", "simples")
    desenho = ImageDraw.Draw(imagem)
    caixa = (0, 0, imagem.width - 1, imagem.height - 1)
    if estilo != "pontilhada":
        desenho.rectangle(caixa, outline=cor, width=largura)
    if estilo == "dupla":
        recuo = largura * 3
        if imagem.width > recuo * 2 and imagem.height > recuo * 2:
            desenho.rectangle((recuo, recuo, imagem.width - 1 - recuo, imagem.height - 1 - recuo),
                              outline=cor, width=largura)
    elif estilo == "pontilhada":
        passo = max(3, largura * 3)
        segmento = max(1, largura)
        for x in range(0, imagem.width, passo):
            desenho.line((x, 0, min(x + segmento, imagem.width - 1), 0), fill=cor, width=largura)
            desenho.line((x, imagem.height - 1, min(x + segmento, imagem.width - 1), imagem.height - 1),
                         fill=cor, width=largura)
        for y in range(0, imagem.height, passo):
            desenho.line((0, y, 0, min(y + segmento, imagem.height - 1)), fill=cor, width=largura)
            desenho.line((imagem.width - 1, y, imagem.width - 1, min(y + segmento, imagem.height - 1)),
                         fill=cor, width=largura)
    return imagem


def desenhar_guias(img, linhas_sangria, linhas_seguranca, linhas_dobra):
    """Guias só para a prévia. Cada lista tem retângulos (x0, y0, x1, y1) ou linhas verticais x."""
    desenho = ImageDraw.Draw(img)
    for caixa in linhas_sangria:
        desenho.rectangle(caixa, outline=(230, 60, 60), width=1)
    for caixa in linhas_seguranca:
        desenho.rectangle(caixa, outline=(60, 170, 230), width=1)
    for x in linhas_dobra:
        for y in range(0, img.height, 8):
            desenho.line([(x, y), (x, min(img.height, y + 4))], fill=(120, 120, 120), width=1)
    return img
