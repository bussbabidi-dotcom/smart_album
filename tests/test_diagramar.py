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
