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
   - Quadros que encostam na sangria (transpasse, meio termo, sangria total)
     aceitam até CORTE_MAXIMO * 1.08.
"""

import itertools
import math
import uuid

from produtos import area_pagina_mm, area_util_pagina_mm, espacos_do_modelo, lamina_mm, normalizar_mm

CORTE_MAXIMO = 1.22
MAX_POR_PAGINA = 8
MAX_LINHAS = 4

PESO_CORTE = 10.0
PESO_VAZIO = 14.0
PESO_VAZIO_LAMINA = 16.0
PESO_BURACO = 6.0
PESO_PEQUENA = 1.8
PESO_DESEQUILIBRIO = 0.30
PESO_PAGINA_VAZIA = 3.2
PESO_PAGINA_ABAIXO_MIN = 2.5
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
        "separar_pastas": True,
        "respeitar_grupos_manuais": True,
        "reconhecer_cenarios": True,
        "limiar_cenario": 0.28,
        "ordem_pessoas_grupo": "manter",
        "reconhecer_rostos": True,
        "nao_cortar_pessoas": True,
        "permitir_transpasse": True,
        "permitir_meio_termo": True,
        "permitir_sangria_total": True,
        "modo_corte": "elastico",
        "corte_maximo": CORTE_MAXIMO,
        "preencher_espacos_extras": False,
        "excluir_repetidas": False,
        "auto_salvar_minutos": 5,
        "fundo": "#ffffff",
        # 1. Função Independente de Fundos Automáticos (Ativada por padrão)
        "auto_fundos": True,
        "modo_auto_fundos": "misturar",  # "misturar" | "gerados" | "pasta"
        "estilo_fundo_gerado": "degrade_e_abstrato",  # "degrade_e_abstrato" | "degrade" | "abstrato"
        "escopo_auto_fundos": "adaptativo",  # "adaptativo" | "lamina" | "pagina"
        # 2. Função Independente de Degradê Automático (Ativada por padrão)
        "auto_degrade": True,
        "regra_auto_degrade": "destaque_e_fusao",  # "destaque_e_fusao" | "somente_destaque" | "somente_fusao"
        "intensidade_auto_degrade": "suave",  # "suave" | "medio" | "forte"
        # 3. Função Independente de Molduras Automáticas (Ativada por padrão)
        "auto_molduras": True,
        "regra_auto_molduras": "grades_com_margem",  # "grades_com_margem" | "todas_com_margem" | "secundarias"
        "estilo_auto_molduras": "por_evento",  # "por_evento" | um estilo de composicao.ESTILOS_MOLDURA
        "cor_auto_molduras": "cores_do_evento",  # "cores_do_evento" | "contraste_automatico" | "branca" | "paleta"
        "espessura_auto_molduras_mm": 1.2,
    }
    for chave, valor in (projeto_cfg or {}).items():
        if valor is not None:
            cfg[chave] = valor
    # Limites por página coerentes com os da lâmina, a menos que o projeto defina os seus
    paginas = max(1, int(produto.get("paginas_por_lamina", 1) or 1))
    if (projeto_cfg or {}).get("max_fotos_pagina") is None:
        cfg["max_fotos_pagina"] = max(1, int(cfg["max_fotos_lamina"]) // paginas)
    # O mínimo por página sempre sai de "fotos por lâmina" (um único lugar para o usuário mudar)
    cfg["min_fotos_pagina"] = max(1, int(math.ceil(int(cfg["fotos_por_lamina"]) / float(paginas))))
    return cfg


def _limites_pagina(cfg, produto):
    """(mínimo, máximo) de fotos numa página."""
    paginas = max(1, int((produto or {}).get("paginas_por_lamina", 1) or 1))
    try:
        maximo = int(cfg.get("max_fotos_pagina") or 0)
    except (TypeError, ValueError):
        maximo = 0
    if maximo <= 0:
        maximo = max(1, int(cfg.get("max_fotos_lamina", MAX_POR_PAGINA * paginas)) // paginas)
    try:
        minimo = int(cfg.get("min_fotos_pagina") or 0)
    except (TypeError, ValueError):
        minimo = 0
    if minimo <= 0:
        minimo = max(1, int(math.ceil(int(cfg.get("fotos_por_lamina", 1) or 1) / float(paginas))))
    return max(1, min(minimo, maximo)), max(1, maximo)


def _penalidade_pagina_vazia_demais(divisao, n, minimo_pagina):
    """Penaliza página com menos fotos que o mínimo quando a lâmina tem fotos para as duas."""
    if len(divisao) < 2 or n < 2 * minimo_pagina:
        return 0.0
    return PESO_PAGINA_ABAIXO_MIN * sum(max(0, minimo_pagina - k) for k in divisao)


def _limite_corte_cfg(cfg):
    if not cfg:
        return CORTE_MAXIMO
    if cfg.get("modo_corte") == "sem_corte":
        return 1.0
    try:
        return max(1.0, min(1.50, float(cfg.get("corte_maximo", CORTE_MAXIMO))))
    except (TypeError, ValueError):
        return CORTE_MAXIMO


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


def quadros_1_foto(proporcao, area, corte_max=CORTE_MAXIMO):
    """
    Para 1 única foto na área:
    - Se preencher toda a área útil causar corte <= corte_max (22%),
      preenche 100% da área.
    - Se distorcer/cortar mais que corte_max, usa a proporção padrão
      original da imagem ocupando a dimensão máxima da área.
    """
    x0, y0, W, H = area
    p = max(0.1, float(proporcao or 1.0))
    quadro_cheio = (x0, y0, W, H)
    corte_cheio = _corte_quadro(p, quadro_cheio)
    if corte_cheio <= corte_max + 1e-4:
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


def _fotos_por_linha(n):
    """Máximo de fotos numa linha: MAX_LINHAS, ou mais quando a página recebe mais fotos que MAX_LINHAS²."""
    return max(MAX_LINHAS, int(math.ceil(n / float(MAX_LINHAS))))


def _ordens_por_proporcao(proporcoes):
    """
    Ordens testadas ao montar linhas e colunas: a recebida, da mais vertical para a mais horizontal,
    o inverso e intercalada (vertical, horizontal, vertical...). Assim as linhas podem juntar fotos da
    mesma orientação ou misturá-las, o que preencher melhor a página.
    """
    n = len(proporcoes)
    crescente = sorted(range(n), key=lambda i: (proporcoes[i], i))
    intercalada = []
    a, b = 0, n - 1
    while a <= b:
        intercalada.append(crescente[a])
        a += 1
        if a <= b:
            intercalada.append(crescente[b])
            b -= 1
    vistas, ordens = set(), []
    for ordem in (list(range(n)), crescente, crescente[::-1], intercalada):
        chave = tuple(round(float(proporcoes[i]), 3) for i in ordem)
        if chave not in vistas:
            vistas.add(chave)
            ordens.append(ordem)
    return ordens


def _classe_orientacao(proporcao):
    """0 vertical, 1 quadrada, 2 horizontal, 3 panorâmica."""
    p = float(proporcao or 1.0)
    if p >= 1.85:
        return 3
    if p >= 1.05:
        return 2
    if p > 0.95:
        return 1
    return 0


def _distribuir_larguras_linha(proporcoes_linha, largura_util, altura_linha, corte_max=CORTE_MAXIMO):
    """
    Distribui as larguras das fotos dentro de uma linha de altura `altura_linha`
    tentando preencher `largura_util`:
    - Se o ajuste proporcional mantiver todas as fotos com corte <= corte_max,
      preenche 100% da largura da linha.
    - Se alguma foto for forçada além de corte_max, trava essa foto na sua
      proporção padrão (`w = altura_linha * p`) e expande as demais fotos da
      linha (até o limite seguro de corte_max) para absorver o espaço restante.
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
    if corte_uniforme <= corte_max + 1e-4:
        return [w * escala for w in larguras_nat], False

    # Quando estoura corte_max, identificamos fotos com proporção extrema na linha
    # para mantê-las na proporção padrão enquanto as demais se expandem até corte_max.
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

    # Tenta travar na proporção padrão a(s) foto(s) mais extrema(s) e expandir as vizinhas
    for candidato in ordem_discrepancia:
        travadas.add(candidato)
        largura_travada = sum(larguras_nat[i] for i in travadas)
        livres = [i for i in range(k) if i not in travadas]
        if not livres:
            break
        restante = largura_util - largura_travada
        soma_livres = sum(larguras_nat[i] for i in livres)
        if restante > 0 and soma_livres > 0:
            fator_livres = restante / soma_livres
            # Limita a expansão das fotos vizinhas a corte_max para não distorcê-las
            fator_clamped = min(corte_max, max(1.0 / max(1.0, corte_max), fator_livres))
            for i in range(k):
                larguras[i] = larguras_nat[i] if i in travadas else larguras_nat[i] * fator_clamped
            if abs(fator_livres - fator_clamped) <= 1e-4:
                soma_p = sum(larguras)
                if soma_p > largura_util + 1e-4:
                    red = largura_util / soma_p
                    larguras = [w * red for w in larguras]
                return larguras, True

    # Se todas precisarem de ajuste no mesmo sentido (ex.: linha inteira muito alta ou baixa),
    # expande/contrai até o limite seguro corte_max sem jamais ultrapassar largura_util
    fator_seguro = min(corte_max, max(1.0 / max(1.0, corte_max), escala))
    larguras_finais = [w * fator_seguro for w in larguras_nat]
    soma_final = sum(larguras_finais)
    if soma_final > largura_util + 1e-4:
        red = largura_util / soma_final
        larguras_finais = [w * red for w in larguras_finais]
    return larguras_finais, True


def _layout_linhas(proporcoes, area, espaco, linhas, corte_max=CORTE_MAXIMO):
    """
    Fotos em linhas tentando preencher 100% da área útil da página.
    - Se a escala vertical global ficar dentro de corte_max (22%), fecha
      100% da página (largura e altura totais).
    - Se alguma linha distorcer além de corte_max, essa linha volta para a
      proporção padrão das suas fotos e as demais linhas da página se expandem
      para ocupar a altura restante.
    """
    x0, y0, W, H = area
    n_linhas = len(linhas)
    if n_linhas == 0 or W <= 0 or H <= 0:
        return None
    if len(proporcoes) == 1 and n_linhas == 1:
        q, corte, vazio, _tipo = quadros_1_foto(proporcoes[0], area, corte_max=corte_max)[0]
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

    if corte_global <= corte_max + 1e-4:
        # Preenche 100% da página dentro do limite configurado
        alturas = [h * s for h in alturas_nat]
    else:
        # Quando o esticamento uniforme estoura corte_max:
        # Se houver mais de 1 linha, preserva na proporção padrão a linha com fotos
        # mais sensíveis (ou de menor folga) e expande as outras linhas até corte_max.
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
                fator_livres = min(corte_max, max(1.0 / max(1.0, corte_max), fator_livres))
                for r in livres:
                    alturas[r] = alturas_nat[r] * fator_livres
                # Se ainda sobrar espaço na página e a linha fixa puder absorver até corte_max:
                sobra = disponivel_h - sum(alturas)
                if abs(sobra) > 1e-3:
                    for r in fixas:
                        alvo_r = alturas[r] + sobra / len(fixas)
                        fator_r = alvo_r / alturas_nat[r]
                        if max(fator_r, 1.0 / max(1e-6, fator_r)) <= corte_max + 1e-4:
                            alturas[r] = alvo_r
                        else:
                            # Mantém proporção padrão na foto que estouraria o limite!
                            alturas[r] = alturas_nat[r]
        else:
            # 1 única linha com várias fotos:
            if s < 1.0 / max(1.0, corte_max):
                alturas = [disponivel_h]
            else:
                alturas = [alturas_nat[0] * corte_max if s > corte_max else alturas_nat[0]]

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
        larguras, _padrao = _distribuir_larguras_linha(ps, w_util, h, corte_max=corte_max)
        largura_linha = sum(larguras) + espaco * (len(ps) - 1)
        x = x0 + (W - largura_linha) / 2.0
        for p, w in zip(ps, larguras):
            q = (x, y, w, h)
            c_foto = _corte_quadro(p, q)
            # Proteção final individual: se por geometria extrema este quadro ainda
            # passou de corte_max + 0.02, crava na proporção padrão dentro da célula
            if c_foto > corte_max + 0.02:
                q = caixa_proporcao_padrao(p, q)
                c_foto = 1.0
            quadros.append(q)
            usado += q[2] * q[3]
            maior_corte = max(maior_corte, c_foto)
            x += w + espaco
        y += h + espaco

    vazio = max(0.0, 1.0 - usado / max(1.0, W * H))
    return quadros, maior_corte, vazio


def _layouts_assimetricos(proporcoes, area, espaco, corte_max=CORTE_MAXIMO):
    """
    Gera composições dinâmicas assimétricas (1 foto destaque + coluna/linha com as
    demais fotos) calculadas exatamente pelas proporções das fotos:
    - Destaque à Esquerda + Pilha à Direita
    - Pilha à Esquerda + Destaque à Direita
    - Destaque no Topo + Linha na Base
    - Linha no Topo + Destaque na Base
    Se alguma foto exceder corte_max (22%), usa sua proporção padrão e expande
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

            if max(fator_w, 1.0 / max(1e-6, fator_w)) <= corte_max + 1e-4:
                w_hero = w_hero_nat * fator_w
                w_pilha = w_pilha_nat * fator_w
                h_hero = H
            else:
                # Se estourar corte_max, trava a foto de destaque na proporção padrão e expande a pilha
                # (ou vice-versa) para ocupar o espaço restante da página!
                w_hero = min(w_disp * 0.68, max(w_disp * 0.25, w_hero_nat))
                w_pilha = w_disp - w_hero
                corte_pilha = max((w_pilha / max(1e-6, w_pilha_nat)), (w_pilha_nat / max(1e-6, w_pilha)))
                if corte_pilha > corte_max:
                    w_pilha = min(
                        w_disp * 0.72,
                        w_pilha_nat * (corte_max if w_pilha > w_pilha_nat else 1.0 / max(1.0, corte_max)),
                    )
                    w_hero = min(w_disp - w_pilha, w_hero_nat * corte_max)
                if w_hero + w_pilha > w_disp + 1e-4:
                    red_w = w_disp / (w_hero + w_pilha)
                    w_hero *= red_w
                    w_pilha *= red_w
                h_hero = min(H, w_hero / p_hero) if abs(w_hero - w_hero_nat) < 1e-3 else H

            for lado_hero in ("esq", "dir"):
                total_w = min(W, w_hero + espaco + w_pilha)
                inicio_x = x0 + max(0.0, (W - total_w) / 2.0)
                if lado_hero == "esq":
                    x_hero = inicio_x
                    x_pilha = inicio_x + w_hero + espaco
                else:
                    x_pilha = inicio_x
                    x_hero = inicio_x + w_pilha + espaco

                q_hero = (x_hero, y0 + (H - h_hero) / 2.0, w_hero, h_hero)
                if _corte_quadro(p_hero, q_hero) > corte_max + 0.02:
                    q_hero = caixa_proporcao_padrao(p_hero, (x_hero, y0, w_hero, H))

                # Alturas da pilha à direita/esquerda
                alturas_p_nat = [w_pilha / p for p in p_outros]
                soma_hp = sum(alturas_p_nat)
                fator_hp = h_pilha_disp / max(1e-6, soma_hp)
                fator_hp_seguro = min(corte_max, max(1.0 / max(1.0, corte_max), fator_hp))
                alturas_p = [h * fator_hp_seguro for h in alturas_p_nat]
                soma_alturas_p = sum(alturas_p)
                if soma_alturas_p > h_pilha_disp + 1e-4:
                    red_hp = h_pilha_disp / soma_alturas_p
                    alturas_p = [h * red_hp for h in alturas_p]
                total_hp = sum(alturas_p) + espaco * (k - 1)
                yp = y0 + max(0.0, (H - total_hp) / 2.0)

                quadros_mapa = {idx_hero: q_hero}
                for i_outro, p_o, hp in zip(outros, p_outros, alturas_p):
                    qp = (x_pilha, yp, w_pilha, hp)
                    if _corte_quadro(p_o, qp) > corte_max + 0.02:
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
                custo = _custo(quadros, props_ordem, area, maior_corte, vazio, corte_max=corte_max) + desordem
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

            if max(fator_h, 1.0 / max(1e-6, fator_h)) <= corte_max + 1e-4:
                h_hero = h_hero_nat * fator_h
                h_linha = h_linha_nat * fator_h
                w_hero = W
            else:
                h_hero = min(h_disp * 0.68, max(h_disp * 0.25, h_hero_nat))
                h_linha = h_disp - h_hero
                corte_linha = max((h_linha / max(1e-6, h_linha_nat)), (h_linha_nat / max(1e-6, h_linha)))
                if corte_linha > corte_max:
                    h_linha = min(
                        h_disp * 0.72,
                        h_linha_nat * (corte_max if h_linha > h_linha_nat else 1.0 / max(1.0, corte_max)),
                    )
                    h_hero = min(h_disp - h_linha, h_hero_nat * corte_max)
                if h_hero + h_linha > h_disp + 1e-4:
                    red_h = h_disp / (h_hero + h_linha)
                    h_hero *= red_h
                    h_linha *= red_h
                w_hero = min(W, h_hero * p_hero) if abs(h_hero - h_hero_nat) < 1e-3 else W

            for pos_hero in ("topo", "base"):
                total_h = min(H, h_hero + espaco + h_linha)
                inicio_y = y0 + max(0.0, (H - total_h) / 2.0)
                if pos_hero == "topo":
                    y_hero = inicio_y
                    y_linha = inicio_y + h_hero + espaco
                else:
                    y_linha = inicio_y
                    y_hero = inicio_y + h_linha + espaco

                q_hero = (x0 + (W - w_hero) / 2.0, y_hero, w_hero, h_hero)
                if _corte_quadro(p_hero, q_hero) > corte_max + 0.02:
                    q_hero = caixa_proporcao_padrao(p_hero, (x0, y_hero, W, h_hero))

                larguras_l, _padrao = _distribuir_larguras_linha(p_outros, w_linha_disp, h_linha, corte_max=corte_max)
                total_wl = min(W, sum(larguras_l) + espaco * (k - 1))
                xl = x0 + max(0.0, (W - total_wl) / 2.0)

                quadros_mapa = {idx_hero: q_hero}
                for i_outro, p_o, wl in zip(outros, p_outros, larguras_l):
                    ql = (xl, y_linha, wl, h_linha)
                    if _corte_quadro(p_o, ql) > corte_max + 0.02:
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
                custo = _custo(quadros, props_ordem, area, maior_corte, vazio, corte_max=corte_max) + desordem
                opcoes.append((custo, quadros, None, ordem))

    return opcoes


def _custo(quadros, proporcoes, area, corte, vazio, corte_max=CORTE_MAXIMO):
    _x, _y, W, H = area
    # Penaliza fortemente qualquer corte que tente passar de corte_max
    excesso_corte = max(0.0, corte - corte_max)
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


def _layouts_sem_corte(proporcoes, area, espaco, priorizar=False):
    """
    Layouts que preservam 100% da proporção original de cada foto, mas
    expandindo cada linha ao máximo possível dentro da página.
    """
    n = len(proporcoes)
    if not n:
        return []
    x0, y0, W, H = area
    saida = []
    for linhas in _composicoes(n, min(MAX_LINHAS, n), _fotos_por_linha(n)):
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
        base = 0.02 if priorizar else 0.35
        custo = base + PESO_VAZIO * (0.25 if priorizar else 0.55) * vazio + 0.06 * (len(linhas) - 1)
        saida.append((custo, quadros, "__sem_corte__", list(range(n))))
    return saida


def _caixa_grupo_rosto(foto):
    """
    Devolve (qtd, gx0, gy0, gx1, gy1, lista_rostos) normalizados (0..1) para a foto,
    considerando todos os rostos detectados e a margem de segurança de cada pessoa
    (incluindo topo da cabeça/capelo). Para fotos verticais (retrato) sem rosto detectado,
    projeta uma caixa de proteção vertical do sujeito para impedir que retratos sejam
    esticados em faixas horizontais que cortem a cabeça.
    """
    rosto = foto.get("rosto") if isinstance(foto, dict) else None
    if not rosto or not isinstance(rosto, dict):
        prop = max(0.1, float(foto.get("proporcao") or 1.5)) if isinstance(foto, dict) else 1.5
        if prop < 0.92:
            sintetico = [{"cx": 0.5, "cy": 0.30, "x0": 0.18, "y0": 0.05, "x1": 0.82, "y1": 0.85}]
            return 1, 0.18, 0.05, 0.82, 0.85, sintetico
        return 0, 0.0, 0.0, 1.0, 1.0, []
    rx = float(rosto.get("cx", 0.5))
    ry = float(rosto.get("cy", 0.45))
    rh = max(0.08, float(rosto.get("h", 0.14)))
    gx0 = max(0.0, min(1.0, min(float(rosto.get("x0", 1.0)), max(0.0, rx - max(0.10, rh * 0.72)))))
    gy0 = max(0.0, min(1.0, min(float(rosto.get("y0", 1.0)), max(0.0, ry - max(0.14, rh * 1.02)))))
    gx1 = max(gx0 + 0.04, min(1.0, max(float(rosto.get("x1", 0.0)), min(1.0, rx + max(0.10, rh * 0.72)))))
    gy1 = max(gy0 + 0.04, min(1.0, max(float(rosto.get("y1", 0.0)), min(1.0, ry + max(0.14, rh * 1.02)))))
    lista = rosto.get("rostos") or [{"cx": rx, "cy": ry, "x0": gx0, "y0": gy0, "x1": gx1, "y1": gy1}]
    qtd = max(1, int(rosto.get("qtd") or len(lista) or 1))
    return qtd, gx0, gy0, gx1, gy1, lista


def _risco_cortar_pessoas(foto, quadro):
    """
    Verifica se a proporção do quadro `quadro = (qx, qy, qw, qh)` cortaria alguma
    pessoa detectada na foto (horizontalmente ou verticalmente).
    Devolve (excesso_x, excesso_y, penalidade).
    """
    qtd, gx0, gy0, gx1, gy1, _lista = _caixa_grupo_rosto(foto)
    if qtd <= 0:
        return 0.0, 0.0, 0.0
    _qx, _qy, qw, qh = quadro
    if qw <= 1e-4 or qh <= 1e-4:
        return 0.0, 0.0, 0.0
    prop_foto = max(0.1, float(foto.get("proporcao") or 1.5))
    aspecto_q = qw / float(qh)

    # Se o quadro for mais estreito que a foto, a fração horizontal visível da foto é (aspecto_q / prop_foto)
    frac_vis_x = min(1.0, aspecto_q / prop_foto)
    # Se o quadro for mais baixo/largo que a foto, a fração vertical visível da foto é (prop_foto / aspecto_q)
    frac_vis_y = min(1.0, prop_foto / aspecto_q)

    span_x = gx1 - gx0
    span_y = gy1 - gy0

    excesso_x = max(0.0, span_x - frac_vis_x)
    excesso_y = max(0.0, span_y - frac_vis_y)
    if excesso_x <= 0.002 and excesso_y <= 0.002:
        return 0.0, 0.0, 0.0

    fator_grupo = 1.0 + min(2.5, (qtd - 1) * 0.45)
    penalidade = (excesso_x * 28.0 + excesso_y * 32.0) * fator_grupo
    return excesso_x, excesso_y, penalidade


def caixa_segura_pessoas(foto, area):
    """
    Ajusta a caixa `area = (x0, y0, W, H)` para o menor recorte possível que
    garanta que 100% de todas as pessoas detectadas em `foto` fiquem visíveis
    dentro do quadro sem cortar ninguém.
    """
    x0, y0, W, H = area
    if W <= 0 or H <= 0:
        return area
    qtd, gx0, gy0, gx1, gy1, _lista = _caixa_grupo_rosto(foto)
    if qtd <= 0:
        return area
    p = max(0.1, float(foto.get("proporcao") or 1.5))
    aspecto_a = W / float(H)
    span_x = min(1.0, max(0.15, (gx1 - gx0) * 1.03))
    span_y = min(1.0, max(0.15, (gy1 - gy0) * 1.03))

    # Proporção mínima permitida para não cortar pessoas na horizontal: p * span_x
    aspecto_min = p * span_x
    # Proporção máxima permitida para não cortar cabeças/pessoas na vertical: p / span_y
    aspecto_max = p / span_y

    if aspecto_a < aspecto_min - 1e-4:
        # Quadro estreito demais para o grupo de pessoas -> ajusta altura para respeitar aspecto_min
        h_novo = min(float(H), float(W) / max(0.1, aspecto_min))
        return (x0, y0 + (H - h_novo) / 2.0, float(W), h_novo)
    if aspecto_a > aspecto_max + 1e-4:
        # Quadro baixo/largo demais para a altura das pessoas -> ajusta largura para respeitar aspecto_max
        w_novo = min(float(W), float(H) * aspecto_max)
        return (x0 + (W - w_novo) / 2.0, y0, w_novo, float(H))
    return area


def _proteger_espacos_contra_corte_pessoas(espacos, fotos, produto, cfg):
    """
    Percorre os espaços gerados em uma opção de layout e garante que nenhum espaço
    corte cabeças/pessoas ou estique fotos além do limite seguro.
    - Espaços sangrados (Meio Termo, Transpasse Sangrado, Sangria Total) são preservados
      intactos na sangria SE a foto couber sem cortar pessoas e dentro do corte máximo;
      caso contrário, recebem penalidade alta (para o motor descartar o candidato ruim)
      e têm sua caixa ajustada para nunca decepar cabeças.
    """
    if not cfg or not cfg.get("reconhecer_rostos", True) or not cfg.get("nao_cortar_pessoas", True):
        return espacos, 0.0
    lw, lh = lamina_mm(produto)
    corte_max = _limite_corte_cfg(cfg)
    mapa_fotos = {f.get("caminho"): f for f in (fotos or []) if isinstance(f, dict) and f.get("caminho")}
    novos = []
    penalidade_total = 0.0
    for e in espacos:
        f_info = mapa_fotos.get(e.get("foto"))
        if not f_info:
            novos.append(e)
            continue
        quadro_mm = (e["x"] * lw, e["y"] * lh, e["w"] * lw, e["h"] * lh)
        prop_f = max(0.1, float(f_info.get("proporcao") or 1.5))
        corte_q = _corte_quadro(prop_f, quadro_mm)
        exc_x, exc_y, pen = _risco_cortar_pessoas(f_info, quadro_mm)
        eh_sangrado = (
            float(e.get("x", 0.0)) <= 0.006
            or float(e.get("y", 0.0)) <= 0.006
            or (float(e.get("x", 0.0)) + float(e.get("w", 0.0))) >= 0.994
            or (float(e.get("y", 0.0)) + float(e.get("h", 0.0))) >= 0.994
        )
        if eh_sangrado:
            if exc_x <= 0.003 and exc_y <= 0.003 and corte_q <= corte_max * 1.08:
                novos.append(e)
                continue
            # Candidato sangrado cortaria pessoa ou esticaria a foto além do limite:
            # penaliza fortemente para não vencer layouts compatíveis e corrige a caixa
            penalidade_total += pen * 1.8 + max(0.0, corte_q - corte_max) * 14.0
            quadro_base = caixa_proporcao_padrao(prop_f, quadro_mm) if corte_q > corte_max * 1.12 else quadro_mm
            quadro_seguro = caixa_segura_pessoas(f_info, quadro_base)
            # Se tocava a borda esquerda ou direita da sangria, ancora naquela borda
            if float(e.get("x", 0.0)) <= 0.006 and (float(e.get("x", 0.0)) + float(e.get("w", 0.0))) < 0.994:
                quadro_seguro = (0.0, quadro_seguro[1], quadro_seguro[2], quadro_seguro[3])
            elif (float(e.get("x", 0.0)) + float(e.get("w", 0.0))) >= 0.994 and float(e.get("x", 0.0)) > 0.006:
                quadro_seguro = (lw - quadro_seguro[2], quadro_seguro[1], quadro_seguro[2], quadro_seguro[3])
            novos.append(_espaco(produto, quadro_seguro, e.get("foto"), e.get("edicao")))
            continue

        if exc_x > 0.003 or exc_y > 0.003:
            quadro_seguro = caixa_segura_pessoas(f_info, quadro_mm)
            e_seg = _espaco(produto, quadro_seguro, e.get("foto"), e.get("edicao"))
            novos.append(e_seg)
            penalidade_total += min(2.4, pen * 0.25)
        else:
            novos.append(e)
    return novos, penalidade_total


def _custo_rosto_na_dobra(foto, quadro, lw):
    """
    Calcula o risco/custo de QUALQUER rosto detectado na foto cair sobre a linha de
    dobra central da lâmina (meio = lw / 2.0) e devolve (penalidade, dobra_x).
    """
    qx, _qy, qw, qh = quadro
    meio = lw / 2.0
    if qw <= 1e-4 or not (qx < meio - 1.5 and qx + qw > meio + 1.5):
        return 0.0, None
    dobra_x = (meio - qx) / float(qw)
    qtd, gx0, _gy0, gx1, _gy1, lista_rostos = _caixa_grupo_rosto(foto)
    if qtd <= 0:
        return 0.0, round(dobra_x, 4)
    prop_foto = max(0.1, float(foto.get("proporcao") or 1.5))
    aspecto_q = qw / max(1e-4, float(qh))
    # Folga horizontal disponível sem cortar nenhuma pessoa das pontas (gx0..gx1)
    frac_vis_x = min(1.0, aspecto_q / prop_foto) if prop_foto > aspecto_q else 1.0
    folga_h = max(0.0, frac_vis_x - (gx1 - gx0)) if prop_foto > aspecto_q else 0.0
    menor_dist = min(abs(float(r.get("cx", 0.5)) - dobra_x) for r in lista_rostos)
    dist_efetiva = menor_dist + folga_h * 0.40
    if dist_efetiva < 0.14:
        return (0.14 - dist_efetiva) * (15.0 + min(10.0, (qtd - 1) * 3.5)), round(dobra_x, 4)
    return 0.0, round(dobra_x, 4)


def _ajuste_estilo_transpasse(foto, proporcao):
    """
    Custo extra de uma foto atravessar a dobra: só compensa para foto de destaque ou panorâmica,
    ou quando a grade não consegue preencher as páginas.
    """
    if foto.get("destaque"):
        return 0.25
    if proporcao >= 1.85:
        return 0.40
    return 0.85


def _layouts_transpasse_central(fotos, produto, cfg):
    """
    Gera vários layouts que atravessam o meio da lâmina (x = 50%):
    - Variação SANGRADA: A foto em destaque atravessa a dobra e vai até o limite da SANGRIA (passando da Safe Zone),
      enquanto as fotos coadjuvantes permanecem na Safe Zone.
    - Variação Safe Zone: Transpasse 60/40 e 70/30 dentro da margem.
    - Faixa Panorâmica Sangrada atravessando o meio no topo.
    Todos desviam automaticamente rostos detectados da linha de dobra central.
    """
    if not cfg.get("permitir_transpasse", True):
        return []
    n = len(fotos)
    if n < 2 or n > 9 or produto.get("paginas_por_lamina", 1) < 2:
        return []
    # A foto que atravessa ocupa uma página; as demais ficam todas na outra
    if n - 1 > _limites_pagina(cfg, produto)[1]:
        return []
    W, H = lamina_mm(produto)
    b = float(produto.get("sangria_mm", 3.0))
    seg = float(produto.get("seguranca_mm", 8.0))
    margem = b + max(seg, float(cfg["margem_mm"]))
    intervalo = max(0.0, float(cfg["espacamento_mm"]))
    corte_max = _limite_corte_cfg(cfg)
    area_w = W - 2 * margem
    area_h = H - 2 * margem
    if area_w <= intervalo * 2 or area_h <= intervalo * 2:
        return []

    saida = []

    # Candidatos a foto principal que atravessa o meio (prioriza fotos de destaque ou horizontais)
    candidatos_hero = sorted(
        range(n),
        key=lambda i: (bool(fotos[i].get("destaque")), float(fotos[i].get("proporcao") or 1.0)),
        reverse=True,
    )[:min(n, 3)]

    def montar(idx_hero, q_hero, area_resto, etiqueta, extra):
        f_hero = fotos[idx_hero]
        indices_rest = [i for i in range(n) if i != idx_hero]
        props_rest = [max(0.1, float(fotos[i]["proporcao"])) for i in indices_rest]
        opcoes_r = _opcoes_pagina(props_rest, area_resto, intervalo, 1, corte_max)
        if not opcoes_r or not opcoes_r[0][1]:
            return
        _cr, quadros_r, _mod_r, ordem_r = opcoes_r[0]
        quadros_mapa = {idx_hero: q_hero}
        for qr, idx_rel in zip(quadros_r, ordem_r):
            quadros_mapa[indices_rest[idx_rel]] = qr
        pen_rosto, _dobra_h = _custo_rosto_na_dobra(f_hero, q_hero, W)
        espacos = []
        for i in range(n):
            _pen, dobra_rel = _custo_rosto_na_dobra(fotos[i], quadros_mapa[i], W)
            ed = {"dobra_x": dobra_rel} if dobra_rel is not None else {}
            espacos.append(_espaco(produto, quadros_mapa[i], fotos[i]["caminho"], ed))
        saida.append({
            "custo": _ajuste_estilo_transpasse(f_hero, max(0.15, float(f_hero["proporcao"]))) + extra + pen_rosto,
            "espacos": espacos,
            "sobreposicoes": [],
            "modelo": None,
            "tipo_layout": etiqueta,
        })

    # 1. Foto em destaque atravessando a dobra (sangrada ou dentro da Safe Zone). A largura dela sai da
    #    sua proporção (a largura natural na altura disponível), além de frações vizinhas entre 52% e 80%.
    for idx_hero in candidatos_hero:
        f_hero = fotos[idx_hero]
        p_hero = max(0.15, float(f_hero["proporcao"]))
        fracoes = sorted({
            round(min(0.80, max(0.52, f)), 2)
            for f in (0.55, 0.62, 0.70, 0.78, H * p_hero / W, area_h * p_hero / (area_w - intervalo))
        })
        for fracao_hero in fracoes:
            etiqueta_tipo = "transpasse_60_40" if fracao_hero < 0.65 else "transpasse_70_30"

            # A) Variação SANGRADA: a foto em destaque vai até a sangria; as demais ficam na Safe Zone
            w_hero_sang = W * fracao_hero
            w_resto_sang = W - w_hero_sang - intervalo - margem
            if w_resto_sang > 20.0:
                for lado_hero in ("esq", "dir"):
                    if lado_hero == "esq":
                        x_hero_s, x_resto_s = 0.0, w_hero_sang + intervalo
                    else:
                        x_hero_s, x_resto_s = W - w_hero_sang, margem
                    q_hero_sang = (x_hero_s, 0.0, w_hero_sang, H)
                    if (
                        _corte_quadro(p_hero, q_hero_sang) > corte_max * 1.08
                        or _risco_cortar_pessoas(f_hero, q_hero_sang)[2] > 0.0
                    ):
                        continue
                    montar(idx_hero, q_hero_sang, (x_resto_s, margem, w_resto_sang, area_h), etiqueta_tipo, 0.0)

            # B) Variação Safe Zone
            w_hero = (area_w - intervalo) * fracao_hero
            w_resto = (area_w - intervalo) - w_hero
            if w_hero <= 20 or w_resto <= 20:
                continue
            for lado_hero in ("esq", "dir"):
                if lado_hero == "esq":
                    x_hero, x_resto = margem, margem + w_hero + intervalo
                else:
                    x_resto, x_hero = margem, margem + w_resto + intervalo
                area_hero = (x_hero, margem, w_hero, area_h)
                q_hero = area_hero
                if _corte_quadro(p_hero, q_hero) > corte_max or _risco_cortar_pessoas(f_hero, q_hero)[2] > 0.0:
                    q_hero = caixa_proporcao_padrao(p_hero, area_hero)
                    if lado_hero == "esq":
                        q_hero = (margem, q_hero[1], q_hero[2], q_hero[3])
                    else:
                        q_hero = (W - margem - q_hero[2], q_hero[1], q_hero[2], q_hero[3])
                meio = W / 2.0
                if not (q_hero[0] < meio - 4.0 and q_hero[0] + q_hero[2] > meio + 4.0):
                    continue
                montar(idx_hero, q_hero, (x_resto, margem, w_resto, area_h), etiqueta_tipo, 0.05)

    # 2. Layout Panorâmico Horizontal Sangrado atravessando o meio no topo (APENAS para fotos realmente panorâmicas)
    indice_hero = candidatos_hero[0]
    f_hero_pan = fotos[indice_hero]
    p_hero = max(0.2, float(f_hero_pan["proporcao"]))
    if p_hero >= 1.90:
        hero_h_ideal = W / p_hero
        hero_h = min(H * 0.56, max(H * 0.34, hero_h_ideal))
        quadro_hero = (0.0, 0.0, W, hero_h)
        corte_pan = _corte_quadro(p_hero, quadro_hero)
        _ex_px, _ex_py, pen_pessoas_pan = _risco_cortar_pessoas(f_hero_pan, quadro_hero)
        if hero_h > 0 and corte_pan <= corte_max * 1.08 and pen_pessoas_pan <= 0.0:
            inferior_y = max(margem, hero_h + intervalo)
            inferior_h = H - margem - inferior_y
            if inferior_h > 15:
                indices_restantes = [i for i in range(n) if i != indice_hero]
                props_restantes = [max(0.1, float(fotos[i]["proporcao"])) for i in indices_restantes]
                area_inferior = (margem, inferior_y, area_w, inferior_h)
                melhores_inf = _opcoes_pagina(props_restantes, area_inferior, intervalo, 1, corte_max)
                if melhores_inf and melhores_inf[0][1]:
                    _c_inf, quadros_inf, _mod_inf, ordem_inf = melhores_inf[0]
                    quadros_mapa = {indice_hero: quadro_hero}
                    for q_inf, idx_rel in zip(quadros_inf, ordem_inf):
                        quadros_mapa[indices_restantes[idx_rel]] = q_inf
                    quadros = [quadros_mapa[i] for i in range(n)]
                    pen_rosto, _dobra = _custo_rosto_na_dobra(f_hero_pan, quadro_hero, W)
                    bonus_pan = -0.22 if f_hero_pan.get("destaque") else -0.06
                    custo = 0.30 + bonus_pan + pen_rosto
                    espacos = []
                    for foto_item, quadro_item in zip(fotos, quadros):
                        _pen, dobra_rel = _custo_rosto_na_dobra(foto_item, quadro_item, W)
                        ed = {"dobra_x": dobra_rel} if dobra_rel is not None else {}
                        espacos.append(_espaco(produto, quadro_item, foto_item["caminho"], ed))
                    saida.append({
                        "custo": custo,
                        "espacos": espacos,
                        "sobreposicoes": [],
                        "modelo": None,
                        "tipo_layout": "panoramico_meio",
                    })

    saida.sort(key=lambda op: op["custo"])
    return saida


def _layouts_meio_termo(fotos, produto, cfg):
    """
    Layouts Híbridos ("Meio Termo"):
    - Uma página (esquerda ou direita) com 1 foto em destaque sangrada (passando das linhas da Safe Zone
      até a sangria, respeitando a proporção e a altura das pessoas para nunca cortar cabeças)
      e a outra página com 1, 2, 3 ou mais fotos rigorosamente dentro das linhas da Safe Zone.
    """
    if not cfg.get("permitir_meio_termo", True):
        return []
    n = len(fotos)
    if n < 2 or n > 9 or produto.get("paginas_por_lamina", 1) < 2:
        return []
    # Uma página com a foto sangrada; as demais ficam todas na outra
    if n - 1 > _limites_pagina(cfg, produto)[1]:
        return []
    lw, _lh = lamina_mm(produto)
    margem = float(cfg["margem_mm"])
    intervalo = max(0.0, float(cfg["espacamento_mm"]))
    corte_max = _limite_corte_cfg(cfg)
    saida = []

    for pagina_sangrada in (0, 1):
        pagina_safe = 1 - pagina_sangrada
        area_sangrada = area_pagina_mm(produto, pagina_sangrada)
        area_safe = area_util_pagina_mm(produto, pagina_safe, margem, respeitar_seguranca=True)
        etiqueta = "meio_termo_esq" if pagina_sangrada == 0 else "meio_termo_dir"
        aspecto_pag = area_sangrada[2] / max(1.0, float(area_sangrada[3]))

        # Escolhe as melhores candidatas para ocupar a página sangrada
        candidatos_hero = sorted(
            range(n),
            key=lambda i: (
                not bool(fotos[i].get("destaque")),
                abs(math.log(max(0.15, float(fotos[i]["proporcao"])) / max(0.15, aspecto_pag))),
            ),
        )[:min(n, 3)]

        for idx_hero in candidatos_hero:
            f_hero = fotos[idx_hero]
            p_hero = max(0.15, float(f_hero["proporcao"]))
            corte_full = _corte_quadro(p_hero, area_sangrada)
            _ex_x, _ex_y, pen_pessoas_full = _risco_cortar_pessoas(f_hero, area_sangrada)

            if corte_full <= corte_max * 1.08 and pen_pessoas_full <= 0.0:
                q_hero = area_sangrada
            else:
                # Se a foto for vertical (retrato) e a página for horizontal, sangra no topo, base e lateral externa
                # mantendo a proporção segura para nunca esticar a foto nem cortar a cabeça
                sx, sy, sw, sh = area_sangrada
                w_seguro = min(sw, sh * p_hero * min(corte_max, 1.15))
                if w_seguro < sw * 0.42:
                    continue
                x_seguro = 0.0 if pagina_sangrada == 0 else (lw - w_seguro)
                q_cand = (x_seguro, sy, w_seguro, sh)
                if _corte_quadro(p_hero, q_cand) > corte_max * 1.08 or _risco_cortar_pessoas(f_hero, q_cand)[2] > 0.0:
                    continue
                q_hero = q_cand

            eh_destaque = bool(f_hero.get("destaque"))

            indices_safe = [i for i in range(n) if i != idx_hero]
            props_safe = [max(0.1, float(fotos[i]["proporcao"])) for i in indices_safe]
            opcoes_safe = _opcoes_pagina(props_safe, area_safe, intervalo, 2, corte_max)
            if not opcoes_safe:
                continue

            for _c_safe, quadros_safe, _mod_s, ordem_safe in opcoes_safe[:2]:
                quadros_mapa = {idx_hero: q_hero}
                for qs, idx_rel in zip(quadros_safe, ordem_safe):
                    quadros_mapa[indices_safe[idx_rel]] = qs

                if pagina_sangrada == 0:
                    ordem_final = [idx_hero] + [indices_safe[idx_rel] for idx_rel in ordem_safe]
                else:
                    ordem_final = [indices_safe[idx_rel] for idx_rel in ordem_safe] + [idx_hero]

                espacos = [_espaco(produto, quadros_mapa[i], fotos[i]["caminho"]) for i in ordem_final]
                saida.append({
                    "custo": 0.25 if eh_destaque else 0.60,
                    "espacos": espacos,
                    "sobreposicoes": [],
                    "modelo": None,
                    "tipo_layout": etiqueta,
                })

    saida.sort(key=lambda op: op["custo"])
    return saida


def _layouts_preenchimento_maximo(fotos, produto, cfg):
    """
    Layouts de Preenchimento Máximo ("Sangria Total / Sem Margem"):
    - Variações opcionais que expandem as fotos até o limite da página/lâmina (incluindo sangria externa),
      mas somente quando a proporção das fotos é compatível e não corta cabeças/pessoas.
    """
    if not cfg.get("permitir_sangria_total", True):
        return []
    n = len(fotos)
    if n == 0:
        return []
    lw, lh = lamina_mm(produto)
    paginas = produto.get("paginas_por_lamina", 1)
    intervalo = max(0.0, float(cfg["espacamento_mm"]))
    max_pagina = _limites_pagina(cfg, produto)[1]
    corte_max = _limite_corte_cfg(cfg)
    saida = []

    if n == 1:
        f0 = fotos[0]
        p0 = max(0.15, float(f0["proporcao"]))
        eh_dest0 = bool(f0.get("destaque"))
        q_total = (0.0, 0.0, lw, lh)
        corte_tot = _corte_quadro(p0, q_total)
        # Só permite que 1 única foto ocupe a lâmina inteira de ponta a ponta se for panorâmica compatível e não cortar pessoas
        if p0 >= 1.85 and corte_tot <= corte_max * 1.08 and _risco_cortar_pessoas(f0, q_total)[2] <= 0.0:
            saida.append({
                "custo": 0.18 if eh_dest0 else 0.55,
                "espacos": [_espaco(produto, q_total, f0["caminho"])],
                "sobreposicoes": [],
                "modelo": None,
                "tipo_layout": "sangria_total",
            })
        if paginas >= 2:
            for p_idx, etq in ((0, "meio_termo_esq"), (1, "meio_termo_dir")):
                area_p = area_pagina_mm(produto, p_idx)
                corte_p = _corte_quadro(p0, area_p)
                if corte_p <= corte_max * 1.08 and _risco_cortar_pessoas(f0, area_p)[2] <= 0.0:
                    saida.append({
                        "custo": 0.28 if eh_dest0 else 0.68,
                        "espacos": [_espaco(produto, area_p, f0["caminho"])],
                        "sobreposicoes": [],
                        "modelo": None,
                        "tipo_layout": etq,
                    })
        return saida

    # Divisões página a página ocupando cada página até a sangria (apenas quando não estoura o limite de corte nem corta pessoas)
    divisoes = [(n,)] if paginas == 1 else [(k, n - k) for k in range(1, n)]
    for divisao in divisoes:
        if any(k > max_pagina for k in divisao):
            continue
        por_pagina = []
        inicio = 0
        viavel = True
        for pagina, k in enumerate(divisao):
            grupo = fotos[inicio:inicio + k]
            inicio += k
            px, py, pw, ph = area_pagina_mm(produto, pagina)
            if paginas > 1 and intervalo > 0:
                if pagina == 0:
                    area_p = (px, py, max(10.0, pw - intervalo / 2.0), ph)
                else:
                    area_p = (px + intervalo / 2.0, py, max(10.0, pw - intervalo / 2.0), ph)
            else:
                area_p = (px, py, pw, ph)
            ops_p = _opcoes_pagina([f["proporcao"] for f in grupo], area_p, intervalo, 2, corte_max * 1.08)
            ops_validas = []
            for op_cand in ops_p:
                _c_cand, quadros_cand, _m_cand, ord_cand = op_cand
                if _m_cand == "__sem_corte__":
                    continue
                if any(
                    _corte_quadro(grupo[idx_f]["proporcao"], q_c) > corte_max * 1.08
                    or _risco_cortar_pessoas(grupo[idx_f], q_c)[2] > 0.0
                    for q_c, idx_f in zip(quadros_cand, ord_cand)
                ):
                    continue
                ops_validas.append(op_cand)
            if not ops_validas:
                viavel = False
                break
            por_pagina.append((grupo, ops_validas))
        if not viavel:
            continue

        for combinacao in itertools.product(*[p[1] for p in por_pagina]):
            espacos = []
            for (grupo, _ops), (_c, quadros, _mod, ordem) in zip(por_pagina, combinacao):
                for quadro, indice in zip(quadros, ordem):
                    espacos.append(_espaco(produto, quadro, grupo[indice]["caminho"]))
            saida.append({
                "custo": 0.45,
                "espacos": espacos,
                "sobreposicoes": [],
                "modelo": None,
                "tipo_layout": "sangria_total",
            })

    saida.sort(key=lambda op: op["custo"])
    return saida


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


def layouts_pagina(proporcoes, area, espaco, modelos_pagina=(), produto=None, pagina=0, maximo=5, corte_max=CORTE_MAXIMO):
    """
    Opções para uma página: [(custo, quadros em mm, modelo, ordem)].
    Prioriza preencher 100% da área útil da página mantendo corte <= corte_max (22%).
    Caso o preenchimento total distorça alguma imagem > corte_max, usa a proporção padrão
    daquela imagem e expande as demais imagens da página.
    """
    n = len(proporcoes)
    opcoes = []
    if n == 0:
        return [(0.0, [], None, [])]
    somente_sem_corte = corte_max <= 1.005

    if n == 1:
        for quadro, corte, vazio, tipo in quadros_1_foto(proporcoes[0], area, corte_max=corte_max):
            marcador = "__sem_corte__" if tipo == "proporcao_padrao" else None
            opcoes.append((_custo([quadro], proporcoes, area, corte, vazio, corte_max=corte_max), [quadro], marcador, [0]))
    else:
        if not somente_sem_corte:
            x0, y0, W, H = area
            for ordem in _ordens_por_proporcao(proporcoes):
                props_o = [proporcoes[i] for i in ordem]
                invertidas = [1.0 / max(0.1, float(p)) for p in props_o]
                for linhas in _composicoes(n, MAX_LINHAS, _fotos_por_linha(n)):
                    # 1. Partições em Linhas Horizontais Elásticas
                    r = _layout_linhas(props_o, area, espaco, linhas, corte_max=corte_max)
                    if r:
                        quadros, corte, vazio = r
                        opcoes.append((_custo(quadros, props_o, area, corte, vazio, corte_max=corte_max), quadros, None, list(ordem)))
                    # 2. Partições em Colunas Verticais Elásticas (transpondo x<->y e w<->h)
                    r_col = _layout_linhas(invertidas, (y0, x0, H, W), espaco, linhas, corte_max=corte_max)
                    if r_col:
                        quadros_t, corte, vazio = r_col
                        quadros = [(qy, qx, qh, qw) for (qx, qy, qw, qh) in quadros_t]
                        opcoes.append((_custo(quadros, props_o, area, corte, vazio, corte_max=corte_max) + 0.01, quadros, None, list(ordem)))

            # 3. Composições Assimétricas Dinâmicas (Destaque + Coadjuvantes)
            opcoes.extend(_layouts_assimetricos(proporcoes, area, espaco, corte_max=corte_max))

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
                # Se não tiver molde PNG rígido e estourar corte_max, adapta os quadros
                quadros_finais = list(quadros)
                if not modelo.get("molde_png"):
                    for idx_q, idx_f in enumerate(melhor[1]):
                        if _corte_quadro(proporcoes[idx_f], quadros_finais[idx_q]) > corte_max:
                            quadros_finais[idx_q] = caixa_proporcao_padrao(proporcoes[idx_f], quadros_finais[idx_q])
                opcoes.append((melhor[0], quadros_finais, modelo, melhor[1]))

    # 5. Opções com proporção 100% nativa sem corte
    for ordem in _ordens_por_proporcao(proporcoes):
        for custo, quadros, marcador, ordem_rel in _layouts_sem_corte(
            [proporcoes[i] for i in ordem], area, espaco, priorizar=somente_sem_corte
        ):
            opcoes.append((custo, quadros, marcador, [ordem[j] for j in ordem_rel]))

    # Garantia estrita de que nenhum quadro ultrapasse os limites de `area` (Safe Zone ou página)
    ax0, ay0, aW, aH = area
    opcoes_clamped = []
    for c_op, q_op, m_op, ord_op in opcoes:
        if not q_op:
            opcoes_clamped.append((c_op, q_op, m_op, ord_op))
            continue
        min_x = min(q[0] for q in q_op)
        min_y = min(q[1] for q in q_op)
        max_x = max(q[0] + q[2] for q in q_op)
        max_y = max(q[1] + q[3] for q in q_op)
        span_w = max_x - min_x
        span_h = max_y - min_y
        if min_x < ax0 - 1e-3 or min_y < ay0 - 1e-3 or max_x > ax0 + aW + 1e-3 or max_y > ay0 + aH + 1e-3:
            esc_x = aW / span_w if span_w > aW else 1.0
            esc_y = aH / span_h if span_h > aH else 1.0
            esc = min(esc_x, esc_y)
            novo_w = span_w * esc
            novo_h = span_h * esc
            off_x = ax0 + (aW - novo_w) / 2.0
            off_y = ay0 + (aH - novo_h) / 2.0
            q_op = [
                (off_x + (qx - min_x) * esc, off_y + (qy - min_y) * esc, qw * esc, qh * esc)
                for (qx, qy, qw, qh) in q_op
            ]
        opcoes_clamped.append((c_op, q_op, m_op, ord_op))
    opcoes = opcoes_clamped
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
    # A variação sem corte entra como alternativa, nunca no lugar da única opção pedida
    if sem_corte is not None and not any(opcao[2] == "__sem_corte__" for opcao in unicas):
        if len(unicas) < maximo:
            unicas.append(sem_corte)
        elif maximo >= 2:
            unicas[-1] = sem_corte
    unicas.sort(key=lambda opcao: opcao[0])
    return unicas


_CACHE_PAGINA = {}


def _opcoes_pagina(proporcoes, area, espaco, maximo=3, corte_max=CORTE_MAXIMO):
    """
    layouts_pagina calculado sobre as proporções em ordem crescente (o resultado vale para qualquer
    página com as mesmas proporções e fica em cache). Cada foto vai para o quadro calculado para a sua
    proporção; entre fotos de proporção igual, a ordem do álbum segue a ordem de leitura dos quadros.
    """
    n = len(proporcoes)
    if n == 0:
        return [(0.0, [], None, [])]
    arredondadas = [round(max(0.1, float(p)), 3) for p in proporcoes]
    base = sorted(arredondadas)
    chave = (tuple(base), tuple(round(float(v), 2) for v in area), round(float(espaco), 2), maximo, round(float(corte_max), 4))
    calculadas = _CACHE_PAGINA.get(chave)
    if calculadas is None:
        if len(_CACHE_PAGINA) > 5000:
            _CACHE_PAGINA.clear()
        calculadas = [
            (custo, tuple(quadros), marcador, tuple(base[i] for i in ordem))
            for custo, quadros, marcador, ordem in layouts_pagina(base, area, espaco, (), None, 0, maximo, corte_max)
        ]
        _CACHE_PAGINA[chave] = calculadas
    saida = []
    for custo, quadros, marcador, aspectos in calculadas:
        livres = {}
        for i, p in enumerate(arredondadas):
            livres.setdefault(p, []).append(i)
        ordem = [0] * len(quadros)
        for j in sorted(range(len(quadros)), key=lambda j: (round(quadros[j][1], 1), quadros[j][0])):
            ordem[j] = livres[aspectos[j]].pop(0)
        saida.append((custo, list(quadros), marcador, ordem))
    return saida


# ------------------------------------------------------------
# Opções para uma lâmina e ajuste global para todas as ações
# ------------------------------------------------------------

def _toca_sangria(e):
    return (
        float(e.get("x", 0.0)) <= 0.006
        or float(e.get("y", 0.0)) <= 0.006
        or float(e.get("x", 0.0)) + float(e.get("w", 0.0)) >= 0.994
        or float(e.get("y", 0.0)) + float(e.get("h", 0.0)) >= 0.994
    )


def _custo_lamina(espacos, fotos, produto, cfg):
    """
    Custo único para comparar layouts de qualquer tipo (grade, transpasse, meio termo, sangria, sem corte).
    O que mais pesa é a área útil de cada página que fica sem foto; depois o corte, fotos pequenas ou
    muito desproporcionais entre si e o equilíbrio de fotos entre as páginas.
    """
    if not espacos:
        return 0.0
    lw, lh = lamina_mm(produto)
    paginas = max(1, int(produto.get("paginas_por_lamina", 1) or 1))
    margem = float(cfg.get("margem_mm", produto.get("margem_mm", 0.0)))
    corte_max = _limite_corte_cfg(cfg)
    props = {f.get("caminho"): max(0.1, float(f.get("proporcao") or 1.5)) for f in fotos}
    quadros = [(e["x"] * lw, e["y"] * lh, e["w"] * lw, e["h"] * lh) for e in espacos]
    areas = [area_util_pagina_mm(produto, p, margem, respeitar_seguranca=True) for p in range(paginas)]
    n = len(quadros)

    vazios = []
    for ux, uy, uw, uh in areas:
        coberto = sum(
            max(0.0, min(x + w, ux + uw) - max(x, ux)) * max(0.0, min(y + h, uy + uh) - max(y, uy))
            for x, y, w, h in quadros
        )
        vazios.append(max(0.0, 1.0 - coberto / max(1.0, uw * uh)))
    custo = PESO_VAZIO_LAMINA * sum(vazios) / len(vazios) + PESO_BURACO * sum(max(0.0, v - 0.20) for v in vazios)

    soma_corte = 0.0
    for e, q in zip(espacos, quadros):
        corte = _corte_quadro(props.get(e.get("foto"), q[2] / max(1e-6, q[3])), q)
        soma_corte += math.log(max(1.0, corte)) ** 2
        limite = corte_max * (1.08 if _toca_sangria(e) else 1.0)
        custo += 200.0 * max(0.0, corte - limite - 1e-3)
    custo += PESO_CORTE * soma_corte / n

    menor = 0.22 * min(min(a[2], a[3]) for a in areas)
    for _x, _y, w, h in quadros:
        if min(w, h) < menor:
            custo += PESO_PEQUENA * (1.0 - min(w, h) / menor)
    if n > 1:
        logs = [math.log(max(1e-6, w * h)) for _x, _y, w, h in quadros]
        media = sum(logs) / n
        custo += 0.15 * sum((v - media) ** 2 for v in logs) / n

    if paginas == 2 and n > 1:
        meio = lw / 2.0
        if not any(x < meio - 2.0 and x + w > meio + 2.0 for x, _y, w, _h in quadros):
            por_pagina = [0, 0]
            for x, _y, w, _h in quadros:
                por_pagina[0 if x + w / 2.0 < meio else 1] += 1
            custo += PESO_DESEQUILIBRIO * abs(por_pagina[0] - por_pagina[1])
            custo += _penalidade_pagina_vazia_demais(por_pagina, n, _limites_pagina(cfg, produto)[0])
    return custo


def _divisoes_paginas(fotos, max_pagina, limite=36):
    """
    Índices das fotos da página esquerda em cada divisão testada: a sequência do álbum cortada em cada
    ponto e, por orientação, cada quantidade de verticais/quadradas/horizontais/panorâmicas na esquerda
    (as primeiras de cada orientação), para casar as fotos com o formato das páginas.
    """
    n = len(fotos)
    menor_k = max(0, n - max_pagina)
    maior_k = min(n, max_pagina)
    divisoes = {tuple(range(k)): None for k in range(menor_k, maior_k + 1)}
    por_classe = {}
    for i, f in enumerate(fotos):
        por_classe.setdefault(_classe_orientacao(f["proporcao"]), []).append(i)
    classes = sorted(por_classe)
    por_orientacao = []
    for qtds in itertools.product(*[range(len(por_classe[c]) + 1) for c in classes]):
        if menor_k <= sum(qtds) <= maior_k:
            por_orientacao.append(tuple(sorted(i for c, q in zip(classes, qtds) for i in por_classe[c][:q])))
    por_orientacao.sort(key=lambda d: abs(2 * len(d) - n))
    for d in por_orientacao[:limite]:
        divisoes.setdefault(d, None)
    return [list(d) for d in divisoes]


def _opcoes_grade(fotos, produto, cfg, modelos_pagina, max_pagina, corte_max, maximo):
    """Layouts em grade dentro da área útil, testando que fotos vão para cada página."""
    n = len(fotos)
    paginas = produto["paginas_por_lamina"]
    margem = float(cfg["margem_mm"])
    espaco = float(cfg["espacamento_mm"])
    min_pagina = _limites_pagina(cfg, produto)[0]
    areas = [area_util_pagina_mm(produto, p, margem, respeitar_seguranca=True) for p in range(paginas)]
    if paginas == 1:
        divisoes = [[list(range(n))]]
    else:
        divisoes = []
        for esquerda in _divisoes_paginas(fotos, max_pagina):
            no_lado = set(esquerda)
            divisoes.append([esquerda, [i for i in range(n) if i not in no_lado]])

    candidatos = []
    for divisao in divisoes:
        por_pagina = []
        for pagina, indices in enumerate(divisao):
            props = [fotos[i]["proporcao"] for i in indices]
            ops = _opcoes_pagina(props, areas[pagina], espaco, 3, corte_max)
            if modelos_pagina and indices:
                ops = ops + [
                    o for o in layouts_pagina(props, areas[pagina], espaco, modelos_pagina, produto, pagina, 3, corte_max)
                    if isinstance(o[2], dict)
                ]
            por_pagina.append((indices, pagina, ops))
        qtds = [len(d) for d in divisao]
        penalidade = 0.0
        if any(k == 0 for k in qtds):
            penalidade = PESO_PAGINA_VAZIA if n > 1 else 0.12
        if len(qtds) == 2 and n > 1:
            penalidade += PESO_DESEQUILIBRIO * abs(qtds[0] - qtds[1])
        penalidade += _penalidade_pagina_vazia_demais(qtds, n, min_pagina)
        for combinacao in itertools.product(*[p[2] for p in por_pagina]):
            candidatos.append((penalidade + sum(c[0] for c in combinacao), por_pagina, combinacao))

    candidatos.sort(key=lambda c: c[0])
    saida = []
    for _estimativa, por_pagina, combinacao in candidatos[:max(16, maximo * 3)]:
        espacos, sobreposicoes, ids = [], [], []
        sem_corte = False
        for (indices, pagina, _ops), (_c, quadros, modelo, ordem) in zip(por_pagina, combinacao):
            for quadro, rel in zip(quadros, ordem):
                espacos.append(_espaco(produto, quadro, fotos[indices[rel]]["caminho"]))
            if modelo == "__sem_corte__":
                sem_corte = True
            elif isinstance(modelo, dict):
                ids.append(modelo["id"])
                _esp, sobre = espacos_do_modelo(modelo, produto, pagina)
                if sobre:
                    sobreposicoes.append(sobre)
        opcao = {
            "custo": 0.0,
            "espacos": espacos,
            "sobreposicoes": sobreposicoes,
            "modelo": "+".join(ids) if ids else None,
        }
        if sem_corte:
            opcao["tipo_layout"] = "sem_corte"
        saida.append(opcao)
    return saida

def _espaco(produto, quadro, foto, edicao=None):
    e = normalizar_mm(produto, quadro)
    e["foto"] = foto
    e["edicao"] = dict(edicao or {})
    return e


def opcoes_lamina(fotos, produto, cfg, modelos=(), maximo=10):
    """
    fotos: [{"caminho", "proporcao", "destaque"}] na ordem.
    Os layouts são calculados na hora pelas proporções das fotos. `modelos` (salvos pelo usuário)
    só entram quando o chamador os passa de propósito; a diagramação automática não passa nenhum.
    Devolve [{"custo", "espacos", "sobreposicoes", "modelo"}], a melhor primeiro.
    """
    n = len(fotos)
    if n == 0:
        return [{"custo": 0.0, "espacos": [], "sobreposicoes": [], "modelo": None}]
    lw, lh = lamina_mm(produto)
    margem = float(cfg["margem_mm"])
    paginas = produto["paginas_por_lamina"]
    max_pagina = _limites_pagina(cfg, produto)[1]
    # Lâmina montada à mão com mais fotos que o limite: ainda precisa de pelo menos uma divisão possível
    max_pagina = max(max_pagina, int(math.ceil(n / float(max(1, paginas)))))
    corte_max = _limite_corte_cfg(cfg)

    modelos_lamina = [m for m in modelos if m.get("escopo") == "lamina" and len(m["espacos"]) == n]
    modelos_pagina = [m for m in modelos if m.get("escopo") == "pagina"]
    opcoes = []

    if n == 1:
        p = max(0.1, float(fotos[0]["proporcao"]))
        b = float(produto.get("sangria_mm", 3.0))
        seg = float(produto.get("seguranca_mm", 8.0))
        m_seg = b + max(seg, margem)
        area_lamina_util = (m_seg, m_seg, max(1.0, lw - 2 * m_seg), max(1.0, lh - 2 * m_seg))
        corte_lamina = _corte_quadro(p, area_lamina_util)
        _ex_x1, _ex_y1, pen_p1 = _risco_cortar_pessoas(fotos[0], area_lamina_util)
        if cfg.get("permitir_transpasse", True) and p >= 1.85 and corte_lamina <= corte_max and pen_p1 <= 0.0:
            bonus = -0.20 if fotos[0].get("destaque") else 0.0
            opcoes.append({
                "custo": 0.25 + bonus,
                "espacos": [_espaco(produto, area_lamina_util, fotos[0]["caminho"])],
                "sobreposicoes": [],
                "modelo": None,
                "tipo_layout": "panoramico_meio",
            })

    for modelo in modelos_lamina:
        espacos_m, sobre = espacos_do_modelo(modelo, produto)
        quadros = [(e["x"] * lw, e["y"] * lh, e["w"] * lw, e["h"] * lh) for e in espacos_m]
        if not modelo.get("molde_png"):
            for i_q, f in enumerate(fotos):
                if _corte_quadro(f["proporcao"], quadros[i_q]) > corte_max:
                    quadros[i_q] = caixa_proporcao_padrao(f["proporcao"], quadros[i_q])
        opcoes.append({
            "custo": 0.0,
            "espacos": [_espaco(produto, q, f["caminho"]) for f, q in zip(fotos, quadros)],
            "sobreposicoes": [sobre] if sobre else [],
            "modelo": modelo["id"],
        })

    # Nos layouts especiais, "custo" traz só o ajuste de estilo (destaque, rosto na dobra);
    # a qualidade geométrica de todas as opções sai de _custo_lamina, abaixo.
    if cfg.get("permitir_transpasse", True):
        opcoes.extend(_layouts_transpasse_central(fotos, produto, cfg))
    if cfg.get("permitir_meio_termo", True):
        opcoes.extend(_layouts_meio_termo(fotos, produto, cfg))
    if cfg.get("permitir_sangria_total", True):
        opcoes.extend(_layouts_preenchimento_maximo(fotos, produto, cfg))

    opcoes.extend(_opcoes_grade(fotos, produto, cfg, modelos_pagina, max_pagina, corte_max, maximo))

    proteger = cfg.get("reconhecer_rostos", True) and cfg.get("nao_cortar_pessoas", True)
    for op in opcoes:
        ajuste = float(op.get("custo", 0.0))
        if proteger and not op.get("sobreposicoes"):
            op["espacos"], pen_pessoas = _proteger_espacos_contra_corte_pessoas(op["espacos"], fotos, produto, cfg)
            ajuste += pen_pessoas
        op["custo"] = _custo_lamina(op["espacos"], fotos, produto, cfg) + ajuste

    opcoes.sort(key=lambda o: o["custo"])
    unicas = []
    tipos_especiais = (
        "meio_termo_esq",
        "meio_termo_dir",
        "sangria_total",
        "transpasse_60_40",
        "transpasse_70_30",
        "panoramico_meio",
    )
    tipos_incluidos = set()
    reserva_especiais = []
    for opcao in opcoes:
        t_lay = opcao.get("tipo_layout")
        if t_lay in tipos_especiais and t_lay not in tipos_incluidos:
            tipos_incluidos.add(t_lay)
            reserva_especiais.append(opcao)
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

    # Garante que as variações Meio Termo, Sangria Total e Transpasse 60/40 e 70/30 apareçam nas sugestões quando forem viáveis
    if maximo >= 6 and unicas:
        melhor_custo = float(unicas[0].get("custo", 0.0))
        for op_esp in reserva_especiais:
            if float(op_esp.get("custo", 0.0)) > melhor_custo + 2.0:
                continue
            if not any(u.get("tipo_layout") == op_esp.get("tipo_layout") for u in unicas):
                if len(unicas) >= maximo:
                    idx_sub = next(
                        (idx for idx in range(len(unicas) - 1, 0, -1) if not unicas[idx].get("tipo_layout")),
                        len(unicas) - 1,
                    )
                    unicas.pop(idx_sub)
                unicas.append(op_esp)
        unicas.sort(key=lambda o: o["custo"])
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
        area_u = area_util_pagina_mm(produto, p, margem, respeitar_seguranca=True)
        area_sang = area_pagina_mm(produto, p)
        # Detecta se esta página está em modo sangrado (Meio Termo ou Sangria Total / Preencher Página)
        pagina_sangrada = any(
            (e["y"] * lh) < area_u[1] - 2.0 or ((e["y"] + e["h"]) * lh) > (area_u[1] + area_u[3] + 2.0)
            for e in grupo_e
        )
        if pagina_sangrada and len(grupo_e) == 1:
            p_unica = prop_de(grupo_e[0]["foto"])
            if _corte_quadro(p_unica, area_sang) <= CORTE_MAXIMO * 1.08:
                novos_espacos.append(_espaco(produto, area_sang, grupo_e[0]["foto"], grupo_e[0].get("edicao")))
                continue
            pagina_sangrada = False
        area_alvo = area_sang if pagina_sangrada else area_u
        ux, uy, uw, uh = area_alvo
        usado_p = sum((e["w"] * lw) * (e["h"] * lh) for e in grupo_e)
        vazio_p = max(0.0, 1.0 - usado_p / max(1.0, uw * uh))
        corte_max_p = max(
            _corte_quadro(prop_de(e["foto"]), (e["x"] * lw, e["y"] * lh, e["w"] * lw, e["h"] * lh))
            for e in grupo_e
        )
        limite_p = CORTE_MAXIMO * 1.08 if pagina_sangrada else CORTE_MAXIMO
        if corte_max_p > limite_p + 0.01 or (vazio_p > 0.25 and corte_max_p < 1.05):
            props = [prop_de(e["foto"]) for e in grupo_e]
            opcoes_p = layouts_pagina(props, area_alvo, intervalo, (), produto, p, maximo=1, corte_max=limite_p)
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


def espaco_inclui_na_pagina(espaco, produto, pagina):
    """
    Determina se um espaço da lâmina deve ser incluído ao aplicar um layout de página única
    na página `pagina` (0 = Esquerda, 1 = Direita):
    - Se a foto atravessa o vinco central (ex.: layouts 60/40, 70/30 ou panorâmicos onde x < 0.5 < x + w),
      ela é incluída na página que está sendo editada (para ser diagramada dentro dela).
    - Caso contrário, verifica em qual página está o centro do espaço.
    """
    if produto.get("paginas_por_lamina", 1) < 2:
        return True
    x0 = float(espaco.get("x", 0.0))
    x1 = x0 + float(espaco.get("w", 0.0))
    # Se atravessa o meio (com margem de tolerância de 0.5%), inclui na página editada
    if x0 < 0.495 and x1 > 0.505:
        return True
    centro = (x0 + x1) / 2.0
    return (centro < 0.5) if pagina == 0 else (centro >= 0.5)


def _recortar_area_livre_pagina(area, espacos_fixos, produto, intervalo):
    """
    Quando uma foto atravessa o meio da lâmina e invade a página sendo diagramada,
    preserva essa foto intacta e devolve o maior retângulo livre restante dentro de `area`
    (descontando a foto que passou do meio e o espaçamento `intervalo` em mm).
    """
    if not espacos_fixos:
        return area
    W_mm, H_mm = lamina_mm(produto)
    ax, ay, aw, ah = [float(v) for v in area]
    gap = max(0.0, float(intervalo or 0.0))

    for esp in espacos_fixos:
        ex = float(esp.get("x", 0.0)) * W_mm
        ey = float(esp.get("y", 0.0)) * H_mm
        ew = float(esp.get("w", 0.0)) * W_mm
        eh = float(esp.get("h", 0.0)) * H_mm

        ix0 = max(ax, ex)
        iy0 = max(ay, ey)
        ix1 = min(ax + aw, ex + ew)
        iy1 = min(ay + ah, ey + eh)
        if ix1 - ix0 <= 0.5 or iy1 - iy0 <= 0.5:
            continue

        candidatos = []
        # 1. Espaço livre à direita da foto que atravessou
        rx0 = ex + ew + gap
        rw = (ax + aw) - rx0
        if rw >= 15.0:
            candidatos.append((rx0, ay, rw, ah))
        # 2. Espaço livre à esquerda da foto que atravessou
        lx1 = ex - gap
        lw = lx1 - ax
        if lw >= 15.0:
            candidatos.append((ax, ay, lw, ah))
        # 3. Espaço livre abaixo da foto que atravessou
        by0 = ey + eh + gap
        bh = (ay + ah) - by0
        if bh >= 15.0:
            candidatos.append((ax, by0, aw, bh))
        # 4. Espaço livre acima da foto que atravessou
        ty1 = ey - gap
        th = ty1 - ay
        if th >= 15.0:
            candidatos.append((ax, ay, aw, th))

        if candidatos:
            ax, ay, aw, ah = max(candidatos, key=lambda r: r[2] * r[3])

    return (ax, ay, max(15.0, aw), max(15.0, ah))


def opcoes_pagina_unica(fotos, produto, pagina=0, cfg=None, maximo=14, espacos_fixos=()):
    """
    Gera opções de layout exclusivas para uma única página (0 = Esquerda, 1 = Direita)
    sem tocar na outra página da lâmina nem em fotos que atravessam o meio (`espacos_fixos`).
    Inclui variações:
    - Safe Zone Elástico (respeita as linhas da Safe Zone e limite de corte de 22%)
    - Sangria Total da Página (preenche a página até a sangria ignorando as linhas da Safe Zone)
    - Proporção Original Sem Corte (0% corte dentro da Safe Zone)
    """
    cfg = cfg or configuracao(produto)
    margem = float(cfg.get("margem_mm", produto["margem_mm"]))
    intervalo = max(0.0, float(cfg.get("espacamento_mm", produto["espacamento_mm"])))
    corte_max = _limite_corte_cfg(cfg)
    corte_sangria = max(corte_max, 1.38)

    lista_fotos = list(fotos) if fotos else [{"caminho": None, "proporcao": 1.5}]
    n = len(lista_fotos)
    proporcoes = [max(0.15, float(f.get("proporcao") or 1.5)) for f in lista_fotos]
    area_safe = _recortar_area_livre_pagina(
        area_util_pagina_mm(produto, pagina, margem, respeitar_seguranca=True),
        espacos_fixos,
        produto,
        intervalo,
    )
    area_sang = _recortar_area_livre_pagina(
        area_pagina_mm(produto, pagina),
        espacos_fixos,
        produto,
        intervalo,
    )

    opcoes_brutas = []

    def montar_espacos(quadros, ordem):
        espacos = []
        for quadro, idx_f in zip(quadros, ordem):
            f_item = lista_fotos[idx_f]
            ed = dict(f_item.get("edicao") or {})
            ed.pop("corte", None)
            espacos.append(_espaco(produto, quadro, f_item.get("caminho"), ed))
        return espacos

    # 1. Variações dentro da Safe Zone (Elástico até 22% corte)
    ops_safe = layouts_pagina(
        proporcoes, area_safe, intervalo, (), produto=produto, pagina=pagina, maximo=8, corte_max=corte_max
    )
    for idx_op, (custo, quadros, marcador, ordem) in enumerate(ops_safe):
        if not quadros:
            continue
        eh_sem_corte = marcador == "__sem_corte__"
        estilo = "sem_corte" if eh_sem_corte else "safe_zone"
        if eh_sem_corte:
            nome = "Sem Corte · Proporção Original"
        elif n == 1:
            nome = "Safe Zone · Página Inteira"
        elif idx_op == 0:
            nome = "Safe Zone · Equilibrado (%d fotos)" % n
        else:
            nome = "Safe Zone · Variação %d (%d fotos)" % (idx_op + 1, n)
        opcoes_brutas.append({
            "custo": custo + (0.05 if eh_sem_corte else 0.0),
            "espacos": montar_espacos(quadros, ordem),
            "nome": nome,
            "estilo": estilo,
            "pagina": pagina,
        })

    # 2. Variações de Sangria Total da Página (ultrapassa a Safe Zone e preenche a página até a sangria)
    if n == 1:
        opcoes_brutas.append({
            "custo": 0.85,
            "espacos": montar_espacos([area_sang], [0]),
            "nome": "Sangria Total · Página 100% Cheia",
            "estilo": "sangria_pagina",
            "pagina": pagina,
        })
    else:
        ops_sang = layouts_pagina(
            proporcoes, area_sang, intervalo, (), produto=produto, pagina=pagina, maximo=6, corte_max=corte_sangria
        )
        for idx_s, (custo_s, quadros_s, marcador_s, ordem_s) in enumerate(ops_sang):
            if not quadros_s or marcador_s == "__sem_corte__":
                continue
            opcoes_brutas.append({
                "custo": 0.88 + custo_s + idx_s * 0.03,
                "espacos": montar_espacos(quadros_s, ordem_s),
                "nome": "Sangria Total · Preenchimento %d" % (idx_s + 1),
                "estilo": "sangria_pagina",
                "pagina": pagina,
            })

    # 3. Garante variações Sem Corte adicionais dentro da Safe Zone
    ops_nc = _layouts_sem_corte(proporcoes, area_safe, intervalo, priorizar=True)
    for idx_nc, (custo_nc, quadros_nc, _m_nc, ordem_nc) in enumerate(ops_nc[:3]):
        if not quadros_nc:
            continue
        opcoes_brutas.append({
            "custo": 0.30 + idx_nc * 0.04,
            "espacos": montar_espacos(quadros_nc, ordem_nc),
            "nome": "Sem Corte · Nativo %d" % (idx_nc + 1),
            "estilo": "sem_corte",
            "pagina": pagina,
        })

    # Aplica a proteção inteligente de múltiplos rostos na página única
    if cfg.get("reconhecer_rostos", True) and cfg.get("nao_cortar_pessoas", True):
        for op in opcoes_brutas:
            esp_seg, pen_p = _proteger_espacos_contra_corte_pessoas(op["espacos"], lista_fotos, produto, cfg)
            op["espacos"] = esp_seg
            op["custo"] = float(op.get("custo", 0.0)) + pen_p
        opcoes_brutas.sort(key=lambda o: o["custo"])

    # Deduplicação geométrica preservando pelo menos cada estilo
    unicas = []
    for op in opcoes_brutas:
        parecida = next(
            (
                u for u in unicas
                if u.get("estilo") == op.get("estilo")
                and _geometrias_parecidas(op["espacos"], u["espacos"], produto)
            ),
            None,
        )
        if parecida is not None:
            continue
        unicas.append(op)
        if len(unicas) >= maximo:
            break

    return unicas


# ------------------------------------------------------------
# Divisão Estrita por Grupos Numerados (Ordem Manual) e Pastas
# ------------------------------------------------------------

def eh_foto_em_grupo(foto):
    """
    Identifica se a foto é 'Em Grupo / Turma' (True, 2+ pessoas) ou 'Individual' (False, 0-1 pessoa).
    Respeita primeiro `tipo_pessoas` ('individual' | 'grupo') ou `em_grupo_pessoas`, e usa `rosto.qtd >= 2` como padrão automático.
    """
    if not isinstance(foto, dict):
        return False
    tp = str(foto.get("tipo_pessoas") or "").strip().lower()
    if tp == "grupo":
        return True
    if tp == "individual":
        return False
    if "em_grupo_pessoas" in foto and isinstance(foto["em_grupo_pessoas"], bool):
        return foto["em_grupo_pessoas"]
    rosto = foto.get("rosto")
    if isinstance(rosto, dict):
        try:
            return int(rosto.get("qtd") or 0) >= 2
        except (TypeError, ValueError):
            return False
    return False


def _chave_prioridade_pessoas(foto, modo_ordem):
    modo = str(modo_ordem or "manter").strip().lower()
    if modo == "individuais_primeiro":
        return 1 if eh_foto_em_grupo(foto) else 0
    if modo in ("turma_primeiro", "grupo_primeiro", "grupos_primeiro"):
        return 0 if eh_foto_em_grupo(foto) else 1
    return 0


def _assinatura_de_foto(foto, info=None):
    if not isinstance(foto, dict):
        return None
    ass = foto.get("assinatura_cenario")
    if isinstance(ass, (list, tuple)) and len(ass) > 0:
        return [float(x) for x in ass]
    cen = foto.get("cenario")
    if isinstance(cen, (list, tuple)) and len(cen) > 0:
        return [float(x) for x in cen]
    if isinstance(info, dict) and foto.get("caminho"):
        from comum import normalizar_caminho
        dados_i = info.get(normalizar_caminho(str(foto["caminho"]))) or info.get(str(foto["caminho"])) or {}
        cen_i = dados_i.get("cenario")
        if isinstance(cen_i, (list, tuple)) and len(cen_i) > 0:
            return [float(x) for x in cen_i]
    return None


def detectar_nome_evento_foto(foto, info=None, indice=0):
    """
    Identifica o nome de um Evento/Capítulo APENAS quando houver indicação explícita
    no nome do grupo, pasta ou arquivo. Nunca inventa eventos fixos por índice ou brilho,
    pois nem todo álbum tem todos os eventos e quem decide os eventos e sua ordem é o usuário.
    """
    if not isinstance(foto, dict):
        return ""
    nome_g = str(foto.get("nome_grupo") or "").strip()
    if (
        nome_g
        and not nome_g.startswith("Cenário ")
        and not nome_g.startswith("Grupo ")
        and not nome_g.startswith("Capítulo ")
    ):
        return nome_g

    # Só o fim dos caminhos: a pasta-raiz do fotógrafo (ex.: "D:\Estudio Fulano") não é o evento.
    from pathlib import PureWindowsPath

    partes = [
        nome_g,
        " ".join(PureWindowsPath(str(foto.get("pasta") or "")).parts[-2:]),
        str(foto.get("nome_pasta") or ""),
        " ".join(PureWindowsPath(str(foto.get("caminho") or "")).parts[-3:]),
    ]
    return evento_por_palavras(" ".join(partes))


# Ordem importa: "colação 2" antes de "colação".
PALAVRAS_EVENTO = (
    (("jaleco", "cerimonia_jaleco", "cerimonia do jaleco", "cerimônia do jaleco"), "Cerimônia do Jaleco"),
    (("externa", "parque", "praia", "ar_livre", "jardim", "botanic", "outdoor"), "Fotos Externas"),
    (("baile", "gala", "valsa", "festa_formatura"), "Baile de Gala"),
    (("culto", "missa", "igreja"), "Culto Ecumênico"),
    (("ensaio", "pre_wedding", "making"), "Ensaio Fotográfico"),
    (("colacao 2", "colação 2", "colacao_2", "solenidade", "tribuna", "canudo"), "Colação 2"),
    (("colacao", "colação", "beca", "juramento"), "Colação 1"),
    (("estudio", "estúdio", "studio"), "Estúdio"),
)

# Evento -> tema de composicao.CATALOGO_TEMAS (fundo, textura e molduras).
TEMA_DO_EVENTO = {
    "Estúdio": "colacao_royal",
    "Colação 1": "colacao_royal",
    "Colação 2": "estudio_dark_bokeh",
    "Cerimônia do Jaleco": "jaleco_fine_art",
    "Fotos Externas": "externas_botanica",
    "Baile de Gala": "baile_dark_luxury",
    "Culto Ecumênico": "culto_solenidade",
    "Ensaio Fotográfico": "ensaio_linho",
}

# Cena reconhecida pela IA (ia_cena) -> evento sugerido. Só vira nome quando o usuário aceita.
EVENTO_DA_CENA = {
    "Colação/Palco": "Colação 2",
    "Baile/Festa": "Baile de Gala",
    "Culto/Igreja": "Culto Ecumênico",
    "Externa": "Fotos Externas",
    "Estúdio": "Estúdio",
}
CONFIANCA_CENA_TEMA = 0.5
CONFIANCA_CENA_SINAL = 0.45
# Ligado pela opção "usar a cena para escolher o tema" (desligado = comportamento de sempre).
USAR_CENA_TEMA = False


def _cena_confiavel(foto, minimo):
    cena = foto.get("cena") if isinstance(foto, dict) else None
    if not isinstance(cena, dict) or not cena.get("rotulo"):
        return None
    try:
        confianca = float(cena.get("confianca") or 0)
    except (TypeError, ValueError):
        return None
    return (cena["rotulo"], confianca) if confianca >= minimo else None


def _distancia_com_cena(f_a, f_b, distancia):
    """Cena diferente (com confiança) afasta as fotos; a mesma cena aproxima. Sem cena não muda nada."""
    cena_a = _cena_confiavel(f_a, CONFIANCA_CENA_SINAL)
    cena_b = _cena_confiavel(f_b, CONFIANCA_CENA_SINAL)
    if not (cena_a and cena_b):
        return distancia
    if cena_a[0] != cena_b[0]:
        return distancia + 0.12 * min(cena_a[1], cena_b[1])
    return distancia * 0.85


def sugestao_evento_das_fotos(fotos):
    """{"nome", "cena", "confianca"} do evento mais provável pela cena das fotos, ou None (nunca é aplicado sozinho)."""
    from collections import Counter

    votos = Counter()
    lista = [f for f in (fotos or []) if isinstance(f, dict)]
    for f in lista:
        achado = _cena_confiavel(f, CONFIANCA_CENA_SINAL)
        if achado and achado[0] in EVENTO_DA_CENA:
            votos[achado[0]] += achado[1]
    if not votos or not lista:
        return None
    cena, soma = votos.most_common(1)[0]
    confianca = soma / float(len(lista))
    if confianca < 0.35:
        return None
    nome = EVENTO_DA_CENA[cena]
    brancas = [float((f.get("cena") or {}).get("roupa_branca") or 0) for f in lista]
    if cena in ("Colação/Palco", "Estúdio") and brancas and sorted(brancas)[len(brancas) // 2] >= 0.45:
        return {"nome": "Cerimônia do Jaleco", "cena": cena, "confianca": round(min(0.4, confianca), 2)}
    return {"nome": nome, "cena": cena, "confianca": round(confianca, 2)}


def evento_por_palavras(texto):
    txt = str(texto or "").lower()
    for palavras, evento in PALAVRAS_EVENTO:
        if any(k in txt for k in palavras):
            return evento
    return ""


def tema_evento_das_fotos(fotos, info=None, preferido=None):
    """
    Tema do evento da maioria das fotos (pelo nome do grupo, da pasta ou do arquivo), ou None
    quando menos da metade das fotos tem evento reconhecível. Em empate vence `preferido`
    (o tema que a lâmina já tinha), para uma foto de outro evento não trocar a lâmina toda.
    """
    from collections import Counter

    lista = [f for f in (fotos or []) if isinstance(f, dict)]
    if not lista:
        return None
    votos = Counter()
    for foto in lista:
        nome = detectar_nome_evento_foto(foto, info)
        tema = TEMA_DO_EVENTO.get(nome) or TEMA_DO_EVENTO.get(evento_por_palavras(nome))
        if not tema and not nome and USAR_CENA_TEMA:
            achado = _cena_confiavel(foto, CONFIANCA_CENA_TEMA)
            if achado:
                tema = TEMA_DO_EVENTO.get(EVENTO_DA_CENA.get(achado[0], ""))
        if tema:
            votos[tema] += 1
    if not votos:
        return None
    tema, qtd = votos.most_common(1)[0]
    if preferido and votos.get(preferido) == qtd:
        tema = preferido
    return tema if qtd * 2 >= len(lista) else None


def separar_fotos_por_evento_automatico(fotos, info=None, limiar=0.28):
    """
    Separa uma lista de fotos em blocos quando o usuário aciona explicitamente
    'Separar p/ Evento / Cenário', agrupando por pasta/evento explícito e transições reais
    de cenário visual (assinatura HSV de fundo), sem inventar eventos inexistentes nem forçar
    um ciclo fixo de formatura.
    Devolve lista de dicts: [{"nome": str, "fotos": [dict, ...], "recolhido": False}]
    """
    lista = [dict(f) for f in (fotos or []) if isinstance(f, dict)]
    if not lista:
        return []
    try:
        lim = max(0.14, min(0.55, float(limiar or 0.28)))
    except (TypeError, ValueError):
        lim = 0.28

    if isinstance(info, dict):
        from comum import normalizar_caminho
        for f in lista:
            if not f.get("cenario") and f.get("caminho"):
                ch_n = normalizar_caminho(str(f["caminho"]))
                dados_i = info.get(ch_n) or info.get(str(f["caminho"])) or {}
                if dados_i.get("cenario"):
                    f["cenario"] = dados_i["cenario"]

    blocos = []  # [{"ev_exp": str, "pasta": str, "fotos": [dict, ...], "centroide": list|None}]
    for i, f in enumerate(lista):
        ev_exp = detectar_nome_evento_foto(f, info=info, indice=i)
        pasta_f = str(f.get("pasta") or f.get("nome_pasta") or "")
        vetor = _assinatura_de_foto(f, info=info)

        if not blocos:
            blocos.append({
                "ev_exp": ev_exp,
                "pasta": pasta_f,
                "fotos": [f],
                "centroide": list(vetor) if vetor else None,
            })
            continue

        ult = blocos[-1]
        mudou_pasta = bool(ult["pasta"] and pasta_f and ult["pasta"] != pasta_f)
        mudou_ev = bool(ult["ev_exp"] and ev_exp and ult["ev_exp"] != ev_exp)

        d_ult = distancia_cenario(ult["fotos"][-1], f)
        d_centro = (
            0.5 * sum(abs(a - b) for a, b in zip(ult["centroide"], vetor))
            if (ult["centroide"] and vetor and len(ult["centroide"]) == len(vetor))
            else d_ult
        )
        d_efetiva = _distancia_com_cena(ult["fotos"][-1], f, min(d_ult, d_centro * 0.95))

        retorno_logo_apos = False
        if not mudou_pasta and not mudou_ev and d_efetiva > lim and (i + 1) < len(lista):
            v_prox = _assinatura_de_foto(lista[i + 1], info=info)
            if ult["centroide"] and v_prox and len(ult["centroide"]) == len(v_prox):
                d_prox = 0.5 * sum(abs(a - b) for a, b in zip(ult["centroide"], v_prox))
                d_prox = _distancia_com_cena(ult["fotos"][-1], lista[i + 1], d_prox)
                if d_prox <= lim * 0.95:
                    retorno_logo_apos = True

        if mudou_pasta or mudou_ev or (d_efetiva > lim and not retorno_logo_apos):
            blocos.append({
                "ev_exp": ev_exp,
                "pasta": pasta_f,
                "fotos": [f],
                "centroide": list(vetor) if vetor else None,
            })
        else:
            ult["fotos"].append(f)
            if vetor and ult["centroide"] and len(vetor) == len(ult["centroide"]):
                k = float(len(ult["fotos"]))
                ult["centroide"] = [
                    ((k - 1.0) * c_val + v_val) / k
                    for c_val, v_val in zip(ult["centroide"], vetor)
                ]

    # Mescla fragmentos isolados de 1 foto ao bloco vizinho mais parecido quando da mesma pasta
    if len(blocos) > 2:
        for idx_b in range(len(blocos) - 1, -1, -1):
            if len(blocos) <= 1:
                break
            b_at = blocos[idx_b]
            if len(b_at["fotos"]) == 1:
                v_esq = blocos[idx_b - 1] if idx_b > 0 else None
                v_dir = blocos[idx_b + 1] if (idx_b + 1) < len(blocos) else None
                d_esq = (
                    distancia_cenario(v_esq["fotos"][-1], b_at["fotos"][0])
                    if (v_esq and (not b_at["pasta"] or not v_esq["pasta"] or b_at["pasta"] == v_esq["pasta"]))
                    else 999.0
                )
                d_dir = (
                    distancia_cenario(v_dir["fotos"][0], b_at["fotos"][0])
                    if (v_dir and (not b_at["pasta"] or not v_dir["pasta"] or b_at["pasta"] == v_dir["pasta"]))
                    else 999.0
                )
                if d_esq <= d_dir and d_esq <= lim * 1.40 and v_esq is not None:
                    v_esq["fotos"].extend(b_at["fotos"])
                    blocos.pop(idx_b)
                elif d_dir < d_esq and d_dir <= lim * 1.40 and v_dir is not None:
                    v_dir["fotos"] = b_at["fotos"] + v_dir["fotos"]
                    blocos.pop(idx_b)

    total_por_base = {}
    for b in blocos:
        base_n = (b["ev_exp"] or "").rstrip("0123456789 ").strip()
        if base_n:
            total_por_base[base_n] = total_por_base.get(base_n, 0) + 1

    contagem_por_base = {}
    resultado = []
    for idx_b, b in enumerate(blocos, start=1):
        base_n = (b["ev_exp"] or "").rstrip("0123456789 ").strip()
        if not base_n:
            nome_final = "Evento %d" % idx_b
        else:
            num_oc = contagem_por_base.get(base_n, 0) + 1
            contagem_por_base[base_n] = num_oc
            if total_por_base.get(base_n, 1) > 1:
                nome_final = "%s %d" % (base_n, num_oc)
            else:
                nome_final = b["ev_exp"]
        grupo = {
            "nome": nome_final,
            "fotos": b["fotos"],
            "recolhido": False,
        }
        if not base_n:
            sugestao = sugestao_evento_das_fotos(b["fotos"])
            if sugestao:
                grupo["sugestao_evento"] = sugestao
        resultado.append(grupo)
    return resultado


def distancia_cenario(f_a, f_b):
    """
    Calcula a distância visual entre os cenários (fundo + iluminação + cores) de duas fotos
    no intervalo [0.0, 1.0]. Valores <= limiar (ex.: 0.28) indicam o mesmo cenário.
    """
    va = _assinatura_de_foto(f_a) if isinstance(f_a, dict) else (list(f_a) if isinstance(f_a, (list, tuple)) else None)
    vb = _assinatura_de_foto(f_b) if isinstance(f_b, dict) else (list(f_b) if isinstance(f_b, (list, tuple)) else None)
    if va and vb and len(va) == len(vb):
        return 0.5 * sum(abs(a - b) for a, b in zip(va, vb))
    if isinstance(f_a, dict) and isinstance(f_b, dict):
        ca = f_a.get("rotulo_cenario") or (f_a.get("cenario") if isinstance(f_a.get("cenario"), str) else None)
        cb = f_b.get("rotulo_cenario") or (f_b.get("cenario") if isinstance(f_b.get("cenario"), str) else None)
        if ca and cb:
            return 0.05 if ca == cb else 0.85
    return 0.0


def agrupar_por_cenario_no_bloco(fotos_bloco, limiar=0.28, offset_cenario=0):
    """
    Agrupa fotos de um mesmo grupo numerado (ou sub-bloco) por semelhança de cenário visual
    (assinatura HSV de 128 bins do fundo/ambiente), mantendo juntas as fotos do mesmo cenário
    na ordem em que aparecem.
    Devolve (fotos_ordenadas_por_cenario, lista_de_sub_blocos_por_cenario).
    """
    lista = [dict(f) for f in (fotos_bloco or [])]
    if not lista:
        return [], []
    try:
        lim = max(0.10, min(0.60, float(limiar or 0.28)))
    except (TypeError, ValueError):
        lim = 0.28

    clusters = []  # [{"centroide": list|None, "rotulo_str": str|None, "fotos": [dict, ...]}]
    for f in lista:
        vetor = _assinatura_de_foto(f)
        rot_str = (
            f.get("cenario")
            if isinstance(f.get("cenario"), str)
            else (f.get("nome_grupo") or f.get("pasta") or f.get("nome_pasta") or None)
        )
        melhor_idx = -1
        menor_dist = 999.0

        for idx_c, cl in enumerate(clusters):
            if vetor is not None and cl["centroide"] is not None and len(vetor) == len(cl["centroide"]):
                d_centro = 0.5 * sum(abs(a - b) for a, b in zip(vetor, cl["centroide"]))
                ult_v = _assinatura_de_foto(cl["fotos"][-1])
                d_ult = (
                    0.5 * sum(abs(a - b) for a, b in zip(vetor, ult_v))
                    if (ult_v and len(ult_v) == len(vetor))
                    else d_centro
                )
                bonus_cont = 0.92 if idx_c == (len(clusters) - 1) else 1.0
                d = min(d_centro, d_ult * 0.95) * bonus_cont
                if d <= lim and d < menor_dist:
                    menor_dist = d
                    melhor_idx = idx_c
            elif rot_str and cl["rotulo_str"]:
                if rot_str == cl["rotulo_str"]:
                    melhor_idx = idx_c
                    menor_dist = 0.0
                    break
            elif vetor is None and not rot_str and cl["centroide"] is None and not cl["rotulo_str"]:
                melhor_idx = idx_c
                menor_dist = 0.0
                break

        if melhor_idx == -1:
            clusters.append({
                "centroide": list(vetor) if vetor is not None else None,
                "rotulo_str": rot_str,
                "fotos": [f],
            })
        else:
            cl = clusters[melhor_idx]
            cl["fotos"].append(f)
            if vetor is not None and cl["centroide"] is not None:
                k = float(len(cl["fotos"]))
                cl["centroide"] = [
                    ((k - 1.0) * c_val + v_val) / k
                    for c_val, v_val in zip(cl["centroide"], vetor)
                ]

    sub_blocos = []
    fotos_ordenadas = []
    for idx_c, cl in enumerate(clusters, start=1):
        num_cen = offset_cenario + idx_c
        bloco_c = []
        for f in cl["fotos"]:
            copia = dict(f)
            copia["cenario_subgrupo"] = num_cen
            copia["rotulo_cenario"] = "Cenário %d" % idx_c
            bloco_c.append(copia)
            fotos_ordenadas.append(copia)
        sub_blocos.append(bloco_c)

    return fotos_ordenadas, sub_blocos


def ordenar_e_agrupar_por_grupos(fotos, cfg=None):
    """
    Ordena e agrupa fotos conforme os grupos numerados definidos manualmente pelo usuário
    (`grupo_ordem` = 1, 2, 3... e `ordem_manual`), sem alterar os arquivos em disco.
    Dentro de cada grupo numerado:
    - Respeita `ordem_pessoas_grupo` ('manter', 'individuais_primeiro', 'turma_primeiro')
    - Se `reconhecer_cenarios` estiver ativo em `cfg`, identifica e agrupa fotos do mesmo
      cenário visual dentro de cada grupo (sem nunca misturar grupos numerados diferentes).
    """
    if not fotos:
        return []
    modo_pessoas = (
        cfg.get("ordem_pessoas_grupo", "manter")
        if isinstance(cfg, dict)
        else (str(cfg) if isinstance(cfg, str) else "manter")
    )
    usar_cenario = bool(cfg.get("reconhecer_cenarios", False)) if isinstance(cfg, dict) else False
    limiar_cen = float(cfg.get("limiar_cenario", 0.28)) if isinstance(cfg, dict) else 0.28

    enriquecidas = []
    for pos, foto in enumerate(fotos):
        try:
            gid = max(1, int(foto.get("grupo_ordem") or 1))
        except (TypeError, ValueError):
            gid = 1
        try:
            ord_m = int(foto.get("ordem_manual")) if foto.get("ordem_manual") is not None else pos
        except (TypeError, ValueError):
            ord_m = pos
        prio_p = _chave_prioridade_pessoas(foto, modo_pessoas)
        copia = dict(foto)
        copia["grupo_ordem"] = gid
        copia["cenario_grupo"] = gid
        copia["ordem_manual"] = ord_m
        copia["em_grupo_pessoas"] = eh_foto_em_grupo(foto)
        enriquecidas.append((gid, prio_p, ord_m, pos, copia))

    enriquecidas.sort(key=lambda item: (item[0], item[1], item[2], item[3]))
    base = [item[4] for item in enriquecidas]
    if not usar_cenario:
        return base

    # Agrupa por cenário dentro de cada (grupo_ordem, prioridade_pessoas)
    saida = []
    offset_cen = 0
    for (_gid, _prio), fatia in itertools.groupby(
        base, key=lambda f: (int(f.get("grupo_ordem") or 1), _chave_prioridade_pessoas(f, modo_pessoas))
    ):
        ordenadas_c, sub_blocos = agrupar_por_cenario_no_bloco(list(fatia), limiar=limiar_cen, offset_cenario=offset_cen)
        offset_cen += len(sub_blocos)
        saida.extend(ordenadas_c)
    return saida


def ordenar_e_agrupar_por_cenario(fotos, limiar=0.28):
    """Ordena e agrupa fotos por cenário visual dentro de cada grupo numerado."""
    return ordenar_e_agrupar_por_grupos(
        fotos,
        {"reconhecer_cenarios": True, "limiar_cenario": limiar, "ordem_pessoas_grupo": "manter"},
    )


def faixa_fotos_lamina(produto, cfg):
    """
    (mínimo, máximo) de fotos por lâmina. "Fotos por página" (fotos_por_lamina) é o mínimo;
    o máximo é o menor entre max_fotos_lamina e max_fotos_pagina x páginas.
    """
    paginas = max(1, int(produto.get("paginas_por_lamina", 1) or 1))
    try:
        maximo = int(cfg.get("max_fotos_lamina") or 0)
    except (TypeError, ValueError):
        maximo = 0
    try:
        max_pagina = int(cfg.get("max_fotos_pagina") or 0)
    except (TypeError, ValueError):
        max_pagina = 0
    if max_pagina > 0:
        maximo = min(maximo, max_pagina * paginas) if maximo > 0 else max_pagina * paginas
    maximo = max(1, maximo or MAX_POR_PAGINA * paginas)
    try:
        minimo = int(cfg.get("fotos_por_lamina") or 1)
    except (TypeError, ValueError):
        minimo = 1
    return max(1, min(maximo, minimo)), maximo


def laminas_ideais(n, produto, cfg):
    """Nº de lâminas que deixa cada uma no meio da faixa mínimo-máximo (no máximo o limite do produto)."""
    if n <= 0:
        return 0
    minimo, maximo = faixa_fotos_lamina(produto, cfg)
    ideal = int(math.ceil(n / max(1.0, (minimo + maximo) / 2.0)))
    return max(1, min(int(produto.get("laminas_max", ideal) or ideal), ideal))


def _dividir_bloco_unico(fotos, minimo, maximo, s):
    """
    Divide um bloco (mesmo grupo/pasta) em `s` lâminas seguidas com entre `minimo` e `maximo` fotos,
    em quantidades parecidas. Prefere cortar na troca de cenário ou num intervalo grande de horário
    e evita duas fotos de destaque na mesma lâmina.
    """
    n = len(fotos)
    if n == 0:
        return []
    maximo = max(1, int(maximo))
    s = max(1, min(int(s), n), int(math.ceil(n / float(maximo))))
    if s == 1:
        return [list(fotos)]
    minimo = max(1, min(int(minimo), n // s))
    media = n / float(s)

    bonus = [0.0] * (n + 1)
    for i in range(1, n):
        a = fotos[i - 1].get("data") or 0
        b = fotos[i].get("data") or 0
        b_val = PESO_INTERVALO * min(1.0, abs(b - a) / INTERVALO_CHEIO)
        cen_a = fotos[i - 1].get("cenario_subgrupo")
        cen_b = fotos[i].get("cenario_subgrupo")
        if cen_a is not None and cen_b is not None and cen_a != cen_b:
            b_val += PESO_INTERVALO * 1.45
        bonus[i] = b_val

    destaques_ate = [0] * (n + 1)
    for i, f in enumerate(fotos):
        destaques_ate[i + 1] = destaques_ate[i] + (1 if f.get("destaque") else 0)

    def custo_grupo(j, i):
        tamanho = i - j
        if tamanho < minimo or tamanho > maximo:
            return None
        extras = max(0, destaques_ate[i] - destaques_ate[j] - 1)
        return 3.0 * (tamanho - media) ** 2 / max(1.0, media) - bonus[j] + 4.0 * extras

    infinito = float("inf")
    melhor = [[infinito] * (n + 1) for _ in range(s + 1)]
    volta = [[-1] * (n + 1) for _ in range(s + 1)]
    melhor[0][0] = 0.0
    for k in range(1, s + 1):
        for i in range(k * minimo, n + 1):
            for j in range(max((k - 1) * minimo, i - maximo), i - minimo + 1):
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
        base, resto = divmod(n, s)
        grupos, inicio = [], 0
        for k in range(s):
            tamanho = base + (1 if k < resto else 0)
            grupos.append(list(fotos[inicio:inicio + tamanho]))
            inicio += tamanho
        return grupos
    grupos = []
    i = n
    for k in range(s, 0, -1):
        j = volta[k][i]
        grupos.append(list(fotos[j:i]))
        i = j
    grupos.reverse()
    return grupos


def _ordenar_bloco_por_pessoas(bloco, modo_pessoas):
    modo = str(modo_pessoas or "manter").strip().lower()
    if modo not in ("individuais_primeiro", "turma_primeiro", "grupo_primeiro", "grupos_primeiro"):
        return list(bloco)
    return [
        f for _p, _pos, f in sorted(
            ((_chave_prioridade_pessoas(f, modo), idx, f) for idx, f in enumerate(bloco)),
            key=lambda it: (it[0], it[1]),
        )
    ]


def _processar_subbloco_pessoas_e_cenario(bloco, modo_pessoas, usar_cenario, limiar_cenario, estado_cen):
    """
    Aplica a ordenação Individual vs. Em Grupo e a identificação de cenários dentro de um bloco
    pertencente a um mesmo grupo numerado.
    - Se `usar_cenario` estiver desativado, devolve `[bloco_ordenado]`.
    - Se `usar_cenario` estiver ativado:
      - Quando `modo_pessoas` é `'individuais_primeiro'` ou `'turma_primeiro'`, ordena cada faixa
        por cenário e mantém no mesmo bloco do grupo numerado (permitindo dividir a lâmina na transição
        entre individuais e grupo, enquanto prioriza quebras entre cenários diferentes).
      - Quando `modo_pessoas` é `'manter'`, subdivide o grupo pelos cenários visuais detectados
        para que fotos do mesmo cenário fiquem juntas.
    """
    modo = str(modo_pessoas or "manter").strip().lower()
    tem_prioridade_pessoas = modo in ("individuais_primeiro", "turma_primeiro", "grupo_primeiro", "grupos_primeiro")
    ordenado_p = _ordenar_bloco_por_pessoas(bloco, modo)
    if not usar_cenario:
        estado_cen["total"] += 1
        return [ordenado_p]

    if tem_prioridade_pessoas:
        combinado = []
        for _prio, fatia in itertools.groupby(ordenado_p, key=lambda f: _chave_prioridade_pessoas(f, modo)):
            ord_c, sub_c = agrupar_por_cenario_no_bloco(
                list(fatia),
                limiar=limiar_cenario,
                offset_cenario=estado_cen["total"],
            )
            estado_cen["total"] += len(sub_c)
            combinado.extend(ord_c)
        return [combinado] if combinado else []

    _ord_c, sub_c = agrupar_por_cenario_no_bloco(
        ordenado_p,
        limiar=limiar_cenario,
        offset_cenario=estado_cen["total"],
    )
    estado_cen["total"] += len(sub_c)
    return sub_c


def _blocos_pastas_e_cenarios(fotos, cfg):
    """
    Organiza a lista de fotos em blocos sequenciais respeitando:
    - Grupos numerados e ordem manual definida pelo usuário (`respeitar_grupos_manuais`, ativo por padrão):
      Fotos do Grupo 1 vêm primeiro, depois Grupo 2, Grupo 3, etc., sem nunca misturar grupos diferentes na mesma lâmina.
    - Identificação por cenário dentro de cada grupo (`reconhecer_cenarios` e `limiar_cenario`).
    - Ordem de fotos individuais vs. em grupo (`ordem_pessoas_grupo`) dentro de cada grupo/bloco.
    - Divisão por pastas (`separar_pastas`), quando ativada.
    Devolve (blocos_finais, qtd_pastas, qtd_grupos, qtd_cenarios).
    """
    if not fotos:
        return [], 0, 0

    pastas_unicas = []
    for f in fotos:
        ch_pasta = f.get("pasta") or f.get("nome_pasta") or ""
        if ch_pasta not in pastas_unicas:
            pastas_unicas.append(ch_pasta)

    respeitar_grupos = bool(cfg.get("respeitar_grupos_manuais", True))
    separar_pastas = bool(cfg.get("separar_pastas", True))
    modo_pessoas = cfg.get("ordem_pessoas_grupo", "manter")
    usar_cenario = bool(cfg.get("reconhecer_cenarios", True))
    limiar_cenario = float(cfg.get("limiar_cenario", 0.28))

    tem_multiplos_grupos = len({int(f.get("grupo_ordem") or 1) for f in fotos}) > 1
    estado_cen = {"total": 0}
    blocos_finais = []

    if respeitar_grupos:
        cfg_sem_cen = dict(cfg)
        cfg_sem_cen["reconhecer_cenarios"] = False
        ordenadas = ordenar_e_agrupar_por_grupos(fotos, cfg_sem_cen)
        grupos_map = {}
        ordem_gids = []
        for f in ordenadas:
            gid = int(f.get("grupo_ordem") or 1)
            if gid not in grupos_map:
                grupos_map[gid] = []
                ordem_gids.append(gid)
            grupos_map[gid].append(f)

        for gid in ordem_gids:
            fotos_g = grupos_map[gid]
            if separar_pastas and not tem_multiplos_grupos:
                sub_pastas = {}
                ordem_p = []
                for f in fotos_g:
                    ch_p = f.get("pasta") or f.get("nome_pasta") or ""
                    if ch_p not in sub_pastas:
                        sub_pastas[ch_p] = []
                        ordem_p.append(ch_p)
                    sub_pastas[ch_p].append(f)
                for ch_p in ordem_p:
                    blocos_finais.extend(
                        _processar_subbloco_pessoas_e_cenario(
                            sub_pastas[ch_p], modo_pessoas, usar_cenario, limiar_cenario, estado_cen
                        )
                    )
            else:
                blocos_finais.extend(
                    _processar_subbloco_pessoas_e_cenario(
                        fotos_g, modo_pessoas, usar_cenario, limiar_cenario, estado_cen
                    )
                )
    else:
        if separar_pastas:
            mapa_pastas = {}
            ordem_p = []
            for f in fotos:
                ch_pasta = f.get("pasta") or f.get("nome_pasta") or ""
                if ch_pasta not in mapa_pastas:
                    mapa_pastas[ch_pasta] = []
                    ordem_p.append(ch_pasta)
                mapa_pastas[ch_pasta].append(f)
            for ch in ordem_p:
                blocos_finais.extend(
                    _processar_subbloco_pessoas_e_cenario(
                        mapa_pastas[ch], modo_pessoas, usar_cenario, limiar_cenario, estado_cen
                    )
                )
        else:
            blocos_finais.extend(
                _processar_subbloco_pessoas_e_cenario(
                    list(fotos), modo_pessoas, usar_cenario, limiar_cenario, estado_cen
                )
            )

    qtd_grupos = max(1, len({int(f.get("grupo_ordem") or 1) for f in fotos})) if respeitar_grupos else max(1, len(blocos_finais))
    qtd_cenarios = max(qtd_grupos, estado_cen["total"], len(blocos_finais))
    return blocos_finais, max(1, len(pastas_unicas)), qtd_cenarios


def _blocos_duros(fotos, cfg):
    """
    Blocos que nunca se misturam: grupo numerado e, com separar_pastas, pasta. As fotos do mesmo
    cenário continuam juntas e marcadas em cenario_subgrupo, para servirem só de ponto de corte preferido.
    """
    blocos, _p, _c = _blocos_pastas_e_cenarios(fotos, cfg)
    respeitar = bool(cfg.get("respeitar_grupos_manuais", True))
    multiplos = len({int(f.get("grupo_ordem") or 1) for f in fotos}) > 1
    por_pasta = bool(cfg.get("separar_pastas", True)) and not (respeitar and multiplos)

    def chave(bloco):
        f = bloco[0]
        return (
            int(f.get("grupo_ordem") or 1) if respeitar else 0,
            (f.get("pasta") or f.get("nome_pasta") or "") if por_pasta else "",
        )

    duros = []
    for bloco in blocos:
        if not bloco:
            continue
        if duros and chave(duros[-1]) == chave(bloco):
            duros[-1] = duros[-1] + list(bloco)
        else:
            duros.append(list(bloco))
    return duros


def _nome_bloco(bloco):
    f = bloco[0] if bloco else {}
    nome = str(f.get("nome_grupo") or "")
    if not nome or nome.startswith("Grupo "):
        nome = str(f.get("nome_pasta") or nome or "fotos")
    return nome


def _juntar_blocos_vizinhos(blocos, alvo):
    """
    Junta blocos vizinhos até sobrarem `alvo`: primeiro os do mesmo grupo numerado (pastas diferentes),
    e entre eles o par com menos fotos. Devolve (blocos, nomes de cada junção feita).
    """
    blocos = [list(b) for b in blocos]
    nomes = [[_nome_bloco(b)] for b in blocos]
    while len(blocos) > max(1, alvo):
        def chave(i):
            mesmo_grupo = int(blocos[i][0].get("grupo_ordem") or 1) == int(blocos[i + 1][0].get("grupo_ordem") or 1)
            return (0 if mesmo_grupo else 1, len(blocos[i]) + len(blocos[i + 1]), i)

        i = min(range(len(blocos) - 1), key=chave)
        blocos[i:i + 2] = [blocos[i] + blocos[i + 1]]
        nomes[i:i + 2] = [nomes[i] + nomes[i + 1]]
    juncoes = [list(dict.fromkeys(n)) for n in nomes if len(n) > 1]
    return blocos, juncoes


def _repartir_laminas(tamanhos, total):
    """
    Lâminas de cada bloco somando `total` (no máximo uma por foto): cada bloco começa com uma e as
    demais vão sempre para o bloco com mais fotos por lâmina, o que deixa as lâminas equilibradas.
    """
    alocadas = [1] * len(tamanhos)
    while sum(alocadas) < total:
        candidatos = [i for i, n in enumerate(tamanhos) if alocadas[i] < n]
        if not candidatos:
            break
        i = max(candidatos, key=lambda k: (tamanhos[k] / float(alocadas[k]), -k))
        alocadas[i] += 1
    return alocadas


def planejar_distribuicao(fotos, produto, cfg):
    """
    Divide as fotos em lâminas, nesta ordem de prioridade:
    1. o nº de lâminas desejadas (limitado ao máximo do produto e ao nº de fotos) sai exato, mesmo que
       as lâminas fiquem abaixo do mínimo ou acima do máximo de fotos — equilibradas e com aviso;
    2. não mistura grupos numerados nem, com separar_pastas, pastas; se houver mais blocos que lâminas
       desejadas, junta os vizinhos menores (com aviso);
    3. lâminas entre o mínimo ("Fotos por página") e o máximo sempre que o nº de lâminas permite;
    4. cortes preferidos na troca de cenário ou num intervalo de horário, com lâminas equilibradas.
    Sem nº desejado, usa laminas_ideais dentro do que o mínimo e o máximo de cada bloco permitem.
    Só sobram fotos quando nem o máximo de lâminas do produto com o máximo de fotos comporta todas.
    Devolve {"grupos", "sobras", "avisos", "minimo", "maximo", "desejadas", "laminas"}.
    """
    minimo, maximo = faixa_fotos_lamina(produto, cfg)
    laminas_max = max(1, int(produto["laminas_max"]))
    ordenadas = (
        ordenar_e_agrupar_por_grupos(fotos, cfg)
        if cfg.get("respeitar_grupos_manuais", True)
        else _ordenar_bloco_por_pessoas(list(fotos), cfg.get("ordem_pessoas_grupo", "manter"))
    )
    try:
        pedidas = int(cfg.get("laminas_desejadas") or 0)
    except (TypeError, ValueError):
        pedidas = 0
    desejadas = pedidas if pedidas > 0 else laminas_ideais(len(ordenadas), produto, cfg)
    plano = {"grupos": [], "sobras": [], "avisos": [], "minimo": minimo, "maximo": maximo,
             "desejadas": desejadas, "laminas": 0}
    if not ordenadas:
        return plano
    avisos = plano["avisos"]

    sobras = []
    capacidade = laminas_max * maximo
    if len(ordenadas) > capacidade:
        sobras = ordenadas[capacidade:]
        ordenadas = ordenadas[:capacidade]
        avisos.append(
            "%d foto(s) não couberam: o produto vai até %d lâminas com no máximo %d fotos cada."
            % (len(sobras), laminas_max, maximo)
        )
    usadas = len(ordenadas)
    blocos = _blocos_duros(ordenadas, cfg)

    if pedidas > 0:
        total = desejadas
        if total > laminas_max:
            avisos.append("O produto vai até %d lâminas; foram usadas %d, não %d." % (laminas_max, laminas_max, total))
            total = laminas_max
        if total > usadas:
            avisos.append("Só há %d foto(s): ficou uma por lâmina, em %d lâmina(s), não %d." % (usadas, usadas, total))
            total = usadas
    else:
        soma_min = sum(max(1, int(math.ceil(len(b) / float(maximo)))) for b in blocos)
        soma_max = sum(max(1, int(math.ceil(len(b) / float(maximo))), len(b) // minimo) for b in blocos)
        total = max(soma_min, min(soma_max, laminas_max, desejadas))

    if len(blocos) > total:
        blocos, juncoes = _juntar_blocos_vizinhos(blocos, total)
        avisos.append(
            "Para fechar %d lâmina(s), foram juntados grupos/pastas vizinhos na mesma lâmina: %s."
            % (total, "; ".join(" + ".join(nomes) for nomes in juncoes))
        )

    grupos = []
    abaixo = acima = 0
    for bloco, s in zip(blocos, _repartir_laminas([len(b) for b in blocos], total)):
        partes = _dividir_bloco_unico(bloco, minimo, max(maximo, int(math.ceil(len(bloco) / float(s)))), s)
        for parte in partes:
            if len(parte) < minimo <= len(bloco):
                abaixo += 1
            elif len(parte) > maximo:
                acima += 1
        grupos.extend(partes)
    tamanhos = [len(g) for g in grupos]
    if abaixo:
        avisos.append(
            "Para fechar %d lâminas com %d fotos, %d lâmina(s) ficaram abaixo do mínimo de %d fotos (a menor com %d)."
            % (len(grupos), usadas, abaixo, minimo, min(tamanhos))
        )
    if acima:
        avisos.append(
            "Para fechar %d lâminas com %d fotos, %d lâmina(s) passaram do máximo de %d fotos (a maior com %d)."
            % (len(grupos), usadas, acima, maximo, max(tamanhos))
        )

    pequenos = [b for b in blocos if len(b) < minimo]
    if pequenos:
        nomes = ", ".join(_nome_bloco(b) for b in pequenos[:4]) + ("..." if len(pequenos) > 4 else "")
        plano["avisos"].append(
            "%d grupo(s)/pasta(s) têm menos de %d fotos e ficaram com lâmina abaixo do mínimo: %s."
            % (len(pequenos), minimo, nomes)
        )

    plano.update(grupos=grupos, sobras=sobras, laminas=len(grupos))
    return plano


def dividir(fotos, produto, cfg):
    """Grupos de fotos (um por lâmina) e as fotos que não couberam (ver planejar_distribuicao)."""
    plano = planejar_distribuicao(fotos, produto, cfg)
    return plano["grupos"], plano["sobras"]


def previa_diagramacao(fotos, produto, cfg):
    """
    Calcula instantaneamente a prévia de como ficará a diagramação para exibir
    ao vivo na Janela de Diagramação Automática.
    """
    fotos_lista = list(fotos or [])
    if not fotos_lista:
        return {
            "total_fotos": 0,
            "pastas": 0,
            "grupos": 0,
            "cenarios": 0,
            "total_rostos": 0,
            "fotos_com_rostos": 0,
            "fotos_individuais": 0,
            "fotos_em_grupo": 0,
            "laminas_com_fotos": 0,
            "laminas_extras_vazias": 0,
            "laminas_estimadas": 0,
            "sobras": 0,
            "media_fotos_lamina": 0.0,
        }
    _blocos, qtd_pastas, qtd_cenarios = _blocos_pastas_e_cenarios(fotos_lista, cfg)
    qtd_grupos = max(1, len({int(f.get("grupo_ordem") or 1) for f in fotos_lista}))
    plano = planejar_distribuicao(fotos_lista, produto, cfg)
    grupos, sobras = plano["grupos"], plano["sobras"]
    laminas_com_fotos = len(grupos)
    avisos = list(plano["avisos"])
    laminas_totais = max(laminas_com_fotos, int(produto.get("laminas_min", 0))) if laminas_com_fotos else 0
    extras_vazias = max(0, laminas_totais - laminas_com_fotos)
    if extras_vazias:
        avisos.append("O produto pede no mínimo %d lâminas: %d %s." % (
            laminas_totais, extras_vazias, "ficaria vazia" if extras_vazias == 1 else "ficariam vazias"))
    usadas = sum(len(g) for g in grupos)
    media = usadas / float(max(1, laminas_com_fotos)) if laminas_com_fotos else 0.0
    total_rostos = 0
    fotos_com_rostos = 0
    fotos_individuais = 0
    fotos_em_grupo = 0
    for f in fotos_lista:
        if eh_foto_em_grupo(f):
            fotos_em_grupo += 1
        else:
            fotos_individuais += 1
        rosto = f.get("rosto") if isinstance(f, dict) else None
        if isinstance(rosto, dict):
            q = max(1, int(rosto.get("qtd") or 1))
            total_rostos += q
            fotos_com_rostos += 1
    return {
        "total_fotos": len(fotos_lista),
        "pastas": qtd_pastas,
        "grupos": qtd_grupos,
        "cenarios": qtd_cenarios,
        "total_rostos": total_rostos,
        "fotos_com_rostos": fotos_com_rostos,
        "fotos_individuais": fotos_individuais,
        "fotos_em_grupo": fotos_em_grupo,
        "laminas_com_fotos": laminas_com_fotos,
        "laminas_extras_vazias": extras_vazias,
        "laminas_estimadas": laminas_totais,
        "sobras": len(sobras),
        "media_fotos_lamina": round(media, 1),
        "minimo_lamina": plano["minimo"],
        "maximo_lamina": plano["maximo"],
        "menor_lamina": min((len(g) for g in grupos), default=0),
        "maior_lamina": max((len(g) for g in grupos), default=0),
        "avisos": avisos,
    }


def diagramar_album(fotos, produto, cfg, modelos=(), ao_progresso=None, avisos=None):
    """
    fotos: [{"caminho", "proporcao", "data", "destaque", "grupo_ordem", "ordem_manual", "pasta"}] na ordem do álbum.
    Cada layout é calculado na hora pelas proporções das fotos; `modelos` salvos só entram se o
    chamador os passar de propósito (a diagramação automática do programa não passa).
    Devolve (lâminas, sobras). Com `avisos` (lista), recebe as mensagens da distribuição
    (lâminas abaixo do mínimo ou acima do máximo para fechar as desejadas, pastas juntadas, lâminas vazias).
    """
    plano = planejar_distribuicao(list(fotos), produto, cfg)
    grupos, sobras = plano["grupos"], plano["sobras"]
    if avisos is not None:
        avisos.extend(plano["avisos"])
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
        if melhor.get("tipo_layout"):
            lamina["tipo_layout"] = melhor.get("tipo_layout")
        if grupo and grupo[0].get("grupo_ordem"):
            lamina["grupo_ordem"] = grupo[0].get("grupo_ordem")
            lamina["cenario_grupo"] = grupo[0].get("grupo_ordem")
        if grupo and grupo[0].get("cenario_subgrupo"):
            lamina["cenario_subgrupo"] = grupo[0].get("cenario_subgrupo")
        if grupo and grupo[0].get("rotulo_cenario"):
            lamina["rotulo_cenario"] = grupo[0].get("rotulo_cenario")
        if grupo and grupo[0].get("nome_grupo"):
            lamina["nome_grupo"] = grupo[0].get("nome_grupo")
        if grupo and grupo[0].get("nome_pasta"):
            lamina["nome_pasta"] = grupo[0].get("nome_pasta")
        if grupo and grupo[0].get("pasta"):
            lamina["pasta"] = grupo[0].get("pasta")
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
    # Lâminas vazias só para o mínimo físico do produto (nunca para alcançar as desejadas)
    min_prod = int(produto.get("laminas_min", 0))
    if laminas and len(laminas) < min_prod:
        if avisos is not None:
            vazias = min_prod - len(laminas)
            avisos.append(
                "O produto %s pede no mínimo %d lâminas: %d %s para completar."
                % (produto.get("nome", ""), min_prod, vazias, "ficou vazia" if vazias == 1 else "ficaram vazias")
            )
        while len(laminas) < min_prod:
            laminas.append(lamina_vazia(cfg))
    edicoes_fotos = {
        str(f.get("caminho")): dict(f.get("edicao") or {})
        for f in fotos
        if isinstance(f, dict) and f.get("caminho") and f.get("edicao")
    }
    for idx_lam, lamina in enumerate(laminas):
        for espaco in lamina["espacos"]:
            edicao = espaco.setdefault("edicao", {})
            cam_esp = str(espaco.get("foto") or "")
            if cam_esp and cam_esp in edicoes_fotos:
                for ch_ed, val_ed in edicoes_fotos[cam_esp].items():
                    if ch_ed != "corte" and ch_ed not in edicao:
                        edicao[ch_ed] = val_ed
            if not cfg.get("reconhecer_rostos", True):
                edicao["ignorar_rosto"] = True

        # Fundo, degradê e molduras na mesma sequência usada depois de cada edição na interface
        fotos_desta_lamina = grupos[idx_lam] if idx_lam < len(grupos) else []
        acabamento_lamina(lamina, fotos_desta_lamina, produto, cfg, indice_lamina=idx_lam)

    return laminas, [f["caminho"] for f in sobras]


# ==============================================================================
# Três Motores Independentes de Acabamento: (1) Fundos, (2) Degradê, (3) Molduras
# ==============================================================================

def _fotos_por_lado_da_lamina(lamina, fotos_lamina, produto):
    """
    Separa os caminhos e metadados das fotos que estão na Página Esquerda (x + w/2 < 0.5),
    na Página Direita (x + w/2 >= 0.5) e na Lâmina Inteira, além de indicar se alguma foto
    atravessa a dobra central.
    """
    mapa_f = {str(f.get("caminho")): f for f in (fotos_lamina or []) if isinstance(f, dict) and f.get("caminho")}
    esq_caminhos, dir_caminhos, todos_caminhos = [], [], []
    esq_meta, dir_meta = [], []
    atravessa_meio = False
    paginas = int(produto.get("paginas_por_lamina", 1))

    for esp in lamina.get("espacos") or []:
        cam = esp.get("foto")
        if not cam:
            continue
        cam_s = str(cam)
        todos_caminhos.append(cam_s)
        x0 = float(esp.get("x", 0.0))
        w = float(esp.get("w", 0.0))
        x1 = x0 + w
        if paginas > 1 and x0 < 0.485 and x1 > 0.515:
            atravessa_meio = True
        centro_x = x0 + w * 0.5
        meta = mapa_f.get(cam_s) or {
            "caminho": cam_s,
            "nome_grupo": lamina.get("nome_grupo"),
            "nome_pasta": lamina.get("nome_pasta"),
            "pasta": lamina.get("pasta"),
        }
        if paginas < 2 or centro_x < 0.5:
            esq_caminhos.append(cam_s)
            esq_meta.append(meta)
        else:
            dir_caminhos.append(cam_s)
            dir_meta.append(meta)

    return {
        "todos": todos_caminhos,
        "esquerda": esq_caminhos,
        "direita": dir_caminhos,
        "meta_esquerda": esq_meta,
        "meta_direita": dir_meta,
        "atravessa_meio": atravessa_meio,
    }


def _escolher_ou_gerar_fundo_para_fotos(caminhos_fotos, cfg, indice_seed=0, tema=None, medidas_mm=None):
    """
    Analisa as fotos do alvo (uma página ou a lâmina inteira) e devolve `(cor_hex_base, dados_fundo_imagem, paleta)`:
    - Se a origem permitir usar a Pasta de Fundos do usuário e houver arquivos nela, sugere com base
      na harmonia com as fotos + padrão/histórico de uso do usuário (`sugerir_fundos_da_pasta`).
    - Com `tema` (evento detectado), gera o fundo do tema com textura e um toque das cores das fotos.
    - Sem tema, gera um fundo com cor forte das fotos e textura escolhida pela luminosidade.
    `medidas_mm` = (largura, altura) da área coberta, para a textura sair na escala física certa.
    """
    import time
    from composicao import (
        MAPA_TEMAS, extrair_paleta_fotos, fundo_imagem_padrao, gerar_fundo_de_tema, gerar_galeria_fundos_procedurais,
    )
    from fundos import listar_fundos, sugerir_fundos_da_pasta

    paleta = extrair_paleta_fotos(caminhos_fotos)
    cor_base = paleta.get("cor_base") or "#f4efea"

    modo_origem = str(cfg.get("modo_auto_fundos", "misturar") or "misturar").strip().lower()
    estilo_gerado = str(cfg.get("estilo_fundo_gerado", "degrade_e_abstrato") or "degrade_e_abstrato").strip().lower()
    pasta_fundos = str(cfg.get("pasta_fundos") or "")
    arquivo_cfg_fundos = cfg.get("_arquivo_fundos")
    lista_pasta = cfg.get("_lista_fundos_pasta")
    if lista_pasta is None and pasta_fundos:
        try:
            lista_pasta = listar_fundos(pasta_fundos)
        except Exception:
            lista_pasta = []

    usar_da_pasta = False
    if lista_pasta:
        if modo_origem == "pasta":
            usar_da_pasta = True
        elif modo_origem == "misturar":
            # Alterna entre fundos gerados pelas fotos e sugestões da pasta do usuário
            usar_da_pasta = (int(indice_seed) % 2 == 1)

    if usar_da_pasta and lista_pasta:
        sugeridos = sugerir_fundos_da_pasta(arquivo_cfg_fundos, lista_pasta, paleta, limite=6)
        if sugeridos:
            escolhido = sugeridos[int(indice_seed) % len(sugeridos)]
            dados_pasta = fundo_imagem_padrao(escolhido)
            # Aplica leve harmonização cromática (16%) com a paleta das fotos sem descaracterizar o fundo padrão
            dados_pasta["ajustes"] = {
                "brilho": 0,
                "contraste": 0,
                "saturacao": 0,
                "matiz": 0,
                "tingir_cor": (paleta.get("tons_medios") or [cor_base])[0],
                "tingir_forca": 16,
            }
            return cor_base, dados_pasta, paleta

    seed_base = int(cfg.get("_seed_auto_fundos") or (int(time.time() * 1000) % 900000)) + int(indice_seed) * 7919
    if tema and tema in MAPA_TEMAS:
        dados_tema = gerar_fundo_de_tema(
            tema, fotos_lamina=caminhos_fotos, seed_extra=seed_base % 100000, medidas_mm=medidas_mm, paleta=paleta,
        )
        cores_t = (dados_tema.get("gerado") or {}).get("cores") or [MAPA_TEMAS[tema].get("cor_base") or cor_base]
        return str(cores_t[0]), dados_tema, paleta

    padrao_det = str(cfg.get("padrao_fundo_gerado") or paleta.get("padrao_sugerido") or "preto").lower()
    if padrao_det not in ("preto", "branco"):
        padrao_det = str(paleta.get("padrao_sugerido") or "preto").lower()
    galeria = gerar_galeria_fundos_procedurais(
        paleta, estilo=estilo_gerado, seed_base=seed_base, quantidade=4, padrao_filtro=padrao_det,
        medidas_mm=medidas_mm,
    )
    dados_gerado = galeria[int(indice_seed) % len(galeria)] if galeria else None
    if dados_gerado and isinstance(dados_gerado.get("gerado"), dict):
        cores_g = dados_gerado["gerado"].get("cores") or []
        if cores_g:
            cor_base = str(cores_g[0])
    return cor_base, dados_gerado, paleta


# ------------------------------------------------------------
# Marcação do que o acabamento automático colocou
# ------------------------------------------------------------
# dono["_auto_<chave>"] guarda a assinatura do valor que o automático gravou em dono[<chave>].
# Se o valor atual ainda tem a mesma assinatura, foi o automático e pode ser refeito;
# se mudou (edição à mão), é do usuário e fica. Sem marca, só vale refazer quando está vazio.

CAMPOS_FUNDO = ("fundo", "fundo_esquerda", "fundo_direita", "fundo_imagem", "fundo_imagem_esquerda", "fundo_imagem_direita")


def _assinatura(valor):
    import json

    return json.dumps(valor, sort_keys=True, default=str)


def _pode_auto(dono, chave):
    marca = dono.get("_auto_" + chave)
    if marca is None:
        return dono.get(chave) is None
    return marca == _assinatura(dono.get(chave))


def eh_automatico(dono, chave):
    """O valor dono[chave] existe e foi posto pelo acabamento automático (sem edição à mão depois)."""
    return isinstance(dono, dict) and dono.get(chave) is not None and dono.get("_auto_" + chave) == _assinatura(dono.get(chave))


def _marcar_auto(dono, chave):
    if dono.get(chave) is None:
        dono.pop("_auto_" + chave, None)
    else:
        dono["_auto_" + chave] = _assinatura(dono.get(chave))


def _tirar_auto(dono, chave):
    dono.pop(chave, None)
    dono.pop("_auto_" + chave, None)


def _assinatura_fundo(lamina):
    campos = {}
    for chave in CAMPOS_FUNDO:
        valor = lamina.get(chave)
        if isinstance(valor, dict) and valor.get("gerado"):
            # O caminho do PNG de prévia muda quando ele é recriado; não é edição do usuário
            valor = {k: v for k, v in valor.items() if k != "caminho"}
        campos[chave] = valor
    return _assinatura(campos)


def _fundo_e_automatico(lamina):
    marca = lamina.get("_auto_fundo")
    if marca is None:
        vazia = not any(lamina.get(c) for c in CAMPOS_FUNDO[1:])
        return vazia and str(lamina.get("fundo") or "#ffffff").lower() == "#ffffff"
    return marca == _assinatura_fundo(lamina)


def _semente_lamina(lamina):
    import hashlib

    return int(hashlib.md5(str(lamina.get("id") or "").encode("utf-8")).hexdigest()[:8], 16) % 900000 + 1


def _planejar_fundos(lamina, fotos_lamina, produto, cfg):
    """Como o fundo automático vai ser feito: divisão das fotos, se separa por página e o tema de cada parte."""
    divisao = _fotos_por_lado_da_lamina(lamina, fotos_lamina, produto)
    paginas = int(produto.get("paginas_por_lamina", 1))
    escopo = str(cfg.get("escopo_auto_fundos", "adaptativo") or "adaptativo").strip().lower()
    tipo_layout = str(lamina.get("tipo_layout") or "")

    separar = False
    if paginas >= 2 and divisao["esquerda"] and divisao["direita"] and not divisao["atravessa_meio"]:
        if escopo == "pagina":
            separar = True
        elif escopo == "adaptativo":
            cen_esq = {f.get("cenario_subgrupo") for f in divisao["meta_esquerda"] if f.get("cenario_subgrupo") is not None}
            cen_dir = {f.get("cenario_subgrupo") for f in divisao["meta_direita"] if f.get("cenario_subgrupo") is not None}
            if (cen_esq and cen_dir and cen_esq.isdisjoint(cen_dir)) or tipo_layout in ("meio_termo_esq", "meio_termo_dir"):
                separar = True
    atual = lamina.get("tema_evento")
    return {
        "divisao": divisao,
        "separar": separar,
        "tema": tema_evento_das_fotos(divisao["meta_esquerda"] + divisao["meta_direita"], preferido=atual),
        "tema_esquerda": tema_evento_das_fotos(divisao["meta_esquerda"], preferido=lamina.get("tema_evento_esquerda") or atual) if separar else None,
        "tema_direita": tema_evento_das_fotos(divisao["meta_direita"], preferido=lamina.get("tema_evento_direita") or atual) if separar else None,
    }


def _chave_fundo(plano, cfg):
    """Só o que muda o fundo: evento, divisão por página, origem/estilo e, sem evento, quais fotos."""
    temas = [plano["tema"], plano["tema_esquerda"], plano["tema_direita"]]
    return _assinatura({
        "separar": plano["separar"],
        "temas": temas,
        "fotos": None if any(temas) else sorted(plano["divisao"]["todos"]),
        "origem": cfg.get("modo_auto_fundos"),
        "estilo": cfg.get("estilo_fundo_gerado"),
    })


def acabamento_lamina(lamina, fotos_lamina=None, produto=None, cfg=None, indice_lamina=0, forcar=()):
    """
    Acabamento automático da lâmina, na ordem em que um motor depende do outro:
    fundo (define o evento) -> degradê (exclui moldura) -> molduras e sombra (contrastam com o fundo).

    Sem `forcar`, respeita o que foi mexido à mão e só refaz o que o próprio automático colocou;
    o fundo só é refeito quando muda o evento, a divisão por página ou (sem evento) as fotos.
    `forcar` = motores ("fundos", "degrade", "molduras") que refazem tudo, como nos botões de cada função.
    """
    if not isinstance(lamina, dict):
        return False
    cfg = dict(cfg or {})
    produto = produto or {"paginas_por_lamina": 2}
    forcar = set(forcar or ())
    cfg.setdefault("_seed_auto_fundos", _semente_lamina(lamina))
    alterou = False

    if cfg.get("auto_fundos", True) or "fundos" in forcar:
        plano = _planejar_fundos(lamina, fotos_lamina, produto, cfg)
        chave = _chave_fundo(plano, cfg)
        refazer = "fundos" in forcar or (_fundo_e_automatico(lamina) and lamina.get("_auto_fundo_chave") != chave)
        if refazer and aplicar_fundos_automaticos(lamina, fotos_lamina, produto, dict(cfg, auto_fundos=True), indice_lamina=indice_lamina):
            lamina["_auto_fundo"] = _assinatura_fundo(lamina)
            lamina["_auto_fundo_chave"] = chave
            alterou = True

    if cfg.get("auto_degrade", True) or "degrade" in forcar:
        alterou |= bool(aplicar_degrade_automatico(
            lamina, fotos_lamina, produto, dict(cfg, auto_degrade=True), respeitar_manual="degrade" not in forcar,
        ))

    if cfg.get("auto_molduras", True) or "molduras" in forcar:
        alterou |= bool(aplicar_molduras_automaticas(
            lamina, fotos_lamina, produto, dict(cfg, auto_molduras=True), indice_lamina=indice_lamina,
            respeitar_manual="molduras" not in forcar,
        ))
    return alterou


def _gravar_tema_evento(lamina, tema, tema_esquerda, tema_direita):
    """Guarda na lâmina o tema do evento usado no fundo, para as molduras combinarem."""
    for chave, valor in (("tema_evento", tema), ("tema_evento_esquerda", tema_esquerda), ("tema_evento_direita", tema_direita)):
        if valor:
            lamina[chave] = valor
        else:
            lamina.pop(chave, None)


def aplicar_fundos_automaticos(lamina, fotos_lamina=None, produto=None, cfg=None, indice_lamina=0, **kwargs):
    """
    FUNÇÃO 1 (Independente): Motor de Fundos Automáticos.
    - Analisa as fotos da lâmina inteira ou de cada página separadamente.
    - Evento reconhecido (estúdio, colação, jaleco, externas, baile...): fundo do tema do evento
      (textura e cores próprias, com um toque das cores das fotos). Grava `tema_evento` na lâmina.
    - Sem evento: fundo com cor forte das fotos (FATOR_COR_FUNDO) e textura pela luminosidade.
    """
    if not lamina:
        return False
    # Suporta chamada com lista de lâminas (como feita pela interface em app.pyw)
    if isinstance(lamina, (list, tuple)):
        cfg_eff = dict(produto if isinstance(produto, dict) and "paginas_por_lamina" not in produto else (cfg or {}))
        if kwargs.get("pasta_fundos") is not None:
            cfg_eff["pasta_fundos"] = kwargs["pasta_fundos"]
        if kwargs.get("arquivo_cfg_fundos") is not None:
            cfg_eff["_arquivo_fundos"] = kwargs["arquivo_cfg_fundos"]
        if kwargs.get("caminhos_pasta") is not None:
            cfg_eff["_lista_fundos_pasta"] = kwargs["caminhos_pasta"]
        if kwargs.get("seed_extra") is not None:
            cfg_eff["_seed_auto_fundos"] = int(kwargs["seed_extra"])
        prod_eff = produto if (isinstance(produto, dict) and "paginas_por_lamina" in produto) else {"paginas_por_lamina": 2}
        algum = False
        for idx_l, lam_item in enumerate(lamina):
            if aplicar_fundos_automaticos(lam_item, None, prod_eff, cfg_eff, indice_lamina=idx_l):
                algum = True
        return algum

    cfg = cfg or {}
    if not bool(cfg.get("auto_fundos", True)):
        return False
    produto = produto or {"paginas_por_lamina": 2}
    plano = _planejar_fundos(lamina, fotos_lamina, produto, cfg)
    divisao = plano["divisao"]
    if not divisao["todos"]:
        return False

    paginas = int(produto.get("paginas_por_lamina", 1))
    try:
        largura_lamina_mm, altura_mm = lamina_mm(produto)
    except (KeyError, TypeError, ValueError):
        largura_lamina_mm, altura_mm = None, None
    medidas_lamina = (largura_lamina_mm, altura_mm) if altura_mm else None
    medidas_pagina = (largura_lamina_mm / max(1, paginas), altura_mm) if altura_mm else None
    tema_lamina = plano["tema"]

    if plano["separar"]:
        tema_e = plano["tema_esquerda"]
        tema_d = plano["tema_direita"]
        cor_e, img_e, pal_e = _escolher_ou_gerar_fundo_para_fotos(
            divisao["esquerda"], cfg, indice_seed=indice_lamina * 2, tema=tema_e, medidas_mm=medidas_pagina,
        )
        cor_d, img_d, pal_d = _escolher_ou_gerar_fundo_para_fotos(
            divisao["direita"], cfg, indice_seed=indice_lamina * 2 + 1, tema=tema_d, medidas_mm=medidas_pagina,
        )
        lamina["fundo"] = cor_e
        lamina["fundo_esquerda"] = cor_e
        lamina["fundo_direita"] = cor_d
        if img_e:
            lamina["fundo_imagem_esquerda"] = img_e
        if img_d:
            lamina["fundo_imagem_direita"] = img_d
        lamina["_paleta_esquerda"] = pal_e
        lamina["_paleta_direita"] = pal_d
        _gravar_tema_evento(lamina, tema_lamina or tema_e or tema_d, tema_e, tema_d)
        return True

    cor_l, img_l, pal_l = _escolher_ou_gerar_fundo_para_fotos(
        divisao["todos"], cfg, indice_seed=indice_lamina, tema=tema_lamina, medidas_mm=medidas_lamina,
    )
    _gravar_tema_evento(lamina, tema_lamina, None, None)
    lamina["fundo"] = cor_l
    lamina.pop("fundo_esquerda", None)
    lamina.pop("fundo_direita", None)
    lamina.pop("fundo_imagem_esquerda", None)
    lamina.pop("fundo_imagem_direita", None)
    if img_l:
        lamina["fundo_imagem"] = img_l
    lamina["_paleta_lamina"] = pal_l
    return True


def aplicar_degrade_automatico(lamina, fotos_lamina=None, produto=None, cfg=None, respeitar_manual=False):
    """
    FUNÇÃO 2 (Independente): Motor de Degradê Automático nas Fotos e na Fusão de Páginas.
    Regras próprias:
    1. Foto SOZINHA na página sangrada: layouts Meio-Termo ('meio_termo_esq', 'meio_termo_dir')
       na página 100% sangrada, ou 1 única foto ocupando toda a página até a sangria externa.
    2. Foto marcada com ESTRELA (Destaque ★): quando está em Transpasse (60/40 ou 70/30) ou
       sozinha na sua página.
    - Nunca aplica degradê aleatoriamente em fotos sem estrela em grades ('safe_zone' / 'sem_corte')
      nem em Transpasses sem estrela.
    - Protege os rostos/pessoas detectados na foto para que o esfumaçado nunca atinja ninguém.
    - Quando a lâmina possui fundos separados por página, ativa fusão suave no centro ('fundo_fusao').
    - Degradês postos antes pelo automático são refeitos (e somem onde não cabem mais).
      Com `respeitar_manual`, degradê ou moldura mexidos à mão ficam como estão.
    """
    if not lamina:
        return False
    if isinstance(lamina, (list, tuple)):
        cfg_eff = produto if (isinstance(produto, dict) and "paginas_por_lamina" not in produto) else (cfg or {})
        prod_eff = cfg if (isinstance(cfg, dict) and "paginas_por_lamina" in cfg) else {"paginas_por_lamina": 2}
        algum = False
        for lam_item in lamina:
            if aplicar_degrade_automatico(lam_item, fotos_lamina, prod_eff, cfg_eff, respeitar_manual=respeitar_manual):
                algum = True
        return algum
    cfg = cfg or {}
    if not bool(cfg.get("auto_degrade", True)):
        return False

    try:
        from composicao import tem_fundo_por_pagina
    except ImportError:
        def tem_fundo_por_pagina(lam):
            return bool(lam.get("fundo_esquerda") or lam.get("fundo_direita") or lam.get("fundo_imagem_esquerda") or lam.get("fundo_imagem_direita"))
    from comum import normalizar_caminho

    regra = str(cfg.get("regra_auto_degrade", "destaques") or "destaques").strip().lower()
    intensidade = str(cfg.get("intensidade_auto_degrade", "suave") or "suave").strip().lower()
    mapa_inicio = {"suave": 68, "medio": 56, "forte": 44}
    inicio_base = mapa_inicio.get(intensidade, 68)
    alterou = False

    manuais = set()
    for esp_ in lamina.get("espacos") or []:
        if not esp_.get("foto"):
            continue
        ed_ = esp_.setdefault("edicao", {})
        if eh_automatico(ed_, "degrade"):
            _tirar_auto(ed_, "degrade")
            alterou = True
        elif respeitar_manual and (
            ed_.get("degrade") is not None
            or (ed_.get("moldura") is not None and not eh_automatico(ed_, "moldura"))
        ):
            manuais.add(id(esp_))

    def _gravar_degrade(esp_, dados):
        ed_ = esp_.setdefault("edicao", {})
        ed_["degrade"] = dados
        _marcar_auto(ed_, "degrade")
        # Regra de exclusão mútua: foto com degradê de borda nunca recebe linha de moldura por cima
        _tirar_auto(ed_, "moldura")

    # Regra A: Fusão central suave entre páginas quando esquerda e direita têm fundos próprios
    if regra in ("destaques", "destaque_e_fusao", "somente_fusao") and tem_fundo_por_pagina(lamina):
        if not respeitar_manual or _pode_auto(lamina, "fundo_fusao"):
            lamina["fundo_fusao"] = {"suave": 36, "medio": 50, "forte": 66}.get(intensidade, 36)
            _marcar_auto(lamina, "fundo_fusao")
            alterou = True
    elif eh_automatico(lamina, "fundo_fusao"):
        _tirar_auto(lamina, "fundo_fusao")
        alterou = True

    if regra == "somente_fusao":
        return alterou

    # Regra B: Degradê apenas em fotos com ESTRELA (destaque) ou SOZINHAS na página sangrada
    espacos = lamina.get("espacos") or []
    com_foto = [e for e in espacos if e.get("foto")]
    tipo_layout = str(lamina.get("tipo_layout") or "")
    if tipo_layout in ("safe_zone", "sem_corte", "sangria_total", "panoramico_meio"):
        return alterou

    if len(com_foto) == 1:
        if regra not in ("destaque_apenas", "somente_destaque"):
            e0 = com_foto[0]
            x0, w0, h0 = float(e0.get("x", 0.0)), float(e0.get("w", 0.0)), float(e0.get("h", 0.0))
            if h0 >= 0.88 and w0 <= 0.62 and (x0 <= 0.02 or x0 + w0 >= 0.98) and id(e0) not in manuais:
                direcao_u = "direita" if x0 <= 0.02 else "esquerda"
                _gravar_degrade(e0, {
                    "ativo": True,
                    "direcao": direcao_u,
                    "inicio": inicio_base,
                    "fim": 100,
                    "opacidade_fim": 0,
                })
                return True
        return alterou
    if len(com_foto) < 2:
        return alterou

    mapa_rostos = {}
    mapa_destaque = {}
    if isinstance(fotos_lamina, dict):
        for ch_f, info_f in fotos_lamina.items():
            if isinstance(info_f, dict):
                ch_n = normalizar_caminho(str(ch_f))
                if isinstance(info_f.get("rosto"), dict):
                    mapa_rostos[str(ch_f)] = info_f["rosto"]
                    mapa_rostos[ch_n] = info_f["rosto"]
                if info_f.get("destaque"):
                    mapa_destaque[str(ch_f)] = True
                    mapa_destaque[ch_n] = True
    else:
        for f in fotos_lamina or []:
            if isinstance(f, dict) and f.get("caminho"):
                cam_f = str(f["caminho"])
                cam_n = normalizar_caminho(cam_f)
                if isinstance(f.get("rosto"), dict):
                    mapa_rostos[cam_f] = f["rosto"]
                    mapa_rostos[cam_n] = f["rosto"]
                if f.get("destaque"):
                    mapa_destaque[cam_f] = True
                    mapa_destaque[cam_n] = True

    for esp in com_foto:
        ex = float(esp.get("x", 0.0))
        ew = float(esp.get("w", 0.0))
        eh = float(esp.get("h", 0.0))
        ex1 = ex + ew
        centro_x = ex + ew * 0.5
        cam_esp = str(esp.get("foto") or "")
        cam_esp_norm = normalizar_caminho(cam_esp) if cam_esp else ""

        qtd_mesma_pag = sum(
            1
            for e2 in com_foto
            if ((float(e2.get("x", 0.0)) + float(e2.get("w", 0.0)) * 0.5) < 0.5) == (centro_x < 0.5)
        )
        tem_estrela = bool(
            esp.get("destaque")
            or mapa_destaque.get(cam_esp)
            or (cam_esp_norm and mapa_destaque.get(cam_esp_norm))
        )

        # 1. Foto SOZINHA na página sangrada
        eh_sozinha_sangrada = False
        direcao_deg = None
        if tipo_layout == "meio_termo_esq" and ex <= 0.02 and abs(ew - 0.5) <= 0.05 and eh >= 0.90:
            eh_sozinha_sangrada = True
            direcao_deg = "direita"
        elif tipo_layout == "meio_termo_dir" and ex >= 0.45 and ex1 >= 0.98 and eh >= 0.90:
            eh_sozinha_sangrada = True
            direcao_deg = "esquerda"
        elif qtd_mesma_pag == 1 and eh >= 0.95 and ew <= 0.54 and tipo_layout != "sangria_total":
            if ex <= 0.015:
                eh_sozinha_sangrada = True
                direcao_deg = "direita"
            elif ex1 >= 0.985:
                eh_sozinha_sangrada = True
                direcao_deg = "esquerda"

        # 2. Foto com ESTRELA (Destaque ★) em Transpasse ou sozinha na página
        if not eh_sozinha_sangrada and tem_estrela and regra != "meio_termo_apenas":
            if ex < 0.48 and ex1 > 0.52 and eh >= 0.85:
                if ex <= 0.02 and ex1 < 0.85:
                    direcao_deg = "direita"
                elif ex1 >= 0.98 and ex > 0.15:
                    direcao_deg = "esquerda"
            elif qtd_mesma_pag == 1 and eh >= 0.85:
                if centro_x < 0.5 and ex <= 0.05:
                    direcao_deg = "direita"
                elif centro_x >= 0.5 and ex1 >= 0.95:
                    direcao_deg = "esquerda"

        if regra in ("destaque_apenas", "somente_destaque") and not tem_estrela:
            direcao_deg = None

        if not direcao_deg or id(esp) in manuais:
            continue

        # Proteção inteligente da caixa de rostos/pessoas: o degradê nunca começa sobre alguém
        ini_seguro = inicio_base
        rosto = mapa_rostos.get(cam_esp) or (mapa_rostos.get(cam_esp_norm) if cam_esp_norm else None)
        if isinstance(rosto, dict):
            if direcao_deg == "direita" and rosto.get("x1") is not None:
                limite_rosto = int(round(float(rosto["x1"]) * 100.0)) + 6
                ini_seguro = max(ini_seguro, min(90, limite_rosto))
            elif direcao_deg == "esquerda" and rosto.get("x0") is not None:
                limite_rosto = int(round((1.0 - float(rosto["x0"])) * 100.0)) + 6
                ini_seguro = max(ini_seguro, min(90, limite_rosto))

        _gravar_degrade(esp, {
            "ativo": True,
            "direcao": direcao_deg,
            "inicio": int(ini_seguro),
            "fim": 100,
            "opacidade_fim": 0,
        })
        alterou = True

    return alterou


def aplicar_molduras_automaticas(lamina, fotos_lamina=None, produto=None, cfg=None, indice_lamina=0, respeitar_manual=False):
    """
    FUNÇÃO 3 (Independente): Motor de Molduras Automáticas nas Fotos.
    Regras próprias:
    - Aplica moldura apenas em quadros em grade / com margem ('safe_zone', 'sem_corte', quadros compostos).
    - Nunca aplica moldura em fotos 100% sangradas ('sangria_total', 'panoramico_meio') nem em fotos que
      possuam degradê de borda ativo.
    - Estilo 'por_evento' (padrão): usa o conjunto de molduras do evento da página (composicao.MOLDURAS_EVENTO).
      A foto de destaque (estrela, ou a maior da página) recebe o estilo de destaque e as demais o secundário,
      que reveza com o alternativo de uma lâmina para a outra (pelo índice, sem sorteio).
    - Cor 'cores_do_evento' (padrão): cores do evento, conferidas contra o fundo da página para ter contraste.
    - Molduras e sombras postas antes pelo automático são refeitas (e somem onde não cabem mais).
      Com `respeitar_manual`, as mexidas à mão ficam como estão.
    """
    if not lamina:
        return False
    if isinstance(lamina, (list, tuple)):
        cfg_eff = produto if (isinstance(produto, dict) and "paginas_por_lamina" not in produto) else (cfg or {})
        prod_eff = cfg if (isinstance(cfg, dict) and "paginas_por_lamina" in cfg) else {"paginas_por_lamina": 2}
        algum = False
        for idx_l, lam_item in enumerate(lamina):
            if aplicar_molduras_automaticas(lam_item, None, prod_eff, cfg_eff, indice_lamina=idx_l,
                                            respeitar_manual=respeitar_manual):
                algum = True
        return algum
    cfg = cfg or {}
    if not bool(cfg.get("auto_molduras", True)):
        return False

    from comum import cor_rgb
    from composicao import (
        MAPA_TEMAS, MOLDURAS_EVENTO, MOLDURAS_SEM_EVENTO, NOMES_ESTILOS_MOLDURA,
        cores_moldura_com_contraste, extrair_paleta_fotos, sombra_padrao,
    )

    regra = str(cfg.get("regra_auto_molduras", "grades_com_margem") or "grades_com_margem").strip().lower()
    estilo_cfg = str(cfg.get("estilo_auto_molduras", "por_evento") or "por_evento").strip().lower()
    por_evento = estilo_cfg not in NOMES_ESTILOS_MOLDURA
    cor_modo = str(cfg.get("cor_auto_molduras", "cores_do_evento") or "cores_do_evento").strip().lower()
    cor_sec_cfg = str(cfg.get("cor_secundaria_moldura") or "").strip()
    incluir_sombra = bool(cfg.get("auto_sombra_molduras", False))
    sombra_cfg = cfg.get("cfg_sombra_molduras") or cfg.get("sombra_padrao_cfg")
    try:
        espessura_mm = max(0.2, min(3.0, float(cfg.get("espessura_auto_molduras_mm", 0.6) or 0.6)))
    except (TypeError, ValueError):
        espessura_mm = 0.6

    espacos = [e for e in (lamina.get("espacos") or []) if e.get("foto")]
    if not espacos:
        return False

    produto = produto or {"paginas_por_lamina": 2}
    paginas = int(produto.get("paginas_por_lamina", 2) or 2)
    caminhos_todos = [str(e["foto"]) for e in espacos if e.get("foto")]
    paleta = lamina.get("_paleta_lamina") or extrair_paleta_fotos(caminhos_todos)
    mapa_fotos = {str(f.get("caminho")): f for f in (fotos_lamina or []) if isinstance(f, dict) and f.get("caminho")}

    # Tema do evento: o mesmo que o fundo usou; sem fundo automático, detecta pelas fotos.
    tema_geral = lamina.get("tema_evento")
    if not tema_geral and not lamina.get("tema_evento_esquerda") and not lamina.get("tema_evento_direita"):
        divisao = _fotos_por_lado_da_lamina(lamina, fotos_lamina, produto)
        tema_geral = tema_evento_das_fotos(divisao["meta_esquerda"] + divisao["meta_direita"])

    def _lado(esp):
        centro_x = float(esp.get("x", 0.0)) + float(esp.get("w", 0.0)) * 0.5
        return "esquerda" if (paginas < 2 or centro_x < 0.5) else "direita"

    def _tema(esp):
        tema = lamina.get("tema_evento_" + _lado(esp)) or tema_geral
        return tema if tema in MOLDURAS_EVENTO else None

    def _cor_fundo(esp):
        lado = _lado(esp)
        return lamina.get("fundo_" + lado) or lamina.get("fundo") or "#ffffff"

    def _cores_contraste_classico(esp):
        cor_fundo_hex = _cor_fundo(esp)
        r, g, b = cor_rgb(cor_fundo_hex)
        lum_fundo = (0.299 * r + 0.587 * g + 0.114 * b) / 255.0
        dados_img = lamina.get("fundo_imagem_" + _lado(esp)) or lamina.get("fundo_imagem") or {}
        sub_g = str(((dados_img.get("gerado") or {}).get("subtipo")) or "")
        if "noturn" in sub_g or lum_fundo < 0.78:
            return paleta.get("cor_moldura_clara") or "#faf8f5"
        return paleta.get("cor_moldura_escura") or "#52483e"

    def _cores_moldura(esp):
        """(cor, cor_secundaria) da moldura do espaço."""
        if cor_modo == "branca":
            return "#ffffff", cor_sec_cfg
        if cor_modo == "dourada":
            return "#d4af37", cor_sec_cfg
        if cor_modo == "paleta":
            return (paleta.get("tons_medios") or ["#d4c5b4"])[0], cor_sec_cfg
        if cor_modo in ("cores_do_evento", "evento"):
            tema = _tema(esp)
            if tema:
                conjunto = MOLDURAS_EVENTO[tema]
                return cores_moldura_com_contraste(conjunto["cor"], conjunto.get("cor_secundaria"), _cor_fundo(esp))
            destaque_foto = (paleta.get("cores_destaque_fotos") or [None])[0]
            if destaque_foto:
                return cores_moldura_com_contraste(destaque_foto, _cores_contraste_classico(esp), _cor_fundo(esp))
        return _cores_contraste_classico(esp), cor_sec_cfg

    # Destaque de cada página: a foto com estrela, senão a maior (só quando a página tem mais de uma foto)
    destaques = set()
    for lado in ("esquerda", "direita"):
        da_pagina = [e for e in espacos if _lado(e) == lado]
        if not da_pagina:
            continue
        com_estrela = [e for e in da_pagina if (mapa_fotos.get(str(e["foto"])) or {}).get("destaque")]
        if com_estrela:
            destaques.update(id(e) for e in com_estrela)
        else:
            destaques.add(id(max(da_pagina, key=lambda e: float(e.get("w", 0.0)) * float(e.get("h", 0.0)))))

    def _estilo(esp):
        if not por_evento:
            return estilo_cfg
        conjunto = MOLDURAS_EVENTO.get(_tema(esp)) or MOLDURAS_SEM_EVENTO
        if id(esp) in destaques:
            return conjunto["destaque"]
        return conjunto["secundaria"] if int(indice_lamina) % 2 == 0 else conjunto["alternativa"]

    def _sombra(esp):
        if sombra_cfg:
            s_cfg = dict(sombra_cfg)
        else:
            tema = _tema(esp) if por_evento else None
            s_cfg = dict((MAPA_TEMAS.get(tema) or {}).get("sombra") or sombra_padrao())
        s_cfg["ativa"] = True
        s_cfg["sincronizar_moldura"] = True
        return s_cfg

    qtd_esq = sum(1 for e in espacos if _lado(e) == "esquerda")
    qtd_dir = len(espacos) - qtd_esq
    maior_area = max(float(e.get("w", 0.0)) * float(e.get("h", 0.0)) for e in espacos)

    def _livre(ed, chave):
        return not respeitar_manual or _pode_auto(ed, chave)

    def _retirar(ed, chave):
        nonlocal alterou
        if ed.get(chave) is not None and _livre(ed, chave):
            _tirar_auto(ed, chave)
            alterou = True

    alterou = False
    for esp in espacos:
        ed = esp.setdefault("edicao", {})
        # Regra 1: Nunca coloca moldura se a foto tem degradê ativo
        if (ed.get("degrade") or {}).get("ativo"):
            _retirar(ed, "moldura")
            continue

        ex = float(esp.get("x", 0.0))
        ey = float(esp.get("y", 0.0))
        ew = float(esp.get("w", 0.0))
        eh = float(esp.get("h", 0.0))
        area_e = ew * eh

        # Regra 2: Nunca coloca moldura em foto sangrada nas bordas externas da lâmina
        sangrada_borda = (
            ex <= 0.005
            or ey <= 0.005
            or (ex + ew) >= 0.995
            or (ey + eh) >= 0.995
        )
        if sangrada_borda:
            _retirar(ed, "moldura")
            if incluir_sombra or eh_automatico(ed, "sombra"):
                _retirar(ed, "sombra")
            continue

        qtd_na_pagina = qtd_esq if _lado(esp) == "esquerda" else qtd_dir

        if regra in ("todas_com_margem", "grades_e_margem", "grades_com_margem"):
            elegivel = True
        elif regra == "secundarias":
            elegivel = len(espacos) >= 2 and area_e < maior_area * 0.85
        elif regra == "apenas_grades":
            elegivel = qtd_na_pagina >= 2
        else:
            elegivel = True

        if not elegivel:
            if eh_automatico(ed, "moldura"):
                _retirar(ed, "moldura")
            continue

        if _livre(ed, "moldura"):
            cor_escolhida, cor_secundaria = _cores_moldura(esp)
            dados_m = {
                "estilo": _estilo(esp),
                "espessura_mm": round(espessura_mm, 2),
                "cor": cor_escolhida,
            }
            if cor_secundaria:
                dados_m["cor_secundaria"] = cor_secundaria
            ed["moldura"] = dados_m
            _marcar_auto(ed, "moldura")
            alterou = True
        if incluir_sombra and _livre(ed, "sombra"):
            ed["sombra"] = _sombra(esp)
            _marcar_auto(ed, "sombra")
            alterou = True

    return alterou


def aplicar_sombra_automatica(lamina, cfg_sombra=None, apenas_com_moldura=False):
    """
    Aplica Sombra Projetada identificando automaticamente as fotos com moldura
    ou as fotos em grade/margem elegíveis para moldura (nunca em fotos sangradas 100% ou com degradê).
    Aceita tanto uma lâmina (`dict`) quanto uma lista de lâminas (`list`).
    """
    try:
        from composicao import sombra_padrao, tem_degrade_ativo
    except ImportError:
        def sombra_padrao():
            return {"ativa": False, "cor": "#000000", "opacidade": 40, "desfoque_mm": 2.2, "offset_x_mm": 1.2, "offset_y_mm": 1.6, "expansao_mm": 0.2, "sincronizar_moldura": True}
        def tem_degrade_ativo(d):
            return bool(isinstance(d, dict) and d.get("ativo"))

    if isinstance(lamina, list):
        alterou_alguma = False
        for lam in lamina:
            if aplicar_sombra_automatica(lam, cfg_sombra=cfg_sombra, apenas_com_moldura=apenas_com_moldura):
                alterou_alguma = True
        return alterou_alguma

    s_base = sombra_padrao()
    if isinstance(cfg_sombra, dict):
        s_base.update(cfg_sombra)
    s_base["ativa"] = True

    alterou = False
    for esp in lamina.get("espacos") or []:
        if not esp.get("foto"):
            continue
        ed = esp.setdefault("edicao", {})
        if tem_degrade_ativo(ed.get("degrade")):
            continue
        ex = float(esp.get("x", 0.0))
        ey = float(esp.get("y", 0.0))
        ew = float(esp.get("w", 0.0))
        eh = float(esp.get("h", 0.0))
        sangrada_borda = (
            ex <= 0.005
            or ey <= 0.005
            or (ex + ew) >= 0.995
            or (ey + eh) >= 0.995
        )
        if sangrada_borda:
            ed.pop("sombra", None)
            continue
        if apenas_com_moldura and not ed.get("moldura"):
            continue
        ed["sombra"] = dict(s_base)
        alterou = True
    return alterou


def aplicar_tema_em_laminas(
    laminas_alvo,
    id_tema,
    info=None,
    aplicar_fundo=True,
    aplicar_moldura=True,
    aplicar_sombra=True,
    aplicar_edicao=True,
):
    """
    Aplica um preset completo de Tema (Natal, Páscoa, Casamento, Aniversário, Colação de Grau,
    15 Anos, Bodas ou Batizado) em uma lâmina ou lista de lâminas, gerando fundos `.png`
    editáveis na pasta temporária e coordenando moldura, sombra projetada e edição de cor.
    """
    from composicao import CATALOGO_TEMAS, MAPA_TEMAS, gerar_fundo_de_tema, tem_degrade_ativo

    if isinstance(laminas_alvo, dict):
        laminas_alvo = [laminas_alvo]
    tema_cfg = MAPA_TEMAS.get(id_tema) or CATALOGO_TEMAS[0]
    alteradas = 0

    for idx_l, lam in enumerate(laminas_alvo or []):
        if not isinstance(lam, dict):
            continue
        espacos = [e for e in (lam.get("espacos") or []) if e.get("foto")]
        fotos_caminhos = [str(e["foto"]) for e in espacos if e.get("foto")]

        if aplicar_fundo:
            dados_fundo = gerar_fundo_de_tema(tema_cfg["id"], fotos_lamina=fotos_caminhos, info=info, seed_extra=idx_l * 37)
            lam["fundo"] = str(tema_cfg.get("cor_base") or "#14161c")
            lam["fundo_imagem"] = dados_fundo
            lam.pop("fundo_esquerda", None)
            lam.pop("fundo_direita", None)
            lam.pop("fundo_imagem_esquerda", None)
            lam.pop("fundo_imagem_direita", None)

        for esp in espacos:
            ed = esp.setdefault("edicao", {})
            ex = float(esp.get("x", 0.0))
            ey = float(esp.get("y", 0.0))
            ew = float(esp.get("w", 0.0))
            eh = float(esp.get("h", 0.0))
            sangrada_borda = (
                ex <= 0.005
                or ey <= 0.005
                or (ex + ew) >= 0.995
                or (ey + eh) >= 0.995
            )
            tem_deg = tem_degrade_ativo(ed.get("degrade"))

            if aplicar_sombra and not aplicar_moldura:
                if not sangrada_borda and not tem_deg:
                    ed["sombra"] = dict(tema_cfg.get("sombra") or {"ativa": True, "opacidade": 50, "desfoque_mm": 2.5, "offset_x_mm": 1.3, "offset_y_mm": 1.7, "sincronizar_moldura": True})
                else:
                    ed.pop("sombra", None)

            if aplicar_edicao and isinstance(tema_cfg.get("edicao_foto"), dict):
                for ch_ed, val_ed in tema_cfg["edicao_foto"].items():
                    ed[ch_ed] = val_ed

        # O tema escolhido passa a ser o evento da lâmina: as molduras seguem o conjunto do evento
        # (destaque, secundárias e rodízio) e continuam se ajustando nas próximas edições.
        _gravar_tema_evento(lam, tema_cfg["id"], None, None)
        if aplicar_moldura:
            espessura = float((tema_cfg.get("moldura") or {}).get("espessura_mm") or 0.5)
            aplicar_molduras_automaticas(lam, None, None, {
                "estilo_auto_molduras": "por_evento",
                "cor_auto_molduras": "cores_do_evento",
                "espessura_auto_molduras_mm": espessura,
                "auto_sombra_molduras": bool(aplicar_sombra),
            }, indice_lamina=idx_l)
        lam["tema_aplicado"] = tema_cfg["id"]
        alteradas += 1

    return alteradas


