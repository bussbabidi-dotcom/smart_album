# -*- coding: utf-8 -*-
import re
import struct

import pytest
from PIL import Image

import exportar
import exportar_psd
import projeto as proj
from produtos import carregar_produto, lamina_px

DPI_TESTE = 150


# ------------------------------------------------------------
# Funções puras
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "texto, total, esperado",
    [
        ("1-3, 5", 6, [0, 1, 2, 4]),
        ("5-3", 6, [2, 3, 4]),
        ("2;2 2", 3, [1]),
    ],
)
def test_laminas_do_texto(texto, total, esperado):
    assert exportar.laminas_do_texto(texto, total) == esperado


@pytest.mark.parametrize("texto", ["", "0", "1-9", "a-b"])
def test_laminas_do_texto_recusa_entrada_invalida(texto):
    with pytest.raises(ValueError):
        exportar.laminas_do_texto(texto, 5)


def test_nome_arquivo_usa_o_padrao_e_limpa_caracteres_proibidos():
    projeto = {"nome": "Ana: Casamento?", "laminas": [{}] * 12}
    produto = {"nome": "30X20"}
    assert exportar.nome_arquivo("{n:02d} - {album}", 3, projeto, produto) == "03 - Ana_ Casamento_"
    assert exportar.nome_arquivo("{produto}_{n}de{total}", 1, projeto, produto) == "30X20_1de12"


def test_nome_arquivo_recusa_padrao_invalido():
    with pytest.raises(ValueError):
        exportar.nome_arquivo("{desconhecido}", 1, {"nome": "x"}, {})


def test_formatos_ativos_aceita_opcoes_novas_e_antigas():
    assert exportar.formatos_ativos({"exportar_jpg": True, "exportar_pdf": True}) == ["jpg", "pdf"]
    assert exportar.formatos_ativos({"formato": "todos"}) == ["jpg", "psd", "pdf"]
    assert exportar.formatos_ativos({"formato": "psd"}) == ["psd"]
    assert exportar.formatos_ativos(None) == ["jpg"]


def test_tamanho_produto_texto():
    assert exportar.tamanho_produto_texto({"nome": "Álbum 30 x 20"}) == "30X20"
    assert exportar.tamanho_produto_texto({"nome": "", "pagina_mm": (240, 340)}) == "24X34"


@pytest.mark.parametrize("dpi, esperado", [(150, 150), (300, 300), (200, 150), (260, 300), ("x", 300)])
def test_produto_com_dpi_arredonda_para_150_ou_300(dpi, esperado):
    produto = {"nome": "20X20", "dpi": 300}
    assert exportar.produto_com_dpi(produto, dpi)["dpi"] == esperado
    assert produto["dpi"] == 300


def test_lamina_px_bate_com_as_medidas_do_produto():
    produto = carregar_produto("20X20")
    largura_mm = produto["pagina_mm"][0] * produto["paginas_por_lamina"] + 2 * produto["sangria_mm"]
    altura_mm = produto["pagina_mm"][1] + 2 * produto["sangria_mm"]
    w, h = lamina_px(produto)
    assert w == pytest.approx(largura_mm * produto["dpi"] / 25.4, abs=1)
    assert h == pytest.approx(altura_mm * produto["dpi"] / 25.4, abs=1)


def test_projeto_salvar_e_abrir_preserva_laminas(album, tmp_path):
    caminho = proj.salvar(album["projeto"], tmp_path / "teste.album.json")
    reaberto = proj.abrir(caminho)
    assert len(reaberto["laminas"]) == len(album["laminas"])
    assert [len(l["espacos"]) for l in reaberto["laminas"]] == [len(l["espacos"]) for l in album["laminas"]]


# ------------------------------------------------------------
# Exportação de arquivos
# ------------------------------------------------------------

def _opcoes(indices):
    return {"dpi": DPI_TESTE, "indices": indices}


def test_exportar_jpg_grava_uma_imagem_por_lamina_no_tamanho_certo(album, tmp_path):
    produto = album["produto"]
    arquivos = exportar.exportar_jpg(album["projeto"], produto, album["info"], tmp_path, aviso=None, opcoes=_opcoes([0, 1]))
    assert [a.name for a in arquivos] == ["01 - lamina.jpg", "02 - lamina.jpg"]
    esperado = lamina_px(exportar.produto_com_dpi(produto, DPI_TESTE))
    for arq in arquivos:
        with Image.open(arq) as img:
            assert img.format == "JPEG"
            assert img.size == esperado
            assert round(img.info["dpi"][0]) == DPI_TESTE


def test_exportar_jpg_com_marcas_de_registro_aumenta_a_imagem(album, tmp_path):
    produto = album["produto"]
    opcoes = dict(_opcoes([0]), marcas_registro=True)
    [arq] = exportar.exportar_jpg(album["projeto"], produto, album["info"], tmp_path, aviso=None, opcoes=opcoes)
    esperado = exportar.tamanho_final_px(exportar.produto_com_dpi(produto, DPI_TESTE), DPI_TESTE, marcas_registro=True)
    sem_marcas = lamina_px(exportar.produto_com_dpi(produto, DPI_TESTE))
    with Image.open(arq) as img:
        assert img.size == tuple(esperado)
        assert img.size[0] > sem_marcas[0] and img.size[1] > sem_marcas[1]


def test_exportar_jpg_pula_existentes_quando_pedido(album, tmp_path):
    args = (album["projeto"], album["produto"], album["info"], tmp_path)
    exportar.exportar_jpg(*args, aviso=None, opcoes=_opcoes([0]))
    avisos = []
    novos = exportar.exportar_jpg(*args, aviso=avisos.append, opcoes=dict(_opcoes([0]), existentes="pular"))
    assert novos == []
    assert any("Pulou" in a for a in avisos)


def test_exportar_pdf_unico_tem_uma_pagina_por_lamina(album, tmp_path):
    [arq] = exportar.exportar_pdf(
        album["projeto"], album["produto"], album["info"], tmp_path, aviso=lambda *_a: None, opcoes=_opcoes([0, 1, 2])
    )
    dados = arq.read_bytes()
    assert dados.startswith(b"%PDF")
    assert len(re.findall(rb"/Type\s*/Page\b", dados)) == 3


def test_exportar_psd_grava_cabecalho_e_camadas(album, tmp_path):
    produto = album["produto"]
    [arq] = exportar_psd.exportar_psd(
        album["projeto"], produto, album["info"], tmp_path, aviso=lambda *_a: None, opcoes=_opcoes([0])
    )
    dados = arq.read_bytes()
    assinatura, versao, canais, altura, largura, bits, modo = struct.unpack(">4sH6xHIIHH", dados[:26])
    assert (assinatura, versao, bits, modo) == (b"8BPS", 1, 8, 3)
    assert canais >= 3
    assert (largura, altura) == lamina_px(exportar.produto_com_dpi(produto, DPI_TESTE))

    # Pula Color Mode Data e Image Resources até a seção Layer and Mask Information
    pos = 26
    (tam_cor,) = struct.unpack(">I", dados[pos:pos + 4])
    pos += 4 + tam_cor
    (tam_recursos,) = struct.unpack(">I", dados[pos:pos + 4])
    pos += 4 + tam_recursos
    (_tam_camadas, _tam_info, qtd_camadas) = struct.unpack(">IIh", dados[pos:pos + 10])
    fotos_lamina = [e for e in album["laminas"][0]["espacos"] if e.get("foto")]
    assert abs(qtd_camadas) >= 1 + len(fotos_lamina)
