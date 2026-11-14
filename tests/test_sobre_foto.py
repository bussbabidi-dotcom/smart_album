# -*- coding: utf-8 -*-
"""Foto sobre foto (base + fotos menores por cima), rostos desfocados e destaque do rosto pequeno e nítido."""

import numpy as np
from PIL import Image, ImageFilter

import analise
import diagramar
from produtos import area_pagina_mm, carregar_produto, lamina_mm


def _rosto(x0, y0, x1, y1, h=0.16, nitidez=120.0):
    cx, cy = (x0 + x1) / 2.0, y0 + (y1 - y0) * 0.4
    return {"cx": cx, "cy": cy, "h": h, "qtd": 1, "x0": x0, "y0": y0, "x1": x1, "y1": y1,
            "rostos": [{"cx": cx, "cy": cy, "w": h * 0.7, "h": h, "x0": x0, "y0": y0, "x1": x1, "y1": y1, "nitidez": nitidez}],
            "nitidez": nitidez, "nitidez_maior": nitidez, "nitidez_nativa": nitidez * 3}


def _foto(nome, proporcao=1.5, rosto=None, destaque=False):
    return {"caminho": "D:/t/%s.jpg" % nome, "proporcao": proporcao, "destaque": destaque, "rosto": rosto,
            "pasta": "D:/t", "nome_pasta": "t", "grupo_ordem": 1}


def _cfg(produto, modo="sugerir"):
    cfg = diagramar.configuracao(produto)
    cfg["foto_sobre_foto"] = modo
    return cfg


PESSOA_A_DIREITA = dict(x0=0.56, y0=0.10, x1=0.94, y1=0.62)
PESSOA_NO_MEIO = dict(x0=0.25, y0=0.08, x1=0.75, y1=0.80)


def _grupo_com_base():
    return [
        _foto("base", 1.5, _rosto(**PESSOA_A_DIREITA)),
        _foto("v1", 0.667, _rosto(**PESSOA_NO_MEIO)),
        _foto("v2", 0.667, _rosto(**PESSOA_NO_MEIO)),
        _foto("h1", 1.5, _rosto(**PESSOA_NO_MEIO)),
    ]


def test_padrao_e_so_sugerir_e_o_antigo_nao_existe_mais():
    cfg = diagramar.configuracao(carregar_produto("30X20"))
    assert cfg["foto_sobre_foto"] == "sugerir"
    assert cfg["destacar_rosto_pequeno"] is True
    assert "preencher_espacos_extras" not in cfg


def test_zona_livre_fica_do_lado_sem_pessoas():
    produto = carregar_produto("30X20")
    area = area_pagina_mm(produto, 0)
    motivo, lado, zona = diagramar.zona_livre_da_base(_foto("b", 1.5, _rosto(**PESSOA_A_DIREITA)), area)
    assert (motivo, lado) == ("espaco", "esq")
    assert zona[0] + zona[2] <= area[0] + area[2] * 0.58  # termina antes da pessoa


def test_sem_espaco_livre_nao_serve_de_base():
    produto = carregar_produto("30X20")
    area = area_pagina_mm(produto, 0)
    assert diagramar.zona_livre_da_base(_foto("b", 1.5, _rosto(**PESSOA_NO_MEIO)), area) is None
    assert diagramar.zona_livre_da_base(_foto("sem_rosto", 1.5, None), area) is None


def test_espaco_embaixo_das_pessoas_nunca_e_zona_livre():
    produto = carregar_produto("30X20")
    area = area_pagina_mm(produto, 0)
    no_topo = _foto("b", 1.5, _rosto(x0=0.30, y0=0.02, x1=0.70, y1=0.35))  # 65% livre embaixo: são os corpos
    assert diagramar.zona_livre_da_base(no_topo, area) is None


def test_rostos_muito_desfocados_viram_fundo():
    produto = carregar_produto("30X20")
    area = area_pagina_mm(produto, 0)
    borrada = _foto("b", 1.5, _rosto(nitidez=3.0, **PESSOA_NO_MEIO))
    assert diagramar.rostos_muito_desfocados(borrada)
    motivo, lado, _zona = diagramar.zona_livre_da_base(borrada, area)
    assert (motivo, lado) == ("desfocada", "centro")
    assert not diagramar.rostos_muito_desfocados(_foto("n", 1.5, _rosto(**PESSOA_NO_MEIO)))
    sem_medida = _rosto(**PESSOA_NO_MEIO)
    sem_medida.pop("nitidez")
    assert not diagramar.rostos_muito_desfocados(_foto("antiga", 1.5, sem_medida))  # análise antiga: não inventa


def test_sugerir_nunca_e_a_escolha_automatica_mas_aparece_nas_sugestoes():
    produto = carregar_produto("30X20")
    fotos = _grupo_com_base()
    cfg = _cfg(produto, "sugerir")
    assert not diagramar.eh_layout_sobre_foto(diagramar.opcoes_lamina(fotos, produto, cfg, maximo=1)[0])
    sugestoes = diagramar.opcoes_lamina(fotos, produto, cfg, maximo=18)
    sobre = [o for o in sugestoes if diagramar.eh_layout_sobre_foto(o)]
    assert sobre, "a foto sobre foto tem de aparecer nas sugestões"
    assert not diagramar.eh_layout_sobre_foto(sugestoes[0])


def test_nunca_desliga_e_30x22_nao_usa():
    produto = carregar_produto("30X20")
    fotos = _grupo_com_base()
    assert not any(diagramar.eh_layout_sobre_foto(o)
                   for o in diagramar.opcoes_lamina(fotos, produto, _cfg(produto, "nunca"), maximo=18))
    p22 = carregar_produto("30X22")
    assert diagramar._layouts_sobre_foto(fotos[:2], p22, _cfg(p22, "usar")) == []


def test_fotos_de_cima_nao_tapam_as_pessoas_e_tem_moldura_e_sombra():
    produto = carregar_produto("30X20")
    fotos = _grupo_com_base()
    opcoes = diagramar._layouts_sobre_foto(fotos, produto, _cfg(produto, "usar"))
    assert opcoes
    lw, lh = lamina_mm(produto)
    for op in opcoes:
        espacos = op["espacos"]
        assert sorted(e["foto"] for e in espacos) == sorted(f["caminho"] for f in fotos)  # cada foto uma vez
        base = espacos[0]
        de_cima = [e for e in espacos if e.get("sobre_foto")]
        assert 1 <= len(de_cima) <= diagramar.MAX_SOBRE_FOTO
        assert espacos.index(de_cima[0]) > 0 and all(not e.get("sobre_foto") for e in espacos[:len(espacos) - len(de_cima)])
        if base["foto"].endswith("base.jpg"):
            # a pessoa da base começa em 56% da foto: as de cima ficam antes disso (com folga do corte)
            limite = base["x"] + base["w"] * 0.60
            for e in de_cima:
                assert e["x"] >= base["x"] - 1e-6 and e["x"] + e["w"] <= limite
                assert e["y"] >= base["y"] - 1e-6 and e["y"] + e["h"] <= base["y"] + base["h"] + 1e-6
        for e in de_cima:
            assert min(e["w"] * lw, e["h"] * lh) >= diagramar.LADO_MINIMO_SOBRE_MM - 0.01
            assert e["edicao"]["moldura"]["estilo"] != "nenhuma"
            assert e["edicao"]["sombra"]["ativa"]
            assert diagramar.eh_automatico(e["edicao"], "moldura")


def test_a_outra_pagina_nunca_fica_vazia():
    produto = carregar_produto("30X20")
    for op in diagramar._layouts_sobre_foto(_grupo_com_base(), produto, _cfg(produto, "usar")):
        base = op["espacos"][0]
        pagina_base = 0 if base["x"] + base["w"] / 2.0 < 0.5 else 1
        outras = [e for e in op["espacos"][1:] if not e.get("sobre_foto")]
        assert outras
        for e in outras:
            assert (0 if e["x"] + e["w"] / 2.0 < 0.5 else 1) != pagina_base


def test_usar_nunca_faz_duas_laminas_seguidas():
    produto = carregar_produto("30X20")
    fotos = []
    for k in range(16):
        f = _foto("f%02d" % k, 1.5 if k % 2 == 0 else 0.667,
                  _rosto(**(PESSOA_A_DIREITA if k % 2 == 0 else PESSOA_NO_MEIO)))
        f["data"] = 1000.0 + k
        fotos.append(f)
    cfg = _cfg(produto, "usar")
    laminas, _sobras = diagramar.diagramar_album(fotos, produto, cfg)
    tipos = [diagramar.eh_layout_sobre_foto(l) for l in laminas]
    assert not any(a and b for a, b in zip(tipos, tipos[1:]))


def test_lamina_sobre_foto_nao_e_reorganizada_e_garante_o_acabamento():
    produto = carregar_produto("30X20")
    fotos = _grupo_com_base()
    cfg = _cfg(produto, "usar")
    op = diagramar._layouts_sobre_foto(fotos, produto, cfg)[0]
    lamina = diagramar.lamina_vazia(cfg)
    lamina["espacos"] = op["espacos"]
    lamina["tipo_layout"] = op["tipo_layout"]
    antes = [(e["x"], e["y"], e["w"], e["h"]) for e in lamina["espacos"]]
    assert diagramar.ajustar_espacos_lamina(lamina, {f["caminho"]: f["proporcao"] for f in fotos}, produto, cfg) is False
    assert antes == [(e["x"], e["y"], e["w"], e["h"]) for e in lamina["espacos"]]
    for e in lamina["espacos"]:
        if e.get("sobre_foto"):
            e["edicao"] = {}
    assert diagramar._garantir_acabamento_sobre_foto(lamina)
    assert all(e["edicao"].get("moldura") and e["edicao"].get("sombra") for e in lamina["espacos"] if e.get("sobre_foto"))


def test_rosto_pequeno_e_nitido_ganha_o_destaque_sem_estrela():
    produto = carregar_produto("30X20")
    cfg = _cfg(produto)
    longe = _foto("longe", 1.5, _rosto(h=0.05, nitidez=90.0, **PESSOA_NO_MEIO))
    perto = _foto("perto", 1.5, _rosto(h=0.20, **PESSOA_NO_MEIO))
    assert diagramar.rosto_pequeno_nitido(longe) and not diagramar.rosto_pequeno_nitido(perto)
    borrado_longe = _foto("bl", 1.5, _rosto(h=0.05, nitidez=5.0, **PESSOA_NO_MEIO))
    assert not diagramar.rosto_pequeno_nitido(borrado_longe)
    fotos = [perto, longe, _foto("outra", 0.667, _rosto(**PESSOA_NO_MEIO))]
    marcadas = diagramar._com_destaque_automatico(fotos, cfg)
    assert [bool(f.get("destaque")) for f in marcadas] == [False, True, False]
    assert not longe.get("destaque")  # a foto do projeto não é alterada


def test_estrela_a_mao_manda_e_a_opcao_desliga():
    produto = carregar_produto("30X20")
    cfg = _cfg(produto)
    longe = _foto("longe", 1.5, _rosto(h=0.05, nitidez=90.0, **PESSOA_NO_MEIO))
    com_estrela = _foto("estrela", 1.5, _rosto(**PESSOA_NO_MEIO), destaque=True)
    fotos = [com_estrela, longe]
    assert diagramar._com_destaque_automatico(fotos, cfg) is fotos
    sem = [_foto("a", 1.5, _rosto(**PESSOA_NO_MEIO)), longe]
    assert diagramar._com_destaque_automatico(sem, dict(cfg, destacar_rosto_pequeno=False)) is sem
    todas_longe = [longe, _foto("l2", 1.5, _rosto(h=0.04, nitidez=95.0, **PESSOA_NO_MEIO))]
    assert diagramar._com_destaque_automatico(todas_longe, cfg) is todas_longe  # todas iguais: nada a preferir


def test_nitidez_dos_rostos_separa_nitido_de_desfocado():
    rng = np.random.default_rng(7)
    nitida = Image.fromarray(rng.integers(0, 255, (400, 600), dtype=np.uint8)).convert("RGB")
    borrada = nitida.filter(ImageFilter.GaussianBlur(9))
    rosto = {"rostos": [{"cx": 0.5, "cy": 0.4, "w": 0.2, "h": 0.3}]}
    r_nitido = analise.nitidez_dos_rostos(nitida, {"rostos": [dict(rosto["rostos"][0])]})
    r_borrado = analise.nitidez_dos_rostos(borrada, {"rostos": [dict(rosto["rostos"][0])]})
    assert r_nitido["nitidez"] >= diagramar.ROSTO_NITIDO
    assert r_borrado["nitidez"] < diagramar.ROSTO_DESFOCADO
    assert r_nitido["rostos"][0]["nitidez"] == r_nitido["nitidez"] == r_nitido["nitidez_maior"]
    assert analise.nitidez_dos_rostos(nitida, None) is None
