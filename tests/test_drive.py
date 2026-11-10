# -*- coding: utf-8 -*-
import pytest

import drive


@pytest.mark.parametrize(
    "url, esperado",
    [
        ("https://drive.google.com/drive/folders/1AbCdEfGhIjK?usp=sharing", ("pasta", "1AbCdEfGhIjK", None)),
        ("https://drive.google.com/drive/folders/1AbCdEfGhIjK?resourcekey=0-abc", ("pasta", "1AbCdEfGhIjK", "0-abc")),
        ("https://drive.google.com/file/d/1ZyXwVuTsRqP/view?usp=drive_link", ("arquivo", "1ZyXwVuTsRqP", None)),
        ("https://drive.google.com/open?id=1QwErTyUiOp", ("pasta", "1QwErTyUiOp", None)),
        ("1AbCdEfGhIjKlMnOpQrStUv", ("pasta", "1AbCdEfGhIjKlMnOpQrStUv", None)),
        ("https://exemplo.com/foto.jpg", (None, None, None)),
        ("", (None, None, None)),
    ],
)
def test_extrair_id(url, esperado):
    assert drive.extrair_id(url) == esperado


@pytest.mark.parametrize(
    "nome, mime, esperado",
    [
        ("IMG_001.JPG", "", True),
        ("sem_extensao", "image/heic", True),
        ("video.mp4", "video/mp4", False),
        ("pasta", "application/vnd.google-apps.folder", False),
        ("foto.jpg", "application/vnd.google-apps.shortcut", False),
        ("doc.txt", "text/plain", False),
    ],
)
def test_eh_imagem(nome, mime, esperado):
    assert drive.eh_imagem(nome, mime) is esperado


@pytest.mark.parametrize(
    "cabecalho, esperado",
    [
        (b"\xff\xd8\xff\xe0", True),
        (b"\x89PNG\r\n\x1a\n", True),
        (b"RIFF\x00\x00\x00\x00WEBP", True),
        (b"<!doctype html><html>", False),
        (b'{"error": {"code": 403}}', False),
    ],
)
def test_eh_conteudo_imagem_confere_a_assinatura(cabecalho, esperado):
    assert drive.eh_conteudo_imagem(cabecalho + b"\x00" * 300) is esperado


def test_eh_conteudo_imagem_recusa_arquivo_pequeno():
    assert drive.eh_conteudo_imagem(b"\xff\xd8\xff" + b"\x00" * 10) is False


def test_chave_api_vem_da_variavel_de_ambiente_ou_do_arquivo(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv(drive.VARIAVEL_CHAVE, raising=False)
    assert drive.chave_api() == ""

    drive.gravar_chave_api("  chave-do-arquivo  ")
    assert drive.chave_api() == "chave-do-arquivo"
    assert (tmp_path / "smart_album" / "drive.json").is_file()

    monkeypatch.setenv(drive.VARIAVEL_CHAVE, "chave-da-variavel")
    assert drive.chave_api() == "chave-da-variavel"


def test_sem_chave_nao_chama_a_api(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv(drive.VARIAVEL_CHAVE, raising=False)

    def proibido(*_a, **_k):
        raise AssertionError("não deveria acessar a rede")

    monkeypatch.setattr(drive.urllib.request, "urlopen", proibido)
    assert drive._requisitar_api_v3("files", {"q": "x"}) is None
