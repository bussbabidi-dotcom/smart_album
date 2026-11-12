# -*- coding: utf-8 -*-
"""drive_local: índice do Google Drive para computador montado à mão (sem G: de verdade)."""

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import drive_local  # noqa: E402

PASTA = drive_local.MIME_PASTA
ATALHO = drive_local.MIME_ATALHO
JPG = b"\xff\xd8\xff" + b"0" * 2000


def montar(tmp_path):
    banco = tmp_path / "metadata_sqlite_db"
    c = sqlite3.connect(banco)
    c.executescript(
        """
        CREATE TABLE items (stable_id INTEGER PRIMARY KEY, id TEXT, local_title TEXT, mime_type TEXT,
                            file_size INTEGER, trashed BOOLEAN, is_tombstone BOOLEAN);
        CREATE TABLE stable_parents (item_stable_id INTEGER, parent_stable_id INTEGER);
        CREATE TABLE shortcut_details (shortcut_stable_id INTEGER PRIMARY KEY, target_stable_id INTEGER,
                                       target_mime_type TEXT);
        """
    )
    itens = [
        (1, "raiz", "My Drive", PASTA, None, 0, 0, None),
        (2, "ALUNO", "Fulana", PASTA, None, 0, 0, 1),
        (3, "STUDIO", "STUDIO", PASTA, None, 0, 0, 2),
        (4, "FOTO1", "IMG_1.JPG", "image/jpeg", len(JPG), 0, 0, 3),
        (5, "FOTO2", "IMG_2.JPG", "image/jpeg", len(JPG), 0, 0, 3),
        (6, "SEL", "Seleções", PASTA, None, 0, 0, 2),
        (7, "ALB", "Álbum 20x20", PASTA, None, 0, 0, 6),
        (8, "AT1", "Atalho (1)", ATALHO, 0, 0, 0, 7),
        (9, "AT2", "Atalho (2)", ATALHO, 0, 0, 0, 7),
        (10, "LIXO", "apagada.jpg", "image/jpeg", 10, 1, 0, 7),
        (11, "NUVEM", "SO_NA_NUVEM.JPG", "image/jpeg", 10, 0, 0, None),
        (12, "AT3", "Atalho (3)", ATALHO, 0, 0, 0, 7),
    ]
    for sid, nid, titulo, mime, tam, lixo, tomb, pai in itens:
        c.execute("INSERT INTO items VALUES (?,?,?,?,?,?,?)", (sid, nid, titulo, mime, tam, lixo, tomb))
        if pai:
            c.execute("INSERT INTO stable_parents VALUES (?,?)", (sid, pai))
    c.executemany("INSERT INTO shortcut_details VALUES (?,?,?)",
                  [(8, 4, "image/jpeg"), (9, 5, "image/jpeg"), (12, 11, "image/jpeg")])
    c.commit()
    c.close()

    studio = tmp_path / "G" / "Meu Drive" / "Fulana" / "STUDIO"
    studio.mkdir(parents=True)
    (studio / "IMG_1.JPG").write_bytes(JPG)
    (studio / "IMG_2.JPG").write_bytes(JPG)
    return drive_local.Indice(banco, unidades=[tmp_path / "G"])


def test_atalhos_viram_a_foto_real_com_nome_original(tmp_path):
    indice = montar(tmp_path)
    lst = drive_local.listar(indice, "SEL")
    assert lst.titulo == "Seleções"
    assert sorted(f.item.nome for f in lst.fotos) == ["IMG_1.JPG", "IMG_2.JPG"]
    # o atalho leva a foto para a pasta do evento de origem (dá o grupo e o tema)
    assert all(f.item.subpasta == "Álbum 20x20/STUDIO" for f in lst.fotos)
    assert all(f.caminho.parent.name == "STUDIO" for f in lst.fotos)
    # foto sem caminho no G: fica para a internet; a da lixeira é ignorada
    assert [i.id for i in lst.sem_caminho] == ["NUVEM"]
    indice.fechar()


def test_pasta_fora_do_drive_do_pc_devolve_none(tmp_path):
    indice = montar(tmp_path)
    assert drive_local.listar(indice, "NAO_EXISTE") is None
    indice.fechar()


def test_copiar_lote_confere_tamanho_e_assinatura(tmp_path):
    indice = montar(tmp_path)
    lst = drive_local.listar(indice, "SEL")
    origem = lst.fotos[0].caminho
    falsa = tmp_path / "falsa.jpg"
    falsa.write_bytes(b"<html>erro</html>" * 100)
    res = drive_local.copiar_lote([
        (origem, tmp_path / "saida" / "a.jpg", len(JPG)),
        (origem, tmp_path / "saida" / "b.jpg", len(JPG) + 5),
        (falsa, tmp_path / "saida" / "c.jpg", None),
    ])
    assert res[0].ok and res[0].caminho.read_bytes() == JPG
    assert not res[1].ok and "incompleta" in res[1].motivo
    assert not res[2].ok
    assert not list((tmp_path / "saida").glob("*.parcial"))
    indice.fechar()
