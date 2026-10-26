# -*- coding: utf-8 -*-
"""
Diagramação automática e adaptativa (Motor Elástico Proporcional).

Regras principais (aplicadas em todas as ferramentas do programa):
1. Preenchimento máximo da área útil da página (ou lâmina), sem depender de
   moldes JSON estáticos desatualizados.
2. Limite de distorção/corte de 22% (CORTE_MAXIMO = 1.22, faixa de 20%-25%):
   - Enquanto a diferença entre a proporção da foto e a do quadro for <= 22%,
     o quadro se ajusta para fechar 100% da área útil da página.
   - Caso um quadro exija distorção/corte acima de CORTE_MAXIMO, aquela foto
     volta para a sua proporção padrão original (sem corte forçado) e as
     demais fotos da página se expandem para ocupar o espaço restante com
     margem mínima.
"""

import itertools
import math
import uuid

from modelos_lamina import espacos_do_modelo
from produtos import area_pagina_mm, area_util_pagina_mm, lamina_mm, normalizar_mm

CORTE_MAXIMO = 1.22
MAX_POR_PAGINA = 8
MAX_LINHAS = 4

PESO_CORTE = 10.0
PESO_VAZIO = 9.5
PESO_PEQUENA = 1.8
PESO_DESEQUILIBRIO = 0.30
PESO_PAGINA_VAZIA = 3.2
INTERVALO_CHEIO = 1800.0
PESO_INTERVALO = 1.5


def configuracao(produto, projeto_cfg=None):
    cfg = {
        "margem_mm": produto["margem_mm"],
        "espacamento_mm": produto["espacamento_mm"],
        "fotos_por_lamina": produto["fotos_por_lamina"],
        "max_fotos_lamina": produto["max_fotos_lamina"],
        "max_fotos_pagina": MAX_POR_PAGINA,
        "laminas_desejadas": produto["laminas_min"],
        "reconhecer_cenarios": True,
        "reconhecer_rostos": True,
        "preencher_espacos_extras": False,
        "excluir_repetidas": False,
        "fundo": "#ffffff",
    }
    for chave, valor in (projeto_cfg or {}).items():
        if valor is not None:
            cfg[chave] = valor
    return cfg


def novo_id():
    return uuid.uuid4().hex[:10]


def lamina_vazia(cfg):
    return {
        "id": novo_id(),
        "fundo": cfg.get("fundo", "#ffffff"),
        "espacos": [],
        "sobreposicoes": [],
        "modelo": None,
    }


def _corte_quadro(proporcao, quadro):
    _x, _y, w, h = quadro
    if w <= 1e-6 or h <= 1e-6 or proporcao <= 1e-6:
        return 99.0
    f = w / float(h)
    return max(proporcao / f, f / proporcao)


def caixa_proporcao_padrao(proporcao, area):
    """
    Encaixa uma foto na sua proporção padrão (sem corte) ocupando o máximo
    possível da caixa `area = (x0, y0, W, H)` e centralizada nela.
    """
    x0, y0, W, H = area
    p = max(0.1, float(proporcao or 1.0))
    if W <= 0 or H <= 0:
        return (x0, y0, max(1.0, W), max(1.0, H))
    if W / float(H) >= p:
        h = float(H)
        w = h * p
    else:
        w = float(W)
        h = w / p
    return (x0 + (W - w) / 2.0, y0 + (H - h) / 2.0, w, h)


def quadros_1_foto(proporcao, area):
    """
    Para 1 única foto na área:
    - Se preencher toda a área útil causar corte <= CORTE_MAXIMO (22%),
      preenche 100% da área.
    - Se distorcer/cortar mais que CORTE_MAXIMO, usa a proporção padrão
      original da imagem ocupando a dimensão máxima da área.
    """
    x0, y0, W, H = area
    p = max(0.1, float(proporcao or 1.0))
    quadro_cheio = (x0, y0, W, H)
    corte_cheio = _corte_quadro(p, quadro_cheio)
    if corte_cheio <= CORTE_MAXIMO:
        return [(quadro_cheio, corte_cheio, 0.0, "preenchimento_total")]
    quadro_padrao = caixa_proporcao_padrao(p, area)
    usado = quadro_padrao[2] * quadro_padrao[3]
    vazio = max(0.0, 1.0 - usado / max(1.0, W * H))
    return [(quadro_padrao, 1.0, vazio, "proporcao_padrao")]


# ------------------------------------------------------------
# Layout elástico em linhas e colunas (com expansão vizinha)
# ------------------------------------------------------------

def _composicoes(n, max_partes, max_tamanho):
    """Formas de dividir n fotos em sequência (ex.: 5 = 2 + 3)."""
    if n == 0:
        yield ()
        return
    if max_partes <= 0:
        return
    for primeiro in range(1, min(n, max_tamanho) + 1):
        if max_partes == 1 and primeiro != n:
            continue
        for resto in _composicoes(n - primeiro, max_partes - 1, max_tamanho):
            yield (primeiro,) + resto


def _distribuir_larguras_linha(proporcoes_linha, largura_util, altura_linha):
    """
    Distribui as larguras das fotos dentro de uma linha de altura `altura_linha`
    tentando preencher `largura_util`:
    - Se o ajuste proporcional mantiver todas as fotos com corte <= CORTE_MAXIMO,
      preenche 100% da largura da linha.
    - Se alguma foto for forçada além de CORTE_MAXIMO, trava essa foto na sua
      proporção padrão (`w = altura_linha * p`) e expande as demais fotos da
      linha (até o limite seguro de CORTE_MAXIMO) para absorver o espaço restante.
    """
    k = len(proporcoes_linha)
    if k == 0 or largura_util <= 0 or altura_linha <= 0:
        return [], False
    larguras_nat = [max(0.1, float(p)) * altura_linha for p in proporcoes_linha]
    soma_nat = sum(larguras_nat)
    if soma_nat <= 1e-6:
        return [largura_util / k] * k, False

    escala = largura_util / soma_nat
    corte_uniforme = max(escala, 1.0 / max(1e-6, escala))
    if corte_uniforme <= CORTE_MAXIMO:
        return [w * escala for w in larguras_nat], False

    # Quando estoura CORTE_MAXIMO, identificamos fotos com proporção extrema na linha
    # para mantê-las na proporção padrão enquanto as demais se expandem até CORTE_MAXIMO.
    if k == 1:
        # 1 foto sozinha na linha: trava na proporção padrão limitada à largura_util
        w_padrao = min(largura_util, larguras_nat[0])
        return [w_padrao], True

    media_p = sum(proporcoes_linha) / float(k)
    # Ordena da foto mais discrepante para a mais próxima da média
    ordem_discrepancia = sorted(
        range(k),
        key=lambda i: abs(math.log(max(0.1, proporcoes_linha[i]) / max(0.1, media_p))),
        reverse=True,
    )
    travadas = set()
    larguras = list(larguras_nat)
    usou_padrao = False

    # Tenta travar na proporção padrão a(s) foto(s) mais extrema(s) e expandir as vizinhas
    for candidato in ordem_discrepancia:
        travadas.add(candidato)
        usou_padrao = True
        largura_travada = sum(larguras_nat[i] for i in travadas)
        livres = [i for i in range(k) if i not in travadas]
        if not livres:
            break
        restante = largura_util - largura_travada
        soma_livres = sum(larguras_nat[i] for i in livres)
        if restante > 0 and soma_livres > 0:
            fator_livres = restante / soma_livres
            # Limita a expansão das fotos vizinhas a CORTE_MAXIMO para não distorcê-las
            fator_clamped = min(CORTE_MAXIMO, max(1.0 / CORTE_MAXIMO, fator_livres))
            for i in range(k):
                larguras[i] = larguras_nat[i] if i in travadas else larguras_nat[i] * fator_clamped
            if abs(fator_livres - fator_clamped) <= 1e-4:
                return larguras, True

    # Se todas precisarem de ajuste no mesmo sentido (ex.: linha inteira muito alta ou baixa),
    # expande/contrai até o limite seguro CORTE_MAXIMO ou mantém proporção padrão
    fator_seguro = min(CORTE_MAXIMO, max(1.0 / CORTE_MAXIMO, escala))
    return [w * fator_seguro for w in larguras_nat], True


def _layout_linhas(proporcoes, area, espaco, linhas):
    """
    Fotos em linhas tentando preencher 100% da área útil da página.
    - Se a escala vertical global ficar dentro de CORTE_MAXIMO (22%), fecha
      100% da página (largura e altura totais).
    - Se alguma linha distorcer além de CORTE_MAXIMO, essa linha volta para a
      proporção padrão das suas fotos e as demais linhas da página se expandem
      para ocupar a altura restante.
    """
    x0, y0, W, H = area
    n_linhas = len(linhas)
    if n_linhas == 0 or W <= 0 or H <= 0:
        return None
    if len(proporcoes) == 1 and n_linhas == 1:
        q, corte, vazio, _tipo = quadros_1_foto(proporcoes[0], area)[0]
        return [q], corte, vazio

    disponivel_h = H - espaco * (n_linhas - 1)
    if disponivel_h <= 0:
        return None

    grupos_p = []
    alturas_nat = []
    larguras_uteis = []
    inicio = 0
    for k in linhas:
        ps = [max(0.1, float(p)) for p in proporcoes[inicio:inicio + k]]
        grupos_p.append(ps)
        w_util = W - espaco * (k - 1)
        if w_util <= 0:
            return None
        larguras_uteis.append(w_util)
        alturas_nat.append(w_util / sum(ps))
        inicio += k

    total_nat = sum(alturas_nat)
    if total_nat <= 1e-6:
        return None

    s = disponivel_h / total_nat
    corte_global = max(s, 1.0 / max(1e-6, s))

    if corte_global <= CORTE_MAXIMO:
        # Preenche 100% da página dentro do limite de 22%
        alturas = [h * s for h in alturas_nat]
    else:
        # Quando o esticamento uniforme estoura 22%:
        # Se houver mais de 1 linha, preserva na proporção padrão a linha com fotos
        # mais sensíveis (ou de menor folga) e expande as outras linhas até CORTE_MAXIMO.
        alturas = list(alturas_nat)
        if n_linhas > 1:
            # Ordena linhas pela quantidade de fotos (linhas com 1 foto sentem mais a distorção)
            ordem_linhas = sorted(range(n_linhas), key=lambda r: (len(grupos_p[r]), -alturas_nat[r]))
            fixas = {ordem_linhas[0]}
            h_fixas = sum(alturas_nat[r] for r in fixas)
            livres = [r for r in range(n_linhas) if r not in fixas]
            h_livres_nat = sum(alturas_nat[r] for r in livres)
            restante_h = disponivel_h - h_fixas
            if restante_h > 0 and h_livres_nat > 0:
                fator_livres = restante_h / h_livres_nat
                fator_livres = min(CORTE_MAXIMO, max(1.0 / CORTE_MAXIMO, fator_livres))
                for r in livres:
                    alturas[r] = alturas_nat[r] * fator_livres
                # Se ainda sobrar espaço na página e a linha fixa puder absorver até CORTE_MAXIMO:
                sobra = disponivel_h - sum(alturas)
                if abs(sobra) > 1e-3:
                    for r in fixas:
                        alvo_r = alturas[r] + sobra / len(fixas)
                        fator_r = alvo_r / alturas_nat[r]
                        if max(fator_r, 1.0 / max(1e-6, fator_r)) <= CORTE_MAXIMO:
                            alturas[r] = alvo_r
                        else:
                            # Mantém proporção padrão na foto que estouraria o limite!
                            alturas[r] = alturas_nat[r]
        else:
            # 1 única linha com várias fotos:
            # Se s < 1 / CORTE_MAXIMO (a linha é muito mais alta que a página), limita pela altura H
            if s < 1.0 / CORTE_MAXIMO:
                alturas = [disponivel_h]
            else:
                # Linha mais baixa que a página: tenta expandir até CORTE_MAXIMO se couber,
                # ou mantém proporção padrão se distorcer demais
                alturas = [alturas_nat[0] * CORTE_MAXIMO if s > CORTE_MAXIMO else alturas_nat[0]]

    # Se mesmo assim a soma das alturas passar de disponivel_h, reescala dentro de disponivel_h
    soma_h = sum(alturas)
    if soma_h > disponivel_h + 1e-4:
        fator_ajuste = disponivel_h / soma_h
        alturas = [h * fator_ajuste for h in alturas]

    altura_total = sum(alturas) + espaco * (n_linhas - 1)
    y = y0 + (H - altura_total) / 2.0
    quadros = []
    usado = 0.0
    maior_corte = 1.0

    for ps, w_util, h in zip(grupos_p, larguras_uteis, alturas):
        larguras, _padrao = _distribuir_larguras_linha(ps, w_util, h)
        largura_linha = sum(larguras) + espaco * (len(ps) - 1)
        x = x0 + (W - largura_linha) / 2.0
        for p, w in zip(ps, larguras):
            q = (x, y, w, h)
            c_foto = _corte_quadro(p, q)
            # Proteção final individual: se por geometria extrema este quadro ainda
            # passou de CORTE_MAXIMO + 0.02, crava na proporção padrão dentro da célula
            if c_foto > CORTE_MAXIMO + 0.02:
                q = caixa_proporcao_padrao(p, q)
                c_foto = 1.0
            quadros.append(q)
            usado += q[2] * q[3]
            maior_corte = max(maior_corte, c_foto)
            x += w + espaco
        y += h + espaco

    vazio = max(0.0, 1.0 - usado / max(1.0, W * H))
    return quadros, maior_corte, vazio


def _layouts_assimetricos(proporcoes, area, espaco):
    """
    Gera composições dinâmicas assimétricas (1 foto destaque + coluna/linha com as
    demais fotos) calculadas exatamente pelas proporções das fotos:
    - Destaque à Esquerda + Pilha à Direita
    - Pilha à Esquerda + Destaque à Direita
    - Destaque no Topo + Linha na Base
    - Linha no Topo + Destaque na Base
    Se alguma foto exceder CORTE_MAXIMO (22%), usa sua proporção padrão e expande
    o bloco vizinho para preencher o restante da página.
    """
    n = len(proporcoes)
    if n < 2 or n > 6:
        return []
    x0, y0, W, H = area
    if W <= espaco * 2 or H <= espaco * 2:
        return []

    opcoes = []
    ps = [max(0.1, float(p)) for p in proporcoes]

    # Testa colocar a primeira ou a última foto (ou a foto mais vertical/horizontal) em destaque
    candidatos_hero = sorted({0, n - 1, min(range(n), key=lambda i: ps[i]), max(range(n), key=lambda i: ps[i])})

    for idx_hero in candidatos_hero:
        outros = [i for i in range(n) if i != idx_hero]
        p_hero = ps[idx_hero]
        p_outros = [ps[i] for i in outros]
        k = len(outros)

        # --- 1) Coluna Principal + Coluna Empilhada (Esquerda ou Direita) ---
        h_pilha_disp = H - espaco * (k - 1)
        w_disp = W - espaco
        if h_pilha_disp > 0 and w_disp > 0:
            soma_inv = sum(1.0 / p for p in p_outros)
            w_hero_nat = H * p_hero
            w_pilha_nat = h_pilha_disp / max(1e-6, soma_inv)
            soma_w_nat = w_hero_nat + w_pilha_nat
            fator_w = w_disp / max(1e-6, soma_w_nat)

            if max(fator_w, 1.0 / max(1e-6, fator_w)) <= CORTE_MAXIMO:
                w_hero = w_hero_nat * fator_w
                w_pilha = w_pilha_nat * fator_w
                h_hero = H
            else:
                # Se estourar 22%, trava a foto de destaque na proporção padrão e expande a pilha
                # (ou vice-versa) para ocupar o espaço restante da página!
                w_hero = min(w_disp * 0.68, max(w_disp * 0.25, w_hero_nat))
                w_pilha = w_disp - w_hero
                corte_pilha = max((w_pilha / max(1e-6, w_pilha_nat)), (w_pilha_nat / max(1e-6, w_pilha)))
                if corte_pilha > CORTE_MAXIMO:
                    w_pilha = w_pilha_nat * (CORTE_MAXIMO if w_pilha > w_pilha_nat else 1.0 / CORTE_MAXIMO)
                    w_hero = min(w_disp - w_pilha, w_hero_nat * CORTE_MAXIMO)
                h_hero = min(H, w_hero / p_hero) if abs(w_hero - w_hero_nat) < 1e-3 else H

            for lado_hero in ("esq", "dir"):
                total_w = w_hero + espaco + w_pilha
                inicio_x = x0 + (W - total_w) / 2.0
                if lado_hero == "esq":
                    x_hero = inicio_x
                    x_pilha = inicio_x + w_hero + espaco
                else:
                    x_pilha = inicio_x
                    x_hero = inicio_x + w_pilha + espaco

                q_hero = (x_hero, y0 + (H - h_hero) / 2.0, w_hero, h_hero)
                if _corte_quadro(p_hero, q_hero) > CORTE_MAXIMO + 0.02:
                    q_hero = caixa_proporcao_padrao(p_hero, (x_hero, y0, w_hero, H))

                # Alturas da pilha à direita/esquerda
                alturas_p_nat = [w_pilha / p for p in p_outros]
                soma_hp = sum(alturas_p_nat)
                fator_hp = h_pilha_disp / max(1e-6, soma_hp)
                fator_hp_seguro = min(CORTE_MAXIMO, max(1.0 / CORTE_MAXIMO, fator_hp))
                alturas_p = [h * fator_hp_seguro for h in alturas_p_nat]
                total_hp = sum(alturas_p) + espaco * (k - 1)
                yp = y0 + (H - total_hp) / 2.0

                quadros_mapa = {idx_hero: q_hero}
                for i_outro, p_o, hp in zip(outros, p_outros, alturas_p):
                    qp = (x_pilha, yp, w_pilha, hp)
                    if _corte_quadro(p_o, qp) > CORTE_MAXIMO + 0.02:
                        qp = caixa_proporcao_padrao(p_o, qp)
                    quadros_mapa[i_outro] = qp
                    yp += hp + espaco

                ordem = [idx_hero] + outros if lado_hero == "esq" else outros + [idx_hero]
                quadros = [quadros_mapa[i] for i in ordem]
                props_ordem = [ps[i] for i in ordem]
                maior_corte = max(_corte_quadro(p, q) for p, q in zip(props_ordem, quadros))
                usado = sum(q[2] * q[3] for q in quadros)
                vazio = max(0.0, 1.0 - usado / max(1.0, W * H))
                desordem = sum(1 for pos, idx_orig in enumerate(ordem) if pos != idx_orig) * 0.04
                custo = _custo(quadros, props_ordem, area, maior_corte, vazio) + desordem
                opcoes.append((custo, quadros, None, ordem))

        # --- 2) Faixa Principal (Topo ou Base) + Linha com as Demais ---
        w_linha_disp = W - espaco * (k - 1)
        h_disp = H - espaco
        if w_linha_disp > 0 and h_disp > 0:
            soma_p = sum(p_outros)
            h_hero_nat = W / p_hero
            h_linha_nat = w_linha_disp / max(1e-6, soma_p)
            soma_h_nat = h_hero_nat + h_linha_nat
            fator_h = h_disp / max(1e-6, soma_h_nat)

            if max(fator_h, 1.0 / max(1e-6, fator_h)) <= CORTE_MAXIMO:
                h_hero = h_hero_nat * fator_h
                h_linha = h_linha_nat * fator_h
                w_hero = W
            else:
                h_hero = min(h_disp * 0.68, max(h_disp * 0.25, h_hero_nat))
                h_linha = h_disp - h_hero
                corte_linha = max((h_linha / max(1e-6, h_linha_nat)), (h_linha_nat / max(1e-6, h_linha)))
                if corte_linha > CORTE_MAXIMO:
                    h_linha = h_linha_nat * (CORTE_MAXIMO if h_linha > h_linha_nat else 1.0 / CORTE_MAXIMO)
                    h_hero = min(h_disp - h_linha, h_hero_nat * CORTE_MAXIMO)
                w_hero = min(W, h_hero * p_hero) if abs(h_hero - h_hero_nat) < 1e-3 else W

            for pos_hero in ("topo", "base"):
                total_h = h_hero + espaco + h_linha
                inicio_y = y0 + (H - total_h) / 2.0
                if pos_hero == "topo":
                    y_hero = inicio_y
                    y_linha = inicio_y + h_hero + espaco
                else:
                    y_linha = inicio_y
                    y_hero = inicio_y + h_linha + espaco

                q_hero = (x0 + (W - w_hero) / 2.0, y_hero, w_hero, h_hero)
                if _corte_quadro(p_hero, q_hero) > CORTE_MAXIMO + 0.02:
                    q_hero = caixa_proporcao_padrao(p_hero, (x0, y_hero, W, h_hero))

                larguras_l, _padrao = _distribuir_larguras_linha(p_outros, w_linha_disp, h_linha)
                total_wl = sum(larguras_l) + espaco * (k - 1)
                xl = x0 + (W - total_wl) / 2.0

                quadros_mapa = {idx_hero: q_hero}
                for i_outro, p_o, wl in zip(outros, p_outros, larguras_l):
                    ql = (xl, y_linha, wl, h_linha)
                    if _corte_quadro(p_o, ql) > CORTE_MAXIMO + 0.02:
                        ql = caixa_proporcao_padrao(p_o, ql)
                    quadros_mapa[i_outro] = ql
                    xl += wl + espaco

                ordem = [idx_hero] + outros if pos_hero == "topo" else outros + [idx_hero]
                quadros = [quadros_mapa[i] for i in ordem]
                props_ordem = [ps[i] for i in ordem]
                maior_corte = max(_corte_quadro(p, q) for p, q in zip(props_ordem, quadros))
                usado = sum(q[2] * q[3] for q in quadros)
                vazio = max(0.0, 1.0 - usado / max(1.0, W * H))
                desordem = sum(1 for pos, idx_orig in enumerate(ordem) if pos != idx_orig) * 0.04
                custo = _custo(quadros, props_ordem, area, maior_corte, vazio) + desordem
                opcoes.append((custo, quadros, None, ordem))

    return opcoes


def _custo(quadros, proporcoes, area, corte, vazio):
    _x, _y, W, H = area
    # Penaliza fortemente qualquer corte que tente passar de CORTE_MAXIMO
    excesso_corte = max(0.0, corte - CORTE_MAXIMO)
    custo = (
        PESO_CORTE * (math.log(max(1.0, corte)) ** 2)
        + 45.0 * (excesso_corte ** 2)
        + PESO_VAZIO * max(0.0, vazio - 0.03)
    )
    menor_lado = min(W, H)
    for (_qx, _qy, w, h) in quadros:
        if min(w, h) < 0.22 * menor_lado:
            custo += PESO_PEQUENA * (1.0 - min(w, h) / (0.22 * menor_lado))
    if len(quadros) > 1:
        areas = [math.log(max(1e-6, w * h)) for (_a, _b, w, h) in quadros]
        media = sum(areas) / len(areas)
        custo += 0.12 * sum((a - media) ** 2 for a in areas) / len(areas)
    return custo


def _layouts_sem_corte(proporcoes, area, espaco):
    """
    Layouts que preservam 100% da proporção original de cada foto, mas
    expandindo cada linha ao máximo possível dentro da página.
    """
    n = len(proporcoes)
    if not n:
        return []
    x0, y0, W, H = area
    saida = []
    for linhas in _composicoes(n, min(MAX_LINHAS, n), min(MAX_LINHAS, n)):
        disponivel_h = H - espaco * (len(linhas) - 1)
        if disponivel_h <= 0:
            continue
        alturas_nat = []
        inicio = 0
        for quantidade in linhas:
            soma = sum(max(0.1, float(p)) for p in proporcoes[inicio:inicio + quantidade])
            largura_disponivel = W - espaco * (quantidade - 1)
            if soma <= 0 or largura_disponivel <= 0:
                break
            alturas_nat.append(largura_disponivel / soma)
            inicio += quantidade
        if len(alturas_nat) != len(linhas):
            continue
        soma_h = sum(alturas_nat)
        escala = min(1.0, disponivel_h / max(1e-6, soma_h))
        alturas_linha = [h * escala for h in alturas_nat]
        altura_total = sum(alturas_linha) + espaco * (len(linhas) - 1)
        y = y0 + (H - altura_total) / 2.0
        quadros = []
        inicio = 0
        for quantidade, altura in zip(linhas, alturas_linha):
            larguras = [max(0.1, float(proporcoes[i])) * altura for i in range(inicio, inicio + quantidade)]
            largura_total = sum(larguras) + espaco * (quantidade - 1)
            x = x0 + (W - largura_total) / 2.0
            for largura in larguras:
                quadros.append((x, y, largura, altura))
                x += largura + espaco
            y += altura + espaco
            inicio += quantidade
        usado = sum(w * h for _x, _y, w, h in quadros)
        vazio = max(0.0, 1.0 - usado / max(1.0, W * H))
        custo = 0.35 + PESO_VAZIO * 0.55 * vazio + 0.06 * (len(linhas) - 1)
        saida.append((custo, quadros, "__sem_corte__", list(range(n))))
    return saida


def _layout_cruzando_meio(fotos, produto, cfg):
    """Uma foto panorâmica atravessa a dobra; as restantes preenchem a área abaixo."""
    n = len(fotos)
    if n < 2 or n > 9 or produto.get("paginas_por_lamina", 1) < 2:
        return None
    W, H = lamina_mm(produto)
    margem = max(0.0, float(cfg["margem_mm"]))
    intervalo = max(0.0, float(cfg["espacamento_mm"]))
    area_w = W - 2 * margem
    if area_w <= 0:
        return None

    indice_hero = max(range(n), key=lambda i: fotos[i]["proporcao"])
    p_hero = max(0.2, float(fotos[indice_hero]["proporcao"]))
    hero_h_ideal = area_w / p_hero
    hero_h = min(H * 0.48, max(H * 0.28, hero_h_ideal))
    if hero_h <= 0:
        return None

    hero_y = margem
    inferior_y = hero_y + hero_h + intervalo
    inferior_h = H - margem - inferior_y
    if inferior_h <= 0:
        return None

    indices_restantes = [i for i in range(n) if i != indice_hero]
    props_restantes = [max(0.1, float(fotos[i]["proporcao"])) for i in indices_restantes]
    area_inferior = (margem, inferior_y, area_w, inferior_h)

    melhores_inf = layouts_pagina(props_restantes, area_inferior, intervalo, (), produto=None, pagina=0, maximo=1)
    if not melhores_inf or not melhores_inf[0][1]:
        return None
    _c_inf, quadros_inf, _mod_inf, ordem_inf = melhores_inf[0]

    quadro_hero = (margem, hero_y, area_w, hero_h)
    if _corte_quadro(p_hero, quadro_hero) > CORTE_MAXIMO:
        quadro_hero = caixa_proporcao_padrao(p_hero, quadro_hero)

    quadros_mapa = {indice_hero: quadro_hero}
    for q_inf, idx_rel in zip(quadros_inf, ordem_inf):
        quadros_mapa[indices_restantes[idx_rel]] = q_inf

    quadros = [quadros_mapa[i] for i in range(n)]
    cortes = [_corte_quadro(fotos[i]["proporcao"], quadro) for i, quadro in enumerate(quadros)]
    usado = sum(q[2] * q[3] for q in quadros)
    vazio = max(0.0, 1.0 - usado / max(1.0, area_w * (H - 2 * margem)))
    custo = 0.35 + PESO_CORTE * sum(math.log(max(1.0, c)) ** 2 for c in cortes) / n + PESO_VAZIO * 0.5 * vazio
    espacos = [_espaco(produto, quadro, foto["caminho"]) for foto, quadro in zip(fotos, quadros)]
    return {
        "custo": custo,
        "espacos": espacos,
        "sobreposicoes": [],
        "modelo": None,
        "tipo_layout": "panoramico_meio",
    }


def _geometrias_parecidas(a, b, produto):
    if len(a) != len(b):
        return False
    ordena = lambda espacos: sorted(
        ((e["x"], e["y"], e["w"], e["h"]) for e in espacos), key=lambda q: (round(q[1], 2), q[0])
    )
    aa, bb = ordena(a), ordena(b)
    W, H = lamina_mm(produto)
    return all(
        abs(x[0] - y[0]) * W < 7
        and abs(x[1] - y[1]) * H < 7
        and abs(x[2] - y[2]) * W < 7
        and abs(x[3] - y[3]) * H < 7
        for x, y in zip(aa, bb)
    )


def layouts_pagina(proporcoes, area, espaco, modelos_pagina=(), produto=None, pagina=0, maximo=5):
    """
    Opções para uma página: [(custo, quadros em mm, modelo, ordem)].
    Prioriza preencher 100% da área útil da página mantendo corte <= CORTE_MAXIMO (22%).
    Caso o preenchimento total distorça alguma imagem > 22%, usa a proporção padrão
    daquela imagem e expande as demais imagens da página.
    """
    n = len(proporcoes)
    opcoes = []
    if n == 0:
        return [(0.0, [], None, [])]
    ordem_natural = list(range(n))

    if n == 1:
        for quadro, corte, vazio, tipo in quadros_1_foto(proporcoes[0], area):
            marcador = "__sem_corte__" if tipo == "proporcao_padrao" else None
            opcoes.append((_custo([quadro], proporcoes, area, corte, vazio), [quadro], marcador, [0]))
    else:
        # 1. Partições em Linhas Horizontais Elásticas
        for linhas in _composicoes(n, MAX_LINHAS, MAX_LINHAS):
            r = _layout_linhas(proporcoes, area, espaco, linhas)
            if r:
                quadros, corte, vazio = r
                opcoes.append((_custo(quadros, proporcoes, area, corte, vazio), quadros, None, ordem_natural))
            # 2. Partições em Colunas Verticais Elásticas (transpondo x<->y e w<->h)
            x0, y0, W, H = area
            invertidas = [1.0 / max(0.1, float(p)) for p in proporcoes]
            r_col = _layout_linhas(invertidas, (y0, x0, H, W), espaco, linhas)
            if r_col:
                quadros_t, corte, vazio = r_col
                quadros = [(qy, qx, qh, qw) for (qx, qy, qw, qh) in quadros_t]
                opcoes.append((_custo(quadros, proporcoes, area, corte, vazio) + 0.01, quadros, None, ordem_natural))

        # 3. Composições Assimétricas Dinâmicas (Destaque + Coadjuvantes)
        opcoes.extend(_layouts_assimetricos(proporcoes, area, espaco))

    # 4. Caso o usuário tenha salvado manualmente algum molde PNG/customizado compatível
    if produto is not None and modelos_pagina:
        lw, lh = lamina_mm(produto)
        for modelo in modelos_pagina:
            if len(modelo["espacos"]) != n:
                continue
            espacos_m, _sobre = espacos_do_modelo(modelo, produto, pagina)
            quadros = [(e["x"] * lw, e["y"] * lh, e["w"] * lw, e["h"] * lh) for e in espacos_m]
            permutacoes = itertools.permutations(range(n)) if n <= 4 else [tuple(range(n))]
            melhor = None
            for perm in permutacoes:
                corte = max(_corte_quadro(proporcoes[perm[i]], quadros[i]) for i in range(n))
                desordem = sum(1 for i in range(n) if perm[i] != i) * 0.05
                c = PESO_CORTE * math.log(max(1.0, corte)) ** 2 + desordem
                if melhor is None or c < melhor[0]:
                    melhor = (c, list(perm))
            if melhor is not None:
                # Se não tiver molde PNG rígido e estourar CORTE_MAXIMO, adapta os quadros
                quadros_finais = list(quadros)
                if not modelo.get("molde_png"):
                    for idx_q, idx_f in enumerate(melhor[1]):
                        if _corte_quadro(proporcoes[idx_f], quadros_finais[idx_q]) > CORTE_MAXIMO:
                            quadros_finais[idx_q] = caixa_proporcao_padrao(proporcoes[idx_f], quadros_finais[idx_q])
                opcoes.append((melhor[0], quadros_finais, modelo, melhor[1]))

    # 5. Opções com proporção 100% nativa sem corte
    opcoes.extend(_layouts_sem_corte(proporcoes, area, espaco))
    opcoes.sort(key=lambda o: o[0])

    vistos = set()
    unicas = []
    sem_corte = next((opcao for opcao in opcoes if opcao[2] == "__sem_corte__"), None)
    for opcao in opcoes:
        assinatura = tuple(tuple(round(v, 1) for v in q) for q in opcao[1])
        if assinatura in vistos:
            continue
        vistos.add(assinatura)
        unicas.append(opcao)
        if len(unicas) >= maximo:
            break
    if sem_corte is not None and not any(opcao[2] == "__sem_corte__" for opcao in unicas):
        if len(unicas) >= maximo:
            unicas[-1] = sem_corte
        else:
            unicas.append(sem_corte)
    unicas.sort(key=lambda opcao: opcao[0])
    return unicas


# ------------------------------------------------------------
# Opções para uma lâmina e ajuste global para todas as ações
# ------------------------------------------------------------

def _espaco(produto, quadro, foto, edicao=None):
    e = normalizar_mm(produto, quadro)
    e["foto"] = foto
    e["edicao"] = dict(edicao or {})
    return e


def opcoes_lamina(fotos, produto, cfg, modelos=(), maximo=10):
    """
    fotos: [{"caminho", "proporcao", "destaque"}] na ordem.
    Devolve [{"custo", "espacos", "sobreposicoes", "modelo"}], a melhor primeiro.
    """
    n = len(fotos)
    if n == 0:
        return [{"custo": 0.0, "espacos": [], "sobreposicoes": [], "modelo": None}]
    lw, lh = lamina_mm(produto)
    espaco = float(cfg["espacamento_mm"])
    margem = float(cfg["margem_mm"])
    paginas = produto["paginas_por_lamina"]
    max_pagina = max(1, int(cfg.get("max_fotos_pagina", MAX_POR_PAGINA)))

    modelos_lamina = [m for m in modelos if m.get("escopo") == "lamina" and len(m["espacos"]) == n]
    modelos_pagina = [m for m in modelos if m.get("escopo") == "pagina"]
    opcoes = []

    if n == 1:
        p = max(0.1, float(fotos[0]["proporcao"]))
        area_lamina_util = (margem, margem, max(1.0, lw - 2 * margem), max(1.0, lh - 2 * margem))
        corte_lamina = _corte_quadro(p, (0, 0, lw, lh))
        if corte_lamina <= CORTE_MAXIMO or (fotos[0].get("destaque") and p >= 1.35):
            bonus = -2.0 if fotos[0].get("destaque") and p > 1.25 else 0.0
            quadro_l = (0, 0, lw, lh) if corte_lamina <= CORTE_MAXIMO else caixa_proporcao_padrao(p, area_lamina_util)
            opcoes.append({
                "custo": PESO_CORTE * math.log(max(1.0, min(corte_lamina, CORTE_MAXIMO))) ** 2 * 0.5 + bonus,
                "espacos": [_espaco(produto, quadro_l, fotos[0]["caminho"])],
                "sobreposicoes": [],
                "modelo": None,
                "tipo_layout": "panoramico_meio" if quadro_l == (0, 0, lw, lh) else "sem_corte",
            })

    for modelo in modelos_lamina:
        espacos_m, sobre = espacos_do_modelo(modelo, produto)
        quadros = [(e["x"] * lw, e["y"] * lh, e["w"] * lw, e["h"] * lh) for e in espacos_m]
        if not modelo.get("molde_png"):
            for i_q, f in enumerate(fotos):
                if _corte_quadro(f["proporcao"], quadros[i_q]) > CORTE_MAXIMO:
                    quadros[i_q] = caixa_proporcao_padrao(f["proporcao"], quadros[i_q])
        corte = max(_corte_quadro(f["proporcao"], q) for f, q in zip(fotos, quadros))
        opcoes.append({
            "custo": PESO_CORTE * math.log(max(1.0, corte)) ** 2,
            "espacos": [_espaco(produto, q, f["caminho"]) for f, q in zip(fotos, quadros)],
            "sobreposicoes": [sobre] if sobre else [],
            "modelo": modelo["id"],
        })

    panoramico = _layout_cruzando_meio(fotos, produto, cfg)
    if panoramico:
        opcoes.append(panoramico)

    if paginas == 1:
        divisoes = [(n,)]
    else:
        divisoes = [(k, n - k) for k in range(0, n + 1)]

    for divisao in divisoes:
        if any(k > max_pagina for k in divisao):
            continue
        por_pagina = []
        inicio = 0
        for pagina, k in enumerate(divisao):
            grupo = fotos[inicio:inicio + k]
            inicio += k
            area = area_util_pagina_mm(produto, pagina, margem)
            por_pagina.append((
                grupo,
                pagina,
                layouts_pagina(
                    [f["proporcao"] for f in grupo],
                    area,
                    espaco,
                    modelos_pagina,
                    produto,
                    pagina,
                    maximo=4,
                ),
            ))
        vazias = sum(1 for k in divisao if k == 0)
        penalidade = 0.0
        if vazias:
            penalidade = PESO_PAGINA_VAZIA if n > 1 else 0.8
        if len(divisao) == 2:
            penalidade += PESO_DESEQUILIBRIO * abs(divisao[0] - divisao[1])

        for combinacao in itertools.product(*[p[2] for p in por_pagina]):
            custo = penalidade + sum(c[0] for c in combinacao)
            espacos = []
            sobreposicoes = []
            ids = []
            sem_corte = False
            for (grupo, pagina, _ops), (_c, quadros, modelo, ordem) in zip(por_pagina, combinacao):
                for quadro, indice in zip(quadros, ordem):
                    espacos.append(_espaco(produto, quadro, grupo[indice]["caminho"]))
                if modelo == "__sem_corte__":
                    sem_corte = True
                elif isinstance(modelo, dict):
                    ids.append(modelo["id"])
                    _esp, sobre = espacos_do_modelo(modelo, produto, pagina)
                    if sobre:
                        sobreposicoes.append(sobre)
            opcoes.append({
                "custo": custo,
                "espacos": espacos,
                "sobreposicoes": sobreposicoes,
                "modelo": "+".join(ids) if ids else None,
            })
            if sem_corte:
                opcoes[-1]["tipo_layout"] = "sem_corte"

    opcoes.sort(key=lambda o: o["custo"])
    unicas = []
    for opcao in opcoes:
        semelhante = next(
            (i for i, anterior in enumerate(unicas) if _geometrias_parecidas(opcao["espacos"], anterior["espacos"], produto)),
            None,
        )
        if semelhante is not None:
            anterior = unicas[semelhante]
            if opcao.get("tipo_layout") and not anterior.get("tipo_layout"):
                unicas[semelhante] = opcao
            elif opcao.get("tipo_layout") and anterior.get("tipo_layout") != opcao.get("tipo_layout"):
                unicas.append(opcao)
            continue
        unicas.append(opcao)
        if len(unicas) >= maximo:
            break
    return unicas


def ajustar_espacos_lamina(lamina, mapa_proporcoes, produto, cfg):
    """
    Aplica a regra de preenchimento máximo + proteção de proporção padrão (CORTE_MAXIMO = 1.22)
    em uma lâmina existente (por exemplo, após trocar duas fotos de lugar, colocar uma foto
    nova num espaço, apagar um espaço ou aplicar um modelo).
    Se alguma página tiver fotos com corte > CORTE_MAXIMO, recalcula os espaços daquela
    página preservando as edições (giro, P&B, brilho) de cada foto.
    """
    if not lamina or lamina.get("sobreposicoes"):
        return False
    espacos = lamina.get("espacos") or []
    com_foto = [e for e in espacos if e.get("foto")]
    if not com_foto or len(com_foto) != len(espacos):
        return False

    lw, lh = lamina_mm(produto)
    paginas = produto.get("paginas_por_lamina", 1)
    margem = float(cfg.get("margem_mm", produto["margem_mm"]))
    intervalo = float(cfg.get("espacamento_mm", produto["espacamento_mm"]))

    # Verifica se algum espaço atravessa a dobra central
    atravessa_dobra = False
    if paginas > 1:
        meio = lw / 2.0
        for e in espacos:
            x0 = e["x"] * lw
            x1 = (e["x"] + e["w"]) * lw
            if x0 < meio - 2.0 and x1 > meio + 2.0:
                atravessa_dobra = True
                break

    def prop_de(caminho):
        return max(0.1, float(mapa_proporcoes.get(caminho, 1.5)))

    if atravessa_dobra or paginas == 1:
        precisa = any(
            _corte_quadro(prop_de(e["foto"]), (e["x"] * lw, e["y"] * lh, e["w"] * lw, e["h"] * lh)) > CORTE_MAXIMO
            for e in espacos
        )
        if not precisa:
            return False
        fotos_lista = [{"caminho": e["foto"], "proporcao": prop_de(e["foto"]), "edicao": e.get("edicao") or {}} for e in espacos]
        melhor = opcoes_lamina(fotos_lista, produto, cfg, (), maximo=1)[0]
        edicoes = {f["caminho"]: dict(f["edicao"]) for f in fotos_lista}
        for ed in edicoes.values():
            ed.pop("corte", None)
        lamina["espacos"] = [
            dict(novo_e, edicao=dict(edicoes.get(novo_e["foto"], {}))) for novo_e in melhor["espacos"]
        ]
        return True

    # Avalia página por página (esquerda = 0, direita = 1)
    por_pagina = {p: [] for p in range(paginas)}
    for e in espacos:
        centro = (e["x"] + e["w"] / 2.0) * lw
        pag_idx = 0
        for p in range(paginas):
            px, _py, pw, _ph = area_pagina_mm(produto, p)
            if px <= centro <= px + pw:
                pag_idx = p
                break
        por_pagina[pag_idx].append(e)

    alterou = False
    novos_espacos = []
    for p in range(paginas):
        grupo_e = por_pagina[p]
        if not grupo_e:
            continue
        area_u = area_util_pagina_mm(produto, p, margem)
        ux, uy, uw, uh = area_u
        usado_p = sum((e["w"] * lw) * (e["h"] * lh) for e in grupo_e)
        vazio_p = max(0.0, 1.0 - usado_p / max(1.0, uw * uh))
        corte_max_p = max(
            _corte_quadro(prop_de(e["foto"]), (e["x"] * lw, e["y"] * lh, e["w"] * lw, e["h"] * lh))
            for e in grupo_e
        )
        # Se alguma foto da página estourou CORTE_MAXIMO (22%) ou se a página ficou vazia demais
        # podendo ser preenchida sem estourar 22%, recalcula os quadros desta página!
        if corte_max_p > CORTE_MAXIMO + 0.01 or (vazio_p > 0.25 and corte_max_p < 1.05):
            props = [prop_de(e["foto"]) for e in grupo_e]
            opcoes_p = layouts_pagina(props, area_u, intervalo, (), produto, p, maximo=1)
            if opcoes_p and opcoes_p[0][1]:
                _c, quadros, _mod, ordem = opcoes_p[0]
                for quadro, idx in zip(quadros, ordem):
                    orig = grupo_e[idx]
                    ed = dict(orig.get("edicao") or {})
                    ed.pop("corte", None)
                    novos_espacos.append(_espaco(produto, quadro, orig["foto"], ed))
                alterou = True
                continue
        novos_espacos.extend(grupo_e)

    if alterou:
        lamina["espacos"] = novos_espacos
    return alterou


def espacos_com_modelo(modelo, fotos, produto, pagina=None, cfg=None):
    """
    Aplica um modelo escolhido na mão. Modelo de página vai para a página
    indicada (ou a primeira). Se não houver molde PNG rígido e algum quadro
    distorcer a foto além de CORTE_MAXIMO (22%), adapta a caixa para a proporção
    padrão da foto e expande os quadros vizinhos.
    Devolve (espaços, sobreposição, fotos que não couberam).
    """
    espacos, sobre = espacos_do_modelo(modelo, produto, pagina or 0)
    lw, lh = lamina_mm(produto)
    livres = list(fotos)
    saida = []
    tem_png = bool(modelo.get("molde_png"))

    for e in espacos:
        quadro = (e["x"] * lw, e["y"] * lh, e["w"] * lw, e["h"] * lh)
        escolhida = None
        if livres:
            escolhida = min(
                livres,
                key=lambda f: (_corte_quadro(f["proporcao"], quadro) > CORTE_MAXIMO, _corte_quadro(f["proporcao"], quadro), livres.index(f)),
            )
            livres.remove(escolhida)
        if escolhida and not tem_png and _corte_quadro(escolhida["proporcao"], quadro) > CORTE_MAXIMO:
            quadro_ajustado = caixa_proporcao_padrao(escolhida["proporcao"], quadro)
            novo = normalizar_mm(produto, quadro_ajustado)
        else:
            novo = dict(e)
        novo["foto"] = escolhida["caminho"] if escolhida else None
        novo["edicao"] = {}
        saida.append(novo)

    if not tem_png and saida and all(s.get("foto") for s in saida):
        mapa = {f["caminho"]: f["proporcao"] for f in fotos}
        lam_temp = {"espacos": saida, "sobreposicoes": [sobre] if sobre else []}
        cfg_uso = cfg or configuracao(produto)
        ajustar_espacos_lamina(lam_temp, mapa, produto, cfg_uso)
        saida = lam_temp["espacos"]

    return saida, sobre, livres


# ------------------------------------------------------------
# Dividir o álbum em lâminas
# ------------------------------------------------------------

def _numero_laminas(n, produto, cfg, destaques):
    por_lamina = max(1, int(cfg["fotos_por_lamina"]))
    maximo = max(1, int(cfg["max_fotos_lamina"]))
    minimo_por_grupo = max(1, int(math.floor(por_lamina * 0.75)))
    maximo_por_grupo = min(maximo, int(math.ceil(por_lamina * 1.25)))
    desejadas = int(cfg.get("laminas_desejadas", produto["laminas_min"]))
    desejadas = max(produto["laminas_min"], min(produto["laminas_max"], desejadas))
    s_fotos = int(math.ceil(n / float(por_lamina))) if n else 0
    s_capacidade = int(math.ceil(n / float(maximo))) if n else 0
    s = max(s_fotos, s_capacidade, destaques)
    if n and s_capacidade <= desejadas <= produto["laminas_max"]:
        media_desejada = n / float(desejadas)
        if minimo_por_grupo <= media_desejada <= maximo_por_grupo:
            s = max(destaques, desejadas)
    s = min(s, produto["laminas_max"])
    return min(s, n) if n else 0


def dividir(fotos, produto, cfg):
    """Grupos de fotos (na ordem), um por lâmina, e as fotos que não couberam."""
    maximo = max(1, int(cfg["max_fotos_lamina"]))
    capacidade = produto["laminas_max"] * maximo
    sobras = fotos[capacidade:]
    fotos = fotos[:capacidade]
    n = len(fotos)
    destaques = sum(1 for f in fotos if f.get("destaque"))
    s = _numero_laminas(n, produto, cfg, destaques)
    if s == 0:
        return [], sobras
    alvo = max(1, int(cfg["fotos_por_lamina"]))
    eh_destaque = [bool(f.get("destaque")) for f in fotos]
    bonus = [0.0] * (n + 1)
    for i in range(1, n):
        a = fotos[i - 1].get("data") or 0
        b = fotos[i].get("data") or 0
        intervalo = abs(b - a)
        bonus[i] = PESO_INTERVALO * min(1.0, intervalo / INTERVALO_CHEIO)
        if cfg.get("reconhecer_cenarios"):
            cenario_a, cenario_b = fotos[i - 1].get("cenario"), fotos[i].get("cenario")
            if cenario_a and cenario_b and len(cenario_a) == len(cenario_b):
                afinidade = sum(
                    math.sqrt(max(0.0, x) * max(0.0, y)) for x, y in zip(cenario_a, cenario_b)
                )
                distancia = math.sqrt(max(0.0, 1.0 - min(1.0, afinidade)))
                if distancia > 0.28:
                    bonus[i] += min(2.0, (distancia - 0.28) * 4.0)
    destaques_ate = [0] * (n + 1)
    for i, d in enumerate(eh_destaque):
        destaques_ate[i + 1] = destaques_ate[i] + (1 if d else 0)

    def custo_grupo(j, i):
        tamanho = i - j
        tem_destaque = destaques_ate[i] - destaques_ate[j]
        if tem_destaque:
            return 0.0 if tamanho == 1 else None
        if tamanho > maximo:
            return None
        tolerancia = max(1, int(math.ceil(alvo * 0.25)))
        minimo_grupo = max(1, int(math.floor(alvo - tolerancia)))
        maximo_grupo = min(maximo, int(math.ceil(alvo + tolerancia)))
        if not minimo_grupo <= tamanho <= maximo_grupo:
            return None
        return 2.0 * (tamanho - alvo) ** 2 / max(1.0, alvo) - bonus[j]

    infinito = float("inf")
    melhor = [[infinito] * (n + 1) for _ in range(s + 1)]
    volta = [[-1] * (n + 1) for _ in range(s + 1)]
    melhor[0][0] = 0.0
    for k in range(1, s + 1):
        for i in range(k, n + 1):
            for j in range(max(k - 1, i - maximo), i):
                if melhor[k - 1][j] == infinito:
                    continue
                c = custo_grupo(j, i)
                if c is None:
                    continue
                total = melhor[k - 1][j] + c
                if total < melhor[k][i]:
                    melhor[k][i] = total
                    volta[k][i] = j
    if melhor[s][n] == infinito:
        grupos = [fotos[i:i + maximo] for i in range(0, n, maximo)]
        return grupos, sobras
    grupos = []
    i = n
    for k in range(s, 0, -1):
        j = volta[k][i]
        grupos.append(fotos[j:i])
        i = j
    grupos.reverse()
    return grupos, sobras


def diagramar_album(fotos, produto, cfg, modelos=(), ao_progresso=None):
    """
    fotos: [{"caminho", "proporcao", "data", "destaque"}] na ordem do álbum.
    Devolve (lâminas, sobras).
    """
    grupos, sobras = dividir(list(fotos), produto, cfg)
    laminas = []
    for indice, grupo in enumerate(grupos, start=1):
        grupo_layout = grupo
        foto_sobreposta = None
        if cfg.get("preencher_espacos_extras") and len(grupo) > 1:
            grupo_layout = grupo[:-1]
            foto_sobreposta = grupo[-1]
        melhor = opcoes_lamina(grupo_layout, produto, cfg, modelos, maximo=1)[0]
        lamina = lamina_vazia(cfg)
        lamina["espacos"] = melhor["espacos"]
        lamina["sobreposicoes"] = melhor["sobreposicoes"]
        lamina["modelo"] = melhor["modelo"]
        if foto_sobreposta:
            lw, lh = lamina_mm(produto)
            proporcao = max(0.2, float(foto_sobreposta.get("proporcao") or 1.0))
            largura = lw * 0.34
            altura = largura / proporcao
            if altura > lh * 0.42:
                altura = lh * 0.42
                largura = altura * proporcao
            lamina["espacos"].append(
                _espaco(
                    produto,
                    ((lw - largura) / 2, (lh - altura) / 2, largura, altura),
                    foto_sobreposta["caminho"],
                )
            )
        laminas.append(lamina)
        if ao_progresso:
            ao_progresso(indice, len(grupos))
    desejadas = int(cfg.get("laminas_desejadas", produto["laminas_min"]))
    alvo = max(produto["laminas_min"], min(produto["laminas_max"], desejadas))
    while len(laminas) < alvo:
        laminas.append(lamina_vazia(cfg))
    for lamina in laminas:
        for espaco in lamina["espacos"]:
            edicao = espaco.setdefault("edicao", {})
            if not cfg.get("reconhecer_rostos", True):
                edicao["ignorar_rosto"] = True
    return laminas, [f["caminho"] for f in sobras]
