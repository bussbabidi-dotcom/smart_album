# -*- coding: utf-8 -*-
import pytest

import composicao
import diagramar
from produtos import carregar_produto

PALETA = {"cores_destaque_fotos": ["#2f7fc0"], "cor_moldura_clara": "#faf8f5", "cor_moldura_escura": "#52483e",
          "tons_medios": ["#8090a0"]}


def _lamina(tema=None, fundo="#141117", espacos=None):
    lamina = {
        "id": "t",
        "fundo": fundo,
        "_paleta_lamina": dict(PALETA),
        "espacos": espacos or [
            {"x": 0.05, "y": 0.1, "w": 0.30, "h": 0.6, "foto": "maior.jpg", "edicao": {}},
            {"x": 0.37, "y": 0.1, "w": 0.10, "h": 0.3, "foto": "menor1.jpg", "edicao": {}},
            {"x": 0.37, "y": 0.45, "w": 0.10, "h": 0.3, "foto": "menor2.jpg", "edicao": {}},
        ],
        "sobreposicoes": [],
    }
    if tema:
        lamina["tema_evento"] = tema
    return lamina


@pytest.fixture
def produto():
    return carregar_produto("20X20")


def _aplicar(lamina, produto, indice=0, fotos=None, **cfg_extra):
    cfg = diagramar.configuracao(produto, cfg_extra)
    diagramar.aplicar_molduras_automaticas(lamina, fotos, produto, cfg, indice_lamina=indice)
    return {e["foto"]: (e["edicao"] or {}).get("moldura") for e in lamina["espacos"]}


def test_baile_usa_o_conjunto_do_baile(produto):
    molduras = _aplicar(_lamina("baile_dark_luxury"), produto)
    conjunto = composicao.MOLDURAS_EVENTO["baile_dark_luxury"]
    assert molduras["maior.jpg"]["estilo"] == conjunto["destaque"]
    assert molduras["menor1.jpg"]["estilo"] == conjunto["secundaria"]
    assert {m["cor"] for m in molduras.values()} <= {conjunto["cor"], conjunto["cor_secundaria"]}


def test_destaque_e_secundarias_tem_estilos_diferentes(produto):
    for tema in composicao.MOLDURAS_EVENTO:
        molduras = _aplicar(_lamina(tema), produto)
        assert molduras["maior.jpg"]["estilo"] != molduras["menor1.jpg"]["estilo"], tema


def test_foto_com_estrela_vira_destaque(produto):
    fotos = [{"caminho": "menor2.jpg", "destaque": True}]
    molduras = _aplicar(_lamina("baile_dark_luxury"), produto, fotos=fotos)
    assert molduras["menor2.jpg"]["estilo"] == composicao.MOLDURAS_EVENTO["baile_dark_luxury"]["destaque"]


def test_laminas_seguidas_revezam_o_estilo_das_secundarias(produto):
    par = _aplicar(_lamina("colacao_royal"), produto, indice=0)
    impar = _aplicar(_lamina("colacao_royal"), produto, indice=1)
    conjunto = composicao.MOLDURAS_EVENTO["colacao_royal"]
    assert par["menor1.jpg"]["estilo"] == conjunto["secundaria"]
    assert impar["menor1.jpg"]["estilo"] == conjunto["alternativa"]
    assert par["maior.jpg"]["estilo"] == impar["maior.jpg"]["estilo"] == conjunto["destaque"]


def test_foto_sangrada_ou_com_degrade_fica_sem_moldura(produto):
    espacos = [
        {"x": 0.0, "y": 0.0, "w": 0.5, "h": 1.0, "foto": "sangrada.jpg", "edicao": {"moldura": {"estilo": "simples"}}},
        {"x": 0.6, "y": 0.2, "w": 0.2, "h": 0.4, "foto": "degrade.jpg", "edicao": {"degrade": {"ativo": True}}},
        {"x": 0.6, "y": 0.65, "w": 0.2, "h": 0.2, "foto": "normal.jpg", "edicao": {}},
    ]
    molduras = _aplicar(_lamina("baile_dark_luxury", espacos=espacos), produto)
    assert molduras["sangrada.jpg"] is None
    assert molduras["degrade.jpg"] is None
    assert molduras["normal.jpg"] is not None


def test_estilo_fixo_da_configuracao_continua_valendo(produto):
    molduras = _aplicar(_lamina("baile_dark_luxury"), produto, estilo_auto_molduras="halo")
    assert {m["estilo"] for m in molduras.values()} == {"halo"}


@pytest.mark.parametrize("tema, fundo", [("jaleco_fine_art", "#f3f5f9"), ("externas_botanica", "#eee2c8"),
                                         ("baile_dark_luxury", "#141117"), ("colacao_royal", "#0d1f4f")])
def test_cor_do_evento_tem_contraste_com_o_fundo(produto, tema, fundo):
    molduras = _aplicar(_lamina(tema, fundo=fundo), produto)
    for moldura in molduras.values():
        assert composicao.contraste_cores(moldura["cor"], fundo) >= composicao.CONTRASTE_MINIMO_MOLDURA


@pytest.mark.parametrize("cor, fundo", [("#ffffff", "#fafafa"), ("#101010", "#000000"), ("#808080", "#7a7a7a")])
def test_cores_moldura_com_contraste_sempre_chega_no_minimo(cor, fundo):
    final, _sec = composicao.cores_moldura_com_contraste(cor, None, fundo)
    assert composicao.contraste_cores(final, fundo) >= composicao.CONTRASTE_MINIMO_MOLDURA


def test_sem_evento_alterna_passepartout_e_simples(produto):
    molduras = _aplicar(_lamina(None, fundo="#20304a"), produto)
    assert molduras["maior.jpg"]["estilo"] == composicao.MOLDURAS_SEM_EVENTO["destaque"]
    assert molduras["menor1.jpg"]["estilo"] == composicao.MOLDURAS_SEM_EVENTO["secundaria"]


def test_detecta_evento_pelas_fotos_sem_fundo_automatico(produto):
    fotos = [{"caminho": f, "nome_pasta": "Cerimônia do Jaleco"} for f in ("maior.jpg", "menor1.jpg", "menor2.jpg")]
    molduras = _aplicar(_lamina(None, fundo="#f3f5f9"), produto, fotos=fotos)
    assert molduras["maior.jpg"]["estilo"] == composicao.MOLDURAS_EVENTO["jaleco_fine_art"]["destaque"]


def test_sombra_configurada_na_interface_e_respeitada(produto):
    molduras_cfg = {"auto_sombra_molduras": True, "cfg_sombra_molduras": {"opacidade": 12, "desfoque_mm": 1.0}}
    lamina = _lamina("baile_dark_luxury")
    _aplicar(lamina, produto, **molduras_cfg)
    sombra = lamina["espacos"][0]["edicao"]["sombra"]
    assert sombra["opacidade"] == 12 and sombra["ativa"] is True


def test_sombra_do_tema_quando_nao_ha_configuracao(produto):
    lamina = _lamina("baile_dark_luxury")
    _aplicar(lamina, produto, auto_sombra_molduras=True)
    sombra = lamina["espacos"][0]["edicao"]["sombra"]
    assert sombra["opacidade"] == composicao.MAPA_TEMAS["baile_dark_luxury"]["sombra"]["opacidade"]
