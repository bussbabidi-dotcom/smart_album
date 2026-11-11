# -*- coding: utf-8 -*-
"""
Modelos de IA locais (cena Places365 e edição 3D LUT): leitura, conferência e importação offline.

Nunca acessa a rede. Os modelos ficam em recursos\\modelos_ia (junto com o programa ou dentro do exe);
os importados pelo usuário (zip do pendrive) vão para %LOCALAPPDATA%\\smart_album\\modelos_ia e têm prioridade.
Cada arquivo é conferido pelo SHA-256 do modelos_ia.json; modelo ausente ou corrompido = programa segue sem IA.
"""

import hashlib
import json
import os
import shutil
import tempfile
import threading
import zipfile
from pathlib import Path

from comum import gravar_json, ler_json, pasta_dados, pasta_recurso

NOME_PASTA = "modelos_ia"
ARQUIVO_INDICE = "modelos_ia.json"
ARQUIVO_CONFIG = pasta_dados() / "ia.json"
MODELOS = ("cena", "edicao")
CONFIG_PADRAO = {
    "reconhecer_cenas": True,
    "cena_no_tema": False,
    "edicao_ia": True,
    "modo_leve": False,
}

_trava = threading.Lock()
_config = None
_estado = None
_hashes = {}
_geracao = 0
_local = threading.local()
_threads_cv = None


# ------------------------------------------------------------
# Configuração (ia.json na pasta de dados)
# ------------------------------------------------------------

def config():
    global _config
    with _trava:
        if _config is None:
            dados = ler_json(ARQUIVO_CONFIG, {}) or {}
            cfg = dict(CONFIG_PADRAO)
            if isinstance(dados, dict):
                cfg.update({k: bool(v) for k, v in dados.items() if k in CONFIG_PADRAO})
            _config = cfg
        return dict(_config)


def salvar_config(novos):
    global _config
    cfg = config()
    cfg.update({k: bool(v) for k, v in (novos or {}).items() if k in CONFIG_PADRAO})
    with _trava:
        _config = cfg
    try:
        gravar_json(ARQUIVO_CONFIG, cfg)
    except OSError:
        pass
    return dict(cfg)


def threads_ia():
    return 1 if config().get("modo_leve") else 2


# ------------------------------------------------------------
# Pastas e conferência
# ------------------------------------------------------------

def pasta_local():
    return pasta_dados() / NOME_PASTA


def pasta_embutida():
    return pasta_recurso("recursos") / NOME_PASTA


def sha256_arquivo(caminho):
    caminho = Path(caminho)
    st = caminho.stat()
    chave = (str(caminho), st.st_mtime_ns, st.st_size)
    guardado = _hashes.get(chave)
    if guardado:
        return guardado
    h = hashlib.sha256()
    with open(caminho, "rb") as arq:
        for bloco in iter(lambda: arq.read(1 << 20), b""):
            h.update(bloco)
    valor = h.hexdigest()
    _hashes[chave] = valor
    return valor


def ler_indice(pasta):
    dados = ler_json(Path(pasta) / ARQUIVO_INDICE, None)
    if not isinstance(dados, dict) or not isinstance(dados.get("modelos"), dict) or not isinstance(dados.get("arquivos"), dict):
        return None
    return dados


def conferir_modelo(pasta, indice, modelo):
    """(ok, mensagem) de um modelo da pasta: todos os arquivos existem e batem com o SHA-256."""
    info = (indice or {}).get("modelos", {}).get(modelo)
    if not isinstance(info, dict) or not isinstance(info.get("arquivos"), dict):
        return False, "não incluído"
    for nome in info["arquivos"].values():
        dados_arq = indice["arquivos"].get(nome) or {}
        esperado = str(dados_arq.get("sha256") or "").lower()
        caminho = Path(pasta) / nome
        if not esperado or not caminho.is_file():
            return False, "arquivo ausente: %s" % nome
        try:
            if sha256_arquivo(caminho) != esperado:
                return False, "arquivo corrompido (SHA-256 diferente): %s" % nome
        except OSError as e:
            return False, "não deu para ler %s: %s" % (nome, e)
    return True, "ok"


def _calcular_estado():
    estado = {}
    origens = (("local", pasta_local()), ("programa", pasta_embutida()))
    indices = [(nome, pasta, ler_indice(pasta)) for nome, pasta in origens]
    for modelo in MODELOS:
        escolhido = {"ok": False, "versao": "", "nome": "", "origem": "", "pasta": "", "erro": "não instalado"}
        erros = []
        for origem, pasta, indice in indices:
            if indice is None:
                continue
            ok, msg = conferir_modelo(pasta, indice, modelo)
            if ok:
                info = indice["modelos"][modelo]
                escolhido = {
                    "ok": True,
                    "versao": str(info.get("versao") or ""),
                    "nome": str(info.get("nome") or modelo),
                    "origem": origem,
                    "pasta": str(pasta),
                    "arquivos": {k: str(Path(pasta) / v) for k, v in info["arquivos"].items()},
                    "erro": "",
                }
                break
            if msg != "não incluído":
                erros.append("%s: %s" % (origem, msg))
        if not escolhido["ok"] and erros:
            escolhido["erro"] = "; ".join(erros)
        estado[modelo] = escolhido
    return estado


def estado_modelos(recalcular=False):
    """{"cena": {...}, "edicao": {...}} com ok, versão, origem (local/programa) e erro. Confere o SHA-256 uma vez."""
    global _estado, _geracao
    with _trava:
        if _estado is not None and not recalcular:
            return _estado
    novo = _calcular_estado()
    with _trava:
        _estado = novo
        _geracao += 1
    return novo


def disponivel(modelo):
    try:
        return bool(estado_modelos().get(modelo, {}).get("ok"))
    except Exception:
        return False


def assinatura(modelo):
    """Versão em uso do modelo (entra no cache de análise); vazio quando ausente."""
    info = estado_modelos().get(modelo) or {}
    return "%s|%s" % (modelo, info.get("versao")) if info.get("ok") else ""


def arquivo(modelo, chave):
    info = estado_modelos().get(modelo) or {}
    if not info.get("ok"):
        return None
    caminho = (info.get("arquivos") or {}).get(chave)
    return Path(caminho) if caminho else None


# ------------------------------------------------------------
# Redes (uma por thread) e dados auxiliares
# ------------------------------------------------------------

def _limitar_threads_opencv():
    global _threads_cv
    n = threads_ia()
    if _threads_cv != n:
        import cv2
        cv2.setNumThreads(n)
        _threads_cv = n


def rede(modelo):
    """cv2.dnn.Net do modelo, carregada uma vez por thread; None quando o modelo não está disponível."""
    chave = "rede_" + modelo
    caminho = arquivo(modelo, "rede")
    guardada = getattr(_local, chave, None)
    if guardada is not None and guardada[0] == _geracao:
        return guardada[1]
    if caminho is None:
        return None
    import cv2
    _limitar_threads_opencv()
    try:
        net = cv2.dnn.readNetFromONNX(str(caminho))
    except Exception:
        return None
    setattr(_local, chave, (_geracao, net))
    return net


_dados_cache = {}


def carregar_json_modelo(modelo, chave):
    caminho = arquivo(modelo, chave)
    if caminho is None:
        return None
    ch = (str(caminho), "json")
    if ch not in _dados_cache:
        try:
            _dados_cache[ch] = json.loads(caminho.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
    return _dados_cache[ch]


def carregar_npz_modelo(modelo, chave):
    caminho = arquivo(modelo, chave)
    if caminho is None:
        return None
    ch = (str(caminho), "npz")
    if ch not in _dados_cache:
        import numpy as np
        try:
            with np.load(str(caminho), allow_pickle=False) as dados:
                _dados_cache[ch] = {k: dados[k] for k in dados.files}
        except (OSError, ValueError):
            return None
    return _dados_cache[ch]


# ------------------------------------------------------------
# Importar modelos de um arquivo .zip (pendrive / pasta local)
# ------------------------------------------------------------

def _extrair_seguro(zf, destino):
    destino = Path(destino).resolve()
    for membro in zf.infolist():
        if membro.is_dir():
            continue
        alvo = (destino / membro.filename).resolve()
        if destino not in alvo.parents:
            raise ValueError("caminho inválido dentro do zip: %s" % membro.filename)
        alvo.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(membro) as origem, open(alvo, "wb") as saida:
            shutil.copyfileobj(origem, saida)


def _achar_indice(pasta):
    pasta = Path(pasta)
    if (pasta / ARQUIVO_INDICE).is_file():
        return pasta
    for arq in pasta.rglob(ARQUIVO_INDICE):
        return arq.parent
    return None


def importar_zip(caminho_zip):
    """
    Copia os modelos de um .zip (com modelos_ia.json) para a pasta local, que passa a ter prioridade.
    Recusa o pacote inteiro se algum arquivo listado não bater com o SHA-256. Devolve (ok, mensagem).
    """
    caminho_zip = Path(caminho_zip)
    if not caminho_zip.is_file():
        return False, "Arquivo não encontrado: %s" % caminho_zip
    base = pasta_dados()
    temp = Path(tempfile.mkdtemp(prefix="modelos_ia_import_", dir=str(base)))
    try:
        try:
            with zipfile.ZipFile(str(caminho_zip)) as zf:
                _extrair_seguro(zf, temp)
        except (zipfile.BadZipFile, ValueError, OSError) as e:
            return False, "Não deu para abrir o zip: %s" % e
        raiz = _achar_indice(temp)
        indice = ler_indice(raiz) if raiz else None
        if indice is None:
            return False, "O zip não tem um %s válido." % ARQUIVO_INDICE
        aceitos = []
        for modelo in MODELOS:
            if modelo not in indice["modelos"]:
                continue
            ok, msg = conferir_modelo(raiz, indice, modelo)
            if not ok:
                return False, "Modelo '%s' recusado: %s" % (modelo, msg)
            aceitos.append("%s %s" % (modelo, indice["modelos"][modelo].get("versao", "")))
        if not aceitos:
            return False, "O zip não tem nenhum modelo conhecido (cena / edição)."
        destino = pasta_local()
        antigo = destino.with_name(NOME_PASTA + "_antigo")
        shutil.rmtree(antigo, ignore_errors=True)
        if destino.exists():
            os.replace(destino, antigo)
        try:
            shutil.copytree(raiz, destino)
        except OSError:
            shutil.rmtree(destino, ignore_errors=True)
            if antigo.exists():
                os.replace(antigo, destino)
            raise
        shutil.rmtree(antigo, ignore_errors=True)
        _dados_cache.clear()
        estado_modelos(recalcular=True)
        return True, "Modelos importados: %s" % ", ".join(aceitos)
    finally:
        shutil.rmtree(temp, ignore_errors=True)


def remover_importados():
    shutil.rmtree(pasta_local(), ignore_errors=True)
    _dados_cache.clear()
    estado_modelos(recalcular=True)


def texto_estado():
    """Uma linha por modelo para a janela de Configurações."""
    nomes = {"cena": "Reconhecimento de cena", "edicao": "Edição automática (3D LUT)"}
    linhas = []
    for modelo, info in estado_modelos().items():
        if info.get("ok"):
            origem = "importado" if info.get("origem") == "local" else "junto com o programa"
            linhas.append("✓ %s: instalado · versão %s (%s)" % (nomes[modelo], info.get("versao"), origem))
        else:
            linhas.append("✗ %s: %s" % (nomes[modelo], info.get("erro") or "não instalado"))
    return "\n".join(linhas)
