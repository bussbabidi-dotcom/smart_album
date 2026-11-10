# -*- coding: utf-8 -*-
import itertools

import pytest

import diagramar
from comum import normalizar_caminho
from produtos import carregar_produto, lamina_mm

TOLERANCIA = 1e-3
# Espaços que encostam na sangria aceitam 8% a mais de corte (_proteger_espacos_contra_corte_pessoas).
FOLGA_SANGRADO = 1.08


def _espacos_com_foto(lamina):
    return [e for e in lamina["espacos"] if e.get("foto")]


def _eh_sangrado(e):
    return e["x"] <= 0.006 or e["y"] <= 0.006 or e["x"] + e["w"] >= 0.994 or e["y"] + e["h"] >= 0.994


# ------------------------------------------------------------
# Funções de geometria
# ------------------------------------------------------------

def test_quadros_1_foto_preenche_area_quando_corte_cabe_no_limite():
    area = (0.0, 0.0, 160.0, 100.0)
    [(quadro, corte, vazio, tipo)] = diagramar.quadros_1_foto(1.5, area)
    assert tipo == "preenchimento_total"
    assert quadro == area
    assert corte == pytest.approx(1.6 / 1.5)
    assert vazio == 0.0


def test_quadros_1_foto_mantem_proporcao_quando_corte_passa_do_limite():
    area = (10.0, 20.0, 200.0, 100.0)
    [(quadro, corte, vazio, tipo)] = diagramar.quadros_1_foto(2.0 / 3.0, area)
    x, y, w, h = quadro
    assert tipo == "proporcao_padrao"
    assert corte == 1.0
    assert w / h == pytest.approx(2.0 / 3.0)
    assert h == pytest.approx(100.0)
    assert x + w / 2 == pytest.approx(10.0 + 200.0 / 2)
    assert vazio == pytest.approx(1.0 - (w * h) / (200.0 * 100.0))


def test_caixa_proporcao_padrao_centraliza_sem_cortar():
    x, y, w, h = diagramar.caixa_proporcao_padrao(2.0, (0.0, 0.0, 100.0, 100.0))
    assert (w, h) == pytest.approx((100.0, 50.0))
    assert (x, y) == pytest.approx((0.0, 25.0))


def test_configuracao_aplica_valores_do_projeto_e_ignora_none():
    produto = carregar_produto("20X20")
    cfg = diagramar.configuracao(produto, {"margem_mm": 12, "fundo": None, "auto_molduras": False})
    assert cfg["margem_mm"] == 12
    assert cfg["fundo"] == "#ffffff"
    assert cfg["auto_molduras"] is False
    assert cfg["laminas_desejadas"] == produto["laminas_min"]


@pytest.mark.parametrize(
    "cfg, esperado",
    [
        (None, diagramar.CORTE_MAXIMO),
        ({"modo_corte": "sem_corte"}, 1.0),
        ({"corte_maximo": 9.0}, 1.5),
        ({"corte_maximo": 0.5}, 1.0),
        ({"corte_maximo": "abc"}, diagramar.CORTE_MAXIMO),
    ],
)
def test_limite_de_corte_da_configuracao(cfg, esperado):
    assert diagramar._limite_corte_cfg(cfg) == pytest.approx(esperado)


# ------------------------------------------------------------
# Álbum diagramado (fixture `album` em conftest.py)
# ------------------------------------------------------------

def test_cada_foto_entra_uma_unica_vez(album):
    usadas = [normalizar_caminho(e["foto"]) for l in album["laminas"] for e in _espacos_com_foto(l)]
    sobras = [normalizar_caminho(c) for c in album["sobras"]]
    esperadas = {normalizar_caminho(f["caminho"]) for f in album["fotos"]}
    assert len(usadas) == len(set(usadas))
    assert set(usadas) | set(sobras) == esperadas
    assert not set(usadas) & set(sobras)


def test_quantidade_de_laminas_respeita_o_produto(album):
    produto = album["produto"]
    assert produto["laminas_min"] <= len(album["laminas"]) <= produto["laminas_max"]


def test_espacos_ficam_dentro_da_lamina(album):
    for lamina in album["laminas"]:
        for e in _espacos_com_foto(lamina):
            assert e["w"] > 0 and e["h"] > 0
            assert e["x"] >= -TOLERANCIA and e["y"] >= -TOLERANCIA
            assert e["x"] + e["w"] <= 1 + TOLERANCIA
            assert e["y"] + e["h"] <= 1 + TOLERANCIA


def test_espacos_nao_se_sobrepoem(album):
    for lamina in album["laminas"]:
        for a, b in itertools.combinations(_espacos_com_foto(lamina), 2):
            ix = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])
            iy = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"])
            assert ix <= TOLERANCIA or iy <= TOLERANCIA, (lamina.get("tipo_layout"), a, b)


def test_corte_respeita_o_limite(album):
    lw, lh = lamina_mm(album["produto"])
    limite = diagramar._limite_corte_cfg(album["cfg"])
    for lamina in album["laminas"]:
        for e in _espacos_com_foto(lamina):
            prop_foto = album["info"][normalizar_caminho(e["foto"])]["proporcao"]
            prop_quadro = (e["w"] * lw) / (e["h"] * lh)
            corte = max(prop_foto / prop_quadro, prop_quadro / prop_foto)
            maximo = limite * FOLGA_SANGRADO if _eh_sangrado(e) else limite
            assert corte <= maximo + 1e-3, (lamina.get("tipo_layout"), e, corte)


# ------------------------------------------------------------
# Preenchimento da área útil e orientação (álbum sintético, sem arquivos)
# ------------------------------------------------------------

def _album_misturado(nome_produto, semente=7):
    """60 fotos de orientações misturadas em 15 lâminas (~4 por lâmina, onde sobrava mais espaço em branco)."""
    import random

    sorteio = random.Random(semente)
    fotos = []
    for pasta, qtd in (("Baile", 24), ("Jaleco", 20), ("Externas", 16)):
        for i in range(qtd):
            fotos.append({
                "caminho": "D:/t/%s/f%03d.jpg" % (pasta, i),
                "proporcao": sorteio.choice([1.5, 1.5, 1.5, 0.667, 0.667, 1.0, 2.4]),
                "pasta": "D:/t/" + pasta,
                "nome_pasta": pasta,
                "grupo_ordem": 1,
                "ordem_manual": len(fotos),
                "data": len(fotos) * 30,
            })
    produto = carregar_produto(nome_produto)
    cfg = diagramar.configuracao(produto, {"laminas_desejadas": 15, "auto_fundos": False,
                                           "auto_degrade": False, "auto_molduras": False})
    laminas, sobras = diagramar.diagramar_album(fotos, produto, cfg, ())
    return fotos, produto, cfg, laminas, sobras


def _coberturas_das_paginas(laminas, produto, cfg):
    from produtos import area_util_pagina_mm

    lw, lh = lamina_mm(produto)
    saida = []
    for lamina in laminas:
        espacos = _espacos_com_foto(lamina)
        if not espacos:
            continue
        for pagina in range(produto["paginas_por_lamina"]):
            ux, uy, uw, uh = area_util_pagina_mm(produto, pagina, cfg["margem_mm"], respeitar_seguranca=True)
            coberto = 0.0
            for e in espacos:
                x, y, w, h = e["x"] * lw, e["y"] * lh, e["w"] * lw, e["h"] * lh
                coberto += max(0.0, min(x + w, ux + uw) - max(x, ux)) * max(0.0, min(y + h, uy + uh) - max(y, uy))
            saida.append(coberto / (uw * uh))
    return saida


@pytest.mark.parametrize("nome_produto", ["20X20", "30X20", "30X22"])
def test_layouts_preenchem_a_area_util(nome_produto):
    _fotos, produto, cfg, laminas, _sobras = _album_misturado(nome_produto)
    coberturas = _coberturas_das_paginas(laminas, produto, cfg)
    assert sum(coberturas) / len(coberturas) >= 0.96, sum(coberturas) / len(coberturas)
    assert sum(1 for c in coberturas if c < 0.80) <= 1, sorted(coberturas)[:6]


@pytest.mark.parametrize("nome_produto", ["20X20", "30X20", "30X22"])
def test_foto_vertical_nunca_fica_em_quadro_horizontal_nem_o_contrario(nome_produto):
    fotos, produto, cfg, laminas, sobras = _album_misturado(nome_produto)
    lw, lh = lamina_mm(produto)
    props = {f["caminho"]: f["proporcao"] for f in fotos}
    limite = diagramar._limite_corte_cfg(cfg)
    usadas = []
    for lamina in laminas:
        for a, b in itertools.combinations(_espacos_com_foto(lamina), 2):
            ix = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])
            iy = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"])
            assert ix <= TOLERANCIA or iy <= TOLERANCIA
        for e in _espacos_com_foto(lamina):
            usadas.append(e["foto"])
            p_foto = props[e["foto"]]
            p_quadro = (e["w"] * lw) / (e["h"] * lh)
            if p_foto >= 1.05:
                assert p_quadro > 1.0, (p_foto, p_quadro)
            elif p_foto <= 0.95:
                assert p_quadro < 1.0, (p_foto, p_quadro)
            corte = max(p_foto / p_quadro, p_quadro / p_foto)
            assert corte <= limite * (FOLGA_SANGRADO if _eh_sangrado(e) else 1.0) + 1e-3
    assert sorted(usadas) == sorted(f["caminho"] for f in fotos) and not sobras
    assert sum(1 for l in laminas if _espacos_com_foto(l)) == 15


def test_divisao_entre_paginas_casa_as_orientacoes():
    # Duas verticais e duas horizontais: lado a lado, as verticais fecham uma página e a outra
    # recebe as horizontais (ou cada página mistura uma de cada), nunca deixando página pela metade.
    produto = carregar_produto("30X20")
    cfg = diagramar.configuracao(produto)
    fotos = [{"caminho": "f%d.jpg" % i, "proporcao": p} for i, p in enumerate([1.5, 0.667, 1.5, 0.667])]
    melhor = diagramar.opcoes_lamina(fotos, produto, cfg, maximo=1)[0]
    coberturas = _coberturas_das_paginas([melhor], produto, cfg)
    assert min(coberturas) >= 0.85, (melhor.get("tipo_layout"), coberturas)


def test_uma_opcao_por_pagina_nao_troca_o_preenchimento_pela_versao_sem_corte():
    area = (0.0, 0.0, 268.0, 168.0)
    [(_custo, quadros, marcador, _ordem)] = diagramar.layouts_pagina([0.667, 0.667], area, 4.0, maximo=1)
    assert marcador != "__sem_corte__"
    assert sum(w * h for _x, _y, w, h in quadros) / (268.0 * 168.0) >= 0.95


def test_sem_corte_nao_corta_nenhuma_foto(tmp_path):
    from conftest import montar_album

    dados = montar_album(tmp_path, "30X20", 12, semente=3)
    produto = dados["produto"]
    cfg = diagramar.configuracao(produto, {"modo_corte": "sem_corte", "auto_fundos": False})
    laminas, _ = diagramar.diagramar_album(dados["fotos"], produto, cfg, ())
    lw, lh = lamina_mm(produto)
    for lamina in laminas:
        for e in _espacos_com_foto(lamina):
            prop_foto = dados["info"][normalizar_caminho(e["foto"])]["proporcao"]
            prop_quadro = (e["w"] * lw) / (e["h"] * lh)
            maximo = FOLGA_SANGRADO if _eh_sangrado(e) else 1.0
            assert max(prop_foto / prop_quadro, prop_quadro / prop_foto) <= maximo + 1e-2
