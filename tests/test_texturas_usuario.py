# -*- coding: utf-8 -*-
"""Texturas do usuário: reconhecer na pasta, extrair de uma imagem, PNG como está e degradê só com as cores."""

import numpy as np
import pytest
from PIL import Image, ImageFilter

import composicao
import texturas_usuario as tu


@pytest.fixture(autouse=True)
def _preferencia_limpa():
    arquivo = tu._arquivo()
    if arquivo.is_file():
        arquivo.unlink()
    tu.recarregar()
    yield
    if arquivo.is_file():
        arquivo.unlink()
    tu.recarregar()


def _degrade(caminho, tamanho=(900, 600)):
    x = np.linspace(20, 200, tamanho[0], dtype=np.float32)[None, :].repeat(tamanho[1], axis=0)
    Image.fromarray(np.stack([x, x * 0.6, x * 0.3], axis=-1).astype(np.uint8)).save(caminho)
    return str(caminho)


def _padrao(tamanho=(900, 600), periodo=24):
    yy, xx = np.mgrid[0:tamanho[1], 0:tamanho[0]]
    return (np.sin(xx * 2 * np.pi / periodo) * np.sin(yy * 2 * np.pi / periodo)).astype(np.float32)


def _textura(caminho, tamanho=(900, 600), com_luz=True):
    """Desenho repetido na imagem toda, por cima de uma luz que muda de um lado ao outro."""
    luz = np.linspace(60, 120, tamanho[0], dtype=np.float32)[None, :].repeat(tamanho[1], axis=0) if com_luz else 90.0
    cinza = np.clip(luz + _padrao(tamanho) * 22.0, 0, 255).astype(np.uint8)
    Image.merge("RGB", (Image.fromarray(cinza),) * 3).save(caminho)
    return str(caminho)


def _cenario(caminho, tamanho=(900, 600)):
    """Metade lisa, metade cheia de detalhe e com várias cores: uma foto, não uma textura."""
    rng = np.random.default_rng(3)
    arr = np.zeros((tamanho[1], tamanho[0], 3), dtype=np.uint8)
    arr[:, : tamanho[0] // 2] = (200, 40, 40)
    arr[:, tamanho[0] // 2:] = rng.integers(0, 255, (tamanho[1], tamanho[0] - tamanho[0] // 2, 3), dtype=np.uint8)
    arr[: tamanho[1] // 2, tamanho[0] // 2:, 0] //= 4  # canto verde/azul
    Image.fromarray(arr).save(caminho)
    return str(caminho)


def _png_transparente(caminho, tamanho=(600, 400)):
    arr = np.zeros((tamanho[1], tamanho[0], 4), dtype=np.uint8)
    arr[100:300, 200:400] = (255, 200, 0, 255)
    Image.fromarray(arr, "RGBA").save(caminho)
    return str(caminho)


def test_reconhece_textura_degrade_cenario_e_png_transparente(tmp_path):
    assert tu.classificar(_textura(tmp_path / "t.png")) == "textura"
    assert tu.classificar(_degrade(tmp_path / "d.png")) == "degrade"
    assert tu.classificar(_cenario(tmp_path / "c.png")) == "imagem"
    assert tu.classificar(_png_transparente(tmp_path / "p.png")) == "transparente"
    assert tu.eh_textura(str(tmp_path / "t.png")) and tu.eh_textura(str(tmp_path / "p.png"))
    assert not tu.eh_textura(str(tmp_path / "d.png")) and not tu.eh_textura(str(tmp_path / "c.png"))


def test_marcar_a_mao_manda_e_volta_ao_automatico(tmp_path):
    degrade = _degrade(tmp_path / "d.png")
    tu.marcar(degrade, "textura")
    assert tu.classificar(degrade) == "textura"
    tu.recarregar()  # ficou gravado
    assert tu.classificar(degrade) == "textura"
    tu.marcar(degrade, None)
    assert tu.classificar(degrade) == "degrade"


def test_lista_as_texturas_da_pasta_e_as_importadas(tmp_path):
    pasta = tmp_path / "fundos"
    pasta.mkdir()
    t = _textura(pasta / "damasco.png")
    _degrade(pasta / "liso.png")
    _cenario(pasta / "festa.png")
    fora = _degrade(tmp_path / "carregada.png")
    assert tu.listar_texturas(str(pasta)) == [t]
    tu.importar(fora)
    assert set(tu.listar_texturas(str(pasta))) == {t, fora}  # o que a usuária carrega vale mesmo sendo lisa
    assert tu.listar_texturas(str(tmp_path / "nao_existe")) == [fora]


def test_extrair_tira_a_luz_geral_e_guarda_o_desenho(tmp_path):
    caminho = _textura(tmp_path / "t.png")
    relevo = np.asarray(tu.extrair_relevo(caminho), dtype=np.float32) - 128.0
    assert abs(float(relevo.mean())) < 4.0
    # a luz (mais clara à direita) some: as duas metades ficam com a mesma média
    meio = relevo.shape[1] // 2
    assert abs(float(relevo[:, :meio].mean()) - float(relevo[:, meio:].mean())) < 4.0
    padrao = _padrao()
    correlacao = float(np.corrcoef(relevo[40:-40, 40:-40].ravel(), padrao[40:-40, 40:-40].ravel())[0, 1])
    assert correlacao > 0.9


def test_imagem_lisa_nao_vira_textura_de_ruido(tmp_path):
    relevo = np.asarray(tu.extrair_relevo(_degrade(tmp_path / "d.png")), dtype=np.float32) - 128.0
    assert float(np.abs(relevo).mean()) < 30.0


def test_relevo_extraido_fica_guardado(tmp_path):
    caminho = _textura(tmp_path / "t.png")
    a = tu.caminho_relevo(caminho)
    assert a.is_file() and tu.caminho_relevo(caminho) == a
    assert tu.caminho_relevo(caminho, filtrar=False) != a


def test_preferencia_padrao_e_minhas_texturas_e_fica_gravada(tmp_path):
    assert tu.preferencia()["modo"] == "minhas"
    tu.definir_preferencia(modo="nenhuma", pasta=str(tmp_path))
    tu.recarregar()
    pref = tu.preferencia()
    assert pref["modo"] == "nenhuma" and pref["pasta"] == str(tmp_path)
    tu.definir_preferencia(modo="modo que não existe")
    assert tu.preferencia()["modo"] == "nenhuma"


def test_escolha_da_textura_na_receita(tmp_path):
    pasta = tmp_path / "fundos"
    pasta.mkdir()
    t1 = _textura(pasta / "a.png")
    t2 = _textura(pasta / "b.png", com_luz=False)

    tu.definir_preferencia(modo="programa")
    assert tu.escolher_para_espec({"textura": "veludo", "seed": 1}) == {"textura": "veludo", "seed": 1}

    tu.definir_preferencia(modo="nenhuma")
    assert tu.escolher_para_espec({"textura": "veludo", "seed": 1}) == {"textura": "nenhuma", "seed": 1}

    tu.definir_preferencia(modo="uma", arquivo=t2, arquivo_modo="relevo")
    espec = tu.escolher_para_espec({"textura": "veludo", "seed": 1})
    assert espec["textura"] == "nenhuma" and espec["textura_arquivo"] == t2 and espec["textura_modo"] == "relevo"

    tu.definir_preferencia(modo="minhas", pasta=str(pasta))
    usadas = {tu.escolher_para_espec({"seed": 10}, i)["textura_arquivo"] for i in range(4)}
    assert usadas == {t1, t2}  # reveza entre as texturas reconhecidas
    assert tu.escolher_para_espec({"seed": 10}, 0) == tu.escolher_para_espec({"seed": 10}, 0)  # sempre igual

    tu.definir_preferencia(pasta=str(tmp_path / "vazia"))
    sem = tu.escolher_para_espec({"textura": "veludo", "seed": 1})
    assert sem["textura"] == "nenhuma" and "textura_arquivo" not in sem  # sem texturas: só as cores


def _grao(img):
    cinza = img.convert("L")
    return float((np.asarray(cinza, dtype=np.float32)
                  - np.asarray(cinza.filter(ImageFilter.GaussianBlur(3)), dtype=np.float32)).std())


def test_fundo_gerado_usa_a_textura_do_usuario_e_o_degrade_puro_e_liso(tmp_path):
    textura = _textura(tmp_path / "t.png")
    base = {"padrao_cor": "preto", "tipo": "degrade", "subtipo": "linear_diagonal", "seed": 5,
            "cores": ["#0c2d3a", "#0e5a66", "#083840"], "textura": "nenhuma"}
    liso = composicao.renderizar_fundo_gerado(dict(base), (600, 400))
    com = composicao.renderizar_fundo_gerado(dict(base, textura_arquivo=textura, textura_modo="relevo"), (600, 400))
    antigo = composicao.renderizar_fundo_gerado(dict(base, textura="veludo_ouro"), (600, 400))
    assert _grao(liso) < 1.2
    assert _grao(com) > _grao(liso) * 2.5
    assert liso.tobytes() != antigo.tobytes()  # a textura antiga do programa continua existindo para álbuns velhos
    # as cores continuam as do programa: a média de cor quase não muda com a textura
    m_liso = np.asarray(liso, dtype=np.float32).mean(axis=(0, 1))
    m_com = np.asarray(com, dtype=np.float32).mean(axis=(0, 1))
    assert float(np.abs(m_liso - m_com).max()) < 8.0
    assert com.tobytes() == composicao.renderizar_fundo_gerado(
        dict(base, textura_arquivo=textura, textura_modo="relevo"), (600, 400)).tobytes()


def test_textura_que_sumiu_do_disco_nao_quebra_o_fundo(tmp_path):
    base = {"padrao_cor": "branco", "tipo": "degrade", "subtipo": "radial_suave", "seed": 2,
            "cores": ["#faf9f6", "#f0eae3", "#e8eef2"], "textura": "nenhuma",
            "textura_arquivo": str(tmp_path / "apagada.png"), "textura_modo": "relevo"}
    img = composicao.renderizar_fundo_gerado(base, (300, 200))
    assert img.size == (300, 200)


def test_png_com_transparencia_e_colado_como_esta(tmp_path):
    png = _png_transparente(tmp_path / "p.png")
    assert tu.modo_do_arquivo(png) == "png"
    fundo = Image.new("RGB", (600, 400), (10, 20, 30))
    saida = tu.aplicar(fundo, {"textura_arquivo": png, "textura_modo": "png"})
    assert saida.getpixel((300, 200)) == (255, 200, 0)   # onde o PNG é opaco
    assert saida.getpixel((20, 20)) == (10, 20, 30)      # onde é transparente fica a cor do programa


def test_tema_e_galeria_seguem_a_preferencia(tmp_path):
    textura = _textura(tmp_path / "t.png")
    tu.definir_preferencia(modo="uma", arquivo=textura, arquivo_modo="relevo")
    tema = composicao.gerar_fundo_de_tema("colacao_royal", medidas_mm=(297, 224))["gerado"]
    assert tema["textura"] == "nenhuma" and tema["textura_arquivo"] == textura
    galeria = composicao.gerar_galeria_fundos_procedurais(None, quantidade=4, seed_base=3, materializar=False)
    assert all(g["gerado"]["textura_arquivo"] == textura for g in galeria)
    tu.definir_preferencia(modo="nenhuma")
    galeria = composicao.gerar_galeria_fundos_procedurais(None, quantidade=4, seed_base=3, materializar=False)
    assert all(g["gerado"]["textura"] == "nenhuma" and "textura_arquivo" not in g["gerado"] for g in galeria)
