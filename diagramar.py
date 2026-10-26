# -*- coding: utf-8 -*-
"""
Diagramação automática.

1. As fotos (na ordem) são divididas em grupos, um por lâmina. A divisão
   prefere cortar onde há intervalo maior entre as fotos (data EXIF) e deixa
   as fotos de destaque sozinhas numa lâmina.
2. Cada grupo vira uma lâmina: o programa testa as divisões entre a página
   da esquerda e a da direita, os modelos da biblioteca e layouts gerados na
   hora (linhas e colunas), e fica com o que corta menos as fotos e ocupa
   melhor a página.
"""

import itertools
import math
import uuid

from modelos_lamina import espacos_do_modelo
from produtos import area_util_pagina_mm, lamina_mm, normalizar_mm

CORTE_MAXIMO = 1.18
MAX_POR_PAGINA = 8
MAX_LINHAS = 4
PESO_CORTE = 12.0
PESO_VAZIO = 4.0
PESO_PEQUENA = 1.5
PESO_DESEQUILIBRIO = 0.35
PESO_PAGINA_VAZIA = 3.0
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
    return {"id": novo_id(), "fundo": cfg.get("fundo", "#ffffff"), "espacos": [], "sobreposicoes": [], "modelo": None}


# ------------------------------------------------------------
# Layout de uma página (em mm)
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


def _layout_linhas(proporcoes, area, espaco, linhas):
    """
    Fotos em linhas que preenchem a largura. Se a altura não fecha, corta
    as fotos até CORTE_MAXIMO e o que sobrar vira espaço vazio, centralizado.
    Devolve (quadros, corte, fração vazia).
    """
    x0, y0, W, H = area
    alturas = []
    inicio = 0
    for k in linhas:
        soma = sum(proporcoes[inicio:inicio + k])
        alturas.append((W - espaco * (k - 1)) / soma)
        inicio += k
    total = sum(alturas)
    disponivel = H - espaco * (len(linhas) - 1)
    if disponivel <= 0 or W <= 0:
        return None
    s = disponivel / total
    fator_largura = 1.0
    if s > CORTE_MAXIMO:
        fator_altura = CORTE_MAXIMO
        corte = CORTE_MAXIMO
    elif s < 1.0 / CORTE_MAXIMO:
        fator_altura = s
        fator_largura = s * CORTE_MAXIMO
        corte = CORTE_MAXIMO
    else:
        fator_altura = s
        corte = max(s, 1.0 / s)
    alturas = [h * fator_altura for h in alturas]
    altura_total = sum(alturas) + espaco * (len(linhas) - 1)
    y = y0 + (H - altura_total) / 2.0
    quadros = []
    inicio = 0
    usado = 0.0
    for k, h in zip(linhas, alturas):
        larguras = [proporcoes[i] * h / fator_altura * fator_largura for i in range(inicio, inicio + k)]
        largura_linha = sum(larguras) + espaco * (k - 1)
        x = x0 + (W - largura_linha) / 2.0
        for w in larguras:
            quadros.append((x, y, w, h))
            usado += w * h
            x += w + espaco
        y += h + espaco
        inicio += k
    vazio = max(0.0, 1.0 - usado / (W * H - 0.0001))
    return quadros, corte, vazio


def _custo(quadros, proporcoes, area, corte, vazio):
    _x, _y, W, H = area
    custo = PESO_CORTE * math.log(corte) ** 2 + PESO_VAZIO * max(0.0, vazio - 0.12)
    menor_lado = min(W, H)
    for (_qx, _qy, w, h) in quadros:
        if min(w, h) < 0.22 * menor_lado:
            custo += PESO_PEQUENA * (1 - min(w, h) / (0.22 * menor_lado))
    if len(quadros) > 1:
        areas = [math.log(max(1e-6, w * h)) for (_a, _b, w, h) in quadros]
        media = sum(areas) / len(areas)
        custo += 0.15 * sum((a - media) ** 2 for a in areas) / len(areas)
    return custo


def _layouts_sem_corte(proporcoes, area, espaco):
    """Layouts em linhas que preservam exatamente a proporção de cada foto."""
    n = len(proporcoes)
    if not n:
        return []
    x0, y0, W, H = area
    saida = []
    for linhas in _composicoes(n, min(MAX_LINHAS, n), min(MAX_LINHAS, n)):
        altura_max = (H - espaco * (len(linhas) - 1)) / float(len(linhas))
        if altura_max <= 0:
            continue
        alturas_linha = []
        inicio = 0
        for quantidade in linhas:
            soma = sum(proporcoes[inicio:inicio + quantidade])
            largura_disponivel = W - espaco * (quantidade - 1)
            if soma <= 0 or largura_disponivel <= 0:
                break
            alturas_linha.append(min(altura_max, largura_disponivel / soma))
            inicio += quantidade
        if len(alturas_linha) != len(linhas):
            continue
        altura_total = sum(alturas_linha) + espaco * (len(linhas) - 1)
        y = y0 + (H - altura_total) / 2.0
        quadros = []
        inicio = 0
        for quantidade, altura in zip(linhas, alturas_linha):
            larguras = [proporcoes[i] * altura for i in range(inicio, inicio + quantidade)]
            largura_total = sum(larguras) + espaco * (quantidade - 1)
            x = x0 + (W - largura_total) / 2.0
            for largura in larguras:
                quadros.append((x, y, largura, altura))
                x += largura + espaco
            y += altura + espaco
            inicio += quantidade
        usado = sum(w * h for _x, _y, w, h in quadros)
        vazio = max(0.0, 1.0 - usado / max(1.0, W * H))
        custo = 0.3 + 0.75 * vazio + 0.06 * (len(linhas) - 1)
        saida.append((custo, quadros, "__sem_corte__", list(range(n))))
    return saida


def _layout_cruzando_meio(fotos, produto, cfg):
    """Uma foto panorâmica atravessa a dobra; as restantes ficam abaixo."""
    n = len(fotos)
    if n < 2 or n > 9 or produto.get("paginas_por_lamina", 1) < 2:
        return None
    W, H = lamina_mm(produto)
    margem = max(0.0, float(cfg["margem_mm"]))
    intervalo = max(0.0, float(cfg["espacamento_mm"]))
    area_w = W - 2 * margem
    hero_h = min(H * 0.43, H * 0.5 - margem - intervalo)
    if area_w <= 0 or hero_h <= 0:
        return None
    hero_y = margem
    inferior_y = hero_y + hero_h + intervalo
    inferior_h = H - margem - inferior_y
    restantes = n - 1
    linhas = int(math.ceil(restantes / 2.0))
    altura = (inferior_h - intervalo * (linhas - 1)) / float(linhas)
    if altura <= 0:
        return None
    indice_hero = max(range(n), key=lambda i: fotos[i]["proporcao"])
    quadros = [None] * n
    quadros[indice_hero] = (margem, hero_y, area_w, hero_h)
    indices = [i for i in range(n) if i != indice_hero]
    cursor = 0
    for linha in range(linhas):
        quantidade = min(2, restantes - cursor)
        largura = (area_w - intervalo * (quantidade - 1)) / float(quantidade)
        for coluna in range(quantidade):
            i = indices[cursor]
            quadros[i] = (margem + coluna * (largura + intervalo),
                          inferior_y + linha * (altura + intervalo), largura, altura)
            cursor += 1
    cortes = [_corte_quadro(fotos[i]["proporcao"], quadro) for i, quadro in enumerate(quadros)]
    custo = 0.45 + PESO_CORTE * sum(math.log(corte) ** 2 for corte in cortes) / n
    espacos = [_espaco(produto, quadro, foto["caminho"]) for foto, quadro in zip(fotos, quadros)]
    return {"custo": custo, "espacos": espacos, "sobreposicoes": [], "modelo": None,
            "tipo_layout": "panoramico_meio"}


def _geometrias_parecidas(a, b, produto):
    if len(a) != len(b):
        return False
    ordena = lambda espacos: sorted(
        ((e["x"], e["y"], e["w"], e["h"]) for e in espacos), key=lambda q: (round(q[1], 2), q[0]))
    aa, bb = ordena(a), ordena(b)
    W, H = lamina_mm(produto)
    return all(abs(x[0] - y[0]) * W < 7 and abs(x[1] - y[1]) * H < 7
               and abs(x[2] - y[2]) * W < 7 and abs(x[3] - y[3]) * H < 7
               for x, y in zip(aa, bb))


def _corte_quadro(proporcao, quadro):
    _x, _y, w, h = quadro
    f = w / float(h)
    return max(proporcao / f, f / proporcao)


def layouts_pagina(proporcoes, area, espaco, modelos_pagina=(), produto=None, pagina=0, maximo=4):
    """
    Opções para uma página: [(custo, quadros em mm na ordem das fotos, modelo, ordem)].
    ordem diz qual foto entra em cada quadro (para modelos, que podem trocar a ordem).
    """
    n = len(proporcoes)
    opcoes = []
    if n == 0:
        return [(0.0, [], None, [])]
    ordem_natural = list(range(n))
    for linhas in _composicoes(n, MAX_LINHAS, MAX_LINHAS):
        r = _layout_linhas(proporcoes, area, espaco, linhas)
        if r:
            quadros, corte, vazio = r
            opcoes.append((_custo(quadros, proporcoes, area, corte, vazio), quadros, None, ordem_natural))
        if len(linhas) > 1 or n > 1:
            x0, y0, W, H = area
            invertidas = [1.0 / p for p in proporcoes]
            r = _layout_linhas(invertidas, (y0, x0, H, W), espaco, linhas)
            if r:
                quadros, corte, vazio = r
                quadros = [(qy, qx, qh, qw) for (qx, qy, qw, qh) in quadros]
                opcoes.append((_custo(quadros, proporcoes, area, corte, vazio) + 0.01, quadros, None, ordem_natural))
    if produto is not None:
        lw, lh = lamina_mm(produto)
        for modelo in modelos_pagina:
            if len(modelo["espacos"]) != n:
                continue
            espacos, _sobre = espacos_do_modelo(modelo, produto, pagina)
            quadros = [(e["x"] * lw, e["y"] * lh, e["w"] * lw, e["h"] * lh) for e in espacos]
            permutacoes = itertools.permutations(range(n)) if n <= 4 else [tuple(range(n))]
            melhor = None
            for perm in permutacoes:
                corte = max(_corte_quadro(proporcoes[perm[i]], quadros[i]) for i in range(n))
                desordem = sum(1 for i in range(n) if perm[i] != i) * 0.05
                c = PESO_CORTE * math.log(corte) ** 2 + desordem - 0.2
                if melhor is None or c < melhor[0]:
                    melhor = (c, list(perm))
            opcoes.append((melhor[0], quadros, modelo, melhor[1]))
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
        if unicas:
            unicas[-1] = sem_corte
        else:
            unicas.append(sem_corte)
    unicas.sort(key=lambda opcao: opcao[0])
    return unicas


# ------------------------------------------------------------
# Opções para uma lâmina
# ------------------------------------------------------------

def _espaco(produto, quadro, foto):
    e = normalizar_mm(produto, quadro)
    e["foto"] = foto
    e["edicao"] = {}
    return e


def opcoes_lamina(fotos, produto, cfg, modelos=(), maximo=8):
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
        p = fotos[0]["proporcao"]
        corte = max(p / (lw / lh), (lw / lh) / p)
        bonus = -2.0 if fotos[0].get("destaque") and p > 1.2 else 0.0
        opcoes.append({
            "custo": PESO_CORTE * math.log(corte) ** 2 * 0.5 + bonus,
            "espacos": [_espaco(produto, (0, 0, lw, lh), fotos[0]["caminho"])],
            "sobreposicoes": [],
            "modelo": None,
        })

    for modelo in modelos_lamina:
        espacos, sobre = espacos_do_modelo(modelo, produto)
        quadros = [(e["x"] * lw, e["y"] * lh, e["w"] * lw, e["h"] * lh) for e in espacos]
        corte = max(_corte_quadro(f["proporcao"], q) for f, q in zip(fotos, quadros))
        opcoes.append({
            "custo": PESO_CORTE * math.log(corte) ** 2 - 0.2,
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
            por_pagina.append((grupo, pagina, layouts_pagina(
                [f["proporcao"] for f in grupo], area, espaco, modelos_pagina, produto, pagina, maximo=3,
            )))
        vazias = sum(1 for k in divisao if k == 0)
        penalidade = 0.0
        if vazias:
            penalidade = PESO_PAGINA_VAZIA if n > 1 else 1.0
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
        semelhante = next((i for i, anterior in enumerate(unicas)
                           if _geometrias_parecidas(opcao["espacos"], anterior["espacos"], produto)), None)
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


def espacos_com_modelo(modelo, fotos, produto, pagina=None):
    """
    Aplica um modelo escolhido na mão. Modelo de página vai para a página
    indicada (ou a primeira). Devolve (espaços, sobreposição, fotos que não couberam).
    """
    espacos, sobre = espacos_do_modelo(modelo, produto, pagina or 0)
    lw, lh = lamina_mm(produto)
    livres = list(fotos)
    saida = []
    for e in espacos:
        quadro = (e["x"] * lw, e["y"] * lh, e["w"] * lw, e["h"] * lh)
        escolhida = None
        if livres:
            escolhida = min(livres, key=lambda f: (_corte_quadro(f["proporcao"], quadro) > 1.3, livres.index(f)))
            livres.remove(escolhida)
        novo = dict(e)
        novo["foto"] = escolhida["caminho"] if escolhida else None
        novo["edicao"] = {}
        saida.append(novo)
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
                afinidade = sum(math.sqrt(max(0.0, x) * max(0.0, y))
                                for x, y in zip(cenario_a, cenario_b))
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
            lamina["espacos"].append(_espaco(
                produto, ((lw - largura) / 2, (lh - altura) / 2, largura, altura), foto_sobreposta["caminho"],
            ))
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
