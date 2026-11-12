# -*- coding: utf-8 -*-
"""
Login do Google (OAuth, só leitura do Drive) para importar fotos SEM o Google Drive para computador.

Com o login, a API enxerga as pastas privadas das alunas (a chave da API só vê pastas
"Qualquer pessoa com o link") e informa a pasta de evento de cada atalho da seleção.

NADA SECRETO FICA NA PASTA DO PROGRAMA (ela vai para o GitHub):
  - credencial do Google Cloud: Documents\\credenciais_google_oauth.json (aceita também ".json.json"
    e "client_secret*.json"), ou o caminho da variável SMART_ALBUM_GOOGLE_CREDENCIAIS;
  - login salvo (refresh token): Documents\\token_google_drive.json, ao lado da credencial.
Se a credencial estiver dentro da pasta do programa, ela é ignorada.
"""

import base64
import hashlib
import http.server
import json
import os
import secrets
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

VARIAVEL_CREDENCIAIS = "SMART_ALBUM_GOOGLE_CREDENCIAIS"
ESCOPO_LEITURA = "https://www.googleapis.com/auth/drive.readonly"
# Gravação (organizador: criar ALBUM APROVAÇAO e enviar as lâminas). Também serve para ler.
ESCOPO_GRAVACAO = "https://www.googleapis.com/auth/drive"
ESCOPO = ESCOPO_LEITURA
NOME_TOKEN = "token_google_drive.json"
PASTA_PROGRAMA = Path(__file__).resolve().parent
TEMPO_LOGIN = 300  # segundos esperando a autorização no navegador

_trava = threading.Lock()
_cache = {"token": None, "expira": 0.0, "escopo": ""}


def _cobre(escopo_tem, escopo_pede):
    """O login que tem (escopo_tem) serve para o que se pede? Gravação serve para leitura."""
    tem = set(str(escopo_tem or "").split())
    return escopo_pede in tem or (escopo_pede == ESCOPO_LEITURA and ESCOPO_GRAVACAO in tem)


def _dentro_do_programa(caminho):
    try:
        Path(caminho).resolve().relative_to(PASTA_PROGRAMA)
        return True
    except ValueError:
        return False


def arquivo_credenciais():
    """Credencial OAuth (tipo 'installed') fora da pasta do programa, ou None."""
    variavel = (os.environ.get(VARIAVEL_CREDENCIAIS) or "").strip()
    if variavel:
        candidatos = [Path(variavel)]
    else:
        docs = Path.home() / "Documents"
        candidatos = [docs / "credenciais_google_oauth.json", docs / "credenciais_google_oauth.json.json"]
        candidatos += sorted(docs.glob("client_secret*.json")) if docs.is_dir() else []
    for c in candidatos:
        if c.is_file() and not _dentro_do_programa(c):
            return c
    return None


def _config():
    cred = arquivo_credenciais()
    if not cred:
        return None
    try:
        dados = json.loads(cred.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    cfg = dados.get("installed") or dados.get("web")
    return cfg if isinstance(cfg, dict) and cfg.get("client_id") and cfg.get("client_secret") else None


def arquivo_token():
    cred = arquivo_credenciais()
    return cred.parent / NOME_TOKEN if cred else None


def disponivel():
    """Há credencial do Google configurada (fora da pasta do programa)."""
    return _config() is not None


def logado(escopo=ESCOPO_LEITURA):
    refresh, tem = _ler_salvo()
    return bool(refresh) and _cobre(tem, escopo)


def _ler_salvo():
    """(refresh_token, escopo) do login guardado. Login antigo sem escopo anotado = só leitura."""
    tok = arquivo_token()
    try:
        dados = json.loads(tok.read_text(encoding="utf-8")) if tok and tok.is_file() else {}
    except (OSError, ValueError):
        dados = {}
    return dados.get("refresh_token"), dados.get("escopo") or ESCOPO_LEITURA


def _ler_refresh():
    return _ler_salvo()[0]


def _post(url, dados):
    req = urllib.request.Request(url, urllib.parse.urlencode(dados).encode(), method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def _guardar(resposta, escopo):
    _cache["token"] = resposta.get("access_token")
    _cache["expira"] = time.time() + float(resposta.get("expires_in") or 3600) - 120
    _cache["escopo"] = resposta.get("scope") or escopo
    return _cache["token"]


def entrar(cfg=None, abrir=webbrowser.open, escopo=ESCOPO_LEITURA):
    """Abre o navegador para a usuária autorizar e guarda o login ao lado da credencial."""
    cfg = cfg or _config()
    if not cfg:
        raise RuntimeError("credencial do Google não encontrada em Documentos")
    verificador = secrets.token_urlsafe(64)
    desafio = base64.urlsafe_b64encode(hashlib.sha256(verificador.encode()).digest()).rstrip(b"=").decode()
    recebido = {}

    class Resposta(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
            if "code" in q or "error" in q:
                recebido.update(q)
            try:  # o navegador às vezes fecha a conexão antes; o código já foi recebido
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write("<h2>Smart Album: pronto, pode fechar esta aba.</h2>".encode("utf-8"))
            except OSError:
                pass

        def log_message(self, *_a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Resposta)
    srv.timeout = 1
    redirect = "http://127.0.0.1:%d/" % srv.server_port
    abrir(cfg["auth_uri"] + "?" + urllib.parse.urlencode({
        "client_id": cfg["client_id"], "redirect_uri": redirect, "response_type": "code", "scope": escopo,
        "code_challenge": desafio, "code_challenge_method": "S256", "access_type": "offline", "prompt": "consent",
    }))
    fim = time.time() + TEMPO_LOGIN
    try:
        while not recebido and time.time() < fim:
            srv.handle_request()
    finally:
        srv.server_close()
    if not recebido:
        raise RuntimeError("o login do Google não foi concluído no navegador")
    if "error" in recebido:
        raise RuntimeError("o Google recusou o login (%s)" % recebido["error"])
    resp = _post(cfg["token_uri"], {
        "code": recebido["code"], "client_id": cfg["client_id"], "client_secret": cfg["client_secret"],
        "redirect_uri": redirect, "grant_type": "authorization_code", "code_verifier": verificador,
    })
    concedido = resp.get("scope") or escopo
    if resp.get("refresh_token"):
        arquivo_token().write_text(json.dumps({"refresh_token": resp["refresh_token"], "escopo": concedido}),
                                   encoding="utf-8")
    if not _cobre(concedido, escopo):
        raise RuntimeError("a permissão pedida não foi marcada no navegador")
    return _guardar(resp, concedido)


def token_acesso(interativo=False, escopo=ESCOPO_LEITURA):
    """
    Token de acesso válido (renova sozinho), ou None. Com interativo=True pede login no navegador se ainda
    não houver login, ou se o login guardado é só de leitura e se pede gravação.
    """
    with _trava:
        if _cache["token"] and time.time() < _cache["expira"] and _cobre(_cache["escopo"], escopo):
            return _cache["token"]
        cfg = _config()
        if not cfg:
            return None
        refresh, tem = _ler_salvo()
        if refresh and _cobre(tem, escopo):
            try:
                return _guardar(_post(cfg["token_uri"], {
                    "refresh_token": refresh, "client_id": cfg["client_id"],
                    "client_secret": cfg["client_secret"], "grant_type": "refresh_token",
                }), tem)
            except Exception:
                pass  # login revogado ou expirado: pede de novo se puder
        if not interativo:
            return None
        try:
            return entrar(cfg, escopo=escopo)
        except Exception:
            return None


def esquecer():
    """Apaga o login guardado (a credencial continua)."""
    _cache.update(token=None, expira=0.0, escopo="")
    tok = arquivo_token()
    if tok and tok.is_file():
        tok.unlink()
