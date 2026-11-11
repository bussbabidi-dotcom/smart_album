# -*- coding: utf-8 -*-
import shutil
import socket
import urllib.request
import zipfile

import numpy as np
import pytest
from PIL import Image, ImageDraw

import analise
import diagramar
import ia_cena
import ia_edicao
import ia_modelos
import recorte

TEM_MODELOS = ia_modelos.disponivel("cena") and ia_modelos.disponivel("edicao")
precisa_modelos = pytest.mark.skipif(not TEM_MODELOS, reason="modelos de IA não instalados em recursos/modelos_ia")


def lut_identidade(dim=33):
    g = np.arange(dim, dtype=np.float32) / (dim - 1)
    return np.stack([
        np.broadcast_to(g[None, None, :], (dim, dim, dim)),
        np.broadcast_to(g[None, :, None], (dim, dim, dim)),
        np.broadcast_to(g[:, None, None], (dim, dim, dim)),
    ]).astype(np.float32)


def trilinear_direta(arr, lut):
    """Conta do trilinear.c original (8 cantos, LUT[c][b][g][r]) para conferir a versão rápida."""
    dim = lut.shape[-1]
    binsize = np.float32(ia_edicao.FATOR_BIN / (dim - 1))
    x = arr.astype(np.float32) / np.float32(255.0)
    ids = np.floor(x / binsize).astype(np.int64)
    d = np.fmod(x, binsize) / binsize
    r, g, b = ids[..., 0], ids[..., 1], ids[..., 2]
    dr, dg, db = d[..., 0], d[..., 1], d[..., 2]
    saida = np.zeros(arr.shape, np.float32)
    for c in range(3):
        L = lut[c]
        saida[..., c] = (
            (1 - dr) * (1 - dg) * (1 - db) * L[b, g, r] + dr * (1 - dg) * (1 - db) * L[b, g, r + 1]
            + (1 - dr) * dg * (1 - db) * L[b, g + 1, r] + dr * dg * (1 - db) * L[b, g + 1, r + 1]
            + (1 - dr) * (1 - dg) * db * L[b + 1, g, r] + dr * (1 - dg) * db * L[b + 1, g, r + 1]
            + (1 - dr) * dg * db * L[b + 1, g + 1, r] + dr * dg * db * L[b + 1, g + 1, r + 1])
    return np.floor(np.clip(saida, 0, 1) * 255 + 0.5).astype(np.uint8)


def foto_teste(caminho, cor_fundo=(120, 160, 210)):
    img = Image.new("RGB", (900, 600), cor_fundo)
    d = ImageDraw.Draw(img)
    d.rectangle((0, 380, 900, 600), fill=(70, 120, 60))
    d.ellipse((400, 150, 500, 260), fill=(225, 185, 155))
    d.rectangle((380, 260, 520, 520), fill=(240, 240, 240))
    img.save(caminho, quality=90)
    return caminho


@pytest.fixture
def pastas_modelos(tmp_path, monkeypatch):
    """Isola as pastas de modelos (programa e importados) e devolve (embutida, local)."""
    embutida, local = tmp_path / "programa" / "modelos_ia", tmp_path / "local" / "modelos_ia"
    embutida.mkdir(parents=True)
    monkeypatch.setattr(ia_modelos, "pasta_embutida", lambda: embutida)
    monkeypatch.setattr(ia_modelos, "pasta_local", lambda: local)
    ia_modelos.estado_modelos(recalcular=True)
    yield embutida, local
    monkeypatch.undo()
    ia_modelos._dados_cache.clear()
    ia_modelos.estado_modelos(recalcular=True)


def copiar_modelos(destino):
    origem = ia_modelos.pasta_recurso("recursos") / "modelos_ia"
    for arq in origem.iterdir():
        if arq.is_file():
            shutil.copy2(arq, destino / arq.name)


# ------------------------------------------------------------
# Pré-processamento e mapa de categorias
# ------------------------------------------------------------

def test_preprocessamento_cena_tamanho_e_normalizacao():
    blob = ia_cena.preprocessar(Image.new("RGB", (640, 360), (255, 128, 0)))
    assert blob.shape == (1, 3, 224, 224) and blob.dtype == np.float32
    esperado = (np.array([255, 128, 0]) / 255.0 - ia_cena.MEDIA) / ia_cena.DESVIO
    assert np.allclose(blob[0, :, 100, 100], esperado, atol=1e-5)


def test_preprocessamento_lut_tamanho_e_faixa():
    blob = ia_edicao.preparar_entrada(Image.new("RGB", (2000, 1000), (10, 200, 255)))
    assert blob.shape == (1, 3, 256, 256) and blob.dtype == np.float32
    assert blob.min() >= 0.0 and blob.max() <= 1.0
    assert np.allclose(blob[0, :, 50, 50], np.array([10, 200, 255]) / 255.0, atol=1e-5)


def test_mapa_de_cenas():
    nomes = ["c%d" % i for i in range(365)]
    nomes[0], nomes[1], nomes[2], nomes[3], nomes[4] = "ballroom", "beach", "church/indoor", "auditorium", "office"
    externa = [0] * 365
    externa[1] = 1
    probs = np.zeros(365)
    probs[0], probs[1], probs[4] = 0.6, 0.25, 0.15
    massas = ia_cena.massas_por_cena(probs, nomes, externa)
    assert ia_cena.decidir(massas, 0.0) == (ia_cena.BAILE, 0.6)
    probs[:] = 0
    probs[2], probs[4] = 0.5, 0.5
    assert ia_cena.decidir(ia_cena.massas_por_cena(probs, nomes, externa), 0.0)[0] == ia_cena.CULTO
    probs[:] = 0
    probs[1] = 0.9
    probs[4] = 0.1
    assert ia_cena.decidir(ia_cena.massas_por_cena(probs, nomes, externa), 0.0)[0] == ia_cena.EXTERNA
    probs[:] = 0
    probs[4] = 1.0
    assert ia_cena.decidir(ia_cena.massas_por_cena(probs, nomes, externa), 0.0)[0] == ia_cena.INTERNA
    assert ia_cena.decidir(ia_cena.massas_por_cena(probs, nomes, externa), 0.9)[0] == ia_cena.ESTUDIO
    assert ia_cena.enquadramento({"qtd": 1}) == "Retrato individual"
    assert ia_cena.enquadramento({"qtd": 5}) == "Grupo"
    assert ia_cena.enquadramento(None) == "Sem rosto"


@precisa_modelos
def test_categorias_do_mapa_existem_no_places365():
    nomes = set(ia_cena.categorias()["categorias"])
    faltando = [c for cats in ia_cena.MAPA_CENAS.values() for c in cats if c not in nomes]
    assert not faltando


def test_fundo_uniforme_reconhece_estudio():
    liso = Image.new("RGB", (400, 600), (128, 128, 130))
    ImageDraw.Draw(liso).ellipse((150, 200, 250, 320), fill=(220, 180, 150))
    blocos = np.random.default_rng(1).integers(0, 256, (12, 8, 3), dtype=np.uint8)
    ruido = Image.fromarray(blocos).resize((400, 600), Image.NEAREST)
    assert ia_cena.fundo_uniforme(liso) > 0.8
    assert ia_cena.fundo_uniforme(ruido) < 0.1


# ------------------------------------------------------------
# Aplicação da 3D LUT
# ------------------------------------------------------------

def test_lut_identidade_nao_muda_a_foto():
    arr = np.random.default_rng(2).integers(0, 256, (123, 77, 3), dtype=np.uint8)
    assert np.array_equal(ia_edicao.aplicar_lut_array(arr, lut_identidade(), 100), arr)
    assert np.array_equal(ia_edicao.aplicar_lut_array(arr, lut_identidade(), 55), arr)


def test_lut_em_blocos_igual_direta_e_igual_ao_trilinear_original():
    rng = np.random.default_rng(3)
    lut = np.clip(lut_identidade() ** 0.7 + rng.normal(0, 0.03, (3, 33, 33, 33)), -0.1, 1.1).astype(np.float32)
    arr = rng.integers(0, 256, (211, 157, 3), dtype=np.uint8)
    inteira = ia_edicao.aplicar_lut_array(arr, lut, 100, bloco=10 ** 9)
    em_blocos = ia_edicao.aplicar_lut_array(arr, lut, 100, bloco=997)
    assert np.array_equal(inteira, em_blocos)
    referencia = trilinear_direta(arr, lut)
    dif = np.abs(inteira.astype(int) - referencia.astype(int))
    assert dif.max() <= 1 and dif.mean() < 0.01


def test_forca_mistura_com_original():
    rng = np.random.default_rng(4)
    lut = (lut_identidade() * 0.5 + 0.25).astype(np.float32)
    arr = rng.integers(0, 256, (40, 40, 3), dtype=np.uint8)
    cheia = ia_edicao.aplicar_lut_array(arr, lut, 100).astype(float)
    metade = ia_edicao.aplicar_lut_array(arr, lut, 50).astype(float)
    assert np.abs(metade - (arr + cheia) / 2.0).max() <= 1.0


# ------------------------------------------------------------
# Modelos: ausentes, corrompidos, importação offline
# ------------------------------------------------------------

def test_sem_modelo_programa_segue_sem_ia(pastas_modelos, tmp_path):
    estado = ia_modelos.estado_modelos()
    assert not estado["cena"]["ok"] and not estado["edicao"]["ok"]
    img = Image.open(foto_teste(tmp_path / "f.jpg")).convert("RGB")
    assert ia_cena.reconhecer(img) is None
    assert ia_edicao.pesos_lut(img) is None
    ed = {"exposicao": 10, "ia_lut_forca": 70, "ia_lut": [1.2, 0.5, -0.8]}
    assert recorte.aplicar_ajustes(img, ed).tobytes() == recorte.aplicar_ajustes(img, {"exposicao": 10}).tobytes()
    dados = analise.analisar_ia(str(tmp_path / "f.jpg"))
    assert "cena" not in dados and "ia_lut" not in dados and dados["w"] == 900
    assert "não instalado" in ia_modelos.texto_estado()


@precisa_modelos
def test_arquivo_com_sha256_errado_e_recusado(pastas_modelos):
    embutida, _local = pastas_modelos
    copiar_modelos(embutida)
    assert ia_modelos.estado_modelos(recalcular=True)["edicao"]["ok"]
    dados = bytearray((embutida / "lut3d_classificador.onnx").read_bytes())
    dados[len(dados) // 2] ^= 0xFF
    (embutida / "lut3d_classificador.onnx").write_bytes(bytes(dados))
    estado = ia_modelos.estado_modelos(recalcular=True)
    assert not estado["edicao"]["ok"] and "SHA-256" in estado["edicao"]["erro"]
    assert estado["cena"]["ok"]
    assert ia_modelos.rede("edicao") is None


@precisa_modelos
def test_importar_zip_local_tem_prioridade_e_recusa_pacote_alterado(pastas_modelos, tmp_path):
    embutida, local = pastas_modelos
    origem = tmp_path / "pacote"
    origem.mkdir()
    copiar_modelos(origem)
    zip_ok = tmp_path / "modelos.zip"
    with zipfile.ZipFile(zip_ok, "w", zipfile.ZIP_STORED) as zf:
        for arq in origem.iterdir():
            zf.write(arq, "modelos_ia/" + arq.name)
    ok, msg = ia_modelos.importar_zip(zip_ok)
    assert ok, msg
    estado = ia_modelos.estado_modelos()
    assert estado["cena"]["ok"] and estado["cena"]["origem"] == "local"
    assert (local / "places365_resnet18.onnx").is_file()

    zip_ruim = tmp_path / "alterado.zip"
    with zipfile.ZipFile(zip_ruim, "w", zipfile.ZIP_STORED) as zf:
        for arq in origem.iterdir():
            conteudo = arq.read_bytes()
            if arq.name == "lut3d_base.npz":
                conteudo = conteudo[:-10] + b"0123456789"
            zf.writestr(arq.name, conteudo)
    ok, msg = ia_modelos.importar_zip(zip_ruim)
    assert not ok and "SHA-256" in msg
    assert ia_modelos.estado_modelos()["edicao"]["ok"]

    zip_fuga = tmp_path / "fuga.zip"
    with zipfile.ZipFile(zip_fuga, "w") as zf:
        zf.writestr("../../fora.txt", "x")
    ok, _msg = ia_modelos.importar_zip(zip_fuga)
    assert not ok and not (tmp_path / "fora.txt").exists()


# ------------------------------------------------------------
# Análise, Auto IA e diagramação
# ------------------------------------------------------------

@precisa_modelos
def test_analise_e_auto_ia_sem_rede(tmp_path, monkeypatch):
    def bloqueado(*_a, **_k):
        raise AssertionError("tentou acessar a rede")

    monkeypatch.setattr(socket.socket, "connect", bloqueado)
    monkeypatch.setattr(socket.socket, "connect_ex", bloqueado)
    monkeypatch.setattr(socket, "create_connection", bloqueado)
    monkeypatch.setattr(urllib.request, "urlopen", bloqueado)
    caminho = str(foto_teste(tmp_path / "externa.jpg"))
    dados = analise.analisar_ia(caminho)
    cena = dados["cena"]
    assert cena["rotulo"] in (ia_cena.COLACAO, ia_cena.BAILE, ia_cena.CULTO, ia_cena.EXTERNA, ia_cena.ESTUDIO, ia_cena.INTERNA)
    assert 0.0 <= cena["confianca"] <= 1.0 and len(cena["top5"]) == 5 and cena["io"] in ("interna", "externa")
    assert len(dados["ia_lut"]) == 3 and dados["ia_lut_versao"]
    assert analise.analisar_ia(caminho) is dados and not analise.falta_ia(dados)
    ia = {"pesos": dados["ia_lut"], "cena": cena, "rosto": dados.get("rosto")}
    for nivel, forca in recorte.FORCA_IA_NIVEL.items():
        ed = recorte.calcular_ajuste_inteligente(caminho, nivel=nivel, base_edicao={"rot": 0}, ia=ia)
        assert ed["ia_lut_forca"] == forca and ed["ia_lut"] == [round(p, 6) for p in dados["ia_lut"]]
        assert ed["rot"] == 0 and all(-100 <= ed[c] <= 100 for c in recorte.CHAVES_AJUSTES)
    pronta = recorte.foto_no_espaco(caminho, ed, None, (300, 200))
    sem = recorte.foto_no_espaco(caminho, recorte.sem_ia(ed), None, (300, 200))
    assert pronta.size == (300, 200) and pronta.tobytes() != sem.tobytes()


def test_1clique_por_regras_nao_muda_sem_ia():
    img = Image.new("RGB", (300, 200), (60, 50, 40))
    for nivel in ("leve", "medio", "pesado"):
        assert recorte.calcular_ajuste_inteligente(img, nivel) == recorte.calcular_ajuste_inteligente(img, nivel, ia=None)
        assert "ia_lut_forca" not in recorte.calcular_ajuste_inteligente(img, nivel, base_edicao={})
    assert len(recorte._chave_edicao_cache({"exposicao": 5}, None, None)) == 9
    com_ia = recorte._chave_edicao_cache({"exposicao": 5, "ia_lut_forca": 70, "ia_lut": [1, 2, 3]}, None, None)
    assert len(com_ia) == 10 and com_ia[-1] == (70.0, (1.0, 2.0, 3.0))
    assert recorte.chave_ia({"ia_lut_forca": 0, "ia_lut": [1, 2, 3]}) is None


def _foto_cena(nome, rotulo, conf=0.8, vetor=None):
    return {"caminho": "C:\\x\\%s.jpg" % nome, "cenario": vetor or [1.0] + [0.0] * 127,
            "cena": {"rotulo": rotulo, "confianca": conf, "roupa_branca": 0.0}}


def test_separar_eventos_usa_cena_so_como_sinal_e_sugestao():
    vetor_b = [0.78, 0.22] + [0.0] * 126
    fotos = ([_foto_cena("a%d" % i, "Baile/Festa") for i in range(4)]
             + [_foto_cena("b%d" % i, "Externa", vetor=vetor_b) for i in range(4)])
    grupos = diagramar.separar_fotos_por_evento_automatico(fotos)
    assert [g["nome"] for g in grupos] == ["Evento 1", "Evento 2"]
    assert grupos[0]["sugestao_evento"]["nome"] == "Baile de Gala"
    assert grupos[1]["sugestao_evento"]["nome"] == "Fotos Externas"
    sem_cena = [{k: v for k, v in f.items() if k != "cena"} for f in fotos]
    grupos_sem = diagramar.separar_fotos_por_evento_automatico(sem_cena)
    assert len(grupos_sem) == 1 and "sugestao_evento" not in grupos_sem[0]
    assert all(diagramar.detectar_nome_evento_foto(f) == "" for f in fotos)


def test_tema_pela_cena_so_quando_ligado(monkeypatch):
    fotos = [_foto_cena("a", "Baile/Festa"), _foto_cena("b", "Baile/Festa")]
    assert diagramar.tema_evento_das_fotos(fotos) is None
    monkeypatch.setattr(diagramar, "USAR_CENA_TEMA", True)
    assert diagramar.tema_evento_das_fotos(fotos) == "baile_dark_luxury"
    fotos_pasta = [dict(f, nome_pasta="Jaleco") for f in fotos]
    assert diagramar.tema_evento_das_fotos(fotos_pasta) == "jaleco_fine_art"
