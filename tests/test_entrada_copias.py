# -*- coding: utf-8 -*-
"""Fotos repetidas PELO NOME (mesmo nome de arquivo e mesmo tamanho, ex.: STUDIO e "STUDIO (1)") entram uma vez só."""

from PIL import Image

import entrada


def _foto(caminho, cor):
    caminho.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (60, 40), cor).save(caminho, quality=90)
    return caminho


def test_mesmo_nome_em_pastas_repetidas_entra_uma_vez(tmp_path):
    a = _foto(tmp_path / "STUDIO" / "HIL_2744.JPG", "red")
    b = _foto(tmp_path / "STUDIO" / "HIL_2745.JPG", "blue")
    a2 = tmp_path / "STUDIO (1)" / "HIL_2744.JPG"
    a2.parent.mkdir()
    a2.write_bytes(a.read_bytes())

    fotos, _ = entrada.expandir([str(tmp_path)])
    mantidas, copias = entrada.tirar_copias_identicas(fotos)

    assert sorted(p.name for p in mantidas) == ["HIL_2744.JPG", "HIL_2745.JPG"]
    assert b in mantidas
    assert copias == [(a2, a)]


def test_fotos_iguais_com_nomes_diferentes_ficam(tmp_path):
    """Não olha a imagem: mesmo conteúdo com nomes diferentes não é tirado."""
    a = _foto(tmp_path / "Atalho (1).jpg", "red")
    b = tmp_path / "Atalho (10).jpg"
    b.write_bytes(a.read_bytes())
    mantidas, copias = entrada.tirar_copias_identicas([a, b])
    assert mantidas == [a, b] and copias == []


def test_mesmo_nome_mas_tamanho_diferente_fica(tmp_path):
    """Mesmo nome com outro tamanho é outra foto (outra câmera / foto editada): fica."""
    a = _foto(tmp_path / "STUDIO" / "IMG_0001.JPG", "red")
    b = _foto(tmp_path / "COLAÇÃO" / "IMG_0001.JPG", "white")
    b.write_bytes(b.read_bytes() + b"\x00" * 50)
    mantidas, copias = entrada.tirar_copias_identicas([a, b])
    assert mantidas == [a, b] and copias == []


def test_mesmo_nome_de_foto_que_ja_esta_no_projeto(tmp_path):
    velha = _foto(tmp_path / "A" / "DSC08766.JPG", "green")
    nova = tmp_path / "B" / "DSC08766.JPG"
    nova.parent.mkdir()
    nova.write_bytes(velha.read_bytes())
    outra = _foto(tmp_path / "B" / "DSC08767.JPG", "yellow")
    mantidas, copias = entrada.tirar_copias_identicas([nova, outra], ja_no_projeto=[str(velha)])
    assert mantidas == [outra]
    assert copias == [(nova, str(velha))]
