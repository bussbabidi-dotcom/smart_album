# -*- coding: utf-8 -*-
"""Bloco de texto da lâmina: fontes do PC, desenho transparente, composição, exportação e projeto."""

import json

import pytest
from PIL import Image

import composicao
import exportar
import projeto as proj
import texto_bloco as tb
from produtos import carregar_produto


def _lamina(textos, fundo="#163a2f"):
    return {"id": "t", "fundo": fundo, "espacos": [], "sobreposicoes": [], "textos": textos}


def test_lista_fontes_instaladas():
    fontes = tb.fontes_instaladas()
    assert fontes, "o Windows tem de ter fontes instaladas"
    assert all(str(a).lower().endswith((".ttf", ".otf", ".ttc")) for a in fontes.values())
    nomes = [n.lower() for n in fontes]
    assert nomes == sorted(nomes)


def test_arquivo_da_fonte_cai_na_arial():
    assert tb.arquivo_da_fonte("Fonte que não existe 123") == tb.arquivo_da_fonte("Arial")
    assert tb.arquivo_da_fonte("ARIAL") == tb.arquivo_da_fonte("Arial")


def test_bloco_novo_tem_todos_os_campos():
    b = tb.novo_bloco(0.1, 0.2, 0.3, 0.4, texto="Oi")
    assert (b["x"], b["y"], b["w"], b["h"]) == (0.1, 0.2, 0.3, 0.4)
    assert b["texto"] == "Oi"
    for chave in tb.PADRAO:
        assert chave in b
    assert tb.completar({"texto": "só isso"})["alinhar"] == "centro"


def test_texto_vazio_fica_todo_transparente():
    img = tb.renderizar(tb.novo_bloco(0, 0, 1, 1, texto="   "), (200, 100), 11.8)
    assert img.size == (200, 100)
    assert img.getchannel("A").getbbox() is None


def test_fundo_do_bloco_e_transparente_so_o_texto_pinta():
    img = tb.renderizar(tb.novo_bloco(0, 0, 1, 1, texto="ENFERMAGEM", cor="#ff0000"), (600, 160), 11.8)
    alfa = img.getchannel("A")
    caixa = alfa.getbbox()
    assert caixa is not None
    assert alfa.getpixel((0, 0)) == 0  # canto sem texto: transparente
    cheios = [p for p in img.getdata() if p[3] > 200]
    assert cheios and all(p[:3] == (255, 0, 0) for p in cheios)


def test_encaixar_no_bloco_usa_o_bloco_todo_sem_vazar():
    img = tb.renderizar(tb.novo_bloco(0, 0, 1, 1, texto="Brenda Trindade Araújo", cor="#ffffff"), (700, 500), 11.8)
    x0, y0, x1, y1 = img.getchannel("A").getbbox()
    assert x1 - x0 <= 700 and y1 - y0 <= 500
    assert (x1 - x0) > 700 * 0.3  # ocupa boa parte do bloco


def test_bloco_menor_gera_texto_menor():
    grande = tb.renderizar(tb.novo_bloco(0, 0, 1, 1, texto="Formandos"), (800, 300), 11.8)
    pequeno = tb.renderizar(tb.novo_bloco(0, 0, 1, 1, texto="Formandos"), (400, 150), 11.8)
    lg = grande.getchannel("A").getbbox()
    lp = pequeno.getchannel("A").getbbox()
    assert (lg[3] - lg[1]) > (lp[3] - lp[1])


def test_tamanho_fixo_em_pontos_segue_a_escala_da_lamina():
    bloco = tb.novo_bloco(0, 0, 1, 1, texto="Aa", tamanho_pt=20, alinhar="esquerda", alinhar_v="topo")
    pequeno = tb.renderizar(bloco, (900, 400), 6.0).getchannel("A").getbbox()
    grande = tb.renderizar(bloco, (900, 400), 12.0).getchannel("A").getbbox()
    assert (grande[3] - grande[1]) > (pequeno[3] - pequeno[1]) * 1.6


@pytest.mark.parametrize("alinhar,esperado", [("esquerda", "esq"), ("direita", "dir")])
def test_alinhamento_horizontal(alinhar, esperado):
    bloco = tb.novo_bloco(0, 0, 1, 1, texto="Oi", tamanho_pt=12, alinhar=alinhar, alinhar_v="meio")
    x0, _y0, x1, _y1 = tb.renderizar(bloco, (800, 200), 11.8).getchannel("A").getbbox()
    if esperado == "esq":
        assert x0 < 100
    else:
        assert x1 > 700


def test_maiusculas_e_sombra_nao_quebram():
    bloco = tb.novo_bloco(0, 0, 1, 1, texto="curso", maiusculas=True, sombra=True, espaco_letras=0.2, opacidade=60)
    img = tb.renderizar(bloco, (500, 140), 11.8)
    assert img.getchannel("A").getbbox() is not None
    assert max(img.getchannel("A").getdata()) <= int(255 * 0.6) + 1


def test_compor_cola_o_texto_por_cima_do_fundo_e_so_na_area_do_bloco():
    produto = carregar_produto("30X22")
    lamina = _lamina([tb.novo_bloco(0.5, 0.2, 0.4, 0.3, texto="Nome", cor="#ffffff")])
    sem = exportar.renderizar_lamina(dict(lamina, textos=[]), produto, {}, largura=800)
    com = exportar.renderizar_lamina(lamina, produto, {}, largura=800)
    assert sem.size == com.size
    from PIL import ImageChops
    diferenca = ImageChops.difference(sem.convert("RGB"), com.convert("RGB")).getbbox()
    assert diferenca is not None
    W, H = com.size
    assert diferenca[0] >= int(0.5 * W) - 2 and diferenca[2] <= int(0.9 * W) + 2
    assert diferenca[1] >= int(0.2 * H) - 2 and diferenca[3] <= int(0.5 * H) + 2


def test_compor_mantem_fundo_transparente_no_modo_sem_fundo():
    produto = carregar_produto("30X22")
    lamina = _lamina([tb.novo_bloco(0.1, 0.1, 0.5, 0.3, texto="Oi")])
    img = exportar.renderizar_lamina(lamina, produto, {}, largura=600, sem_fundo=True)
    assert img.mode == "RGBA"
    assert img.getchannel("A").getbbox() is not None


def test_abrir_projeto_antigo_ganha_lista_de_textos(tmp_path):
    caminho = tmp_path / "velho.album.json"
    caminho.write_text(json.dumps({"produto": "30X22", "laminas": [{"id": "a", "espacos": []}]}), encoding="utf-8")
    aberto = proj.abrir(str(caminho))
    assert aberto["laminas"][0]["textos"] == []


def test_texto_sobrevive_a_salvar_e_abrir(tmp_path):
    projeto = proj.novo("30X22")
    bloco = tb.novo_bloco(0.2, 0.2, 0.3, 0.2, texto="Nome\nCurso", fonte="Montserrat Medium", espaco_letras=0.1)
    projeto["laminas"] = [_lamina([bloco])]
    caminho = proj.salvar(projeto, str(tmp_path / "t.album.json"))
    de_volta = proj.abrir(str(caminho))
    assert de_volta["laminas"][0]["textos"][0]["texto"] == "Nome\nCurso"
    assert de_volta["laminas"][0]["textos"][0]["fonte"] == "Montserrat Medium"
