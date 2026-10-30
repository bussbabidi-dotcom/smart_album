# -*- coding: utf-8 -*-
"""Monta a imagem de uma lâmina: fundo (inteiro, por página ou misturado com fusão), fotos nos espaços e moldes PNG por cima."""

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageOps
from collections import OrderedDict
import math
import threading

import analise
from comum import cor_rgb, normalizar_caminho
from produtos import caixa_px
from recorte import foto_no_espaco

MODOS_FUNDO = ("preencher", "esticar", "ajustar", "repetir")
DIRECOES_DEGRADE = ("direita", "esquerda", "baixo", "cima", "radial", "bordas_h")
LADRILHOS_NA_ALTURA = 2
AJUSTES_FUNDO_PADRAO = {"brilho": 0, "contraste": 0, "saturacao": 0, "matiz": 0, "tingir_cor": "#c08a4a", "tingir_forca": 0}

_CACHE_MASCARAS_DEG = OrderedDict()
_CACHE_MASCARAS_FUSAO = OrderedDict()
_CACHE_FUNDO_POS = OrderedDict()
_TRAVA_COMP_CACHE = threading.Lock()


def degrade_padrao():
    return {
        "ativo": False,
        "direcao": "direita",
        "inicio": 30,
        "fim": 100,
        "opacidade_fim": 0,
    }


def tem_degrade_ativo(degrade, opacidade_geral=100):
    if int(round(float(opacidade_geral if opacidade_geral is not None else 100))) < 100:
        return True
    if not isinstance(degrade, dict):
        return False
    return bool(degrade.get("ativo", False))


def gerar_mascara_degrade(tamanho, degrade=None, opacidade_geral=100):
    """
    Gera uma máscara de canal alfa ('L', 0..255) do tamanho (W, H) aplicando
    degradê suave estilo máscara do Photoshop e/ou opacidade geral.
    Retorna None se opacidade for 100% e o degradê estiver desativado.
    """
    W, H = max(1, int(tamanho[0])), max(1, int(tamanho[1]))
    op_int = int(round(max(0.0, min(100.0, float(opacidade_geral if opacidade_geral is not None else 100.0)))))
    op_geral = op_int / 100.0
    d = degrade_padrao()
    if isinstance(degrade, dict):
        d.update(degrade)
    ativo = bool(d.get("ativo", False))
    if not ativo and op_int >= 100:
        return None
    if not ativo:
        return Image.new("L", (W, H), int(round(op_geral * 255.0)))

    direcao = d.get("direcao") if d.get("direcao") in DIRECOES_DEGRADE else "direita"
    ini_int = int(round(max(0.0, min(98.0, float(d.get("inicio", 30))))))
    fim_int = int(round(max(ini_int + 2, min(100.0, float(d.get("fim", 100))))))
    op_fim_int = int(round(max(0.0, min(100.0, float(d.get("opacidade_fim", 0))))))
    chave_m = (W, H, direcao, ini_int, fim_int, op_fim_int, op_int)
    with _TRAVA_COMP_CACHE:
        guardada = _CACHE_MASCARAS_DEG.get(chave_m)
        if guardada is not None:
            _CACHE_MASCARAS_DEG.move_to_end(chave_m)
            return guardada

    inicio = ini_int / 100.0
    fim = fim_int / 100.0
    op_fim = op_fim_int / 100.0
    span = max(0.001, fim - inicio)

    def valor_alfa(pos):
        if pos <= inicio:
            f = 1.0
        elif pos >= fim:
            f = op_fim
        else:
            t = (pos - inicio) / span
            suave = t * t * (3.0 - 2.0 * t)
            f = 1.0 - suave * (1.0 - op_fim)
        return int(round(max(0.0, min(1.0, f * op_geral)) * 255.0))

    if direcao in ("direita", "esquerda", "bordas_h"):
        linha = Image.new("L", (W, 1))
        px = linha.load()
        for x in range(W):
            u = x / float(max(1, W - 1))
            if direcao == "esquerda":
                u = 1.0 - u
            elif direcao == "bordas_h":
                u = abs(u - 0.5) * 2.0
            px[x, 0] = valor_alfa(u)
        res = linha.resize((W, H), Image.NEAREST)
    elif direcao in ("baixo", "cima"):
        coluna = Image.new("L", (1, H))
        px = coluna.load()
        for y in range(H):
            v = y / float(max(1, H - 1))
            if direcao == "cima":
                v = 1.0 - v
            px[0, y] = valor_alfa(v)
        res = coluna.resize((W, H), Image.NEAREST)
    else:
        # Radial (opaco no centro, transparente nas bordas)
        grade_w, grade_h = min(W, 128), min(H, 128)
        rad = Image.new("L", (grade_w, grade_h))
        px = rad.load()
        cx = (grade_w - 1) / 2.0 or 1.0
        cy = (grade_h - 1) / 2.0 or 1.0
        for y in range(grade_h):
            dy = (y - cy) / max(1.0, cy)
            for x in range(grade_w):
                dx = (x - cx) / max(1.0, cx)
                dist = min(1.0, math.sqrt(dx * dx + dy * dy))
                px[x, y] = valor_alfa(dist)
        res = rad.resize((W, H), Image.BILINEAR)

    with _TRAVA_COMP_CACHE:
        _CACHE_MASCARAS_DEG[chave_m] = res
        while len(_CACHE_MASCARAS_DEG) > 120:
            _CACHE_MASCARAS_DEG.popitem(last=False)
    return res


def rosto_de(info, caminho):
    dados = (info or {}).get(normalizar_caminho(caminho)) if caminho else None
    return dados.get("rosto") if dados else None


def fundo_imagem_padrao(caminho=None):
    return {
        "caminho": caminho,
        "modo": "preencher",
        "opacidade": 100,
        "desfoque": 0,
        "pb": False,
        "giro": 0,
        "espelhar_h": False,
        "espelhar_v": False,
        "escala": 1.0,
        "cx": 0.5,
        "cy": 0.5,
    }


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
    if not any(float(a.get(k) or 0) for k in ("brilho", "contraste", "saturacao", "matiz", "tingir_forca")):
        return img
    alfa = img.getchannel("A") if img.mode == "RGBA" else None
    rgb = img.convert("RGB")
    matiz = float(a.get("matiz") or 0)
    if abs(matiz) >= 0.5:
        h, s, v = rgb.convert("HSV").split()
        passo = int(round(matiz * 256 / 360.0))
        h = h.point(lambda x: (x + passo) % 256)
        rgb = Image.merge("HSV", (h, s, v)).convert("RGB")
    if float(a.get("saturacao") or 0):
        rgb = ImageEnhance.Color(rgb).enhance(max(0.0, 1 + float(a["saturacao"]) / 100.0))
    if float(a.get("brilho") or 0):
        rgb = ImageEnhance.Brightness(rgb).enhance(max(0.0, 1 + float(a["brilho"]) / 100.0))
    if float(a.get("contraste") or 0):
        rgb = ImageEnhance.Contrast(rgb).enhance(max(0.0, 1 + float(a["contraste"]) / 100.0))
    forca = max(0.0, min(100.0, float(a.get("tingir_forca") or 0))) / 100.0
    if forca > 0:
        cr, cg, cb = cor_rgb(a.get("tingir_cor") or "#c08a4a")
        cinza = ImageOps.grayscale(rgb)
        cor_escura = (int(round(cr * 0.12)), int(round(cg * 0.12)), int(round(cb * 0.12)))
        cor_clara = (
            int(round(cr + (255 - cr) * 0.18)),
            int(round(cg + (255 - cg) * 0.18)),
            int(round(cb + (255 - cb) * 0.18)),
        )
        tingida = ImageOps.colorize(cinza, black=cor_escura, mid=(cr, cg, cb), white=cor_clara)
        rgb = Image.blend(rgb, tingida, forca)
    if alfa is not None:
        rgb = rgb.convert("RGBA")
        rgb.putalpha(alfa)
    return rgb


def fundo_posicionado(fundo_imagem, tamanho, rapido=False):
    """A imagem de fundo já no tamanho pedido e no modo escolhido (cortar/preencher, distorcer/esticar, ajustar ou repetir), com espelhamento, giro 90° e zoom/enquadramento."""
    dados = fundo_imagem_padrao()
    dados.update(fundo_imagem or {})
    if not dados.get("caminho"):
        return None
    W, H = max(1, int(tamanho[0])), max(1, int(tamanho[1]))
    escala_zoom = max(1.0, min(3.0, float(dados.get("escala") or 1.0)))
    cx = max(0.0, min(1.0, float(dados.get("cx", 0.5) if dados.get("cx") is not None else 0.5)))
    cy = max(0.0, min(1.0, float(dados.get("cy", 0.5) if dados.get("cy") is not None else 0.5)))
    modo = dados.get("modo") if dados.get("modo") in MODOS_FUNDO else "preencher"
    giro = int(dados.get("giro") or 0) % 360
    esp_h = bool(dados.get("espelhar_h"))
    esp_v = bool(dados.get("espelhar_v"))
    pb = bool(dados.get("pb"))

    if rapido:
        fonte = analise.imagem_para(dados["caminho"], int(round(max(W, H) * max(1.0, min(2.0, escala_zoom)))))
        chave_fp = (
            normalizar_caminho(dados["caminho"]),
            id(fonte),
            W,
            H,
            modo,
            giro,
            esp_h,
            esp_v,
            pb,
            round(escala_zoom, 3),
            round(cx, 3),
            round(cy, 3),
        )
        with _TRAVA_COMP_CACHE:
            guardado = _CACHE_FUNDO_POS.get(chave_fp)
            if guardado is not None:
                _CACHE_FUNDO_POS.move_to_end(chave_fp)
                return guardado.copy()
    else:
        fonte = analise.abrir_imagem(dados["caminho"])[0]

    if esp_h:
        fonte = ImageOps.mirror(fonte)
    if esp_v:
        fonte = ImageOps.flip(fonte)
    if giro in (90, 180, 270):
        fonte = fonte.rotate(-giro, expand=True)
    if pb:
        fonte = ImageOps.grayscale(fonte).convert("RGB")
    filtro = Image.BILINEAR if rapido else Image.LANCZOS
    camada = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    if modo == "esticar":
        nw = max(W, int(round(W * escala_zoom)))
        nh = max(H, int(round(H * escala_zoom)))
        redim = fonte.resize((nw, nh), filtro)
        ox = int(round((nw - W) * cx))
        oy = int(round((nh - H) * cy))
        camada.paste(redim.crop((ox, oy, ox + W, oy + H)), (0, 0))
    elif modo == "ajustar":
        escala_fit = min(W / float(max(1, fonte.width)), H / float(max(1, fonte.height))) * escala_zoom
        w = max(1, int(round(fonte.width * escala_fit)))
        h = max(1, int(round(fonte.height * escala_fit)))
        redim = fonte.resize((w, h), filtro)
        px = int(round((W - w) * cx))
        py = int(round((H - h) * cy))
        camada.paste(redim, (px, py))
    elif modo == "repetir":
        h = max(1, int(round((H / float(LADRILHOS_NA_ALTURA)) * escala_zoom)))
        w = max(1, int(round(fonte.width * h / float(max(1, fonte.height)))))
        ladrilho = fonte.resize((w, h), filtro)
        for y in range(0, H, h):
            for x in range(0, W, w):
                camada.paste(ladrilho, (x, y))
    else:
        escala_cover = max(W / float(max(1, fonte.width)), H / float(max(1, fonte.height))) * escala_zoom
        nw = max(W, int(round(fonte.width * escala_cover)))
        nh = max(H, int(round(fonte.height * escala_cover)))
        redim = fonte.resize((nw, nh), filtro)
        ox = max(0, min(nw - W, int(round((nw - W) * cx))))
        oy = max(0, min(nh - H, int(round((nh - H) * cy))))
        camada.paste(redim.crop((ox, oy, ox + W, oy + H)), (0, 0))

    if rapido:
        with _TRAVA_COMP_CACHE:
            _CACHE_FUNDO_POS[chave_fp] = camada
            while len(_CACHE_FUNDO_POS) > 48:
                _CACHE_FUNDO_POS.popitem(last=False)
        return camada.copy()
    return camada


def raio_desfoque_fundo(desfoque, largura):
    """
    Curva suave e calibrada para o desfoque (0 a 50):
    Em 10 produz um desfoque leve e natural; chega ao máximo suavemente em 50.
    """
    d = max(0.0, min(50.0, float(desfoque or 0)))
    if d <= 0:
        return 0.0
    return ((d / 50.0) ** 1.25) * (float(largura) * 0.011)


def acabar_fundo(camada, fundo_imagem, rapido=False):
    """Ajustes de cor, desfoque suave calibrado, opacidade e máscara de degradê sobre a camada posicionada."""
    dados = fundo_imagem_padrao()
    dados.update(fundo_imagem or {})
    entrada = camada
    camada = ajustar_cores(camada, ajustes_fundo(dados))
    raio = raio_desfoque_fundo(dados.get("desfoque"), camada.width)
    if raio >= 0.4:
        if rapido and raio >= 2.2 and camada.width > 160 and camada.height > 80:
            # Na prévia interativa, aplica GaussianBlur em meia resolução para 4x mais velocidade (60fps)
            tam_orig = camada.size
            reduzida = camada.resize((max(1, tam_orig[0] // 2), max(1, tam_orig[1] // 2)), Image.BILINEAR)
            camada = reduzida.filter(ImageFilter.GaussianBlur(raio * 0.5)).resize(tam_orig, Image.BILINEAR)
        else:
            camada = camada.filter(ImageFilter.GaussianBlur(raio))
    opacidade = max(0, min(100, int(dados.get("opacidade", 100))))
    degrade = dados.get("degrade")
    mascara_deg = gerar_mascara_degrade(camada.size, degrade, opacidade)
    if mascara_deg is not None:
        if camada.mode != "RGBA":
            camada = camada.convert("RGBA")
        elif camada is entrada:
            camada = camada.copy()
        camada.putalpha(ImageChops.multiply(camada.getchannel("A"), mascara_deg))
    return camada


def imagem_de_fundo(fundo_imagem, tamanho, rapido=False):
    """
    Camada RGBA do tamanho dado com a imagem de fundo, ou None sem imagem.
    """
    camada = fundo_posicionado(fundo_imagem, tamanho, rapido)
    if camada is None:
        return None
    return acabar_fundo(camada, fundo_imagem, rapido=rapido)


def _mascara_fusao_horizontal(W, H, fusao):
    """
    Máscara L de tamanho (W, H) para transição entre Página Esquerda (0) e Página Direita (255).
    Quando fusao == 0, corte seco no meio (W // 2).
    Quando fusao > 0 (até 100), degradê suave centrado na dobra (W / 2).
    """
    fusao_int = int(round(max(0.0, min(100.0, float(fusao or 0)))))
    chave_f = (W, H, fusao_int)
    with _TRAVA_COMP_CACHE:
        guardada = _CACHE_MASCARAS_FUSAO.get(chave_f)
        if guardada is not None:
            _CACHE_MASCARAS_FUSAO.move_to_end(chave_f)
            return guardada
    meio = W / 2.0
    zona = (fusao_int / 100.0) * (W * 0.36)
    linha = Image.new("L", (W, 1))
    px = linha.load()
    if zona < 1.0:
        corte = W // 2
        for x in range(W):
            px[x, 0] = 0 if x < corte else 255
    else:
        x0 = meio - zona
        larg_z = max(1.0, 2.0 * zona)
        for x in range(W):
            if x <= x0:
                px[x, 0] = 0
            elif x >= meio + zona:
                px[x, 0] = 255
            else:
                t = (x - x0) / larg_z
                suave = t * t * (3.0 - 2.0 * t)
                px[x, 0] = int(round(suave * 255.0))
    res = linha.resize((W, H), Image.NEAREST)
    with _TRAVA_COMP_CACHE:
        _CACHE_MASCARAS_FUSAO[chave_f] = res
        while len(_CACHE_MASCARAS_FUSAO) > 40:
            _CACHE_MASCARAS_FUSAO.popitem(last=False)
    return res


def _renderizar_lado_em_lamina(cor_base, fundo_img_lamina, cor_lado, fundo_img_lado, tamanho, lado, fusao, rapido=False):
    """
    Monta uma imagem RGBA (W, H) representando o fundo de uma das páginas (esquerda ou direita),
    posicionada para que, com fusão suave no meio, a imagem da página cubra até a faixa de transição.
    """
    W, H = int(tamanho[0]), int(tamanho[1])
    cor_efetiva = cor_rgb(cor_lado if cor_lado else cor_base)
    base = Image.new("RGBA", (W, H), cor_efetiva + (255,))
    # Se o lado não tem imagem própria nem cor própria, usa o fundo da lâmina inteira como base
    if not fundo_img_lado and not cor_lado and fundo_img_lamina:
        try:
            cam_inteira = imagem_de_fundo(fundo_img_lamina, (W, H), rapido)
            if cam_inteira is not None:
                base.alpha_composite(cam_inteira)
        except (OSError, ValueError):
            pass
        return base

    if fundo_img_lado and fundo_img_lado.get("caminho"):
        zona = int(round(max(0.0, min(100.0, float(fusao or 0))) / 100.0 * (W * 0.36)))
        meio = W // 2
        if lado == "esquerda":
            x0, x1 = 0, min(W, meio + zona)
        else:
            x0, x1 = max(0, meio - zona), W
        larg_p = max(1, x1 - x0)
        try:
            cam_p = imagem_de_fundo(fundo_img_lado, (larg_p, H), rapido)
            if cam_p is not None:
                folha = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                folha.paste(cam_p, (x0, 0))
                base.alpha_composite(folha)
        except (OSError, ValueError):
            pass
    return base


def tem_fundo_por_pagina(dono):
    if not isinstance(dono, dict):
        return False
    return bool(
        dono.get("fundo_esquerda")
        or dono.get("fundo_direita")
        or (dono.get("fundo_imagem_esquerda") or {}).get("caminho")
        or (dono.get("fundo_imagem_direita") or {}).get("caminho")
    )


def compor_fundo_completo(tamanho, fundo, fundo_imagem=None, rapido=False, opcoes_pagina=None):
    """
    Gera a base RGBA do tamanho da lâmina combinando o fundo da lâmina inteira
    e eventuais fundos por página (esquerda / direita) com fusão suave no meio.
    """
    W, H = int(tamanho[0]), int(tamanho[1])
    cor = cor_rgb(fundo)
    base = Image.new("RGBA", (W, H), cor + (255,))
    try:
        camada = imagem_de_fundo(fundo_imagem, (W, H), rapido)
        if camada is not None:
            base.alpha_composite(camada)
    except (OSError, ValueError):
        pass

    if opcoes_pagina and tem_fundo_por_pagina(opcoes_pagina):
        fusao = float(opcoes_pagina.get("fundo_fusao") or 0)
        esq = _renderizar_lado_em_lamina(
            fundo, fundo_imagem,
            opcoes_pagina.get("fundo_esquerda"),
            opcoes_pagina.get("fundo_imagem_esquerda"),
            (W, H), "esquerda", fusao, rapido
        )
        dir_ = _renderizar_lado_em_lamina(
            fundo, fundo_imagem,
            opcoes_pagina.get("fundo_direita"),
            opcoes_pagina.get("fundo_imagem_direita"),
            (W, H), "direita", fusao, rapido
        )
        mascara = _mascara_fusao_horizontal(W, H, fusao)
        base = Image.composite(dir_, esq, mascara)
    return base


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
           px_por_mm=None, opcoes_pagina=None):
    """
    Imagem RGB com as fotos nos espaços (incluindo máscara de degradê de opacidade).
    ao_espaco(indice, espaco, erro) é chamado para cada espaço, com o erro quando a foto não abre.
    """
    cor = cor_rgb(fundo)
    if fundo is None:
        base = Image.new("RGBA", tamanho, (0, 0, 0, 0))
    else:
        base = compor_fundo_completo(tamanho, fundo, fundo_imagem, rapido, opcoes_pagina=opcoes_pagina)
    for indice, espaco in enumerate(espacos):
        foto = espaco.get("foto")
        if not foto:
            continue
        x, y, w, h = caixa_px(espaco, tamanho)
        ed = espaco.get("edicao") or {}
        ex = float(espaco.get("x", 0.0))
        ew = float(espaco.get("w", 0.0))
        dobra_x = (0.5 - ex) / ew if (ew > 0.05 and ex < 0.495 and ex + ew > 0.505) else None
        try:
            pronta = foto_no_espaco(foto, ed, rosto_de(info, foto), (w, h), cor, rapido, dobra_x=dobra_x)
            aplicar_moldura(pronta, ed.get("moldura"), px_por_mm)
            mascara = gerar_mascara_degrade((w, h), ed.get("degrade"), ed.get("opacidade", 100))
            if mascara is not None:
                base.paste(pronta, (x, y), mascara)
            else:
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
    """Guias estilo Adobe Photoshop: linhas ciano contínuas (0, 229, 255) para Safe Zone e Sangria."""
    desenho = ImageDraw.Draw(img)
    # Linha de Sangria (ciano discreto estilo guia secundária do Photoshop)
    for caixa in linhas_sangria:
        desenho.rectangle(caixa, outline=(0, 175, 215), width=1)
    # Linhas imaginárias da Safe Zone (ciano vivo contínuo estilo Photoshop #00E5FF)
    for caixa in linhas_seguranca:
        desenho.rectangle(caixa, outline=(0, 229, 255), width=1)
    # Linha da dobra central
    for x in linhas_dobra:
        desenho.line([(x, 0), (x, img.height)], fill=(0, 205, 240), width=1)
    return img
