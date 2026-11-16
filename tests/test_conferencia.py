# -*- coding: utf-8 -*-
"""Conferência do álbum: espaços vazios, fotos sumidas, repetidas, resolução baixa, textos vazios."""
import copy

import conferencia
from comum import normalizar_caminho
from conftest import montar_album


def _album(tmp_path):
    return montar_album(tmp_path / "fotos", "20X20", 12)


def _tipos(problemas):
    return [p["tipo"] for p in problemas]


def test_album_normal_nao_tem_erros(tmp_path):
    a = _album(tmp_path)
    problemas = conferencia.conferir(a["projeto"], a["produto"], a["info"])
    assert not [p for p in problemas if p["nivel"] == "erro"]
    assert "sem_foto" not in _tipos(problemas) or all(p["nivel"] == "aviso" for p in problemas if p["tipo"] == "sem_foto")


def test_espaco_sem_foto_e_aviso_na_lamina_certa(tmp_path):
    a = _album(tmp_path)
    projeto = copy.deepcopy(a["projeto"])
    projeto["laminas"][1]["espacos"][0]["foto"] = None
    achados = [p for p in conferencia.conferir(projeto, a["produto"], a["info"]) if p["tipo"] == "sem_foto"]
    assert any(p["lamina"] == 1 and p["espaco"] == 0 and p["nivel"] == "aviso" for p in achados)


def test_foto_que_sumiu_do_disco_e_erro_e_vem_primeiro(tmp_path):
    a = _album(tmp_path)
    projeto = copy.deepcopy(a["projeto"])
    projeto["laminas"][0]["espacos"][0]["foto"] = str(tmp_path / "nao_existe.jpg")
    problemas = conferencia.conferir(projeto, a["produto"], a["info"])
    assert problemas[0]["nivel"] == "erro" and problemas[0]["tipo"] == "foto_sumiu"
    assert problemas[0]["lamina"] == 0 and "nao_existe.jpg" in problemas[0]["texto"]


def test_foto_repetida_aponta_a_outra_lamina(tmp_path):
    a = _album(tmp_path)
    projeto = copy.deepcopy(a["projeto"])
    foto = projeto["laminas"][0]["espacos"][0]["foto"]
    projeto["laminas"][2]["espacos"][0]["foto"] = foto
    rep = [p for p in conferencia.conferir(projeto, a["produto"], a["info"]) if p["tipo"] == "repetida"]
    assert {p["lamina"] for p in rep} == {0, 2}
    na_primeira = next(p for p in rep if p["lamina"] == 0)
    assert "3" in na_primeira["texto"]


def test_resolucao_baixa_considera_o_zoom_do_corte(tmp_path):
    a = _album(tmp_path)
    projeto = copy.deepcopy(a["projeto"])
    espaco = projeto["laminas"][0]["espacos"][0]
    chave = normalizar_caminho(espaco["foto"])
    info = copy.deepcopy(a["info"])
    base = conferencia.dpi_efetivo(espaco, info[chave], a["produto"])
    espaco.setdefault("edicao", {})["corte"] = {"cx": 0.5, "cy": 0.5, "zoom": 4.0}
    com_zoom = conferencia.dpi_efetivo(espaco, info[chave], a["produto"])
    assert abs(com_zoom - base / 4) < 0.5
    info[chave]["w"], info[chave]["h"] = 300, 200   # foto minúscula para o tamanho do espaço
    espaco["edicao"]["corte"]["zoom"] = 1.0
    problemas = conferencia.conferir(projeto, a["produto"], info)
    baixa = [p for p in problemas if p["tipo"] == "resolucao" and p["lamina"] == 0 and p["espaco"] == 0]
    assert baixa and baixa[0]["nivel"] == "erro"   # abaixo de 100 dpi


def test_texto_vazio_e_fundo_sumido(tmp_path):
    a = _album(tmp_path)
    projeto = copy.deepcopy(a["projeto"])
    projeto["laminas"][0]["textos"] = [{"texto": "   "}, {"texto": "Maria"}]
    projeto["laminas"][1]["fundo_imagem"] = {"caminho": str(tmp_path / "fundo_sumiu.jpg")}
    tipos = _tipos(conferencia.conferir(projeto, a["produto"], a["info"]))
    assert tipos.count("texto_vazio") == 1
    assert "fundo_sumiu" in tipos


def test_textura_que_sumiu_avisa_mas_nao_e_erro(tmp_path):
    a = _album(tmp_path)
    projeto = copy.deepcopy(a["projeto"])
    projeto["laminas"][0]["fundo_imagem"] = {"caminho": "__gerado__:x", "gerado": {"textura_arquivo": str(tmp_path / "t.png")}}
    achados = [p for p in conferencia.conferir(projeto, a["produto"], a["info"]) if p["tipo"] == "textura_sumiu"]
    assert achados and achados[0]["nivel"] == "aviso"


def test_so_algumas_laminas_e_sobras(tmp_path):
    a = _album(tmp_path)
    projeto = copy.deepcopy(a["projeto"])
    projeto["laminas"][0]["espacos"][0]["foto"] = None
    projeto["laminas"][3]["espacos"][0]["foto"] = None
    so_3 = conferencia.conferir(projeto, a["produto"], a["info"], indices=[3])
    assert {p["lamina"] for p in so_3 if p["tipo"] == "sem_foto"} == {3}
    assert "sobras" not in _tipos(so_3)   # observação só vale para o álbum inteiro
    tudo = conferencia.conferir(projeto, a["produto"], a["info"])
    assert "sobras" in _tipos(tudo)       # as fotos tiradas ficaram sem lâmina
    assert conferencia.conferir(projeto, a["produto"], a["info"], indices=[999]) == []


def test_resumo_em_portugues():
    assert conferencia.resumo([]).startswith("Tudo certo")
    ps = [{"nivel": "erro"}, {"nivel": "aviso"}, {"nivel": "aviso"}]
    assert conferencia.resumo(ps) == "1 erro · 2 avisos"
