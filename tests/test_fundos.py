# -*- coding: utf-8 -*-
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageFilter

import composicao
import diagramar
import texturas_fundo
from comum import limpar_fundos_temporarios
from produtos import carregar_produto

MEDIDAS_20X20 = (406.0, 206.0)


def _alta_frequencia(img, caixa=None):
    """Desvio do detalhe fino (imagem menos a versão desfocada), em níveis de cinza."""
    cinza = img.convert("L")
    if caixa:
        cinza = cinza.crop(caixa)
    a = np.asarray(cinza, dtype=np.float32)
    b = np.asarray(cinza.filter(ImageFilter.GaussianBlur(2)), dtype=np.float32)
    return float((a - b).std())


def _textura(nome, tamanho=(2398, 1217), padrao="preto", cor=(40, 50, 90), seed=5):
    base = Image.new("RGB", tamanho, cor)
    return texturas_fundo.aplicar_textura(base, nome, seed=seed, altura_mm=MEDIDAS_20X20[1], padrao_cor=padrao)


# ------------------------------------------------------------
# Texturas
# ------------------------------------------------------------

@pytest.mark.parametrize("nome", sorted(texturas_fundo.TEXTURAS))
def test_textura_tem_detalhe_fino_no_tamanho_final(nome):
    padrao = "branco" if nome.startswith(("gesso", "papel", "linho")) else "preto"
    cor = (236, 232, 222) if padrao == "branco" else (40, 50, 90)
    assert _alta_frequencia(_textura(nome, padrao=padrao, cor=cor)) > 0.8


def test_textura_e_deterministica():
    a = _textura("veludo_ouro", tamanho=(900, 457))
    b = _textura("veludo_ouro", tamanho=(900, 457))
    assert np.array_equal(np.asarray(a), np.asarray(b))
    c = _textura("veludo_ouro", tamanho=(900, 457), seed=6)
    assert not np.array_equal(np.asarray(a), np.asarray(c))


def test_grao_parecido_a_150_e_300_dpi():
    a150 = _textura("concreto", tamanho=(2398, 1217))
    a300 = _textura("concreto", tamanho=(4795, 2433)).resize((2398, 1217), Image.LANCZOS)
    e150, e300 = _alta_frequencia(a150), _alta_frequencia(a300)
    assert 0.6 < e150 / e300 < 1.6


def test_centro_da_lamina_e_mais_calmo_que_as_laterais():
    img = _textura("concreto", tamanho=(2398, 1217))
    w, h = img.size
    centro = _alta_frequencia(img, (int(w * 0.47), 0, int(w * 0.53), h))
    lateral = _alta_frequencia(img, (int(w * 0.05), 0, int(w * 0.11), h))
    assert centro < lateral * 0.75


def test_textura_desconhecida_nao_altera_a_imagem():
    base = Image.new("RGB", (64, 32), (10, 20, 30))
    assert texturas_fundo.aplicar_textura(base, "nao_existe") is base


# ------------------------------------------------------------
# Evento -> tema
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "foto, tema",
    [
        ({"caminho": r"D:\fotos\Turma\Estudio\IMG_01.jpg"}, "colacao_royal"),
        ({"caminho": r"D:\fotos\Turma\Studio\IMG_01.jpg"}, "colacao_royal"),
        ({"caminho": r"D:\fotos\Turma\Colação 2\IMG_01.jpg"}, "estudio_dark_bokeh"),
        ({"caminho": r"D:\fotos\Turma\Jaleco\IMG_01.jpg"}, "jaleco_fine_art"),
        ({"caminho": r"D:\fotos\Turma\Externas\IMG_01.jpg"}, "externas_botanica"),
        ({"caminho": r"D:\fotos\Turma\Baile\IMG_01.jpg"}, "baile_dark_luxury"),
        ({"caminho": r"D:\x.jpg", "nome_grupo": "Baile da Turma 2026"}, "baile_dark_luxury"),
        ({"caminho": r"D:\fotos\Turma\Diversas\IMG_01.jpg"}, None),
    ],
)
def test_tema_do_evento_pela_pasta_ou_grupo(foto, tema):
    assert diagramar.tema_evento_das_fotos([foto]) == tema


def test_pasta_raiz_do_fotografo_nao_vira_evento():
    foto = {"caminho": r"D:\Estudio Fulano\Clientes\2026\Turma Medicina\Sem nome\IMG_01.jpg"}
    assert diagramar.tema_evento_das_fotos([foto]) is None


def test_empate_mantem_o_tema_que_a_lamina_ja_tinha():
    baile = {"caminho": r"D:\t\Baile\a.jpg"}
    jaleco = {"caminho": r"D:\t\Jaleco\b.jpg"}
    assert diagramar.tema_evento_das_fotos([jaleco, baile], preferido="baile_dark_luxury") == "baile_dark_luxury"
    assert diagramar.tema_evento_das_fotos([baile, jaleco], preferido="jaleco_fine_art") == "jaleco_fine_art"


def test_tema_precisa_da_metade_das_fotos():
    baile = {"caminho": r"D:\t\Baile\a.jpg"}
    outra = {"caminho": r"D:\t\Diversas\b.jpg"}
    assert diagramar.tema_evento_das_fotos([baile, outra]) == "baile_dark_luxury"
    assert diagramar.tema_evento_das_fotos([baile, outra, dict(outra)]) is None


# ------------------------------------------------------------
# Fundo automático da lâmina
# ------------------------------------------------------------

def _lamina_com_fotos(pasta, cores):
    pasta.mkdir(parents=True, exist_ok=True)
    espacos = []
    for i, cor in enumerate(cores):
        caminho = pasta / ("f%d.jpg" % i)
        Image.new("RGB", (600, 400), cor).save(caminho)
        espacos.append({"x": 0.06 + 0.22 * i, "y": 0.2, "w": 0.18, "h": 0.5, "foto": str(caminho), "edicao": {}})
    return {"id": "t", "fundo": "#ffffff", "espacos": espacos, "sobreposicoes": []}


def _cfg(produto):
    return diagramar.configuracao(produto, {"modo_auto_fundos": "gerados", "escopo_auto_fundos": "lamina", "_seed_auto_fundos": 99})


def test_fundo_de_evento_usa_o_tema_e_grava_tema_evento(tmp_path):
    produto = carregar_produto("20X20")
    lamina = _lamina_com_fotos(tmp_path / "Baile", [(200, 40, 40), (180, 60, 30)])
    assert diagramar.aplicar_fundos_automaticos(lamina, None, produto, _cfg(produto))
    receita = lamina["fundo_imagem"]["gerado"]
    assert lamina["tema_evento"] == "baile_dark_luxury"
    assert receita["id"].startswith("cap_baile_dark_luxury")
    assert receita["textura"] == "veludo_ouro"
    assert (receita["largura_mm"], receita["altura_mm"]) == pytest.approx(MEDIDAS_20X20, abs=0.1)


@pytest.mark.parametrize("cores", [[(30, 90, 200), (20, 120, 190)], [(220, 200, 170), (230, 210, 190)]],
                         ids=["fotos_escuras", "fotos_claras"])
def test_fundo_sem_evento_tem_cor_forte_e_textura(tmp_path, cores):
    produto = carregar_produto("20X20")
    lamina = _lamina_com_fotos(tmp_path / "Diversas", cores)
    assert diagramar.aplicar_fundos_automaticos(lamina, None, produto, _cfg(produto))
    assert "tema_evento" not in lamina
    receita = lamina["fundo_imagem"]["gerado"]
    assert receita["textura"] in composicao.TEXTURAS_ESCURAS + composicao.TEXTURAS_CLARAS
    img = composicao.renderizar_fundo_gerado(receita, (800, 406))
    media = np.asarray(img, dtype=np.float32).reshape(-1, 3).mean(axis=0)
    # Croma (canal maior - menor) da cor média: ~16 a 28 com a mistura antiga de 15% a 25%.
    assert media.max() - media.min() > 45


# ------------------------------------------------------------
# PNG da prévia, exportação e limpeza ao fechar
# ------------------------------------------------------------

def test_previa_tem_a_proporcao_da_area_coberta():
    assert composicao.tamanho_previa_fundo({"largura_mm": 606, "altura_mm": 206}) == (2048, 696)
    assert composicao.tamanho_previa_fundo({"largura_mm": 200, "altura_mm": 206})[1] == 2048
    assert composicao.tamanho_previa_fundo({}) == (2048, 1024)


def test_limpar_fundos_e_recriar_quando_usado():
    dados = composicao.gerar_fundo_de_tema("jaleco_fine_art", medidas_mm=MEDIDAS_20X20)
    png = Path(dados["caminho"])
    assert png.is_file()

    limpar_fundos_temporarios()
    assert not png.exists()

    previa = composicao.fundo_posicionado(dict(dados), (600, 304), rapido=True)
    assert previa is not None and previa.size == (600, 304)
    assert png.is_file()


def test_fundo_gerado_apagado_nao_conta_como_foto_faltando(tmp_path):
    import projeto as proj

    gerado = composicao.gerar_fundo_de_tema("baile_dark_luxury", medidas_mm=MEDIDAS_20X20)
    limpar_fundos_temporarios()
    sumida = {"caminho": str(tmp_path / "fundo_do_cliente.jpg")}
    projeto = {"fotos": [], "laminas": [
        {"espacos": [], "fundo_imagem": gerado},
        {"espacos": [], "fundo_imagem_esquerda": sumida},
    ]}
    assert proj.faltando(projeto) == [sumida["caminho"]]


def test_exportacao_renderiza_da_receita_sem_precisar_do_png():
    dados = composicao.gerar_fundo_de_tema("colacao_royal", medidas_mm=MEDIDAS_20X20)
    limpar_fundos_temporarios()
    camada = composicao.fundo_posicionado(dict(dados), (1200, 609), rapido=False)
    assert camada.size == (1200, 609)
    assert not Path(dados["caminho"]).exists()
