# -*- coding: utf-8 -*-
"""Fundo, degradê e molduras trabalhando juntos depois das edições (diagramar.acabamento_lamina)."""
import copy

import pytest
from PIL import Image

import composicao
import diagramar
from produtos import carregar_produto

GRADE = [
    {"x": 0.06, "y": 0.10, "w": 0.34, "h": 0.70},
    {"x": 0.56, "y": 0.10, "w": 0.18, "h": 0.34},
    {"x": 0.56, "y": 0.50, "w": 0.18, "h": 0.34},
]


@pytest.fixture
def produto():
    return carregar_produto("20X20")


@pytest.fixture
def cfg(produto):
    return diagramar.configuracao(produto)


def _fotos(pasta, quantidade=3, cor=(150, 60, 60)):
    pasta.mkdir(parents=True, exist_ok=True)
    caminhos = []
    for i in range(quantidade):
        caminho = pasta / ("f%d.jpg" % i)
        Image.new("RGB", (600, 400), cor).save(caminho)
        caminhos.append(str(caminho))
    return caminhos


def _meta(caminhos):
    from pathlib import Path

    return [{"caminho": c, "pasta": str(Path(c).parent), "nome_pasta": Path(c).parent.name} for c in caminhos]


def _lamina(caminhos, geometria=GRADE):
    espacos = [dict(g, foto=c, edicao={}) for g, c in zip(geometria, caminhos)]
    return {"id": "lam-teste", "fundo": "#ffffff", "espacos": espacos, "sobreposicoes": []}


def _acabar(lamina, produto, cfg, indice=0, forcar=()):
    caminhos = [e["foto"] for e in lamina["espacos"] if e.get("foto")]
    diagramar.acabamento_lamina(lamina, _meta(caminhos), produto, cfg, indice_lamina=indice, forcar=forcar)
    return [(e.get("edicao") or {}).get("moldura") for e in lamina["espacos"]]


def _trocar(lamina, a, b):
    """Igual a app.trocar_espacos: a foto e a edição dela mudam de quadro."""
    ea, eb = lamina["espacos"][a], lamina["espacos"][b]
    ea["foto"], eb["foto"] = eb["foto"], ea["foto"]
    ea["edicao"], eb["edicao"] = eb["edicao"], ea["edicao"]


# ------------------------------------------------------------
# Molduras acompanham o quadro, não a foto
# ------------------------------------------------------------

def test_troca_de_fotos_mantem_o_papel_de_cada_quadro(tmp_path, produto, cfg):
    lamina = _lamina(_fotos(tmp_path / "Baile"))
    conjunto = composicao.MOLDURAS_EVENTO["baile_dark_luxury"]
    antes = _acabar(lamina, produto, cfg)
    assert lamina["tema_evento"] == "baile_dark_luxury"
    assert antes[0]["estilo"] == conjunto["destaque"]

    # Quadro 0 é o destaque da página esquerda; na direita, o quadro 1 é o destaque e o 2 a secundária
    assert antes[2]["estilo"] == conjunto["secundaria"]
    _trocar(lamina, 0, 2)
    depois = _acabar(lamina, produto, cfg)
    assert depois[0]["estilo"] == conjunto["destaque"]
    assert depois[2]["estilo"] == conjunto["secundaria"]


def test_foto_nova_no_quadro_ganha_a_moldura_do_evento(tmp_path, produto, cfg):
    lamina = _lamina(_fotos(tmp_path / "Baile"))
    _acabar(lamina, produto, cfg)
    nova = _fotos(tmp_path / "Baile" / "extra", 1)[0]
    lamina["espacos"][2].update(foto=nova, edicao={})  # app.colocar_foto zera a edição
    molduras = _acabar(lamina, produto, cfg)
    assert molduras[2]["estilo"] == composicao.MOLDURAS_EVENTO["baile_dark_luxury"]["secundaria"]


# ------------------------------------------------------------
# O que foi feito à mão fica
# ------------------------------------------------------------

def test_moldura_escolhida_a_mao_e_preservada(tmp_path, produto, cfg):
    lamina = _lamina(_fotos(tmp_path / "Baile"))
    _acabar(lamina, produto, cfg)
    lamina["espacos"][1]["edicao"]["moldura"] = {"estilo": "halo", "cor": "#ff0000", "espessura_mm": 0.5}

    molduras = _acabar(lamina, produto, cfg, indice=1)
    assert molduras[1]["estilo"] == "halo"
    assert molduras[2]["estilo"] == composicao.MOLDURAS_EVENTO["baile_dark_luxury"]["alternativa"]


def test_moldura_removida_a_mao_nao_volta(tmp_path, produto, cfg):
    lamina = _lamina(_fotos(tmp_path / "Baile"))
    _acabar(lamina, produto, cfg)
    lamina["espacos"][2]["edicao"].pop("moldura")
    assert _acabar(lamina, produto, cfg)[2] is None


def test_botao_de_molduras_refaz_inclusive_o_manual(tmp_path, produto, cfg):
    lamina = _lamina(_fotos(tmp_path / "Baile"))
    _acabar(lamina, produto, cfg)
    lamina["espacos"][2]["edicao"]["moldura"] = {"estilo": "halo", "cor": "#ff0000"}
    molduras = _acabar(lamina, produto, cfg, forcar=("molduras",))
    assert molduras[2]["estilo"] == composicao.MOLDURAS_EVENTO["baile_dark_luxury"]["secundaria"]


# ------------------------------------------------------------
# Degradê e moldura se excluem e se atualizam juntos
# ------------------------------------------------------------

def test_degrade_automatico_sai_quando_a_foto_vai_para_a_grade(tmp_path, produto, cfg):
    foto = _fotos(tmp_path / "Baile", 1)[0]
    lamina = _lamina([foto], [{"x": 0.0, "y": 0.0, "w": 0.5, "h": 1.0}])
    _acabar(lamina, produto, cfg)
    ed = lamina["espacos"][0]["edicao"]
    assert ed["degrade"]["ativo"] and ed.get("moldura") is None

    lamina["espacos"][0].update(GRADE[0])
    molduras = _acabar(lamina, produto, cfg)
    assert "degrade" not in lamina["espacos"][0]["edicao"]
    assert molduras[0] is not None


def test_degrade_feito_a_mao_fica_e_a_foto_nao_ganha_moldura(tmp_path, produto, cfg):
    lamina = _lamina(_fotos(tmp_path / "Baile"))
    manual = {"ativo": True, "direcao": "baixo", "inicio": 60, "fim": 100, "opacidade_fim": 0}
    lamina["espacos"][1]["edicao"]["degrade"] = dict(manual)
    molduras = _acabar(lamina, produto, cfg)
    assert lamina["espacos"][1]["edicao"]["degrade"] == manual
    assert molduras[1] is None


# ------------------------------------------------------------
# Fundo: estável nas edições, muda quando o evento muda
# ------------------------------------------------------------

def test_fundo_nao_muda_a_cada_edicao_do_mesmo_evento(tmp_path, produto, cfg):
    lamina = _lamina(_fotos(tmp_path / "Baile"))
    _acabar(lamina, produto, cfg)
    fundo = copy.deepcopy(lamina["fundo_imagem"]["gerado"])
    _trocar(lamina, 0, 2)
    _acabar(lamina, produto, cfg)
    assert lamina["fundo_imagem"]["gerado"] == fundo


def test_fundo_e_molduras_seguem_o_novo_evento(tmp_path, produto, cfg):
    lamina = _lamina(_fotos(tmp_path / "Baile"))
    _acabar(lamina, produto, cfg)
    for esp, nova in zip(lamina["espacos"], _fotos(tmp_path / "Jaleco", cor=(235, 235, 240))):
        esp["foto"] = nova
    molduras = _acabar(lamina, produto, cfg)
    assert lamina["tema_evento"] == "jaleco_fine_art"
    assert lamina["fundo_imagem"]["gerado"]["id"].startswith("cap_jaleco_fine_art")
    assert molduras[0]["estilo"] == composicao.MOLDURAS_EVENTO["jaleco_fine_art"]["destaque"]
    for m in molduras:
        assert composicao.contraste_cores(m["cor"], lamina["fundo"]) >= composicao.CONTRASTE_MINIMO_MOLDURA


def test_fundo_escolhido_a_mao_e_preservado(tmp_path, produto, cfg):
    lamina = _lamina(_fotos(tmp_path / "Baile"))
    _acabar(lamina, produto, cfg)
    lamina["fundo"] = "#123456"
    lamina["fundo_imagem"] = {"caminho": str(tmp_path / "meu_fundo.jpg"), "modo": "preencher"}
    escolhido = copy.deepcopy(lamina["fundo_imagem"])
    for esp, nova in zip(lamina["espacos"], _fotos(tmp_path / "Jaleco", cor=(235, 235, 240))):
        esp["foto"] = nova
    _acabar(lamina, produto, cfg)
    assert lamina["fundo"] == "#123456" and lamina["fundo_imagem"] == escolhido

    _acabar(lamina, produto, cfg, forcar=("fundos",))
    assert lamina["fundo_imagem"].get("gerado")


# ------------------------------------------------------------
# Tema aplicado à mão e diagramação inicial
# ------------------------------------------------------------

def test_tema_aplicado_vira_o_evento_da_lamina(tmp_path, produto, cfg):
    lamina = _lamina(_fotos(tmp_path / "Jaleco", cor=(235, 235, 240)))
    _acabar(lamina, produto, cfg)
    diagramar.aplicar_tema_em_laminas(lamina, "baile_dark_luxury")
    conjunto = composicao.MOLDURAS_EVENTO["baile_dark_luxury"]
    assert lamina["tema_evento"] == "baile_dark_luxury"
    assert lamina["espacos"][0]["edicao"]["moldura"]["estilo"] == conjunto["destaque"]

    _trocar(lamina, 0, 1)
    molduras = _acabar(lamina, produto, cfg)
    assert lamina["tema_evento"] == "baile_dark_luxury"
    assert molduras[0]["estilo"] == conjunto["destaque"]


def test_diagramacao_marca_o_acabamento_como_automatico(album):
    for lamina in album["laminas"]:
        for esp in lamina["espacos"]:
            ed = esp.get("edicao") or {}
            for chave in ("moldura", "degrade"):
                if ed.get(chave) is not None:
                    assert diagramar.eh_automatico(ed, chave)
