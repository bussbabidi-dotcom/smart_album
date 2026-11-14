# -*- coding: utf-8 -*-
"""Regras só do 30X22: alternância horizontal/vertical, no máx. 2 fotos do mesmo tamanho, o máximo de lâminas e rajadas."""

import random

import pytest

import analise
import diagramar
from produtos import carregar_produto


def _fotos(n_h, n_v, semente=3):
    sorteio = random.Random(semente)
    tipos = ["h"] * n_h + ["v"] * n_v
    sorteio.shuffle(tipos)
    return [{
        "caminho": "D:/t/COLAÇÃO/f%03d.jpg" % i,
        "proporcao": 1.5 if t == "h" else 0.667,
        "pasta": "D:/t/COLAÇÃO",
        "nome_pasta": "COLAÇÃO",
        "data": 1000.0 + i,
    } for i, t in enumerate(tipos)]


def _cfg(produto, **extra):
    base = {"auto_fundos": False, "auto_degrade": False, "auto_molduras": False}
    base.update(extra)
    return diagramar.configuracao(produto, base)


def test_so_o_30x22_liga_as_regras_novas():
    for nome in ("20X20", "30X20", "24X34"):
        cfg = diagramar.configuracao(carregar_produto(nome))
        assert not cfg["alternar_orientacao"] and not cfg["excluir_rajadas"]
    cfg = diagramar.configuracao(carregar_produto("30X22"))
    assert cfg["alternar_orientacao"] and cfg["excluir_rajadas"] and cfg["excluir_repetidas"]


@pytest.mark.parametrize("n_h, n_v, esperado", [
    (10, 4, 14),     # poucas fotos: 1 por lâmina
    (20, 40, 50),    # 20 horizontais + 20 duplas = 40; com poucas fotos separa duplas até as 50 lâminas
    (30, 37, 50),    # 30 horizontais + 19 duplas = 49; completa as 50 do produto
    (45, 22, 56),    # sem limite: cada horizontal sozinha + 11 duplas
    (0, 80, 50),
    (100, 58, 129),
])
def test_maximo_de_laminas_sem_limite(n_h, n_v, esperado):
    assert diagramar.laminas_alternando(_fotos(n_h, n_v), carregar_produto("30X22")) == esperado


@pytest.mark.parametrize("n_h, n_v", [(30, 37), (45, 22), (10, 60), (60, 40), (5, 3)])
def test_no_maximo_2_fotos_nunca_horizontal_com_vertical(n_h, n_v):
    produto = carregar_produto("30X22")
    fotos = _fotos(n_h, n_v)
    plano = diagramar.planejar_distribuicao(fotos, produto, _cfg(produto))
    grupos = plano["grupos"]
    assert not plano["sobras"]
    assert sorted(f["caminho"] for g in grupos for f in g) == sorted(f["caminho"] for f in fotos)
    assert len(grupos) == diagramar.laminas_alternando(fotos, produto)
    for g in grupos:
        assert 1 <= len(g) <= 2
        if len(g) == 2:  # dupla só de verticais: horizontal fica sempre sozinha
            assert diagramar._eh_vertical(g[0]) and diagramar._eh_vertical(g[1])


def test_alterna_horizontal_e_vertical_quando_da():
    produto = carregar_produto("30X22")
    grupos = diagramar.planejar_distribuicao(_fotos(20, 40), produto, _cfg(produto))["grupos"]
    tipos = ["v" if diagramar._eh_vertical(g[0]) else "h" for g in grupos]
    trocas = sum(1 for a, b in zip(tipos, tipos[1:]) if a != b)
    assert len(grupos) == 50 and tipos.count("h") == 20  # 50 = laminas_max do produto
    assert trocas >= 39  # h, v, h, v... enquanto houver das duas (20 h contra 30 v)


def test_duas_fotos_ficam_do_mesmo_tamanho():
    produto = carregar_produto("30X22")
    laminas, sobras = diagramar.diagramar_album(_fotos(30, 37), produto, _cfg(produto))
    assert not sobras
    for lamina in laminas:
        espacos = [e for e in lamina["espacos"] if e.get("foto")]
        assert len(espacos) <= 2
        assert diagramar._tamanhos_parecidos(espacos)


def test_tema_e_molduras_do_album_variam_e_nunca_e_externa(monkeypatch):
    monkeypatch.setattr(analise, "cor_do_canudo", lambda caminhos: "#027e67")
    temas, trios = set(), set()
    for k in range(60):
        fotos = [{"caminho": "D:/t/aluno%d/IMG_%04d.JPG" % (k, i)} for i in range(5 + k)]
        estilo = diagramar.estilo_do_album(fotos, {})
        assert estilo["tema"] not in ("externas_botanica", "ensaio_linho")
        assert len(set(estilo["molduras"].values())) == 3 and estilo["cor_canudo"] == "#027e67"
        assert estilo == diagramar.estilo_do_album(list(reversed(fotos)), {})  # o mesmo álbum sempre igual
        temas.add(estilo["tema"])
        trios.add(tuple(estilo["molduras"].values()))
    assert len(temas) == 6 and len(trios) > 30


def test_lamina_guarda_tema_molduras_e_cor_do_canudo(monkeypatch):
    monkeypatch.setattr(analise, "cor_do_canudo", lambda caminhos: "#cac042")
    produto = carregar_produto("30X22")
    cfg = diagramar.configuracao(produto, {"auto_fundos": False, "auto_degrade": False, "auto_molduras": False})
    laminas, _ = diagramar.diagramar_album(_fotos(6, 6), produto, cfg)
    assert {l["tema_album"] for l in laminas if l["espacos"]} and all(l.get("cor_canudo") == "#cac042" for l in laminas if l["espacos"])
    outro = carregar_produto("20X20")
    laminas, _ = diagramar.diagramar_album(_fotos(6, 6), outro, diagramar.configuracao(outro, {"auto_fundos": False}))
    assert not any(l.get("tema_album") or l.get("cor_canudo") for l in laminas)


def test_outros_tamanhos_continuam_iguais():
    produto = carregar_produto("20X20")
    cfg = _cfg(produto)
    plano = diagramar.planejar_distribuicao(_fotos(20, 20), produto, cfg)
    assert plano["maximo"] > 2 and len(plano["grupos"]) == produto["laminas_min"]


def test_rajada_so_com_a_opcao(tmp_path, monkeypatch):
    fotos = [tmp_path / n for n in ("XH1A0001.JPG", "XH1A0002.JPG", "XH1A0003.JPG", "_MG_0004.JPG")]
    for f in fotos:
        f.write_bytes(b"x")
    # distâncias para a 1ª: 6, 2 e 4 (a última é de outra câmera e longe da 2ª também)
    hashes = {fotos[0]: 0, fotos[1]: 0b111111, fotos[2]: 0b11, fotos[3]: 0b1111 << 20}
    datas = {fotos[0]: 100.0, fotos[1]: 101.0, fotos[2]: 500.0, fotos[3]: 100.5}
    monkeypatch.setattr(analise, "hash_perceptual", lambda c: hashes[c])
    monkeypatch.setattr(analise, "_data_foto", lambda c: datas[c])

    ficam, saem = analise.separar_repetidas(fotos)
    assert ficam == [fotos[0], fotos[1], fotos[3]] and [s[0] for s in saem] == [fotos[2]]

    ficam, saem = analise.separar_repetidas(fotos, rajadas=True)
    # 0002: mesma câmera, 1 s, dist 6 -> rajada; 0003: cópia (dist 2); _MG_: outra câmera -> fica
    assert ficam == [fotos[0], fotos[3]]
    assert {(s[0], s[2]) for s in saem} == {(fotos[1], "rajada"), (fotos[2], "cópia")}
