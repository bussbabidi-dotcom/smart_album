# -*- coding: utf-8 -*-
"""
Projeto do álbum (.album.json): produto, fotos, lâminas, capa e as edições.

Lâmina: {"id", "fundo", "fundo_imagem", "espacos": [...], "sobreposicoes": [...], "modelo"}
Espaço: {"x", "y", "w", "h" (fração da lâmina), "foto", "edicao"}
Imagem de fundo (lâmina e capa, opcional): {"caminho", "modo", "opacidade", "desfoque", "pb"}
"""

import copy
import json
import zlib
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
        "config": {"auto_salvar_minutos": 5},
        "laminas": [],
        "capa": capa_mod.capa_padrao(),
        "photoshop": {"conjunto": "", "acao": ""},
        "lightroom": {"preset_uuid": "", "preset_nome": ""},
    }
    adicionar_fotos(projeto, fotos)
    return projeto


def pasta_da_foto(foto):
    if isinstance(foto, dict) and foto.get("pasta"):
        return str(foto["pasta"])
    caminho = foto["caminho"] if isinstance(foto, dict) else str(foto)
    return str(Path(caminho).parent)


def nome_pasta_da_foto(foto):
    p = Path(pasta_da_foto(foto))
    return p.name or str(p) or "Fotos"


def listar_pastas_projeto(projeto):
    """Lista ordenada de pastas únicas carregadas no projeto: [(chave_norm, caminho_pasta, nome_pasta, qtd)]."""
    ordem = []
    mapa = {}
    for foto in projeto.get("fotos", []):
        pasta = pasta_da_foto(foto)
        chave = normalizar_caminho(pasta)
        if chave not in mapa:
            mapa[chave] = [chave, pasta, nome_pasta_da_foto(foto), 0]
            ordem.append(chave)
        mapa[chave][3] += 1
    return [tuple(mapa[c]) for c in ordem]


def adicionar_fotos(projeto, caminhos, pasta_origem=None):
    """Acrescenta no fim, mantendo a divisão por pasta e sem repetir. Devolve as que entraram."""
    existentes = {normalizar_caminho(f["caminho"]) for f in projeto["fotos"]}
    novas = []
    # Se já existirem grupos numerados, novas fotos entram no último grupo ou no Grupo 1
    ultimo_grupo = 1
    ultimo_nome_grupo = "Grupo 1"
    for f in projeto.get("fotos", []):
        g = int(f.get("grupo_ordem") or 1)
        if g >= ultimo_grupo:
            ultimo_grupo = g
            ultimo_nome_grupo = f.get("nome_grupo") or ("Grupo %d" % g)
    for caminho in caminhos:
        chave = normalizar_caminho(caminho)
        if chave in existentes:
            continue
        existentes.add(chave)
        p_foto = str(Path(pasta_origem)) if pasta_origem else str(Path(caminho).parent)
        projeto["fotos"].append({
            "caminho": str(Path(caminho)),
            "destaque": False,
            "pasta": p_foto,
            "grupo_ordem": ultimo_grupo,
            "nome_grupo": ultimo_nome_grupo,
            "ordem_manual": len(projeto["fotos"]) + 1,
        })
        novas.append(str(Path(caminho)))
    return novas


def listar_grupos_ordem(projeto):
    """
    Devolve a lista ordenada de grupos numerados do projeto:
    [{"id": 1, "nome": "Grupo 1", "fotos": [dict_foto, ...]}, ...]
    Sem alterar nenhum arquivo no disco.
    """
    fotos = projeto.get("fotos", [])
    if not fotos:
        return []
    grupos_map = {}
    ordem_ids = []
    for idx, f in enumerate(fotos):
        try:
            gid = max(1, int(f.get("grupo_ordem") or 1))
        except (TypeError, ValueError):
            gid = 1
        if gid not in grupos_map:
            nome_g = f.get("nome_grupo") or ("Grupo %d" % gid)
            grupos_map[gid] = {"id": gid, "nome": nome_g, "fotos": []}
            ordem_ids.append(gid)
        grupos_map[gid]["fotos"].append((int(f.get("ordem_manual") or (idx + 1)), idx, f))

    ordem_ids.sort()
    saida = []
    for novo_num, gid in enumerate(ordem_ids, start=1):
        g = grupos_map[gid]
        g["fotos"].sort(key=lambda it: (it[0], it[1]))
        fotos_ordenadas = [it[2] for it in g["fotos"]]
        nome_atual = g["nome"]
        if not nome_atual or nome_atual.startswith("Grupo "):
            nome_atual = "Grupo %d" % novo_num
        saida.append({
            "id": novo_num,
            "grupo_ordem": novo_num,
            "nome": nome_atual,
            "fotos": fotos_ordenadas,
        })
    return saida


def eh_foto_em_grupo(foto, info=None):
    """
    Identifica se uma foto é 'Em Grupo / Turma' (True, 2+ pessoas) ou 'Individual' (False, 0-1 pessoa).
    Respeita primeiro a marcação manual (`tipo_pessoas` = 'individual' | 'grupo') e,
    quando em modo 'auto', utiliza a quantidade de rostos detectados na análise.
    """
    if not isinstance(foto, dict):
        return False
    tipo_manual = str(foto.get("tipo_pessoas") or "auto").strip().lower()
    if tipo_manual == "grupo":
        return True
    if tipo_manual == "individual":
        return False
    rosto = foto.get("rosto")
    if rosto is None and isinstance(info, dict) and foto.get("caminho"):
        dados = info.get(normalizar_caminho(foto["caminho"])) or {}
        rosto = dados.get("rosto")
    if isinstance(rosto, dict):
        try:
            return int(rosto.get("qtd") or 0) >= 2
        except (TypeError, ValueError):
            return False
    return False


def aplicar_grupos_ordem(projeto, grupos_lista):
    """
    Atualiza `grupo_ordem`, `nome_grupo`, `ordem_manual`, `tipo_pessoas` e a ordem da lista `projeto["fotos"]`
    conforme os grupos numerados definidos na janela de Ordem e Grupos, sem alterar arquivos no PC.
    """
    mapa_orig = {
        normalizar_caminho(f["caminho"]): f
        for f in projeto.get("fotos", [])
        if isinstance(f, dict) and f.get("caminho")
    }
    nova_lista = []
    seq = 1
    vistos = set()
    for num_g, g in enumerate(grupos_lista, start=1):
        nome_g = (g.get("nome") or ("Grupo %d" % num_g)).strip() or ("Grupo %d" % num_g)
        for f in g.get("fotos", []):
            ch = normalizar_caminho(f["caminho"])
            if ch in vistos:
                continue
            vistos.add(ch)
            alvo = mapa_orig.get(ch, f)
            alvo["grupo_ordem"] = num_g
            alvo["cenario_grupo"] = num_g
            alvo["nome_grupo"] = nome_g
            alvo["ordem_manual"] = seq
            if "tipo_pessoas" in f:
                tp = str(f.get("tipo_pessoas") or "auto").strip().lower()
                if tp in ("individual", "grupo"):
                    alvo["tipo_pessoas"] = tp
                else:
                    alvo.pop("tipo_pessoas", None)
            seq += 1
            nova_lista.append(alvo)

    # Preserva eventuais fotos que não estavam na lista
    for f in projeto.get("fotos", []):
        ch = normalizar_caminho(f["caminho"])
        if ch not in vistos:
            vistos.add(ch)
            f["grupo_ordem"] = max(1, len(grupos_lista))
            f["ordem_manual"] = seq
            seq += 1
            nova_lista.append(f)

    projeto["fotos"] = nova_lista


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


def pertence_a_pasta(ch_pasta, chave_pasta_alvo):
    """Verifica se `ch_pasta` é exatamente `chave_pasta_alvo` ou uma subpasta dentro dela."""
    if not chave_pasta_alvo:
        return True
    p = normalizar_caminho(ch_pasta).rstrip("/\\")
    alvo = normalizar_caminho(chave_pasta_alvo).rstrip("/\\")
    if p == alvo:
        return True
    return p.startswith(alvo + "/") or p.startswith(alvo + "\\")


def remover_pasta_projeto(projeto, chave_pasta, remover_laminas_da_pasta=True):
    """Remove todas as fotos de uma pasta carregada e opcionalmente remove as lâminas exclusivas dessa pasta."""
    alvo = normalizar_caminho(chave_pasta)
    caminhos_pasta = [
        f["caminho"]
        for f in projeto.get("fotos", [])
        if pertence_a_pasta(pasta_da_foto(f), alvo)
    ]
    if not caminhos_pasta:
        return 0, 0
    remover_fotos(projeto, caminhos_pasta)
    laminas_removidas = 0
    if remover_laminas_da_pasta:
        novas_laminas = []
        for lam in projeto.get("laminas", []):
            ch_lam = normalizar_caminho(lam.get("pasta") or "")
            fotos_restantes = [e for e in lam.get("espacos", []) if e.get("foto")]
            if (pertence_a_pasta(ch_lam, alvo) and not fotos_restantes) or (not fotos_restantes and lam.get("espacos")):
                laminas_removidas += 1
                continue
            novas_laminas.append(lam)
        projeto["laminas"] = novas_laminas
    return len(caminhos_pasta), laminas_removidas


def mapa_registros(projeto):
    """Registro de cada foto do projeto pelo caminho normalizado (montar uma vez e reaproveitar)."""
    mapa = {}
    for f in projeto.get("fotos", []):
        if f.get("caminho"):
            mapa.setdefault(normalizar_caminho(f["caminho"]), f)
    return mapa


def pasta_da_lamina(projeto, lamina, mapa_fotos=None):
    """
    Identifica a pasta principal e a lista de pastas presentes nos espaços de uma lâmina.
    Devolve (chave_pasta, nome_pasta, lista_pastas_distintas).
    """
    if not isinstance(lamina, dict):
        return "", "", []
    if mapa_fotos is None:
        mapa_fotos = mapa_registros(projeto)
    pastas_vistas = []
    nomes_pastas = {}
    for esp in lamina.get("espacos", []):
        cam = esp.get("foto")
        if not cam:
            continue
        reg = mapa_fotos.get(normalizar_caminho(cam))
        p_raw = pasta_da_foto(reg if reg else cam)
        ch_p = normalizar_caminho(p_raw)
        if ch_p not in nomes_pastas:
            nomes_pastas[ch_p] = nome_pasta_da_foto(reg if reg else cam)
            pastas_vistas.append(ch_p)
    if pastas_vistas:
        ch0 = pastas_vistas[0]
        return ch0, nomes_pastas[ch0], pastas_vistas
    if lamina.get("pasta"):
        ch_l = normalizar_caminho(lamina["pasta"])
        nm_l = lamina.get("nome_pasta") or Path(lamina["pasta"]).name or "Pasta"
        return ch_l, nm_l, [ch_l]
    return "", "", []


def laminas_com_pastas_misturadas(projeto):
    """Retorna os índices das lâminas que misturam fotos de 2 ou mais pastas carregadas diferentes."""
    misturadas = []
    mapa = mapa_registros(projeto)
    for idx, lam in enumerate(projeto.get("laminas", [])):
        _ch, _nm, distintas = pasta_da_lamina(projeto, lam, mapa)
        if len(distintas) > 1:
            misturadas.append(idx)
    return misturadas



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
    """Ordena as fotos respeitando os grupos numerados e/ou pastas sem alterar arquivos em disco."""
    from comum import chave_natural

    pastas_ordem = {chave: idx for idx, (chave, _p, _n, _q) in enumerate(listar_pastas_projeto(projeto))}

    if por in ("grupos", "ordem", "cenario"):
        grupos = listar_grupos_ordem(projeto)
        aplicar_grupos_ordem(projeto, grupos)
        return

    if por == "data":
        def chave(f):
            gid = int(f.get("grupo_ordem") or 1)
            ch_p = normalizar_caminho(pasta_da_foto(f))
            dados = info.get(normalizar_caminho(f["caminho"])) or {}
            return (gid, pastas_ordem.get(ch_p, 9999), dados.get("data") or 0, chave_natural(f["caminho"]))
    else:
        def chave(f):
            gid = int(f.get("grupo_ordem") or 1)
            ch_p = normalizar_caminho(pasta_da_foto(f))
            return (gid, pastas_ordem.get(ch_p, 9999), [chave_natural(p) for p in Path(f["caminho"]).parts])
    projeto["fotos"].sort(key=chave)
    for idx, f in enumerate(projeto["fotos"], start=1):
        f["ordem_manual"] = idx


def fotos_para_diagramar(projeto, info, somente_nao_usadas=False, pasta_alvo=None):
    """Fotos analisadas, na ordem do projeto, com grupo numerado, ordem manual, cenário visual, tipo individual/grupo, pasta e rosto para o diagramar."""
    from diagramar import USAR_CENA_TEMA

    ja_usadas = usos(projeto) if somente_nao_usadas else Counter()
    chave_pasta_alvo = normalizar_caminho(pasta_alvo) if pasta_alvo else None
    saida = []
    for idx, foto in enumerate(projeto["fotos"], start=1):
        chave = normalizar_caminho(foto["caminho"])
        dados = info.get(chave)
        if not dados or ja_usadas.get(chave):
            continue
        ch_pasta = normalizar_caminho(pasta_da_foto(foto))
        if chave_pasta_alvo and not pertence_a_pasta(ch_pasta, chave_pasta_alvo):
            continue
        gid = max(1, int(foto.get("grupo_ordem") or 1))
        ord_m = int(foto.get("ordem_manual") or idx)
        tp = str(foto.get("tipo_pessoas") or "auto").strip().lower()
        ass_cen = dados.get("cenario") or foto.get("cenario")
        meta = {
            "caminho": foto["caminho"],
            "proporcao": dados_proporcao(dados),
            "data": dados.get("data"),
            "destaque": bool(foto.get("destaque")),
            "grupo_ordem": gid,
            "nome_grupo": foto.get("nome_grupo") or ("Grupo %d" % gid),
            "ordem_manual": ord_m,
            "tipo_pessoas": tp if tp in ("individual", "grupo") else "auto",
            "em_grupo_pessoas": eh_foto_em_grupo(foto, info),
            "rosto": dados.get("rosto"),
            "cenario": ass_cen,
            "assinatura_cenario": ass_cen if isinstance(ass_cen, (list, tuple)) else None,
            "pasta": ch_pasta,
            "nome_pasta": nome_pasta_da_foto(foto),
            "edicao": dict(foto.get("edicao_padrao") or {}),
        }
        if USAR_CENA_TEMA and dados.get("cena"):
            meta["cena"] = dados["cena"]
        saida.append(meta)
    return saida


def dados_proporcao(dados):
    return float(dados.get("proporcao") or (dados["w"] / float(dados["h"])))


def fundos_imagem(projeto, incluir_gerados=True):
    """
    As imagens de fundo das lâminas (inteira, página esquerda e página direita).
    Fundos gerados pelo programa têm a receita em "gerado" e o PNG é recriado quando falta;
    com incluir_gerados=False ficam de fora (não contam como foto sumida nem são relinkados).
    """
    for dono in projeto.get("laminas", []):
        for ch in ("fundo_imagem", "fundo_imagem_esquerda", "fundo_imagem_direita"):
            dados = dono.get(ch)
            if isinstance(dados, dict) and dados.get("caminho"):
                if not incluir_gerados and isinstance(dados.get("gerado"), dict) and dados["gerado"]:
                    continue
                yield dados


def trocar_caminho(projeto, antigo, novo):
    """Troca a foto em todo o projeto (lista, espaços, capa e imagens de fundo)."""
    chave = normalizar_caminho(antigo)
    novo = str(novo)
    nova_pasta = str(Path(novo).parent)
    for foto in projeto["fotos"]:
        if normalizar_caminho(foto["caminho"]) == chave:
            foto["caminho"] = novo
            foto["pasta"] = nova_pasta
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
    caminhos += [d["caminho"] for d in fundos_imagem(projeto, incluir_gerados=False)]
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


def caminho_projeto(caminho):
    caminho = Path(caminho)
    if not caminho.name.endswith(EXTENSAO):
        caminho = caminho.with_name(caminho.name.split(".")[0] + EXTENSAO)
    return caminho


def salvar(projeto, caminho):
    caminho = caminho_projeto(caminho)
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
    """Cópia compacta e rápida do estado que pode ser desfeito."""
    return json.dumps(
        {k: projeto.get(k) for k in ("fotos", "laminas", "capa", "config")},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def restaurar_retrato(projeto, retrato):
    dados = json.loads(retrato)
    for chave, valor in dados.items():
        projeto[chave] = valor


CHAVES_RETRATO = ("fotos", "laminas", "capa", "config")


class Historico:
    """
    Desfazer e refazer guardando o estado antes de cada mudança.
    Cada retrato fica comprimido; a lista de fotos (grande e quase sempre igual) é
    guardada uma vez e reaproveitada pelos retratos seguintes enquanto não muda.
    """

    def __init__(self, limite=30):
        self.limite = limite
        self.atras = []
        self.frente = []
        self._fotos_atual = (None, None)

    def _comprimir(self, valor):
        return zlib.compress(json.dumps(valor, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), 6)

    def _retrato(self, projeto):
        texto_fotos = json.dumps(projeto.get("fotos"), ensure_ascii=False, separators=(",", ":"))
        if self._fotos_atual[0] != texto_fotos:
            self._fotos_atual = (texto_fotos, zlib.compress(texto_fotos.encode("utf-8"), 6))
        resto = {k: projeto.get(k) for k in CHAVES_RETRATO if k != "fotos"}
        return (self._fotos_atual[1], self._comprimir(resto))

    @staticmethod
    def _restaurar(projeto, retrato):
        fotos_z, resto_z = retrato
        projeto["fotos"] = json.loads(zlib.decompress(fotos_z).decode("utf-8"))
        for chave, valor in json.loads(zlib.decompress(resto_z).decode("utf-8")).items():
            projeto[chave] = valor

    def marcar(self, projeto):
        retrato = self._retrato(projeto)
        if self.atras and self.atras[-1] == retrato:
            return
        self.atras.append(retrato)
        if len(self.atras) > self.limite:
            self.atras.pop(0)
        self.frente.clear()

    def desfazer(self, projeto):
        if not self.atras:
            return False
        self.frente.append(self._retrato(projeto))
        self._restaurar(projeto, self.atras.pop())
        return True

    def refazer(self, projeto):
        if not self.frente:
            return False
        self.atras.append(self._retrato(projeto))
        self._restaurar(projeto, self.frente.pop())
        return True

    def limpar(self):
        self.atras.clear()
        self.frente.clear()


def copia(objeto):
    return copy.deepcopy(objeto)
