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
            return guardada.copy()

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
    return res.copy()


def rosto_de(info, caminho):
    dados = (info or {}).get(normalizar_caminho(caminho)) if caminho else None
    return dados.get("rosto") if dados else None


_CACHE_FUNDO_GERADO = OrderedDict()
_CACHE_PALETA_FOTO = OrderedDict()


def eh_fundo_gerado(dados_ou_caminho):
    """Indica se um descritor ou caminho de fundo é um fundo gerado pelo programa (Degradê ou Abstrato)."""
    if isinstance(dados_ou_caminho, dict):
        if isinstance(dados_ou_caminho.get("gerado"), dict) and dados_ou_caminho["gerado"]:
            return True
        cam = str(dados_ou_caminho.get("caminho") or "")
        return cam.startswith("__gerado__:")
    if isinstance(dados_ou_caminho, str):
        return dados_ou_caminho.startswith("__gerado__:")
    return False


def _hsv_para_rgb_tupla(h_deg, s_frac, v_frac):
    import colorsys

    h = (float(h_deg) % 360.0) / 360.0
    s = max(0.0, min(1.0, float(s_frac)))
    v = max(0.0, min(1.0, float(v_frac)))
    r, g, b = colorsys.hsv_to_rgb(h, s, v)
    return (int(round(r * 255)), int(round(g * 255)), int(round(b * 255)))


def _rgb_para_hex(rgb):
    return "#%02x%02x%02x" % (
        max(0, min(255, int(rgb[0]))),
        max(0, min(255, int(rgb[1]))),
        max(0, min(255, int(rgb[2]))),
    )


def _rgb_para_hsv_deg(rgb):
    import colorsys

    r, g, b = [max(0.0, min(255.0, float(c))) / 255.0 for c in rgb[:3]]
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    return h * 360.0, s, v


def extrair_paleta_fotos(caminhos_fotos, info=None):
    """
    Analisa as fotos informadas (de uma página específica ou da lâmina inteira) e extrai
    tons suaves e elegantes para fundos degradês/abstratos, além da cor ideal de moldura.
    Devolve dict com:
      - 'tons_suaves': [hex1, hex2, hex3, hex4]
      - 'tons_medios': [hex1, hex2, hex3]
      - 'tons_escuros': [hex1, hex2]
      - 'cor_base': hex
      - 'cor_moldura_clara': hex
      - 'cor_moldura_escura': hex
      - 'matizes': [h1, h2, h3]
      - 'luminosidade': float (0..1)
    """
    import colorsys
    import numpy as np

    lista = [str(c) for c in (caminhos_fotos or []) if c]
    matizes_pesos = [0.0] * 18  # bins de 20 graus (0..360)
    somas_lum = []
    somas_sat = []

    for cam in lista[:10]:
        ch_norm = normalizar_caminho(cam)
        with _TRAVA_COMP_CACHE:
            cached = _CACHE_PALETA_FOTO.get(ch_norm)
        if cached is not None:
            h_bins, lum_m, sat_m = cached
            for idx_b, val_b in enumerate(h_bins):
                matizes_pesos[idx_b] += val_b
            somas_lum.append(lum_m)
            somas_sat.append(sat_m)
            continue

        try:
            mini = analise.miniatura(cam, 96).convert("RGB").resize((48, 48), Image.BILINEAR)
            arr = np.asarray(mini, dtype=np.float32) / 255.0
            # Prioriza o anel externo (fundo/ambiente da foto) com peso 1.8x e o centro com 0.7x
            pesos_esp = np.ones((48, 48), dtype=np.float32) * 1.8
            pesos_esp[12:36, 12:36] = 0.7

            r = arr[:, :, 0]
            g = arr[:, :, 1]
            b = arr[:, :, 2]
            cmax = np.maximum(np.maximum(r, g), b)
            cmin = np.minimum(np.minimum(r, g), b)
            delta = cmax - cmin

            lum_m = float(np.mean(0.299 * r + 0.587 * g + 0.114 * b))
            sat_map = np.where(cmax > 1e-4, delta / np.maximum(1e-4, cmax), 0.0)
            sat_m = float(np.mean(sat_map))

            h_bins = [0.0] * 18
            for y in range(0, 48, 2):
                for x in range(0, 48, 2):
                    rv, gv, bv = float(r[y, x]), float(g[y, x]), float(b[y, x])
                    hv, sv, vv = colorsys.rgb_to_hsv(rv, gv, bv)
                    if vv < 0.08 or vv > 0.96 or sv < 0.06:
                        continue
                    ang = (hv * 360.0) % 360.0
                    peso = float(pesos_esp[y, x]) * (0.4 + sv * 0.6)
                    # Suaviza peso de tons de pele muito saturados no centro para preferir cenário/roupa
                    if 12.0 <= ang <= 36.0 and pesos_esp[y, x] < 1.0 and 0.22 <= sv <= 0.65:
                        peso *= 0.45
                    idx_b = int(ang // 20.0) % 18
                    h_bins[idx_b] += peso

            with _TRAVA_COMP_CACHE:
                _CACHE_PALETA_FOTO[ch_norm] = (h_bins, lum_m, sat_m)
                while len(_CACHE_PALETA_FOTO) > 400:
                    _CACHE_PALETA_FOTO.popitem(last=False)

            for idx_b, val_b in enumerate(h_bins):
                matizes_pesos[idx_b] += val_b
            somas_lum.append(lum_m)
            somas_sat.append(sat_m)
        except Exception:
            # Fallback para assinatura de cenário no dict info se o arquivo for virtual/teste
            dados_f = (info or {}).get(ch_norm) if isinstance(info, dict) else None
            cen = (dados_f or {}).get("cenario") if isinstance(dados_f, dict) else None
            if isinstance(cen, (list, tuple)) and len(cen) >= 8:
                for i_c in range(min(8, len(cen))):
                    idx_b = int((i_c * 45.0) // 20.0) % 18
                    matizes_pesos[idx_b] += float(cen[i_c]) * 10.0

    # Escolhe os 3 matizes mais fortes com separação mínima de 35 graus para riqueza cromática
    ordenados = sorted(range(18), key=lambda i: matizes_pesos[i], reverse=True)
    escolhidos = []
    for idx_b in ordenados:
        if matizes_pesos[idx_b] <= 1e-5 and escolhidos:
            break
        ang = idx_b * 20.0 + 10.0
        if all(min(abs(ang - a), 360.0 - abs(ang - a)) >= 35.0 for a in escolhidos):
            escolhidos.append(ang)
        if len(escolhidos) >= 3:
            break

    if not escolhidos:
        escolhidos = [32.0, 210.0, 155.0]  # Marfim dourado, azul sereno e sálvia suave
    while len(escolhidos) < 3:
        novo_ang = (escolhidos[-1] + (42.0 if len(escolhidos) == 1 else 135.0)) % 360.0
        escolhidos.append(novo_ang)

    lum_media = sum(somas_lum) / float(len(somas_lum)) if somas_lum else 0.54
    sat_media = sum(somas_sat) / float(len(somas_sat)) if somas_sat else 0.28
    s_suave = max(0.12, min(0.28, sat_media * 0.62 + 0.08))
    s_medio = max(0.18, min(0.38, sat_media * 0.78 + 0.12))

    h1, h2, h3 = escolhidos[0], escolhidos[1], escolhidos[2]
    tons_suaves = [
        _rgb_para_hex(_hsv_para_rgb_tupla(h1, s_suave * 0.78, 0.94)),
        _rgb_para_hex(_hsv_para_rgb_tupla(h2, s_suave * 0.92, 0.88)),
        _rgb_para_hex(_hsv_para_rgb_tupla(h3, s_suave * 0.85, 0.91)),
        _rgb_para_hex(_hsv_para_rgb_tupla((h1 + 18.0) % 360.0, s_suave * 1.05, 0.83)),
    ]
    tons_medios = [
        _rgb_para_hex(_hsv_para_rgb_tupla(h1, s_medio, 0.78)),
        _rgb_para_hex(_hsv_para_rgb_tupla(h2, s_medio * 0.95, 0.68)),
        _rgb_para_hex(_hsv_para_rgb_tupla(h3, s_medio * 0.90, 0.73)),
    ]
    tons_escuros = [
        _rgb_para_hex(_hsv_para_rgb_tupla(h1, min(0.42, s_medio * 1.1), 0.26)),
        _rgb_para_hex(_hsv_para_rgb_tupla(h2, min(0.38, s_medio), 0.18)),
    ]
    cor_moldura_clara = _rgb_para_hex(_hsv_para_rgb_tupla(h1, 0.05, 0.98))
    cor_moldura_escura = _rgb_para_hex(_hsv_para_rgb_tupla(h1, 0.22, 0.34))

    return {
        "tons_suaves": tons_suaves,
        "tons_medios": tons_medios,
        "tons_escuros": tons_escuros,
        "cor_base": tons_suaves[0],
        "cor_moldura_clara": cor_moldura_clara,
        "cor_moldura_escura": cor_moldura_escura,
        "matizes": [round(h1, 1), round(h2, 1), round(h3, 1)],
        "luminosidade": round(lum_media, 3),
    }


def criar_dados_fundo_gerado(espec):
    """Cria um dicionário `fundo_imagem` válido a partir de uma especificação procedural (`espec`)."""
    import hashlib
    import json

    esp = dict(espec or {})
    assinatura = hashlib.sha1(json.dumps(esp, sort_keys=True).encode("utf-8")).hexdigest()[:12]
    caminho_virtual = "__gerado__:%s_%s_%s" % (
        esp.get("tipo", "degrade"),
        esp.get("subtipo", "suave"),
        assinatura,
    )
    dados = fundo_imagem_padrao(caminho_virtual)
    dados["gerado"] = esp
    dados["nome_exibicao"] = esp.get("titulo") or "Fundo Gerado pelas Fotos"
    return dados


def gerar_galeria_fundos_procedurais(paleta, estilo="degrade_e_abstrato", seed_base=None, quantidade=8):
    """
    Gera na hora uma galeria com `quantidade` variações de fundos baseados na paleta das fotos,
    combinando Degradês Suaves e Fundos Abstratos com aleatoriedade controlada (`seed_base`).
    A aleatoriedade afeta exclusivamente estes fundos gerados pelo programa.
    """
    import random
    import time

    pal = paleta or extrair_paleta_fotos([])
    if seed_base is None:
        seed_base = int((time.time() * 1000) % 999983)
    rng = random.Random(int(seed_base))

    suaves = list(pal.get("tons_suaves") or ["#f4efeA", "#e6dfd5", "#dde5e9", "#eadce2"])
    medios = list(pal.get("tons_medios") or ["#cbb8a0", "#a7b8c4", "#bfa5b2"])
    escuros = list(pal.get("tons_escuros") or ["#2d3138", "#1e2229"])
    h_base = (pal.get("matizes") or [32.0, 205.0, 150.0])[0]

    def _variar_cor(hex_cor, dh_max=16.0, ds_max=0.07, dv_max=0.06):
        h, s, v = _rgb_para_hsv_deg(cor_rgb(hex_cor))
        nh = (h + rng.uniform(-dh_max, dh_max)) % 360.0
        ns = max(0.06, min(0.45, s + rng.uniform(-ds_max, ds_max)))
        nv = max(0.14, min(0.97, v + rng.uniform(-dv_max, dv_max)))
        return _rgb_para_hex(_hsv_para_rgb_tupla(nh, ns, nv))

    templates_degrade = [
        ("degrade", "linear_diagonal", "Degradê Seda Diagonal", [suaves[0], suaves[1], medios[0]]),
        ("degrade", "radial_suave", "Degradê Luz Central", [suaves[0], suaves[2 % len(suaves)], medios[1 % len(medios)]]),
        ("degrade", "tres_tons_suave", "Degradê Três Tons", [suaves[1 % len(suaves)], suaves[0], suaves[3 % len(suaves)]]),
        ("degrade", "linear_horizontal", "Degradê Editorial Suave", [suaves[2 % len(suaves)], suaves[0], medios[2 % len(medios)]]),
        ("degrade", "noturno_elegante", "Degradê Sóbrio Elegante", [escuros[0], escuros[1 % len(escuros)], medios[0]]),
    ]
    templates_abstrato = [
        ("abstrato", "ondas_sedosas", "Abstrato Ondas de Seda", [suaves[0], suaves[1], medios[0], medios[1 % len(medios)]]),
        ("abstrato", "bokeh_luz", "Abstrato Bokeh de Luz", [suaves[1 % len(suaves)], suaves[2 % len(suaves)], medios[0], "#ffffff"]),
        ("abstrato", "aquarela_nuvens", "Abstrato Aquarela Suave", [suaves[0], suaves[3 % len(suaves)], medios[1 % len(medios)], suaves[2 % len(suaves)]]),
        ("abstrato", "prisma_geometrico", "Abstrato Feixes de Luz", [suaves[2 % len(suaves)], suaves[0], medios[2 % len(medios)], "#ffffff"]),
        ("abstrato", "ondas_noturnas", "Abstrato Sóbrio Iluminado", [escuros[0], escuros[1 % len(escuros)], medios[0], suaves[0]]),
    ]

    est = str(estilo or "degrade_e_abstrato").lower()
    sequencia = []
    if est == "degrade":
        sequencia = templates_degrade * 2
    elif est == "abstrato":
        sequencia = templates_abstrato * 2
    else:
        for d_item, a_item in zip(templates_degrade, templates_abstrato):
            sequencia.append(d_item)
            sequencia.append(a_item)

    galeria = []
    for idx in range(quantidade):
        tipo, subtipo, titulo_base, cores_base = sequencia[idx % len(sequencia)]
        seed_item = rng.randint(1000, 999999)
        cores_var = [_variar_cor(c) if idx >= 2 or rng.random() > 0.25 else c for c in cores_base]
        angulo = rng.choice([25, 45, 65, 115, 135, 155, 0, 90])
        espec = {
            "tipo": tipo,
            "subtipo": subtipo,
            "titulo": "%s #%d" % (titulo_base, idx + 1),
            "cores": cores_var,
            "angulo": angulo,
            "matiz_base": round(h_base, 1),
            "seed": seed_item,
        }
        galeria.append(criar_dados_fundo_gerado(espec))
    return galeria


def renderizar_fundo_gerado(espec, tamanho, rapido=False):
    """
    Sintetiza em alta qualidade uma imagem RGB/RGBA de tamanho (W, H) para um fundo
    gerado pelo programa (Degradê ou Abstrato) usando as cores extraídas das fotos.
    """
    import json
    import random
    import numpy as np

    W, H = max(1, int(tamanho[0])), max(1, int(tamanho[1]))
    esp = dict(espec or {})
    chave_cache = (W, H, bool(rapido), json.dumps(esp, sort_keys=True))
    with _TRAVA_COMP_CACHE:
        guardado = _CACHE_FUNDO_GERADO.get(chave_cache)
        if guardado is not None:
            _CACHE_FUNDO_GERADO.move_to_end(chave_cache)
            return guardado.copy()

    # Sintetiza numa malha intermediária otimizada e interpola com LANCZOS/BILINEAR para 60fps e zero serrilhado
    escala_malha = 220 if rapido else min(520, max(260, max(W, H) // 3))
    aspecto = W / float(max(1, H))
    if aspecto >= 1.0:
        gw = min(W, escala_malha)
        gh = max(16, int(round(gw / aspecto)))
    else:
        gh = min(H, escala_malha)
        gw = max(16, int(round(gh * aspecto)))

    cores_hex = esp.get("cores") or ["#f4efea", "#e4dcd3", "#cbd5dc"]
    cores = [np.array(cor_rgb(c), dtype=np.float32) for c in cores_hex]
    while len(cores) < 3:
        cores.append(cores[-1].copy())

    tipo = str(esp.get("tipo") or "degrade").lower()
    subtipo = str(esp.get("subtipo") or "linear_diagonal").lower()
    seed = int(esp.get("seed") or 42)
    angulo_rad = math.radians(float(esp.get("angulo") or 45.0))
    rng = random.Random(seed)

    yy, xx = np.mgrid[0:gh, 0:gw].astype(np.float32)
    u = xx / float(max(1, gw - 1))
    v = yy / float(max(1, gh - 1))

    # Base de degradê suave contínuo
    if subtipo == "radial_suave":
        cx = rng.uniform(0.35, 0.65)
        cy = rng.uniform(0.35, 0.65)
        dist = np.sqrt(((u - cx) * 1.15) ** 2 + ((v - cy) * 1.15) ** 2)
        t = np.clip(dist, 0.0, 1.0)
    elif subtipo == "linear_horizontal":
        t = u
    elif subtipo == "linear_vertical":
        t = v
    else:
        cos_a = math.cos(angulo_rad)
        sin_a = math.sin(angulo_rad)
        proj = (u - 0.5) * cos_a + (v - 0.5) * sin_a
        p_min, p_max = float(np.min(proj)), float(np.max(proj))
        t = (proj - p_min) / max(1e-5, p_max - p_min)

    # Curva S suave (smoothstep) com 3 paradas cromáticas
    t_suave = t * t * (3.0 - 2.0 * t)
    m1 = np.clip(t_suave * 2.0, 0.0, 1.0)[..., None]
    m2 = np.clip((t_suave - 0.5) * 2.0, 0.0, 1.0)[..., None]
    canvas = cores[0] * (1.0 - m1) + cores[1] * m1
    canvas = canvas * (1.0 - m2) + cores[2] * m2

    if tipo == "abstrato":
        c_extra = cores[3] if len(cores) > 3 else np.clip(cores[0] * 1.08, 0, 255)
        if "ondas" in subtipo:
            # Ondas sedosas orgânicas suaves
            for idx_w in range(3):
                freq_u = rng.uniform(1.4, 3.2)
                freq_v = rng.uniform(1.0, 2.4)
                fase = rng.uniform(0.0, math.pi * 2.0)
                amp = rng.uniform(0.10, 0.22)
                curva = np.sin(u * math.pi * freq_u + fase) * amp + np.cos(v * math.pi * freq_v - fase * 0.6) * (amp * 0.6)
                centro_v = 0.25 + idx_w * 0.25 + curva
                largura_faixa = rng.uniform(0.14, 0.26)
                onda = np.exp(-((v - centro_v) ** 2) / (2.0 * (largura_faixa ** 2)))[..., None]
                cor_onda = cores[(idx_w + 1) % len(cores)] if idx_w % 2 == 0 else c_extra
                op_onda = rng.uniform(0.22, 0.42)
                canvas = canvas * (1.0 - onda * op_onda) + cor_onda * (onda * op_onda)
        elif "bokeh" in subtipo:
            # Bokeh artístico de luz suave (círculos difusos translúcidos)
            qtd_orbes = rng.randint(7, 12)
            for _ in range(qtd_orbes):
                bx = rng.uniform(0.05, 0.95)
                by = rng.uniform(0.08, 0.92)
                raio = rng.uniform(0.08, 0.26)
                suavidade = rng.uniform(0.03, 0.09)
                dist_b = np.sqrt(((u - bx) * max(1.0, aspecto * 0.85)) ** 2 + (v - by) ** 2)
                anel = np.clip(1.0 - (dist_b - raio) / max(0.01, suavidade), 0.0, 1.0)
                anel = (anel * anel * (3.0 - 2.0 * anel))[..., None]
                cor_luz = np.clip(cores[rng.randint(0, len(cores) - 1)] * 0.55 + 255.0 * 0.45, 0, 255)
                op_b = rng.uniform(0.12, 0.28)
                canvas = canvas * (1.0 - anel * op_b) + cor_luz * (anel * op_b)
        elif "aquarela" in subtipo:
            # Manchas suaves estilo nuvem/aquarela orgânica
            for idx_m in range(5):
                mx = rng.uniform(0.1, 0.9)
                my = rng.uniform(0.1, 0.9)
                rx = rng.uniform(0.18, 0.42)
                ry = rng.uniform(0.18, 0.42)
                rot = rng.uniform(0.0, math.pi)
                du = (u - mx) * math.cos(rot) + (v - my) * math.sin(rot)
                dv = -(u - mx) * math.sin(rot) + (v - my) * math.cos(rot)
                ond = 0.04 * np.sin(u * 9.0 + idx_m) * np.cos(v * 9.0 - idx_m)
                mancha = np.exp(-((du / rx) ** 2 + (dv / ry) ** 2) + ond)[..., None]
                cor_m = cores[idx_m % len(cores)]
                op_m = rng.uniform(0.24, 0.45)
                canvas = canvas * (1.0 - mancha * op_m) + cor_m * (mancha * op_m)
        else:
            # Feixes de luz / prisma suave diagonal
            for idx_p in range(4):
                ang_p = angulo_rad + rng.uniform(-0.45, 0.45)
                coord = (u - 0.5) * math.cos(ang_p) + (v - 0.5) * math.sin(ang_p)
                pos_p = rng.uniform(-0.35, 0.35)
                larg_p = rng.uniform(0.08, 0.18)
                feixe = np.exp(-((coord - pos_p) ** 2) / (2.0 * (larg_p ** 2)))[..., None]
                cor_f = np.clip(cores[idx_p % len(cores)] * 0.70 + 255.0 * 0.30, 0, 255)
                op_f = rng.uniform(0.16, 0.32)
                canvas = canvas * (1.0 - feixe * op_f) + cor_f * (feixe * op_f)

    arr_u8 = np.clip(np.round(canvas), 0, 255).astype(np.uint8)
    img_malha = Image.fromarray(arr_u8, mode="RGB")
    if tipo == "abstrato":
        img_malha = img_malha.filter(ImageFilter.GaussianBlur(1.4))
    filtro_redim = Image.BILINEAR if rapido else Image.LANCZOS
    saida = img_malha.resize((W, H), filtro_redim)

    with _TRAVA_COMP_CACHE:
        _CACHE_FUNDO_GERADO[chave_cache] = saida
        while len(_CACHE_FUNDO_GERADO) > 64:
            _CACHE_FUNDO_GERADO.popitem(last=False)
    return saida.copy()


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
    eh_gerado = eh_fundo_gerado(dados)
    if not dados.get("caminho") and not eh_gerado:
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

    if eh_gerado:
        espec = dados.get("gerado") or {"tipo": "degrade", "subtipo": "linear_diagonal"}
        nw_g = max(W, int(round(W * escala_zoom)))
        nh_g = max(H, int(round(H * escala_zoom)))
        fonte = renderizar_fundo_gerado(espec, (nw_g, nh_g), rapido=rapido)
    elif rapido:
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

    if rapido and not eh_gerado:
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
            return guardada.copy()
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
    return res.copy()


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

    if fundo_img_lado and (fundo_img_lado.get("caminho") or eh_fundo_gerado(fundo_img_lado)):
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
        or eh_fundo_gerado(dono.get("fundo_imagem_esquerda"))
        or eh_fundo_gerado(dono.get("fundo_imagem_direita"))
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
