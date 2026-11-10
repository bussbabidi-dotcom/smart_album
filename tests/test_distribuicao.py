# -*- coding: utf-8 -*-
"""Divisão das fotos em lâminas: mínimo, máximo, lâminas desejadas, pastas/grupos e cenários."""
import random

import pytest

import diagramar
from produtos import carregar_produto


def _fotos(n, pasta="D:/turma/Baile", grupo=1, cenario_a_cada=2, destaque_a_cada=0, semente=1):
    sorteio = random.Random(semente)
    fotos = []
    for i in range(n):
        vetor = [0.0] * 16
        vetor[(i // max(1, cenario_a_cada)) % 16] = 1.0
        fotos.append({
            "caminho": "%s/f%03d.jpg" % (pasta, i),
            "proporcao": sorteio.choice([1.5, 0.667, 1.0]),
            "pasta": pasta,
            "nome_pasta": pasta.rsplit("/", 1)[-1],
            "grupo_ordem": grupo,
            "ordem_manual": i,
            "cenario": vetor,
            "assinatura_cenario": vetor,
            "data": i * 30,
            "destaque": bool(destaque_a_cada and i % destaque_a_cada == 0),
        })
    return fotos


@pytest.fixture
def produto():
    return carregar_produto("30X20")


def _plano(fotos, produto, **cfg_extra):
    cfg = diagramar.configuracao(produto, cfg_extra)
    return diagramar.planejar_distribuicao(fotos, produto, cfg)


def _pasta(grupo):
    return grupo[0]["pasta"]


# ------------------------------------------------------------
# Mínimo e máximo
# ------------------------------------------------------------

@pytest.mark.parametrize("desejadas", [8, 15, 25, 40])
def test_toda_lamina_fica_entre_o_minimo_e_o_maximo(produto, desejadas):
    fotos = _fotos(70, "D:/t/Baile") + _fotos(45, "D:/t/Jaleco") + _fotos(23, "D:/t/Externas")
    plano = _plano(fotos, produto, laminas_desejadas=desejadas)
    for grupo in plano["grupos"]:
        assert plano["minimo"] <= len(grupo) <= plano["maximo"], [len(g) for g in plano["grupos"]]


def test_so_fica_abaixo_do_minimo_a_pasta_que_ja_e_menor(produto):
    fotos = _fotos(40, "D:/t/Baile") + _fotos(3, "D:/t/Extra") + _fotos(30, "D:/t/Jaleco")
    plano = _plano(fotos, produto, laminas_desejadas=12)
    abaixo = [g for g in plano["grupos"] if len(g) < plano["minimo"]]
    assert [_pasta(g) for g in abaixo] == ["D:/t/Extra"]
    assert any("Extra" in a for a in plano["avisos"])


def test_cenario_a_cada_foto_nao_gera_lamina_de_uma_foto(produto):
    plano = _plano(_fotos(60, cenario_a_cada=1), produto, laminas_desejadas=10)
    assert min(len(g) for g in plano["grupos"]) >= plano["minimo"]
    assert len(plano["grupos"]) == 10


def test_maximo_nunca_e_passado_mesmo_com_poucas_laminas_desejadas(produto):
    plano = _plano(_fotos(100), produto, laminas_desejadas=3)
    assert max(len(g) for g in plano["grupos"]) <= plano["maximo"]


def test_laminas_ficam_equilibradas(produto):
    plano = _plano(_fotos(60, cenario_a_cada=6), produto, laminas_desejadas=10)
    tamanhos = [len(g) for g in plano["grupos"]]
    assert max(tamanhos) - min(tamanhos) <= 3, tamanhos


# ------------------------------------------------------------
# Lâminas desejadas
# ------------------------------------------------------------

def test_numero_desejado_e_exato_quando_cabe(produto):
    fotos = _fotos(70, "D:/t/Baile") + _fotos(45, "D:/t/Jaleco")
    plano = _plano(fotos, produto, laminas_desejadas=18)
    assert len(plano["grupos"]) == 18
    assert not plano["avisos"]


def test_desejadas_demais_para_o_minimo_usa_menos_e_avisa(produto):
    plano = _plano(_fotos(40), produto, laminas_desejadas=30)
    assert len(plano["grupos"]) == 40 // plano["minimo"]
    assert any("no mínimo" in a for a in plano["avisos"])


def test_desejadas_de_menos_para_o_maximo_usa_mais_e_avisa(produto):
    plano = _plano(_fotos(100), produto, laminas_desejadas=3)
    assert len(plano["grupos"]) > 3
    assert any("no máximo" in a for a in plano["avisos"])


def test_sem_desejadas_usa_o_numero_ideal(produto):
    cfg = diagramar.configuracao(produto)
    cfg.pop("laminas_desejadas")
    plano = diagramar.planejar_distribuicao(_fotos(80), produto, cfg)
    assert len(plano["grupos"]) == diagramar.laminas_ideais(80, produto, cfg)


# ------------------------------------------------------------
# Pastas, grupos e destaques
# ------------------------------------------------------------

def test_pastas_nunca_se_misturam(produto):
    fotos = _fotos(23, "D:/t/Baile") + _fotos(17, "D:/t/Jaleco") + _fotos(9, "D:/t/Externas")
    plano = _plano(fotos, produto, laminas_desejadas=9)
    for grupo in plano["grupos"]:
        assert len({f["pasta"] for f in grupo}) == 1


def test_grupos_numerados_nunca_se_misturam(produto):
    fotos = _fotos(21, "D:/t/A", grupo=1) + _fotos(13, "D:/t/A", grupo=2, semente=2)
    for f in fotos[21:]:
        f["caminho"] = f["caminho"].replace("/f", "/g")
    plano = _plano(fotos, produto, laminas_desejadas=6)
    for grupo in plano["grupos"]:
        assert len({f["grupo_ordem"] for f in grupo}) == 1


def test_destaques_ficam_em_laminas_diferentes(produto):
    # Um cenário só: a ordem fica a original e os destaques ficam a 8 fotos um do outro
    plano = _plano(_fotos(40, cenario_a_cada=40, destaque_a_cada=8), produto, laminas_desejadas=8)
    for grupo in plano["grupos"]:
        assert sum(1 for f in grupo if f["destaque"]) <= 1


def test_minimo_vence_a_preferencia_de_separar_destaques(produto):
    fotos = _fotos(12, cenario_a_cada=12)
    fotos[0]["destaque"] = fotos[2]["destaque"] = True
    plano = _plano(fotos, produto, laminas_desejadas=3)
    assert min(len(g) for g in plano["grupos"]) >= plano["minimo"]


# ------------------------------------------------------------
# Faixa, nº ideal e lâminas vazias
# ------------------------------------------------------------

def test_faixa_usa_o_menor_maximo_e_o_minimo_por_lamina(produto):
    cfg = diagramar.configuracao(produto, {"fotos_por_lamina": 6, "max_fotos_lamina": 12, "max_fotos_pagina": 4})
    assert diagramar.faixa_fotos_lamina(produto, cfg) == (6, 8)
    assert cfg["min_fotos_pagina"] == 3


def test_numero_ideal_fica_no_meio_da_faixa(produto):
    cfg = diagramar.configuracao(produto, {"fotos_por_lamina": 4, "max_fotos_lamina": 12})
    assert diagramar.laminas_ideais(80, produto, cfg) == 10


def test_laminas_vazias_so_para_o_minimo_do_produto():
    produto = carregar_produto("20X20")
    cfg = diagramar.configuracao(produto, {"laminas_desejadas": 20, "auto_fundos": False,
                                           "auto_degrade": False, "auto_molduras": False})
    avisos = []
    laminas, _ = diagramar.diagramar_album(_fotos(8), produto, cfg, (), avisos=avisos)
    com_fotos = [l for l in laminas if l["espacos"]]
    assert len(com_fotos) == 2
    assert len(laminas) == produto["laminas_min"]
    assert any("vazias" in a for a in avisos)

    sem_minimo = dict(produto, laminas_min=0)
    laminas, _ = diagramar.diagramar_album(_fotos(8), sem_minimo, cfg, ())
    assert len(laminas) == 2


# ------------------------------------------------------------
# Máximo por página nos layouts especiais
# ------------------------------------------------------------

@pytest.mark.parametrize("n", [4, 5, 6])
def test_layouts_respeitam_o_maximo_por_pagina(produto, n):
    cfg = diagramar.configuracao(produto, {"max_fotos_pagina": 3, "max_fotos_lamina": 6, "fotos_por_lamina": 2})
    fotos = [dict(f, proporcao=2.4 if i == 0 else 1.0) for i, f in enumerate(_fotos(n))]
    opcoes = diagramar.opcoes_lamina(fotos, produto, cfg, (), maximo=40)
    assert opcoes
    for op in opcoes:
        esquerda = sum(1 for e in op["espacos"] if e["x"] + e["w"] / 2 < 0.5)
        direita = len(op["espacos"]) - esquerda
        assert max(esquerda, direita) <= 3, (op.get("tipo_layout"), esquerda, direita)
