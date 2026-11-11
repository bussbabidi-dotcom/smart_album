# -*- coding: utf-8 -*-
"""Monta a imagem de uma lâmina: fundo (inteiro, por página ou misturado com fusão), fotos nos espaços e moldes PNG por cima."""

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageOps
from collections import OrderedDict
import math
import threading

from pathlib import Path
import analise
import texturas_fundo
from comum import cor_rgb, normalizar_caminho, pasta_fundos_temporarios
from produtos import caixa_px
from recorte import _marca_arquivo, foto_no_espaco

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

# Quanto da cor das fotos entra nos fundos gerados pela paleta (sobre a base preta ou branca).
FATOR_COR_FUNDO = (0.55, 0.75)
TEXTURAS_ESCURAS = ("veludo", "concreto")
TEXTURAS_CLARAS = ("papel", "gesso")


def eh_fundo_gerado(dados_ou_caminho):
    """Indica se um descritor ou caminho de fundo é um fundo gerado pelo programa (Degradê ou Abstrato)."""
    if isinstance(dados_ou_caminho, dict):
        if isinstance(dados_ou_caminho.get("gerado"), dict) and dados_ou_caminho["gerado"]:
            return True
        cam = str(dados_ou_caminho.get("caminho") or "")
        if cam.startswith("__gerado__:"):
            return True
        try:
            p = Path(cam)
            if p.name.startswith("di_fundo_") and normalizar_caminho(p.parent) == normalizar_caminho(pasta_fundos_temporarios()):
                return True
        except Exception:
            pass
        return False
    if isinstance(dados_ou_caminho, str):
        if dados_ou_caminho.startswith("__gerado__:"):
            return True
        try:
            p = Path(dados_ou_caminho)
            if p.name.startswith("di_fundo_") and normalizar_caminho(p.parent) == normalizar_caminho(pasta_fundos_temporarios()):
                return True
        except Exception:
            pass
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


def _misturar_rgb_hex(rgb_base, rgb_cor, fator_cor):
    """Mistura `rgb_base` (Preto ou Branco) com `rgb_cor` (cor extraída da foto) na proporção `fator_cor` (ex.: 0.15 a 0.25)."""
    f = max(0.0, min(1.0, float(fator_cor)))
    r = int(round(rgb_base[0] * (1.0 - f) + rgb_cor[0] * f))
    g = int(round(rgb_base[1] * (1.0 - f) + rgb_cor[1] * f))
    b = int(round(rgb_base[2] * (1.0 - f) + rgb_cor[2] * f))
    return _rgb_para_hex((r, g, b))


def extrair_paleta_fotos(caminhos_fotos, info=None):
    """
    Analisa as fotos informadas (de uma página específica ou da lâmina inteira), detecta se há
    predominância de tonalidades escuras/pretas ou claras/brancas e extrai as cores reais das fotos
    ancoradas nos DOIS PADRÕES SÓBRIOS (com toque sutil e elegante de 15% a 25% das cores das fotos):
      1. Padrão Preto + Cores da Foto (`tons_preto_cores`)
      2. Padrão Branco + Cores da Foto (`tons_branco_cores`)
    Devolve dict com:
      - 'padrao_sugerido': 'preto' | 'branco'
      - 'proporcao_preto': float (0..1)
      - 'proporcao_branco': float (0..1)
      - 'tons_preto_cores': [hex0, hex1, hex2, hex3] (Preto + 8% a 25% das cores das fotos)
      - 'tons_branco_cores': [hex0, hex1, hex2, hex3] (Branco + 8% a 25% das cores das fotos)
      - 'cores_destaque_fotos': [hex_f1, hex_f2, hex_f3]
      - 'tons_suaves': alias p/ tons_branco_cores
      - 'tons_medios': cores de destaque suavizadas
      - 'tons_escuros': alias p/ tons_preto_cores
      - 'cor_base': hex base do padrão sugerido
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
    somas_preto = []
    somas_branco = []

    for cam in lista[:10]:
        ch_norm = normalizar_caminho(cam)
        with _TRAVA_COMP_CACHE:
            cached = _CACHE_PALETA_FOTO.get(ch_norm)
        if cached is not None and len(cached) == 5:
            h_bins, lum_m, sat_m, p_preto, p_branco = cached
            for idx_b, val_b in enumerate(h_bins):
                matizes_pesos[idx_b] += val_b
            somas_lum.append(lum_m)
            somas_sat.append(sat_m)
            somas_preto.append(p_preto)
            somas_branco.append(p_branco)
            continue

        try:
            mini = analise.miniatura(cam, 96).convert("RGB").resize((48, 48), Image.BILINEAR)
            arr = np.asarray(mini, dtype=np.float32) / 255.0
            # Prioriza o anel externo (fundo/ambiente/roupas da foto) com peso 1.8x e o centro com 0.7x
            pesos_esp = np.ones((48, 48), dtype=np.float32) * 1.8
            pesos_esp[12:36, 12:36] = 0.7
            soma_pesos_esp = float(np.sum(pesos_esp))

            r = arr[:, :, 0]
            g = arr[:, :, 1]
            b = arr[:, :, 2]
            cmax = np.maximum(np.maximum(r, g), b)
            cmin = np.minimum(np.minimum(r, g), b)
            delta = cmax - cmin

            lum_map = 0.299 * r + 0.587 * g + 0.114 * b
            lum_m = float(np.mean(lum_map))
            sat_map = np.where(cmax > 1e-4, delta / np.maximum(1e-4, cmax), 0.0)
            sat_m = float(np.mean(sat_map))

            # Detecta presença de tons pretos/escuros vs brancos/claros ponderada pelo ambiente da foto
            mascara_preto = ((cmax < 0.28) | ((lum_map < 0.34) & (sat_map < 0.32))).astype(np.float32)
            mascara_branco = ((lum_map > 0.78) | ((cmax > 0.74) & (sat_map < 0.24))).astype(np.float32)
            p_preto = float(np.sum(mascara_preto * pesos_esp)) / max(1.0, soma_pesos_esp)
            p_branco = float(np.sum(mascara_branco * pesos_esp)) / max(1.0, soma_pesos_esp)

            h_bins = [0.0] * 18
            for y in range(0, 48, 2):
                for x in range(0, 48, 2):
                    rv, gv, bv = float(r[y, x]), float(g[y, x]), float(b[y, x])
                    hv, sv, vv = colorsys.rgb_to_hsv(rv, gv, bv)
                    if vv < 0.07 or vv > 0.97 or sv < 0.05:
                        continue
                    ang = (hv * 360.0) % 360.0
                    peso = float(pesos_esp[y, x]) * (0.35 + sv * 0.65)
                    # Suaviza peso de tons de pele no centro para capturar melhor roupas, luzes e cenário
                    if 12.0 <= ang <= 36.0 and pesos_esp[y, x] < 1.0 and 0.22 <= sv <= 0.65:
                        peso *= 0.45
                    idx_b = int(ang // 20.0) % 18
                    h_bins[idx_b] += peso

            with _TRAVA_COMP_CACHE:
                _CACHE_PALETA_FOTO[ch_norm] = (h_bins, lum_m, sat_m, p_preto, p_branco)
                while len(_CACHE_PALETA_FOTO) > 400:
                    _CACHE_PALETA_FOTO.popitem(last=False)

            for idx_b, val_b in enumerate(h_bins):
                matizes_pesos[idx_b] += val_b
            somas_lum.append(lum_m)
            somas_sat.append(sat_m)
            somas_preto.append(p_preto)
            somas_branco.append(p_branco)
        except Exception:
            dados_f = (info or {}).get(ch_norm) if isinstance(info, dict) else None
            cen = (dados_f or {}).get("cenario") if isinstance(dados_f, dict) else None
            if isinstance(cen, (list, tuple)) and len(cen) >= 8:
                for i_c in range(min(8, len(cen))):
                    idx_b = int((i_c * 45.0) // 20.0) % 18
                    matizes_pesos[idx_b] += float(cen[i_c]) * 10.0

    # Escolhe os 3 matizes mais presentes nas fotos com separação mínima de 30 graus
    ordenados = sorted(range(18), key=lambda i: matizes_pesos[i], reverse=True)
    escolhidos = []
    for idx_b in ordenados:
        if matizes_pesos[idx_b] <= 1e-5 and escolhidos:
            break
        ang = idx_b * 20.0 + 10.0
        if all(min(abs(ang - a), 360.0 - abs(ang - a)) >= 30.0 for a in escolhidos):
            escolhidos.append(ang)
        if len(escolhidos) >= 3:
            break

    if not escolhidos:
        escolhidos = [32.0, 210.0, 155.0]
    while len(escolhidos) < 3:
        novo_ang = (escolhidos[-1] + (38.0 if len(escolhidos) == 1 else 120.0)) % 360.0
        escolhidos.append(novo_ang)

    lum_media = sum(somas_lum) / float(len(somas_lum)) if somas_lum else 0.46
    sat_media = sum(somas_sat) / float(len(somas_sat)) if somas_sat else 0.28
    prop_preto = sum(somas_preto) / float(len(somas_preto)) if somas_preto else 0.32
    prop_branco = sum(somas_branco) / float(len(somas_branco)) if somas_branco else 0.24

    # Define se as fotos puxam mais para o padrão Preto + Cores ou Branco + Cores
    if prop_preto >= prop_branco * 0.92 or lum_media < 0.50:
        padrao_sugerido = "preto"
    else:
        padrao_sugerido = "branco"

    h1, h2, h3 = escolhidos[0], escolhidos[1], escolhidos[2]
    # Cores vivas extraídas das fotos que servirão apenas como toque de 15% a 25% sobre o Preto ou Branco
    sat_ref = max(0.38, min(0.72, sat_media * 1.35 + 0.22))
    rgb_foto_1 = _hsv_para_rgb_tupla(h1, sat_ref, 0.78)
    rgb_foto_2 = _hsv_para_rgb_tupla(h2, sat_ref * 0.92, 0.74)
    rgb_foto_3 = _hsv_para_rgb_tupla(h3, sat_ref * 0.88, 0.76)

    cores_destaque_fotos = [
        _rgb_para_hex(rgb_foto_1),
        _rgb_para_hex(rgb_foto_2),
        _rgb_para_hex(rgb_foto_3),
    ]

    # PADRÃO 1: Base Preta (#0b0c0f .. #14161b) + 15% a 25% das cores das fotos
    base_preta_0 = (11, 12, 15)
    base_preta_1 = (15, 16, 20)
    base_preta_2 = (18, 20, 25)
    tons_preto_cores = [
        _misturar_rgb_hex(base_preta_0, rgb_foto_1, 0.08),  # Preto profundo ancorado (8%)
        _misturar_rgb_hex(base_preta_1, rgb_foto_1, 0.17),  # Preto + 17% Cor 1 da foto
        _misturar_rgb_hex(base_preta_2, rgb_foto_2, 0.21),  # Preto + 21% Cor 2 da foto
        _misturar_rgb_hex(base_preta_2, rgb_foto_3, 0.24),  # Preto + 24% Cor 3 da foto
    ]

    # PADRÃO 2: Base Branca (#fcfbf9 .. #f2f0ec) + 15% a 25% das cores das fotos
    base_branca_0 = (252, 251, 249)
    base_branca_1 = (248, 247, 244)
    base_branca_2 = (244, 242, 238)
    tons_branco_cores = [
        _misturar_rgb_hex(base_branca_0, rgb_foto_1, 0.08),  # Branco editorial ancorado (8%)
        _misturar_rgb_hex(base_branca_1, rgb_foto_1, 0.16),  # Branco + 16% Cor 1 da foto
        _misturar_rgb_hex(base_branca_2, rgb_foto_2, 0.20),  # Branco + 20% Cor 2 da foto
        _misturar_rgb_hex(base_branca_2, rgb_foto_3, 0.23),  # Branco + 23% Cor 3 da foto
    ]

    tons_medios = [
        _misturar_rgb_hex((128, 128, 128), rgb_foto_1, 0.45),
        _misturar_rgb_hex((128, 128, 128), rgb_foto_2, 0.42),
        _misturar_rgb_hex((128, 128, 128), rgb_foto_3, 0.40),
    ]
    cor_moldura_clara = _misturar_rgb_hex((252, 251, 249), rgb_foto_1, 0.08)
    cor_moldura_escura = _misturar_rgb_hex((24, 25, 30), rgb_foto_1, 0.18)
    cor_base = tons_preto_cores[0] if padrao_sugerido == "preto" else tons_branco_cores[0]

    return {
        "padrao_sugerido": padrao_sugerido,
        "proporcao_preto": round(prop_preto, 3),
        "proporcao_branco": round(prop_branco, 3),
        "tons_preto_cores": tons_preto_cores,
        "tons_branco_cores": tons_branco_cores,
        "cores_destaque_fotos": cores_destaque_fotos,
        "tons_suaves": tons_branco_cores,
        "tons_medios": tons_medios,
        "tons_escuros": tons_preto_cores,
        "cor_base": cor_base,
        "cor_moldura_clara": cor_moldura_clara,
        "cor_moldura_escura": cor_moldura_escura,
        "matizes": [round(h1, 1), round(h2, 1), round(h3, 1)],
        "luminosidade": round(lum_media, 3),
    }


def tamanho_previa_fundo(espec, lado=2048):
    """Tamanho do PNG de prévia na proporção real da área coberta (largura_mm x altura_mm da receita)."""
    try:
        largura_mm = float((espec or {}).get("largura_mm") or 0)
        altura_mm = float((espec or {}).get("altura_mm") or 0)
    except (TypeError, ValueError):
        largura_mm = altura_mm = 0.0
    if largura_mm <= 0 or altura_mm <= 0:
        return (lado, lado // 2)
    if largura_mm >= altura_mm:
        return (lado, max(16, int(round(lado * altura_mm / largura_mm))))
    return (max(16, int(round(lado * largura_mm / altura_mm))), lado)


def materializar_fundo_gerado_em_arquivo(espec, tamanho=None, caminho_destino=None):
    """
    Salva um fundo gerado pelo programa como arquivo `.png` real dentro da pasta temporária
    (`pasta_fundos_temporarios()`), permitindo que ele seja livremente alterado, ajustado,
    colorido, rotacionado ou editado externamente como qualquer fundo de imagem.
    """
    import hashlib
    import json

    esp = dict(espec or {})
    if tamanho is None:
        tamanho = tamanho_previa_fundo(esp)
    assinatura = hashlib.sha1(json.dumps(esp, sort_keys=True).encode("utf-8")).hexdigest()[:12]
    padrao_c = str(esp.get("padrao_cor") or ("preto" if "noturn" in str(esp.get("subtipo", "")) else "branco")).lower()
    tipo_c = str(esp.get("tipo") or "degrade").lower()
    sub_c = str(esp.get("subtipo") or "suave").lower()
    nome_arq = "di_fundo_%s_%s_%s_%s.png" % (padrao_c, tipo_c, sub_c, assinatura)

    if caminho_destino and not str(caminho_destino).startswith("__gerado__:"):
        destino = Path(caminho_destino)
    else:
        destino = pasta_fundos_temporarios() / nome_arq

    try:
        if destino.is_file() and destino.stat().st_size > 128:
            return str(destino)
    except OSError:
        pass

    try:
        destino.parent.mkdir(parents=True, exist_ok=True)
        img = renderizar_fundo_gerado(esp, tamanho, rapido=False)
        tmp = destino.with_name("%s.%s.tmp" % (destino.name, threading.get_ident()))
        try:
            img.save(str(tmp), format="PNG", compress_level=3)
            import os
            os.replace(str(tmp), str(destino))
        finally:
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
    except Exception:
        pass
    return str(destino)


def criar_dados_fundo_gerado(espec, materializar=True):
    """
    Cria um dicionário `fundo_imagem` válido a partir de uma especificação procedural (`espec`)
    e salva imediatamente o arquivo `.png` correspondente na pasta temporária para que
    possa ser alterado sem restrições.
    """
    import hashlib
    import json

    esp = dict(espec or {})
    if materializar:
        caminho_real = materializar_fundo_gerado_em_arquivo(esp)
    else:
        assinatura = hashlib.sha1(json.dumps(esp, sort_keys=True).encode("utf-8")).hexdigest()[:12]
        caminho_real = str(
            pasta_fundos_temporarios()
            / (
                "di_fundo_%s_%s_%s_%s.png"
                % (
                    esp.get("padrao_cor", "preto"),
                    esp.get("tipo", "degrade"),
                    esp.get("subtipo", "suave"),
                    assinatura,
                )
            )
        )
    dados = fundo_imagem_padrao(caminho_real)
    dados["gerado"] = esp
    dados["padrao_cor"] = esp.get("padrao_cor", "preto")
    dados["nome_exibicao"] = esp.get("titulo") or "Fundo Gerado pelas Fotos"
    return dados


def gerar_galeria_fundos_procedurais(paleta, estilo="degrade_e_abstrato", seed_base=None, quantidade=8, padrao_filtro="ambos",
                                     medidas_mm=None, materializar=True):
    """
    Gera uma galeria de fundos nos dois padrões, com cor forte das fotos (FATOR_COR_FUNDO) e textura:
      - Padrão Preto + Cores da Foto (veludo ou concreto)
      - Padrão Branco + Cores da Foto (papel ou gesso)
    `medidas_mm` = (largura, altura) da área coberta, guardada na receita para a escala da textura.
    Salva cada fundo gerado na pasta temporária (`pasta_fundos_temporarios()`) para a prévia.
    """
    import random
    import time

    pal = paleta or extrair_paleta_fotos([])
    if seed_base is None:
        seed_base = int((time.time() * 1000) % 999983)
    rng = random.Random(int(seed_base))

    pretos = list(pal.get("tons_preto_cores") or pal.get("tons_escuros") or ["#0c0d11", "#181520", "#151e24", "#211916"])
    while len(pretos) < 4:
        pretos.append(pretos[-1])
    brancos = list(pal.get("tons_branco_cores") or pal.get("tons_suaves") or ["#faf9f6", "#f0eae3", "#e8eef2", "#efe7eb"])
    while len(brancos) < 4:
        brancos.append(brancos[-1])

    destaques = list(pal.get("cores_destaque_fotos") or pal.get("tons_medios") or ["#c08a4a", "#5c8096", "#8c6d79"])
    while len(destaques) < 3:
        destaques.append(destaques[-1])

    h_base = (pal.get("matizes") or [32.0, 205.0, 150.0])[0]
    padrao_sugerido = str(pal.get("padrao_sugerido") or "preto").lower()

    def _variar_cor_ancorada(hex_cor_destaque, padrao_alvo, fator_min=FATOR_COR_FUNDO[0], fator_max=FATOR_COR_FUNDO[1]):
        """Varia levemente o matiz da cor da foto e mistura na base Preta ou Branca (FATOR_COR_FUNDO)."""
        h, s, v = _rgb_para_hsv_deg(cor_rgb(hex_cor_destaque))
        nh = (h + rng.uniform(-12.0, 12.0)) % 360.0
        ns = max(0.32, min(0.75, s + rng.uniform(-0.06, 0.06)))
        nv = max(0.65, min(0.88, v + rng.uniform(-0.05, 0.05)))
        rgb_cor = _hsv_para_rgb_tupla(nh, ns, nv)
        fator = rng.uniform(fator_min, fator_max)
        if padrao_alvo == "preto":
            base_rgb = (rng.randint(10, 16), rng.randint(11, 17), rng.randint(14, 21))
        else:
            base_rgb = (rng.randint(245, 253), rng.randint(244, 252), rng.randint(240, 250))
        return _misturar_rgb_hex(base_rgb, rgb_cor, fator)

    # Templates Padrão Preto + Cores das Fotos (15% a 25%)
    templates_preto_degrade = [
        ("preto", "degrade", "linear_diagonal", "Preto + Cores · Seda Diagonal", [pretos[0], pretos[1], pretos[2]]),
        ("preto", "degrade", "radial_suave", "Preto + Cores · Luz Sutil", [pretos[1], pretos[0], pretos[3]]),
        ("preto", "degrade", "tres_tons_suave", "Preto + Cores · Três Tons", [pretos[0], pretos[2], pretos[1]]),
        ("preto", "degrade", "linear_horizontal", "Preto + Cores · Editorial", [pretos[0], pretos[3], pretos[1]]),
    ]
    templates_preto_abstrato = [
        ("preto", "abstrato", "ondas_noturnas", "Preto + Cores · Ondas Sóbrias", [pretos[0], pretos[1], pretos[2], pretos[3]]),
        ("preto", "abstrato", "bokeh_luz", "Preto + Cores · Bokeh Escuro", [pretos[0], pretos[2], pretos[1], pretos[3]]),
        ("preto", "abstrato", "aquarela_nuvens", "Preto + Cores · Nuvens Noturnas", [pretos[0], pretos[1], pretos[3], pretos[2]]),
        ("preto", "abstrato", "prisma_geometrico", "Preto + Cores · Feixes Sóbrios", [pretos[0], pretos[2], pretos[3], pretos[1]]),
    ]

    # Templates Padrão Branco + Cores das Fotos (15% a 25%)
    templates_branco_degrade = [
        ("branco", "degrade", "linear_diagonal", "Branco + Cores · Seda Diagonal", [brancos[0], brancos[1], brancos[2]]),
        ("branco", "degrade", "radial_suave", "Branco + Cores · Luz Central", [brancos[0], brancos[2], brancos[3]]),
        ("branco", "degrade", "tres_tons_suave", "Branco + Cores · Três Tons", [brancos[1], brancos[0], brancos[2]]),
        ("branco", "degrade", "linear_horizontal", "Branco + Cores · Editorial", [brancos[0], brancos[3], brancos[1]]),
    ]
    templates_branco_abstrato = [
        ("branco", "abstrato", "ondas_sedosas", "Branco + Cores · Ondas de Seda", [brancos[0], brancos[1], brancos[2], brancos[3]]),
        ("branco", "abstrato", "bokeh_luz", "Branco + Cores · Bokeh Suave", [brancos[0], brancos[2], brancos[1], "#ffffff"]),
        ("branco", "abstrato", "aquarela_nuvens", "Branco + Cores · Aquarela Clara", [brancos[0], brancos[1], brancos[3], brancos[2]]),
        ("branco", "abstrato", "prisma_geometrico", "Branco + Cores · Feixes Claros", [brancos[0], brancos[2], brancos[3], "#ffffff"]),
    ]

    est = str(estilo or "degrade_e_abstrato").lower()
    if est == "degrade":
        lista_preto = templates_preto_degrade
        lista_branco = templates_branco_degrade
    elif est == "abstrato":
        lista_preto = templates_preto_abstrato
        lista_branco = templates_branco_abstrato
    else:
        lista_preto = []
        lista_branco = []
        for dp, ap in zip(templates_preto_degrade, templates_preto_abstrato):
            lista_preto.extend([dp, ap])
        for db, ab in zip(templates_branco_degrade, templates_branco_abstrato):
            lista_branco.extend([db, ab])

    filtro_p = str(padrao_filtro or "ambos").lower()
    sequencia = []
    if filtro_p == "preto":
        sequencia = lista_preto * 2
    elif filtro_p == "branco":
        sequencia = lista_branco * 2
    else:
        # Coloca o padrão detectado pelas fotos na frente e intercala Preto+Cores e Branco+Cores na galeria
        primaria = lista_preto if padrao_sugerido == "preto" else lista_branco
        secundaria = lista_branco if padrao_sugerido == "preto" else lista_preto
        for item_p, item_s in zip(primaria, secundaria):
            sequencia.append(item_p)
            sequencia.append(item_s)

    galeria = []
    for idx in range(quantidade):
        padrao_cor, tipo, subtipo, titulo_base, cores_base = sequencia[idx % len(sequencia)]
        seed_item = rng.randint(1000, 999999)
        # Âncora mais fechada (dá profundidade) e as demais cores das fotos fortes, variando entre os itens
        f_min, f_max = FATOR_COR_FUNDO
        ordem = [(idx + k) % 3 for k in range(3)]
        cores_var = [
            _variar_cor_ancorada(destaques[ordem[0]], padrao_cor, f_min * 0.55, f_min * 0.7),
            _variar_cor_ancorada(destaques[ordem[0]], padrao_cor, f_min, f_max),
            _variar_cor_ancorada(destaques[ordem[1]], padrao_cor, f_min, f_max),
            _variar_cor_ancorada(destaques[ordem[2]], padrao_cor, f_min, f_max),
        ][: len(cores_base)]
        angulo = rng.choice([25, 45, 65, 115, 135, 155, 0, 90])
        texturas = TEXTURAS_ESCURAS if padrao_cor == "preto" else TEXTURAS_CLARAS
        espec = {
            "padrao_cor": padrao_cor,
            "tipo": tipo,
            "subtipo": subtipo,
            "titulo": "%s #%d" % (titulo_base, idx + 1),
            "cores": cores_var,
            "angulo": angulo,
            "matiz_base": round(h_base, 1),
            "seed": seed_item,
            "textura": texturas[idx % len(texturas)],
        }
        if medidas_mm and medidas_mm[0] and medidas_mm[1]:
            espec["largura_mm"] = round(float(medidas_mm[0]), 1)
            espec["altura_mm"] = round(float(medidas_mm[1]), 1)
        galeria.append(criar_dados_fundo_gerado(espec, materializar=materializar))
    return galeria


def renderizar_fundo_gerado(espec, tamanho, rapido=False):
    """
    Sintetiza em alta qualidade uma imagem RGB de tamanho (W, H) para um fundo
    gerado pelo programa (Degradê ou Abstrato) respeitando rigorosamente os dois padrões:
      - 'preto': Base preta/grafite com 15% a 25% das cores das fotos (sem embranquecer!)
      - 'branco': Base branca/off-white com 15% a 25% das cores das fotos (sem escurecer!)
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

    escala_malha = 220 if rapido else min(520, max(260, max(W, H) // 3))
    aspecto = W / float(max(1, H))
    if aspecto >= 1.0:
        gw = min(W, escala_malha)
        gh = max(16, int(round(gw / aspecto)))
    else:
        gh = min(H, escala_malha)
        gw = max(16, int(round(gh * aspecto)))

    subtipo = str(esp.get("subtipo") or "linear_diagonal").lower()
    padrao_cor = str(esp.get("padrao_cor") or ("preto" if "noturn" in subtipo else "branco")).lower()
    cores_padrao = ["#0c0d11", "#191622", "#162026"] if padrao_cor == "preto" else ["#faf9f6", "#f0eae3", "#e8eef2"]
    cores_hex = esp.get("cores") or cores_padrao
    cores = [np.array(cor_rgb(c), dtype=np.float32) for c in cores_hex]
    while len(cores) < 3:
        cores.append(cores[-1].copy())

    tipo = str(esp.get("tipo") or "degrade").lower()
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

    t_suave = t * t * (3.0 - 2.0 * t)
    m1 = np.clip(t_suave * 2.0, 0.0, 1.0)[..., None]
    m2 = np.clip((t_suave - 0.5) * 2.0, 0.0, 1.0)[..., None]
    canvas = cores[0] * (1.0 - m1) + cores[1] * m1
    canvas = canvas * (1.0 - m2) + cores[2] * m2

    if tipo == "abstrato":
        c_extra = cores[3] if len(cores) > 3 else cores[1]
        if "ondas" in subtipo:
            for idx_w in range(3):
                freq_u = rng.uniform(1.4, 3.0)
                freq_v = rng.uniform(1.0, 2.2)
                fase = rng.uniform(0.0, math.pi * 2.0)
                amp = rng.uniform(0.10, 0.20)
                curva = np.sin(u * math.pi * freq_u + fase) * amp + np.cos(v * math.pi * freq_v - fase * 0.6) * (amp * 0.6)
                centro_v = 0.25 + idx_w * 0.25 + curva
                largura_faixa = rng.uniform(0.15, 0.26)
                onda = np.exp(-((v - centro_v) ** 2) / (2.0 * (largura_faixa ** 2)))[..., None]
                cor_onda = cores[(idx_w + 1) % len(cores)] if idx_w % 2 == 0 else c_extra
                op_onda = rng.uniform(0.22, 0.38)
                canvas = canvas * (1.0 - onda * op_onda) + cor_onda * (onda * op_onda)
        elif "bokeh" in subtipo:
            qtd_orbes = rng.randint(6, 11)
            for _ in range(qtd_orbes):
                bx = rng.uniform(0.06, 0.94)
                by = rng.uniform(0.08, 0.92)
                raio = rng.uniform(0.09, 0.25)
                suavidade = rng.uniform(0.04, 0.09)
                dist_b = np.sqrt(((u - bx) * max(1.0, aspecto * 0.85)) ** 2 + (v - by) ** 2)
                anel = np.clip(1.0 - (dist_b - raio) / max(0.01, suavidade), 0.0, 1.0)
                anel = (anel * anel * (3.0 - 2.0 * anel))[..., None]
                cor_ref = cores[rng.randint(1, len(cores) - 1)]
                if padrao_cor == "preto":
                    # No padrão preto, o bokeh é um reflexo sutil da cor da foto sem embranquecer o fundo
                    cor_luz = np.clip(cor_ref * 1.35 + 14.0, 0, 78)
                    op_b = rng.uniform(0.16, 0.30)
                else:
                    cor_luz = np.clip(cor_ref * 0.65 + 255.0 * 0.35, 215, 255)
                    op_b = rng.uniform(0.18, 0.32)
                canvas = canvas * (1.0 - anel * op_b) + cor_luz * (anel * op_b)
        elif "aquarela" in subtipo:
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
                cor_m = cores[(idx_m % (len(cores) - 1)) + 1]
                op_m = rng.uniform(0.24, 0.42)
                canvas = canvas * (1.0 - mancha * op_m) + cor_m * (mancha * op_m)
        else:
            for idx_p in range(4):
                ang_p = angulo_rad + rng.uniform(-0.42, 0.42)
                coord = (u - 0.5) * math.cos(ang_p) + (v - 0.5) * math.sin(ang_p)
                pos_p = rng.uniform(-0.35, 0.35)
                larg_p = rng.uniform(0.09, 0.18)
                feixe = np.exp(-((coord - pos_p) ** 2) / (2.0 * (larg_p ** 2)))[..., None]
                cor_ref = cores[(idx_p % (len(cores) - 1)) + 1]
                if padrao_cor == "preto":
                    cor_f = np.clip(cor_ref * 1.28 + 10.0, 0, 72)
                    op_f = rng.uniform(0.18, 0.32)
                else:
                    cor_f = np.clip(cor_ref * 0.72 + 255.0 * 0.28, 218, 255)
                    op_f = rng.uniform(0.18, 0.32)
                canvas = canvas * (1.0 - feixe * op_f) + cor_f * (feixe * op_f)

    # Texturas detalhadas de superfície por capítulo (cimento queimado, lona de estúdio, gesso/mármore, papel linho/botânico, poeira de ouro)
    textura_cap = str(esp.get("textura") or "").lower()
    textura_final = texturas_fundo.tem_textura(textura_cap)
    rng_np = np.random.RandomState(seed % 2147483647)
    ruido_fino = rng_np.normal(0.0, 1.0, (gh, gw, 1)).astype(np.float32)

    if textura_final:
        pass  # desenhada depois, já no tamanho final (texturas_fundo)
    elif textura_cap == "concreto_royal" or "colacao_royal" in str(esp.get("id") or ""):
        # Microtextura de cimento queimado + trama sutil + vinheta de estúdio com centro 2:1 limpo
        ond_c = (np.sin(u * 38.0 + v * 19.0) * np.cos(v * 29.0 - u * 14.0))[..., None] * 4.5
        trama = (((xx.astype(np.int32) % 6 == 0) | (yy.astype(np.int32) % 6 == 0)).astype(np.float32))[..., None] * 2.2
        dist_centro = np.sqrt(((u - 0.5) * 1.15) ** 2 + ((v - 0.5) * 1.25) ** 2)[..., None]
        vinheta = np.clip((dist_centro - 0.35) * 0.55, 0.0, 0.38)
        canvas = canvas * (1.0 - vinheta) + ond_c + trama + ruido_fino * 3.2

    elif textura_cap == "lona_bokeh" or "estudio_bokeh" in str(esp.get("id") or ""):
        # Trama cruzada de lona de estúdio + granulação + luzes bokeh periféricas
        trama_x = (np.sin(xx * 1.55) * np.cos(yy * 1.55))[..., None] * 3.8
        canvas = canvas + trama_x + ruido_fino * 3.6

    elif textura_cap == "gesso_marmore" or "jaleco" in str(esp.get("id") or "") or "perola" in str(esp.get("id") or ""):
        # Relevo mineral de gesso/estuque + veios orgânicos de mármore Carrara + grãos minerais
        veio1 = np.exp(-((np.sin(u * 5.2 + v * 3.4 + 0.7) * 0.45 + (v - 0.38)) ** 2) / 0.0018)[..., None]
        veio2 = np.exp(-((np.cos(u * 4.1 - v * 4.8 + 1.3) * 0.38 + (v - 0.66)) ** 2) / 0.0024)[..., None]
        relevo_gesso = (np.sin(u * 27.0) * np.cos(v * 23.0))[..., None] * 3.0
        canvas = canvas - veio1 * 11.0 - veio2 * 8.5 + relevo_gesso + ruido_fino * 2.6

    elif textura_cap == "papel_botanico" or "botanica" in str(esp.get("id") or "") or "linho" in str(esp.get("id") or ""):
        # Trama de papel algodão/linho + projeções suaves de sombras de folhas nas bordas
        fibra_h = (np.sin(yy * 2.1) * 2.4 + np.cos(xx * 1.8) * 2.0)[..., None]
        # Sombra projetada de folhagens nos cantos superiores/laterais (deixando o centro livre)
        folha_esq = (
            np.exp(-(((u - 0.14) / 0.18) ** 2 + ((v - 0.22) / 0.28) ** 2))
            * (0.5 + 0.5 * np.sin((u * 28.0 - v * 18.0)))
        )[..., None]
        folha_dir = (
            np.exp(-(((u - 0.86) / 0.18) ** 2 + ((v - 0.24) / 0.28) ** 2))
            * (0.5 + 0.5 * np.sin((u * 26.0 + v * 19.0)))
        )[..., None]
        sombra_folha = np.clip((folha_esq + folha_dir) * 18.0, 0.0, 22.0)
        canvas = canvas + fibra_h - sombra_folha + ruido_fino * 2.8

    elif textura_cap == "veludo_ouro" or "baile" in str(esp.get("id") or "") or "culto" in str(esp.get("id") or ""):
        # Textura de veludo escovado escuro + explosão de poeira de ouro nas bordas
        escovado = (np.sin(yy * 1.9 + u * 6.0) * 2.6)[..., None]
        canvas = canvas + escovado + ruido_fino * 3.0
        cor_ouro = np.array([250.0, 208.0, 92.0], dtype=np.float32)
        for _ in range(56):
            px_g = rng.randint(0, gw - 1)
            py_g = rng.randint(0, gh - 1)
            # Concentra mais poeira de ouro nas bordas para manter o centro limpo
            if 0.28 < (px_g / float(max(1, gw))) < 0.72 and 0.22 < (py_g / float(max(1, gh))) < 0.78:
                if rng.random() < 0.65:
                    continue
            for dy_g in (-1, 0, 1):
                for dx_g in (-1, 0, 1):
                    ny_g, nx_g = py_g + dy_g, px_g + dx_g
                    if 0 <= ny_g < gh and 0 <= nx_g < gw:
                        peso_g = 0.75 if (dx_g == 0 and dy_g == 0) else 0.28
                        canvas[ny_g, nx_g] = canvas[ny_g, nx_g] * (1.0 - peso_g) + cor_ouro * peso_g
    else:
        # Microtextura editorial padrão para não ficar chapado
        canvas = canvas + ruido_fino * (2.4 if padrao_cor == "branco" else 2.9)

    arr_u8 = np.clip(np.round(canvas), 0, 255).astype(np.uint8)
    img_malha = Image.fromarray(arr_u8, mode="RGB")
    if tipo == "abstrato" and not textura_cap:
        img_malha = img_malha.filter(ImageFilter.GaussianBlur(1.1))
    filtro_redim = Image.BILINEAR if rapido else Image.LANCZOS
    saida = img_malha.resize((W, H), filtro_redim)
    if textura_final:
        saida = texturas_fundo.aplicar_textura(
            saida, textura_cap, seed=seed, altura_mm=esp.get("altura_mm"),
            padrao_cor=padrao_cor, cores=cores_hex,
        )

    # Fundos no tamanho de exportação ocupam dezenas de MB cada; só a prévia fica no cache.
    if W * H <= 4_000_000:
        with _TRAVA_COMP_CACHE:
            _CACHE_FUNDO_GERADO[chave_cache] = saida
            while len(_CACHE_FUNDO_GERADO) > 8:
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

    # Na exportação o fundo gerado sai direto da receita no tamanho final; o PNG é só para a prévia.
    # Se o arquivo da prévia ainda não existe em disco, materializa agora.
    if eh_gerado and rapido:
        cam_atual = str(dados.get("caminho") or "")
        if not cam_atual or cam_atual.startswith("__gerado__:") or not Path(cam_atual).is_file():
            espec = dados.get("gerado") or {"tipo": "degrade", "subtipo": "linear_diagonal"}
            cam_real = materializar_fundo_gerado_em_arquivo(espec, caminho_destino=cam_atual if not cam_atual.startswith("__gerado__:") else None)
            dados["caminho"] = cam_real
            if isinstance(fundo_imagem, dict):
                fundo_imagem["caminho"] = cam_real

    caminho_arq = str(dados.get("caminho") or "")
    tem_arquivo_real = bool(caminho_arq and not caminho_arq.startswith("__gerado__:") and Path(caminho_arq).is_file())

    tem_receita = isinstance(dados.get("gerado"), dict) and bool(dados.get("gerado"))
    if eh_gerado and ((not rapido and tem_receita) or not tem_arquivo_real):
        espec = dados.get("gerado") or {"tipo": "degrade", "subtipo": "linear_diagonal"}
        nw_g = max(W, int(round(W * escala_zoom)))
        nh_g = max(H, int(round(H * escala_zoom)))
        fonte = renderizar_fundo_gerado(espec, (nw_g, nh_g), rapido=rapido)
    elif rapido:
        fonte = analise.imagem_para(caminho_arq, int(round(max(W, H) * max(1.0, min(2.0, escala_zoom)))))
        chave_fp = (
            normalizar_caminho(caminho_arq),
            _marca_arquivo(caminho_arq),
            fonte.size,
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
        fonte = analise.abrir_imagem(caminho_arq)[0]

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

    if rapido and (tem_arquivo_real or not eh_gerado):
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
            sombra_cfg = ed.get("sombra")
            if sombra_cfg and sombra_cfg.get("ativa", True) and not tem_degrade_ativo(ed.get("degrade")):
                eh_sangrada_100 = (
                    float(espaco.get("x", 0.0)) <= 0.008
                    and float(espaco.get("y", 0.0)) <= 0.008
                    and float(espaco.get("w", 0.0)) >= 0.984
                    and float(espaco.get("h", 0.0)) >= 0.984
                )
                if not (sombra_cfg.get("sincronizar_moldura", True) and eh_sangrada_100):
                    aplicar_sombra_projetada(base, (x, y, w, h), sombra_cfg, ed.get("moldura"), px_por_mm=px_por_mm, rapido=rapido)
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


ESTILOS_MOLDURA = (
    ("simples", "Filete Fino Clássico"),
    ("dupla", "Vitral Luminoso (Glass Glow)"),
    ("pontilhada", "Costura Editorial (Pontilhada)"),
    ("royal", "Royal Imperial (Cantoneiras)"),
    ("degrade", "Degradê Linear Bicolor"),
    ("degrade_real", "Ouro Imperial (Reflexo 5 Paradas)"),
    ("cantoneira", "Cantoneira Clássica em L"),
    ("chanfrada", "Chanfrada 3D (Bisel)"),
    ("passepartout", "Galeria Slim (Passe-partout Fino)"),
    ("tripla", "Diamante Imperial (Cantos 45°)"),
    ("cruzada", "Arquitetônica (Cruzada nos Cantos)"),
    ("halo", "Halo Suave Interno"),
)

NOMES_ESTILOS_MOLDURA = dict(ESTILOS_MOLDURA)

PRESETS_SOMBRA_PROJETADA = (
    ("suave", "Suave Editorial", {"opacidade": 38, "desfoque_mm": 1.8, "offset_x_mm": 0.8, "offset_y_mm": 1.1, "cor": "#000000"}),
    ("flutuante", "Flutuante 3D", {"opacidade": 50, "desfoque_mm": 2.6, "offset_x_mm": 1.4, "offset_y_mm": 1.8, "cor": "#000000"}),
    ("profunda", "Profunda Estúdio", {"opacidade": 68, "desfoque_mm": 3.8, "offset_x_mm": 2.0, "offset_y_mm": 2.6, "cor": "#000000"}),
    ("deslocada", "Deslocada Clássica", {"opacidade": 55, "desfoque_mm": 1.0, "offset_x_mm": 2.2, "offset_y_mm": 2.2, "cor": "#111827"}),
    ("halo_escuro", "Halo Difuso (Central)", {"opacidade": 52, "desfoque_mm": 3.2, "offset_x_mm": 0.0, "offset_y_mm": 0.0, "cor": "#000000"}),
)


def sombra_padrao():
    return {
        "ativa": True,
        "preset": "flutuante",
        "cor": "#000000",
        "opacidade": 50,
        "desfoque_mm": 2.6,
        "offset_x_mm": 1.4,
        "offset_y_mm": 1.8,
        "sincronizar_moldura": True,
    }


def aplicar_sombra_projetada(base, caixa_foto, sombra, moldura=None, px_por_mm=None, rapido=False):
    """
    Desenha sombra projetada realista (Gaussian blur RGBA) na lâmina `base`
    logo abaixo da foto `(x, y, w, h)`, identificando e acompanhando as molduras.
    """
    if not sombra or not sombra.get("ativa", True):
        return base
    x, y, w, h = caixa_foto
    if w <= 2 or h <= 2:
        return base
    opacidade = max(0, min(100, int(round(float(sombra.get("opacidade", 50))))))
    if opacidade <= 0:
        return base

    escala_mm = float(px_por_mm) if (px_por_mm and px_por_mm > 0) else max(1.0, min(base.size) / 300.0)
    desfoque_mm = max(0.0, min(12.0, float(sombra.get("desfoque_mm", 2.6))))
    ox_mm = max(-10.0, min(10.0, float(sombra.get("offset_x_mm", 1.4))))
    oy_mm = max(-10.0, min(10.0, float(sombra.get("offset_y_mm", 1.8))))

    raio_px = max(1.0, desfoque_mm * escala_mm)
    ox_px = int(round(ox_mm * escala_mm))
    oy_px = int(round(oy_mm * escala_mm))

    cor_s = cor_rgb(sombra.get("cor", "#000000"))
    alfa_max = int(round((opacidade / 100.0) * 235))
    margem = max(4, int(math.ceil(raio_px * 2.6)) + max(abs(ox_px), abs(oy_px)) + 2)

    x0_patch = max(0, x - margem)
    y0_patch = max(0, y - margem)
    x1_patch = min(base.width, x + w + margem)
    y1_patch = min(base.height, y + h + margem)
    pw = x1_patch - x0_patch
    ph = y1_patch - y0_patch
    if pw <= 2 or ph <= 2:
        return base

    mascara_s = Image.new("L", (pw, ph), 0)
    des_s = ImageDraw.Draw(mascara_s)
    rx0 = (x - x0_patch) + ox_px
    ry0 = (y - y0_patch) + oy_px
    rx1 = rx0 + w - 1
    ry1 = ry0 + h - 1
    des_s.rectangle((rx0, ry0, rx1, ry1), fill=alfa_max)

    if raio_px >= 0.6:
        if rapido and raio_px >= 2.5 and pw > 80 and ph > 80:
            metade = mascara_s.resize((max(1, pw // 2), max(1, ph // 2)), Image.BILINEAR)
            mascara_s = metade.filter(ImageFilter.GaussianBlur(raio_px * 0.5)).resize((pw, ph), Image.BILINEAR)
        else:
            mascara_s = mascara_s.filter(ImageFilter.GaussianBlur(raio_px))

    camada_s = Image.new("RGBA", (pw, ph), cor_s + (0,))
    camada_s.putalpha(mascara_s)
    if base.mode != "RGBA":
        base_rgba = base.convert("RGBA")
        base_rgba.alpha_composite(camada_s, (x0_patch, y0_patch))
        base.paste(base_rgba.convert(base.mode))
    else:
        base.alpha_composite(camada_s, (x0_patch, y0_patch))
    return base


def _cor_secundaria_moldura_rgb(moldura, cor_principal_rgb):
    sec_hex = moldura.get("cor_secundaria")
    if sec_hex:
        return cor_rgb(sec_hex)
    r, g, b = cor_principal_rgb
    lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255.0
    if lum > 0.65:
        return (max(0, int(r * 0.68)), max(0, int(g * 0.64)), max(0, int(b * 0.54)))
    return (min(255, int(r + (255 - r) * 0.55)), min(255, int(g + (245 - g) * 0.52)), min(255, int(b + (210 - b) * 0.42)))


def aplicar_moldura(imagem, moldura, px_por_mm=None):
    """
    Desenha um dos 12 estilos de moldura com espessura fixa em milímetros na prévia e na exportação:
    simples, dupla, pontilhada, royal, degrade, degrade_real, cantoneira, chanfrada,
    passepartout, tripla, cruzada, halo.
    """
    if not moldura:
        return imagem
    from PIL import ImageDraw

    W, H = imagem.width, imagem.height
    if W <= 4 or H <= 4:
        return imagem

    if moldura.get("espessura_mm") is not None and px_por_mm:
        largura = max(1, round(float(moldura["espessura_mm"]) * px_por_mm))
    else:
        espessura = max(0.1, min(12.0, float(moldura.get("espessura", 1.5))))
        largura = max(1, round(min(W, H) * espessura / 100.0))

    cor1 = cor_rgb(moldura.get("cor", "#ffffff"))
    cor2 = _cor_secundaria_moldura_rgb(moldura, cor1)
    estilo = str(moldura.get("estilo", "simples") or "simples").strip().lower()
    desenho = ImageDraw.Draw(imagem)
    caixa = (0, 0, W - 1, H - 1)

    if estilo == "simples":
        desenho.rectangle(caixa, outline=cor1, width=largura)

    elif estilo == "dupla":
        # Vitral Luminoso (Glass Glow): brilho translúcido interno + linha única fina + pérolas nos 4 cantos
        esp_glow = max(3, round(largura * 2.6))
        camada_v = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        dv = ImageDraw.Draw(camada_v)
        for i in range(esp_glow):
            alfa_v = int(round(95 * ((1.0 - (i / float(esp_glow))) ** 1.5)))
            dv.rectangle((i, i, W - 1 - i, H - 1 - i), outline=cor2 + (alfa_v,), width=1)
        dv.rectangle(caixa, outline=cor1 + (230,), width=max(1, round(largura * 0.75)))
        r_ponto = max(2, round(largura * 1.1))
        rec_p = max(3, round(largura * 2.0))
        for px_c, py_c in ((rec_p, rec_p), (W - 1 - rec_p, rec_p), (rec_p, H - 1 - rec_p), (W - 1 - rec_p, H - 1 - rec_p)):
            dv.ellipse((px_c - r_ponto, py_c - r_ponto, px_c + r_ponto, py_c + r_ponto), fill=cor1 + (245,))
        if imagem.mode == "RGBA":
            imagem.alpha_composite(camada_v)
        else:
            comp_v = Image.alpha_composite(imagem.convert("RGBA"), camada_v)
            imagem.paste(comp_v.convert(imagem.mode))

    elif estilo == "pontilhada":
        passo = max(4, largura * 3)
        segmento = max(2, round(largura * 1.3))
        for x in range(0, W, passo):
            desenho.line((x, 0, min(x + segmento, W - 1), 0), fill=cor1, width=largura)
            desenho.line((x, H - 1, min(x + segmento, W - 1), H - 1), fill=cor1, width=largura)
        for y in range(0, H, passo):
            desenho.line((0, y, 0, min(y + segmento, H - 1)), fill=cor1, width=largura)
            desenho.line((W - 1, y, W - 1, min(y + segmento, H - 1)), fill=cor1, width=largura)

    elif estilo == "royal":
        # Borda externa nobre + filete interno + cantoneiras imperiais com losango/diamante nos 4 cantos
        desenho.rectangle(caixa, outline=cor1, width=largura)
        recuo = max(largura + 3, round(largura * 3.2))
        esp_int = max(1, round(largura * 0.65))
        if W > recuo * 3 and H > recuo * 3:
            desenho.rectangle((recuo, recuo, W - 1 - recuo, H - 1 - recuo), outline=cor2, width=esp_int)
            tam_c = max(recuo + 6, min(W, H) // 10)
            for cx_sinal, cy_sinal in ((1, 1), (-1, 1), (1, -1), (-1, -1)):
                vx = recuo if cx_sinal > 0 else (W - 1 - recuo)
                vy = recuo if cy_sinal > 0 else (H - 1 - recuo)
                # Braços reforçados Royal nos vértices internos
                desenho.line((vx, vy, vx + cx_sinal * tam_c, vy), fill=cor1, width=max(1, largura))
                desenho.line((vx, vy, vx, vy + cy_sinal * tam_c), fill=cor1, width=max(1, largura))
                # Pequeno quadrado/diamante imperial no canto
                q = max(3, round(recuo * 0.85))
                qx0 = min(vx, vx + cx_sinal * q)
                qy0 = min(vy, vy + cy_sinal * q)
                qx1 = max(vx, vx + cx_sinal * q)
                qy1 = max(vy, vy + cy_sinal * q)
                desenho.rectangle((qx0, qy0, qx1, qy1), outline=cor1, width=esp_int)

    elif estilo in ("degrade", "degrade_real"):
        # Desenha borda com gradiente contínuo (linear bicolor ou reflexo metálico 5 paradas)
        esp_b = max(2, round(largura * 1.35))
        n_faixas = 32
        for i in range(n_faixas):
            t = i / float(max(1, n_faixas - 1))
            if estilo == "degrade_real":
                # Reflexo especular metálico
                onda = 0.5 + 0.5 * math.sin(t * math.pi * 2.0)
                c_faixa = (
                    int(round(cor1[0] + (cor2[0] - cor1[0]) * onda)),
                    int(round(cor1[1] + (cor2[1] - cor1[1]) * onda)),
                    int(round(cor1[2] + (cor2[2] - cor1[2]) * onda)),
                )
            else:
                c_faixa = (
                    int(round(cor1[0] + (cor2[0] - cor1[0]) * t)),
                    int(round(cor1[1] + (cor2[1] - cor1[1]) * t)),
                    int(round(cor1[2] + (cor2[2] - cor1[2]) * t)),
                )
            xa = int(round((W * i) / float(n_faixas)))
            xb = int(round((W * (i + 1)) / float(n_faixas)))
            ya = int(round((H * i) / float(n_faixas)))
            yb = int(round((H * (i + 1)) / float(n_faixas)))
            desenho.rectangle((xa, 0, min(W - 1, xb), min(H - 1, esp_b - 1)), fill=c_faixa)
            desenho.rectangle((xa, max(0, H - esp_b), min(W - 1, xb), H - 1), fill=c_faixa)
            desenho.rectangle((0, ya, min(W - 1, esp_b - 1), min(H - 1, yb)), fill=c_faixa)
            desenho.rectangle((max(0, W - esp_b), ya, W - 1, min(H - 1, yb)), fill=c_faixa)
        if estilo == "degrade_real":
            recuo_m = esp_b + max(2, largura)
            if W > recuo_m * 2 and H > recuo_m * 2:
                desenho.rectangle((recuo_m, recuo_m, W - 1 - recuo_m, H - 1 - recuo_m), outline=cor2, width=max(1, largura // 2))

    elif estilo == "cantoneira":
        esp_fina = max(1, largura // 2)
        desenho.rectangle(caixa, outline=cor2, width=esp_fina)
        esp_c = max(2, round(largura * 1.6))
        comp = max(12, min(W, H) // 6)
        rec = max(2, largura)
        # 4 Cantoneiras em L
        for x_c, y_c, dx, dy in (
            (rec, rec, 1, 1),
            (W - 1 - rec, rec, -1, 1),
            (rec, H - 1 - rec, 1, -1),
            (W - 1 - rec, H - 1 - rec, -1, -1),
        ):
            desenho.line((x_c, y_c, x_c + dx * comp, y_c), fill=cor1, width=esp_c)
            desenho.line((x_c, y_c, x_c, y_c + dy * comp), fill=cor1, width=esp_c)

    elif estilo == "chanfrada":
        esp_ch = max(2, round(largura * 1.6))
        clara = (min(255, cor1[0] + 45), min(255, cor1[1] + 45), min(255, cor1[2] + 45))
        escura = (max(0, cor1[0] - 55), max(0, cor1[1] - 55), max(0, cor1[2] - 55))
        for i in range(esp_ch):
            # Topo e Esquerda iluminados
            desenho.line((i, i, W - 1 - i, i), fill=clara, width=1)
            desenho.line((i, i, i, H - 1 - i), fill=clara, width=1)
            # Base e Direita sombreados
            desenho.line((i, H - 1 - i, W - 1 - i, H - 1 - i), fill=escura, width=1)
            desenho.line((W - 1 - i, i, W - 1 - i, H - 1 - i), fill=escura, width=1)

    elif estilo == "passepartout":
        # Galeria Slim: passe-partout fino que não ocupa o espaço útil da foto
        esp_galeria = max(2, min(6, round(largura * 1.35)))
        if W > esp_galeria * 3 and H > esp_galeria * 3:
            desenho.rectangle(caixa, outline=cor1, width=esp_galeria)
            rec_in = esp_galeria
            desenho.rectangle((rec_in, rec_in, W - 1 - rec_in, H - 1 - rec_in), outline=cor2, width=1)
        else:
            desenho.rectangle(caixa, outline=cor1, width=largura)

    elif estilo == "tripla":
        # Diamante Imperial (Cantos 45°): filete único fino + losangos nos 4 cantos (sem empilhar linhas)
        esp_f = max(1, round(largura * 0.75))
        desenho.rectangle(caixa, outline=cor1, width=esp_f)
        r_diam = max(3, round(largura * 2.0))
        rec_d = max(r_diam + 1, round(largura * 2.2))
        if W > rec_d * 3 and H > rec_d * 3:
            for cx_d, cy_d in ((rec_d, rec_d), (W - 1 - rec_d, rec_d), (rec_d, H - 1 - rec_d), (W - 1 - rec_d, H - 1 - rec_d)):
                pts = [(cx_d, cy_d - r_diam), (cx_d + r_diam, cy_d), (cx_d, cy_d + r_diam), (cx_d - r_diam, cy_d)]
                desenho.polygon(pts, fill=cor2, outline=cor1)

    elif estilo == "cruzada":
        rec = max(3, round(largura * 2.5))
        ext = max(6, round(largura * 5.0))
        if W > (rec + ext) * 2 and H > (rec + ext) * 2:
            desenho.line((rec - ext, rec, W - 1 - rec + ext, rec), fill=cor1, width=largura)
            desenho.line((rec - ext, H - 1 - rec, W - 1 - rec + ext, H - 1 - rec), fill=cor1, width=largura)
            desenho.line((rec, rec - ext, rec, H - 1 - rec + ext), fill=cor1, width=largura)
            desenho.line((W - 1 - rec, rec - ext, W - 1 - rec, H - 1 - rec + ext), fill=cor1, width=largura)
        else:
            desenho.rectangle(caixa, outline=cor1, width=largura)

    elif estilo == "halo":
        esp_halo = max(3, round(largura * 2.8))
        camada_h = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        dh = ImageDraw.Draw(camada_h)
        for i in range(esp_halo):
            alfa = int(round(190 * ((1.0 - (i / float(esp_halo))) ** 1.6)))
            dh.rectangle((i, i, W - 1 - i, H - 1 - i), outline=cor1 + (alfa,), width=1)
        if imagem.mode == "RGBA":
            imagem.alpha_composite(camada_h)
        else:
            comp_h = Image.alpha_composite(imagem.convert("RGBA"), camada_h)
            imagem.paste(comp_h.convert(imagem.mode))
    else:
        desenho.rectangle(caixa, outline=cor1, width=largura)

    return imagem


CATALOGO_TEMAS = (
    {
        "id": "colacao_royal",
        "nome": "🎓 Colação 1 / Estúdio — Gradiente Royal Sutil",
        "curto": "Colação 1 · Royal",
        "descricao": "Gradiente azul royal profundo (#0b1938) e roxo nobre (#1e1035) com microtextura de cimento queimado e vinheta suave de estúdio (centro limpo 2:1).",
        "cor_base": "#0b152c",
        "fundo_espec": {
            "id": "cap_colacao_royal",
            "titulo": "Lâmina Gradiente Royal Sutil · Colação 1",
            "categoria": "degrade",
            "subtipo": "linear_diagonal",
            "textura": "concreto_royal",
            "padrao_cor": "preto",
            "cores": ["#0d1f4f", "#1c3f94", "#3b2179", "#d4af37"],
            "angulo": 32,
            "seed": 2026,
        },
        "moldura": {"estilo": "royal", "cor": "#d4af37", "cor_secundaria": "#fef08a", "espessura_mm": 0.50},
        "sombra": {"ativa": True, "preset": "profunda", "cor": "#000000", "opacidade": 64, "desfoque_mm": 3.2, "offset_x_mm": 1.6, "offset_y_mm": 2.0, "sincronizar_moldura": True},
        "edicao_foto": {"brilho": 1.02, "contraste": 1.06, "saturacao": 1.04, "matiz": 0.0, "colorir_cor": "#60a5fa", "colorir_int": 3.0},
    },
    {
        "id": "estudio_dark_bokeh",
        "nome": "📸 Colação 2 / Estúdio — Studio Backdrop & Dark Bokeh",
        "curto": "Colação 2 · Bokeh",
        "descricao": "Fundo escuro com textura de lona de estúdio e pontos de luz bokeh desfocados nas bordas periféricas, diferenciando a Colação 2 da Colação 1.",
        "cor_base": "#0d1017",
        "fundo_espec": {
            "id": "cap_estudio_bokeh",
            "titulo": "Lâmina Studio Backdrop / Dark Bokeh · Colação 2",
            "categoria": "abstrato",
            "subtipo": "bokeh_luz",
            "textura": "lona_bokeh",
            "padrao_cor": "preto",
            "cores": ["#0f1a30", "#1f3558", "#2e4c7c", "#9cc4ff"],
            "seed": 2027,
        },
        "moldura": {"estilo": "dupla", "cor": "#cbd5e1", "cor_secundaria": "#475569", "espessura_mm": 0.45},
        "sombra": {"ativa": True, "preset": "flutuante", "cor": "#000000", "opacidade": 60, "desfoque_mm": 3.0, "offset_x_mm": 1.4, "offset_y_mm": 1.8, "sincronizar_moldura": True},
        "edicao_foto": {"brilho": 1.02, "contraste": 1.05, "saturacao": 1.03, "matiz": 0.0, "colorir_cor": "#93c5fd", "colorir_int": 3.0},
    },
    {
        "id": "jaleco_fine_art",
        "nome": "🩺 Cerimônia do Jaleco — Fine Art / Mármore Clean",
        "curto": "Jaleco · Fine Art",
        "descricao": "Textura mineral de gesso e veios suaves de mármore Carrara off-white/cinza claro, ideal para Medicina, Enfermagem, Odonto e área da saúde.",
        "cor_base": "#f8f9fa",
        "fundo_espec": {
            "id": "cap_jaleco_fine_art",
            "titulo": "Lâmina Fine Art / Mármore Clean · Jaleco",
            "categoria": "abstrato",
            "subtipo": "aquarela_nuvens",
            "textura": "gesso_marmore",
            "padrao_cor": "branco",
            "cores": ["#f3f5f9", "#e1e8f1", "#cbd7e6", "#8ea2bf"],
            "seed": 412,
        },
        "moldura": {"estilo": "passepartout", "cor": "#ffffff", "cor_secundaria": "#cbd5e1", "espessura_mm": 0.45},
        "sombra": {"ativa": True, "preset": "suave", "cor": "#1e293b", "opacidade": 30, "desfoque_mm": 2.2, "offset_x_mm": 0.8, "offset_y_mm": 1.2, "sincronizar_moldura": True},
        "edicao_foto": {"brilho": 1.04, "contraste": 1.01, "saturacao": 1.0, "matiz": 0.0, "colorir_cor": "#e2e8f0", "colorir_int": 2.0},
    },
    {
        "id": "externas_botanica",
        "nome": "🌿 Fotos Externas — Minimalista Botânica (Sombras)",
        "curto": "Externas · Botânica",
        "descricao": "Fundo claro em tons areia e bege suave com projeções realistas de sombras de folhas nas bordas e trama de papel algodão fine-art.",
        "cor_base": "#f5f0e6",
        "fundo_espec": {
            "id": "cap_externas_botanica",
            "titulo": "Lâmina Minimalista Botânica (Sombras) · Externas",
            "categoria": "abstrato",
            "subtipo": "ondas_sedosas",
            "textura": "papel_botanico",
            "padrao_cor": "branco",
            "cores": ["#eee2c8", "#dfcca4", "#c9b385", "#7f7a4e"],
            "seed": 707,
        },
        "moldura": {"estilo": "simples", "cor": "#fffdf9", "cor_secundaria": "#d6c7b2", "espessura_mm": 0.45},
        "sombra": {"ativa": True, "preset": "suave", "cor": "#292524", "opacidade": 34, "desfoque_mm": 2.4, "offset_x_mm": 1.0, "offset_y_mm": 1.4, "sincronizar_moldura": True},
        "edicao_foto": {"brilho": 1.03, "contraste": 1.02, "saturacao": 1.04, "matiz": 0.0, "colorir_cor": "#fde68a", "colorir_int": 4.0},
    },
    {
        "id": "baile_dark_luxury",
        "nome": "🥂 Grande Baile de Gala — Dark Luxury (Poeira de Ouro)",
        "curto": "Baile · Dark Luxury",
        "descricao": "Fundo grafite carvão escuro (#111215) com textura de veludo escovado, explosão sutil de poeira de ouro e partículas bokeh douradas.",
        "cor_base": "#111215",
        "fundo_espec": {
            "id": "cap_baile_dark_luxury",
            "titulo": "Lâmina Dark Luxury · Poeira de Ouro (Baile)",
            "categoria": "abstrato",
            "subtipo": "bokeh_luz",
            "textura": "veludo_ouro",
            "padrao_cor": "preto",
            "cores": ["#141117", "#251c27", "#3d2b17", "#e0b040"],
            "seed": 1225,
        },
        "moldura": {"estilo": "degrade_real", "cor": "#d4af37", "cor_secundaria": "#fef08a", "espessura_mm": 0.50},
        "sombra": {"ativa": True, "preset": "profunda", "cor": "#000000", "opacidade": 68, "desfoque_mm": 3.5, "offset_x_mm": 1.8, "offset_y_mm": 2.2, "sincronizar_moldura": True},
        "edicao_foto": {"brilho": 1.02, "contraste": 1.08, "saturacao": 1.06, "matiz": 0.0, "colorir_cor": "#f59e0b", "colorir_int": 5.0},
    },
    {
        "id": "culto_solenidade",
        "nome": "🕯️ Culto Ecumênico / Colação 3 — Gala Obsidiana",
        "curto": "Colação 3 / Culto",
        "descricao": "Obsidiana profundo com textura escovada e feixes sutis de luz esmeralda e dourado imperial para solenidades ou 3ª Colação.",
        "cor_base": "#0b0f12",
        "fundo_espec": {
            "id": "cap_culto_solenidade",
            "titulo": "Lâmina Gala Obsidiana · Solenidade / Colação 3",
            "categoria": "abstrato",
            "subtipo": "prisma_geometrico",
            "textura": "veludo_ouro",
            "padrao_cor": "preto",
            "cores": ["#0a1915", "#0f4034", "#1b2d4f", "#d4af37"],
            "seed": 909,
        },
        "moldura": {"estilo": "cantoneira", "cor": "#d4af37", "cor_secundaria": "#fef08a", "espessura_mm": 0.45},
        "sombra": {"ativa": True, "preset": "flutuante", "cor": "#000000", "opacidade": 58, "desfoque_mm": 2.8, "offset_x_mm": 1.4, "offset_y_mm": 1.8, "sincronizar_moldura": True},
        "edicao_foto": {"brilho": 1.01, "contraste": 1.06, "saturacao": 1.03, "matiz": 0.0, "colorir_cor": "#fbbf24", "colorir_int": 4.0},
    },
    {
        "id": "ensaio_linho",
        "nome": "🏛️ Making Of & Ensaio — Linho Editorial Quente",
        "curto": "Ensaio · Linho",
        "descricao": "Trama de linho marfim aquecido e luz editorial suave, perfeita para retratos individuais, família e homenagem aos pais.",
        "cor_base": "#f8f5ee",
        "fundo_espec": {
            "id": "cap_ensaio_linho",
            "titulo": "Lâmina Linho Editorial · Ensaio & Família",
            "categoria": "degrade",
            "subtipo": "linear_diagonal",
            "textura": "papel_botanico",
            "padrao_cor": "branco",
            "cores": ["#f2e8d4", "#e4d1ab", "#d3b884", "#b8893f"],
            "angulo": 38,
            "seed": 5050,
        },
        "moldura": {"estilo": "tripla", "cor": "#c5a059", "cor_secundaria": "#faf0ca", "espessura_mm": 0.45},
        "sombra": {"ativa": True, "preset": "deslocada", "cor": "#1e293b", "opacidade": 38, "desfoque_mm": 1.8, "offset_x_mm": 1.4, "offset_y_mm": 1.4, "sincronizar_moldura": True},
        "edicao_foto": {"brilho": 1.02, "contraste": 1.03, "saturacao": 0.98, "matiz": 0.0, "colorir_cor": "#d97706", "colorir_int": 4.0},
    },
    {
        "id": "casamento_perola",
        "nome": "💍 Casamento & Bodas — Seda Pérola & Champanhe",
        "curto": "Casamento · Pérola",
        "descricao": "Seda branca pérola e champanhe clássico com textura mineral suave, ideal para álbuns de casamento e bodas.",
        "cor_base": "#faf8f5",
        "fundo_espec": {
            "id": "cap_casamento_perola",
            "titulo": "Lâmina Seda Pérola & Champanhe · Casamento",
            "categoria": "abstrato",
            "subtipo": "ondas_sedosas",
            "textura": "gesso_marmore",
            "padrao_cor": "branco",
            "cores": ["#f7f0e6", "#eddfca", "#dfc8a8", "#c9a35c"],
            "seed": 303,
        },
        "moldura": {"estilo": "royal", "cor": "#c5a880", "cor_secundaria": "#ffffff", "espessura_mm": 0.45},
        "sombra": {"ativa": True, "preset": "suave", "cor": "#0f172a", "opacidade": 36, "desfoque_mm": 2.2, "offset_x_mm": 1.0, "offset_y_mm": 1.4, "sincronizar_moldura": True},
        "edicao_foto": {"brilho": 1.02, "contraste": 1.03, "saturacao": 0.97, "matiz": 0.0, "colorir_cor": "#fde68a", "colorir_int": 4.0},
    },
)

MAPA_TEMAS = {t["id"]: t for t in CATALOGO_TEMAS}

# Molduras automáticas de cada evento: estilo da foto de destaque, das secundárias e a alternativa
# (que reveza com a secundária de uma lâmina para a outra). Estilos de ESTILOS_MOLDURA.
MOLDURAS_EVENTO = {
    "colacao_royal": {"destaque": "royal", "secundaria": "simples", "alternativa": "cantoneira",
                      "cor": "#d4af37", "cor_secundaria": "#fef08a"},
    "estudio_dark_bokeh": {"destaque": "dupla", "secundaria": "passepartout", "alternativa": "halo",
                           "cor": "#cbd5e1", "cor_secundaria": "#7aa7e0"},
    "jaleco_fine_art": {"destaque": "passepartout", "secundaria": "simples", "alternativa": "chanfrada",
                        "cor": "#ffffff", "cor_secundaria": "#7c8ea8"},
    "externas_botanica": {"destaque": "pontilhada", "secundaria": "simples", "alternativa": "cantoneira",
                          "cor": "#fffaf0", "cor_secundaria": "#7f7a4e"},
    "baile_dark_luxury": {"destaque": "degrade_real", "secundaria": "tripla", "alternativa": "royal",
                          "cor": "#d4af37", "cor_secundaria": "#f7e7b0"},
    "culto_solenidade": {"destaque": "cantoneira", "secundaria": "simples", "alternativa": "cruzada",
                         "cor": "#d4af37", "cor_secundaria": "#2f8f6f"},
    "ensaio_linho": {"destaque": "tripla", "secundaria": "simples", "alternativa": "passepartout",
                     "cor": "#c5a059", "cor_secundaria": "#faf0ca"},
    "casamento_perola": {"destaque": "royal", "secundaria": "passepartout", "alternativa": "simples",
                         "cor": "#c5a880", "cor_secundaria": "#ffffff"},
}
MOLDURAS_SEM_EVENTO = {"destaque": "passepartout", "secundaria": "simples", "alternativa": "passepartout"}
CONTRASTE_MINIMO_MOLDURA = 2.0


def _luminancia_relativa(rgb):
    def canal(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb[:3]
    return 0.2126 * canal(r) + 0.7152 * canal(g) + 0.0722 * canal(b)


def contraste_cores(cor_a, cor_b):
    """Razão de contraste (WCAG, 1 a 21) entre duas cores."""
    la = _luminancia_relativa(cor_rgb(cor_a))
    lb = _luminancia_relativa(cor_rgb(cor_b))
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def cores_moldura_com_contraste(cor, cor_secundaria, cor_fundo, minimo=CONTRASTE_MINIMO_MOLDURA):
    """
    Devolve (cor, cor_secundaria) visíveis sobre `cor_fundo`: mantém a cor do evento se já contrasta,
    senão troca pela secundária, senão clareia ou escurece a cor até chegar ao contraste mínimo.
    """
    if contraste_cores(cor, cor_fundo) >= minimo:
        return cor, cor_secundaria
    if cor_secundaria and contraste_cores(cor_secundaria, cor_fundo) >= minimo:
        return cor_secundaria, cor
    fundo_claro = _luminancia_relativa(cor_rgb(cor_fundo)) > 0.18
    alvo = (0, 0, 0) if fundo_claro else (255, 255, 255)
    ajustada = cor
    for passo in range(1, 11):
        ajustada = _misturar_rgb_hex(cor_rgb(cor), alvo, passo / 10.0)
        if contraste_cores(ajustada, cor_fundo) >= minimo:
            break
    return ajustada, cor_secundaria or cor


def gerar_fundo_de_tema(id_tema, fotos_lamina=None, info=None, seed_extra=0, medidas_mm=None, paleta=None):
    """
    Gera o fundo `.png` editável na pasta temporária para o tema escolhido,
    combinando a atmosfera do tema com um toque das cores das fotos da lâmina quando disponível.
    `medidas_mm` = (largura, altura) da área coberta, para a escala da textura.
    """
    tema_cfg = MAPA_TEMAS.get(id_tema) or CATALOGO_TEMAS[0]
    espec = dict(tema_cfg["fundo_espec"])
    espec["tipo"] = espec.get("tipo") or espec.get("categoria") or "degrade"
    cores = list(espec.get("cores") or ["#121318", "#1e222d", "#282c3a"])
    if fotos_lamina or paleta:
        try:
            pal = paleta or extrair_paleta_fotos(fotos_lamina, info=info)
            acento_foto = (pal.get("cores_destaque_fotos") or [None])[0]
            if acento_foto and len(cores) >= 3:
                # Força média: o tema continua reconhecível e ganha a cor das fotos da lâmina.
                # Temas claros recebem metade, para o branco/areia não virar rosa ou verde.
                forca = 0.30 if espec.get("padrao_cor") == "preto" else 0.15
                cores[1] = _misturar_rgb_hex(cor_rgb(cores[1]), cor_rgb(acento_foto), forca)
                cores[2] = _misturar_rgb_hex(cor_rgb(cores[2]), cor_rgb(acento_foto), forca / 2.0)
        except Exception:
            pass
    espec["cores"] = cores
    espec["seed"] = int(espec.get("seed", 100)) + int(seed_extra)
    espec["id"] = f"{espec.get('id', 'tema')}_{espec['seed']}"
    if medidas_mm and medidas_mm[0] and medidas_mm[1]:
        espec["largura_mm"] = round(float(medidas_mm[0]), 1)
        espec["altura_mm"] = round(float(medidas_mm[1]), 1)
    return criar_dados_fundo_gerado(espec, materializar=True)


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

