# -*- coding: utf-8 -*-
"""
Projeto do álbum (.album.json): produto, fotos, lâminas, capa e as edições.

Lâmina: {"id", "fundo", "fundo_imagem", "espacos": [...], "sobreposicoes": [...], "modelo"}
Espaço: {"x", "y", "w", "h" (fração da lâmina), "foto", "edicao"}
Imagem de fundo (lâmina e capa, opcional): {"caminho", "modo", "opacidade", "desfoque", "pb"}
"""

import copy
import json
from collections import Counter
from pathlib import Path

import capa as capa_mod
from comum import gravar_json, ler_json, normalizar_caminho

VERSAO = 1
EXTENSAO = ".album.json"


def novo(produto, pasta_fotos=None, fotos=()):
    projeto = {
        "versao": VERSAO,
        "nome": Path(pasta_fotos).name if pasta_fotos else "Novo álbum",
        "produto": produto,
        "pasta_fotos": str(pasta_fotos) if pasta_fotos else "",
        "pasta_saida": "",
        "fotos": [],
        "config": {},
        "laminas": [],
        "capa": capa_mod.capa_padrao(),
        "photoshop": {"conjunto": "", "acao": ""},
        "lightroom": {"preset_uuid": "", "preset_nome": ""},
    }
    adicionar_fotos(projeto, fotos)
    return projeto


def adicionar_fotos(projeto, caminhos):
    """Acrescenta no fim, sem repetir. Devolve as que entraram."""
    existentes = {normalizar_caminho(f["caminho"]) for f in projeto["fotos"]}
    novas = []
    for caminho in caminhos:
        chave = normalizar_caminho(caminho)
        if chave in existentes:
            continue
        existentes.add(chave)
        projeto["fotos"].append({"caminho": str(Path(caminho)), "destaque": False})
        novas.append(str(Path(caminho)))
    return novas


def remover_fotos(projeto, caminhos):
    tirar = {normalizar_caminho(c) for c in caminhos}
    projeto["fotos"] = [f for f in projeto["fotos"] if normalizar_caminho(f["caminho"]) not in tirar]
    for espaco in todos_espacos(projeto):
        if espaco.get("foto") and normalizar_caminho(espaco["foto"]) in tirar:
            espaco["foto"] = None
            espaco["edicao"] = {}
    for lamina in projeto.get("laminas", []):
        fundo = lamina.get("fundo_imagem") or {}
        if fundo.get("caminho") and normalizar_caminho(fundo["caminho"]) in tirar:
            lamina.pop("fundo_imagem", None)
    capa = projeto.get("capa") or {}
    if capa.get("foto") and normalizar_caminho(capa["foto"]) in tirar:
        capa["foto"] = None
    fundo_capa = capa.get("fundo_imagem") or {}
    if fundo_capa.get("caminho") and normalizar_caminho(fundo_capa["caminho"]) in tirar:
        capa.pop("fundo_imagem", None)


def todos_espacos(projeto):
    for lamina in projeto.get("laminas", []):
        for espaco in lamina.get("espacos", []):
            yield espaco


def usos(projeto):
    """Quantas vezes cada foto aparece nas lâminas (a capa é feita no programa de capas)."""
    contagem = Counter()
    for espaco in todos_espacos(projeto):
        if espaco.get("foto"):
            contagem[normalizar_caminho(espaco["foto"])] += 1
    return contagem


def registro_foto(projeto, caminho):
    chave = normalizar_caminho(caminho)
    for foto in projeto["fotos"]:
        if normalizar_caminho(foto["caminho"]) == chave:
            return foto
    return None


def ordenar_fotos(projeto, info, por="nome"):
    from comum import chave_natural
    if por == "data":
        def chave(f):
            dados = info.get(normalizar_caminho(f["caminho"])) or {}
            return (dados.get("data") or 0, chave_natural(f["caminho"]))
    else:
        def chave(f):
            return [chave_natural(p) for p in Path(f["caminho"]).parts]
    projeto["fotos"].sort(key=chave)


def fotos_para_diagramar(projeto, info, somente_nao_usadas=False):
    """Fotos analisadas, na ordem do projeto, no formato do diagramar."""
    ja_usadas = usos(projeto) if somente_nao_usadas else Counter()
    saida = []
    for foto in projeto["fotos"]:
        chave = normalizar_caminho(foto["caminho"])
        dados = info.get(chave)
        if not dados or ja_usadas.get(chave):
            continue
        saida.append({
            "caminho": foto["caminho"],
            "proporcao": dados_proporcao(dados),
            "data": dados.get("data"),
            "destaque": bool(foto.get("destaque")),
            "cenario": dados.get("cenario"),
        })
    return saida


def dados_proporcao(dados):
    return float(dados.get("proporcao") or (dados["w"] / float(dados["h"])))


def fundos_imagem(projeto):
    """As imagens de fundo das lâminas."""
    for dono in projeto.get("laminas", []):
        dados = dono.get("fundo_imagem")
        if dados and dados.get("caminho"):
            yield dados


def trocar_caminho(projeto, antigo, novo):
    """Troca a foto em todo o projeto (lista, espaços, capa e imagens de fundo)."""
    chave = normalizar_caminho(antigo)
    novo = str(novo)
    for foto in projeto["fotos"]:
        if normalizar_caminho(foto["caminho"]) == chave:
            foto["caminho"] = novo
    for espaco in todos_espacos(projeto):
        if espaco.get("foto") and normalizar_caminho(espaco["foto"]) == chave:
            espaco["foto"] = novo
    capa = projeto.get("capa") or {}
    if capa.get("foto") and normalizar_caminho(capa["foto"]) == chave:
        capa["foto"] = novo
    for dados in fundos_imagem(projeto):
        if normalizar_caminho(dados["caminho"]) == chave:
            dados["caminho"] = novo


def faltando(projeto):
    caminhos = [f["caminho"] for f in projeto["fotos"]]
    caminhos += [e["foto"] for e in todos_espacos(projeto) if e.get("foto")]
    caminhos += [d["caminho"] for d in fundos_imagem(projeto)]
    vistos = set()
    saida = []
    for c in caminhos:
        chave = normalizar_caminho(c)
        if chave in vistos:
            continue
        vistos.add(chave)
        if not Path(c).is_file():
            saida.append(c)
    return saida


def salvar(projeto, caminho):
    caminho = Path(caminho)
    if not caminho.name.endswith(EXTENSAO):
        caminho = caminho.with_name(caminho.name.split(".")[0] + EXTENSAO)
    gravar_json(caminho, projeto)
    return caminho


def abrir(caminho):
    dados = ler_json(caminho, None)
    if not isinstance(dados, dict) or "laminas" not in dados:
        raise ValueError("Arquivo de projeto inválido: %s" % caminho)
    base = novo(dados.get("produto", ""))
    base.update(dados)
    base["capa"] = capa_mod.completar(base.get("capa"))
    for lamina in base["laminas"]:
        lamina.setdefault("espacos", [])
        lamina.setdefault("sobreposicoes", [])
        lamina.setdefault("fundo", "#ffffff")
        for espaco in lamina["espacos"]:
            espaco.setdefault("edicao", {})
            espaco.setdefault("foto", None)
    return base


def foto_retrato(projeto):
    """Cópia do estado que pode ser desfeito."""
    return json.dumps({k: projeto.get(k) for k in ("fotos", "laminas", "capa", "config")}, ensure_ascii=False)


def restaurar_retrato(projeto, retrato):
    dados = json.loads(retrato)
    for chave, valor in dados.items():
        projeto[chave] = valor


class Historico:
    """Desfazer e refazer guardando o estado antes de cada mudança."""

    def __init__(self, limite=60):
        self.limite = limite
        self.atras = []
        self.frente = []

    def marcar(self, projeto):
        retrato = foto_retrato(projeto)
        if self.atras and self.atras[-1] == retrato:
            return
        self.atras.append(retrato)
        if len(self.atras) > self.limite:
            self.atras.pop(0)
        self.frente.clear()

    def desfazer(self, projeto):
        if not self.atras:
            return False
        self.frente.append(foto_retrato(projeto))
        restaurar_retrato(projeto, self.atras.pop())
        return True

    def refazer(self, projeto):
        if not self.frente:
            return False
        self.atras.append(foto_retrato(projeto))
        restaurar_retrato(projeto, self.frente.pop())
        return True

    def limpar(self):
        self.atras.clear()
        self.frente.clear()


def copia(objeto):
    return copy.deepcopy(objeto)
