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


def test_regras_de_16_11_so_no_30x22():
    cfg = diagramar.configuracao(carregar_produto("30X22"))
    assert cfg["molduras_proibidas"] == ["pontilhada"]
    assert cfg["excluir_pose"] and cfg["primeira_foto_sozinha"] and cfg["laminas_par"]
    for nome in ("20X20", "30X20"):
        outro = diagramar.configuracao(carregar_produto(nome))
        assert not (outro["molduras_proibidas"] or outro["excluir_pose"] or outro["primeira_foto_sozinha"]
                    or outro["laminas_par"])


def test_30x22_nunca_sorteia_a_moldura_pontilhada(monkeypatch):
    monkeypatch.setattr(analise, "cor_do_canudo", lambda caminhos: None)
    cfg = diagramar.configuracao(carregar_produto("30X22"))
    for k in range(200):
        fotos = [{"caminho": "D:/t/aluno%d/IMG_%04d.JPG" % (k, i)} for i in range(3 + k % 40)]
        assert "pontilhada" not in diagramar.estilo_do_album(fotos, cfg)["molduras"].values()


def _com_cenario(fotos, cenarios):
    for f, c in zip(fotos, cenarios):
        f["cenario_subgrupo"] = c
    return fotos


def test_alternancia_nao_mistura_cenarios():
    # cenário 1: h v v h v v ; cenário 2: v h v h v ; antes as horizontais eram espalhadas pelo bloco inteiro
    tipos = "hvvhvv" + "vhvhv"
    fotos = [{"caminho": "D:/t/f%02d.jpg" % i, "proporcao": 1.5 if t == "h" else 0.667, "data": 1000.0 + i}
             for i, t in enumerate(tipos)]
    _com_cenario(fotos, [1] * 6 + [2] * 5)
    grupos = diagramar._grupos_alternando([fotos], 9)
    cenarios = [{f["cenario_subgrupo"] for f in g} for g in grupos]
    assert all(len(c) == 1 for c in cenarios)
    sequencia = [c.pop() for c in cenarios]
    assert sequencia == sorted(sequencia)  # todo o cenário 1 antes do 2, sem voltar
    assert sorted(f["caminho"] for g in grupos for f in g) == sorted(f["caminho"] for f in fotos)


@pytest.mark.parametrize("n_h, n_v", [(45, 23), (5, 4), (60, 41), (3, 0), (31, 37)])
def test_30x22_sempre_fecha_em_numero_par(n_h, n_v):
    produto = carregar_produto("30X22")
    fotos = _fotos(n_h, n_v)
    laminas, sobras = diagramar.diagramar_album(fotos, produto, _cfg(produto))
    assert not sobras and len(laminas) % 2 == 0
    usadas = [e["foto"] for l in laminas for e in l["espacos"] if e.get("foto")]
    assert sorted(usadas) == sorted(f["caminho"] for f in fotos)
    assert all(len([e for e in l["espacos"] if e.get("foto")]) <= 2 for l in laminas)


def test_par_separa_uma_dupla_ou_junta_duas_verticais_sozinhas():
    v = lambda i, c=1: {"caminho": "v%d" % i, "proporcao": 0.667, "cenario_subgrupo": c}
    h = lambda i, c=1: {"caminho": "h%d" % i, "proporcao": 1.5, "cenario_subgrupo": c}
    grupos, ok = diagramar._deixar_par([[h(1)], [v(1), v(2)], [h(2)]])
    assert ok and [len(g) for g in grupos] == [1, 1, 1, 1]
    grupos, ok = diagramar._deixar_par([[h(1)], [v(1)], [v(2)]])
    assert ok and [len(g) for g in grupos] == [1, 2]
    grupos, ok = diagramar._deixar_par([[h(1)], [v(1), ], [v(2, c=2)]])  # verticais de cenários diferentes: não junta
    assert not ok and len(grupos) == 3


def _rosto(*alturas):
    return {"qtd": len(alturas), "rostos": [{"h": a, "cx": 0.5, "cy": 0.3} for a in alturas]}


def test_pessoa_sozinha_ignora_plateia_pequena():
    assert diagramar.pessoa_sozinha({"rosto": _rosto(0.12)})
    assert diagramar.pessoa_sozinha({"rosto": _rosto(0.12, 0.03, 0.02)})   # plateia lá atrás
    assert not diagramar.pessoa_sozinha({"rosto": _rosto(0.12, 0.10)})     # duas pessoas
    assert not diagramar.pessoa_sozinha({"rosto": None})
    nitida = lambda h, n: {"h": h, "cx": 0.5, "cy": 0.3, "nitidez": n}
    # alguém desfocado passando atrás (Eliane, 16/11): continua sozinha
    assert diagramar.pessoa_sozinha({"rosto": {"qtd": 2, "rostos": [nitida(0.13, 457.0), nitida(0.082, 7.5)]}})
    # duas pessoas nítidas (Brenda): não
    assert not diagramar.pessoa_sozinha({"rosto": {"qtd": 2, "rostos": [nitida(0.145, 411.0), nitida(0.143, 544.0)]}})


def test_primeira_lamina_e_a_primeira_foto_vertical_da_pessoa_sozinha():
    produto = carregar_produto("30X22")
    fotos = _fotos(10, 10)
    for f in fotos:
        f["rosto"] = _rosto(0.12, 0.11)  # todas com duas pessoas
    verticais = [f for f in fotos if diagramar._eh_vertical(f)]
    horizontais = [f for f in fotos if not diagramar._eh_vertical(f)]
    horizontais[0]["rosto"] = _rosto(0.12)          # sozinha, mas horizontal: não serve
    verticais[3]["rosto"] = _rosto(0.12, 0.02)      # a 1ª vertical sozinha
    verticais[6]["rosto"] = _rosto(0.12)
    cfg = _cfg(produto)
    laminas, _ = diagramar.diagramar_album(fotos, produto, cfg)
    primeira = [e for e in laminas[0]["espacos"] if e.get("foto")]
    assert [e["foto"] for e in primeira] == [verticais[3]["caminho"]] and laminas[0].get("primeira_foto")
    assert not any(l.get("primeira_foto") for l in laminas[1:]) and len(laminas) % 2 == 0
    # sem foto sozinha: avisa e segue
    for f in fotos:
        f["rosto"] = _rosto(0.12, 0.11)
    avisos = []
    laminas, _ = diagramar.diagramar_album(fotos, produto, cfg, avisos=avisos)
    assert not laminas[0].get("primeira_foto") and any("pessoa sozinha" in a for a in avisos)


def _pose(x=0.5, h=0.1, inclinacao=0.0, corpo=(1.0, 0.0), foco=100.0, vertical=True):
    import numpy as np
    v = np.array(corpo, dtype=float)
    return {"vertical": vertical, "foco": foco, "corpo": v / np.linalg.norm(v),
            "rostos": [{"x": x, "y": 0.3, "h": h, "inclinacao": inclinacao, "giro": 0.0, "queixo": 0.5, "foco": foco}]}


def test_semelhanca_de_pose():
    base = _pose()
    assert analise.semelhanca_pose(base, _pose(x=0.53)) > 0
    assert analise.semelhanca_pose(base, _pose(x=0.60)) == 0           # mudou de lugar
    assert analise.semelhanca_pose(base, _pose(h=0.06)) == 0           # outro zoom
    assert analise.semelhanca_pose(base, _pose(corpo=(1.0, 1.0))) == 0  # braços/corpo diferentes (0.71)
    assert analise.semelhanca_pose(base, _pose(vertical=False)) == 0
    assert analise.semelhanca_pose(base, _pose(inclinacao=20)) == 0


def test_mesma_pose_sai_e_fica_a_mais_nitida(tmp_path, monkeypatch):
    fotos = [tmp_path / n for n in ("A001.JPG", "A002.JPG", "A003.JPG", "A004.JPG")]
    for f in fotos:
        f.write_bytes(b"x")
    hashes = {fotos[0]: 0, fotos[1]: 0b111111111111, fotos[2]: 0b1111 << 30, fotos[3]: 0b1111111 << 40}
    datas = {fotos[0]: 100.0, fotos[1]: 110.0, fotos[2]: 120.0, fotos[3]: 5000.0}
    poses = {fotos[0]: _pose(foco=80), fotos[1]: _pose(x=0.52, foco=200),   # mesma pose, a 2ª mais nítida
             fotos[2]: _pose(corpo=(0.0, 1.0)),                             # pose diferente
             fotos[3]: _pose(foco=500)}                                     # mesma pose, mas 1h depois
    monkeypatch.setattr(analise, "hash_perceptual", lambda c: hashes[c])
    monkeypatch.setattr(analise, "_data_foto", lambda c: datas[c])
    monkeypatch.setattr(analise, "assinatura_pose", lambda c: poses[c])
    ficam, saem = analise.separar_repetidas(fotos)
    assert ficam == fotos and not saem  # sem a opção, nada muda
    ficam, saem = analise.separar_repetidas(fotos, pose=True)
    assert ficam == [fotos[1], fotos[2], fotos[3]]
    assert saem == [(fotos[0], fotos[1], "pose")]


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
