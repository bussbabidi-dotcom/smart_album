# -*- coding: utf-8 -*-
import hashlib
import http.server
import io
import json
import re
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

import drive

CHAVE = "chave-teste"
TOKEN_LOGIN = "token-login-teste"


@pytest.fixture(autouse=True)
def config_isolada(tmp_path, monkeypatch):
    monkeypatch.delenv(drive.VARIAVEL_CHAVE, raising=False)
    monkeypatch.setattr(drive, "_arquivo_config", lambda: tmp_path / "config" / "drive.json")


def jpeg(cor=(10, 20, 30), tamanho=(64, 48)):
    saida = io.BytesIO()
    Image.new("RGB", tamanho, cor).save(saida, "JPEG", quality=90)
    return saida.getvalue()


PAGINA_VIRUS = (
    '<!DOCTYPE html><html><head><title>Google Drive - Verificação de vírus</title></head><body>'
    '<p>Não foi possível verificar se este arquivo tem vírus.</p>'
    '<form id="download-form" action="/download" method="get">'
    '<input type="submit" value="Fazer o download mesmo assim">'
    '<input type="hidden" name="id" value="%s"><input type="hidden" name="export" value="download">'
    '<input type="hidden" name="confirm" value="t"><input type="hidden" name="uuid" value="uuid-123">'
    "</form></body></html>"
)


class DriveFalso:
    """Imita a Drive API v3 e as URLs públicas do Google Drive (todas no mesmo servidor local)."""

    def __init__(self):
        self.nos = {}
        self.erro_chave = None
        self.pagina = 1000
        self.requisicoes = []
        self.trava = threading.Lock()

    def pasta(self, pid, nome, pai=None, rk=None):
        self.nos[pid] = {"id": pid, "nome": nome, "mime": "application/vnd.google-apps.folder", "pai": pai, "rk": rk}
        return pid

    def arquivo(self, fid, nome, pai=None, dados=None, mime="image/jpeg", **extra):
        no = {"id": fid, "nome": nome, "mime": mime, "pai": pai, "dados": dados, "contagem": {}}
        no.update(extra)
        self.nos[fid] = no
        return no

    def atalho(self, sid, nome, pai, alvo, alvo_mime, alvo_rk=None):
        self.nos[sid] = {
            "id": sid, "nome": nome, "mime": "application/vnd.google-apps.shortcut", "pai": pai,
            "alvo": alvo, "alvo_mime": alvo_mime, "alvo_rk": alvo_rk,
        }

    def ids_pedidos(self):
        ids = set()
        for caminho, q, _cab in self.requisicoes:
            m = re.fullmatch(r"/drive/v3/files/([^/]+)|/d/([^=]+)=s0", caminho)
            if m:
                ids.add(m.group(1) or m.group(2))
            elif q.get("id"):
                ids.add(q["id"])
        return ids

    def _enviar(self, h, codigo, corpo, tipo="application/json", cabecalhos=None, pedacos=1, pausa=0.0):
        if isinstance(corpo, str):
            corpo = corpo.encode("utf-8")
        try:
            h.send_response(codigo)
            h.send_header("Content-Type", tipo)
            h.send_header("Content-Length", str(len(corpo)))
            for k, v in (cabecalhos or {}).items():
                h.send_header(k, v)
            h.end_headers()
            passo = max(1, -(-len(corpo) // pedacos))
            for i in range(0, len(corpo), passo):
                h.wfile.write(corpo[i:i + passo])
                h.wfile.flush()
                if pausa:
                    time.sleep(pausa)
        except OSError:
            pass

    def _erro_json(self, h, codigo, motivo, mensagem="", detalhe=None, cabecalhos=None):
        erro = {"code": codigo, "message": mensagem, "errors": [{"reason": motivo, "message": mensagem}]}
        if detalhe:
            erro["details"] = [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": detalhe}]
        self._enviar(h, codigo, json.dumps({"error": erro}), cabecalhos=cabecalhos)

    def _chave_ok(self, h, q):
        if h.headers.get("Authorization") == "Bearer " + TOKEN_LOGIN and "key" not in q:
            return True  # login do Google (OAuth)
        if self.erro_chave == "accessNotConfigured":
            self._erro_json(h, 403, "accessNotConfigured",
                            "Google Drive API has not been used in project 123 before or it is disabled.",
                            "SERVICE_DISABLED")
            return False
        if self.erro_chave == "keyInvalid" or q.get("key") != CHAVE:
            self._erro_json(h, 400, "badRequest", "API key not valid. Please pass a valid API key.", "API_KEY_INVALID")
            return False
        return True

    def _rk_ok(self, no, q, cab):
        if not no.get("rk"):
            return True
        pedidos = [p.strip() for p in cab.get("x-goog-drive-resource-keys", "").split(",")]
        return "%s/%s" % (no["id"], no["rk"]) in pedidos or q.get("resourcekey") == no["rk"]

    def _campos(self, no):
        d = {"id": no["id"], "name": no["nome"], "mimeType": no["mime"]}
        if no.get("pai"):
            d["parents"] = [no["pai"]]
        if no.get("rk"):
            d["resourceKey"] = no["rk"]
        if no.get("dados") is not None:
            d["size"] = str(no.get("tamanho_api") or len(no["dados"]))
            if no.get("com_md5"):
                d["md5Checksum"] = hashlib.md5(no["dados"]).hexdigest()
        if no.get("alvo"):
            det = {"targetId": no["alvo"], "targetMimeType": no["alvo_mime"]}
            if no.get("alvo_rk"):
                det["targetResourceKey"] = no["alvo_rk"]
            d["shortcutDetails"] = det
        return d

    def atender(self, h):
        u = urllib.parse.urlparse(h.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        cab = {k.lower(): v for k, v in h.headers.items()}
        with self.trava:
            self.requisicoes.append((u.path, q, cab))
        caminho = u.path
        if caminho == "/drive/v3/files":
            return self._lista(h, q, cab)
        m = re.fullmatch(r"/drive/v3/files/([^/]+)", caminho)
        if m:
            if q.get("alt") == "media":
                return self._baixar(h, m.group(1), "api", q, cab)
            return self._meta(h, m.group(1), q, cab)
        if caminho == "/download":
            return self._baixar(h, q.get("id"), "usercontent", q, cab)
        if caminho == "/uc":
            return self._baixar(h, q.get("id"), "uc", q, cab)
        m = re.fullmatch(r"/d/([^=]+)=s0", caminho)
        if m:
            return self._baixar(h, m.group(1), "lh3", q, cab)
        if caminho == "/thumbnail":
            return self._baixar(h, q.get("id"), "miniatura", q, cab)
        m = re.fullmatch(r"/drive/folders/([^/]+)", caminho)
        if m:
            return self._html_pasta(h, m.group(1))
        if caminho == "/embeddedfolderview":
            return self._enviar(h, 200, "<html><title>Google Drive</title></html>", "text/html")
        self._enviar(h, 404, "<html>404</html>", "text/html")

    def _lista(self, h, q, cab):
        if not self._chave_ok(h, q):
            return
        pid = re.search(r"'([^']+)' in parents", q.get("q", "")).group(1)
        pasta = self.nos.get(pid)
        if not pasta or not self._rk_ok(pasta, q, cab):
            return self._erro_json(h, 404, "notFound", "File not found: %s." % pid)
        filhos = sorted(
            (n for n in self.nos.values()
             if n.get("pai") == pid and not (n.get("lixeira") and "trashed = false" in q.get("q", ""))),
            key=lambda n: n["nome"],
        )
        inicio = int(q.get("pageToken") or 0)
        tam = min(int(q.get("pageSize") or 100), self.pagina)
        resp = {"files": [self._campos(n) for n in filhos[inicio:inicio + tam]]}
        if inicio + tam < len(filhos):
            resp["nextPageToken"] = str(inicio + tam)
        self._enviar(h, 200, json.dumps(resp))

    def _meta(self, h, fid, q, cab):
        if not self._chave_ok(h, q):
            return
        no = self.nos.get(fid)
        if not no or not self._rk_ok(no, q, cab):
            return self._erro_json(h, 404, "notFound", "File not found: %s." % fid)
        self._enviar(h, 200, json.dumps(self._campos(no)))

    def _html_pasta(self, h, pid):
        pasta = self.nos.get(pid)
        if not pasta:
            return self._enviar(h, 404, "<html>404</html>", "text/html")
        filhos = [n for n in self.nos.values() if n.get("pai") == pid][:50]
        entradas = ",".join('["%s",["%s"],"%s","%s"]' % (n["id"], pid, n["nome"], n["mime"]) for n in filhos)
        corpo = "<html><head><title>%s - Google Drive</title></head><body><script>var d=[%s];</script></body></html>" % (
            pasta["nome"], entradas)
        self._enviar(h, 200, corpo, "text/html; charset=utf-8")

    def _baixar(self, h, fid, rota, q, cab):
        if rota == "api" and not self._chave_ok(h, q):
            return
        no = self.nos.get(fid)
        if not no or no.get("dados") is None or no.get("indisponivel") or not self._rk_ok(no, q, cab):
            if rota == "api":
                return self._erro_json(h, 404, "notFound", "File not found: %s." % fid)
            return self._enviar(h, 404, "<html>404</html>", "text/html")
        with self.trava:
            no["contagem"][rota] = no["contagem"].get(rota, 0) + 1
        if no.get("atraso"):
            time.sleep(no["atraso"])
        if no.get("so_miniatura") and rota != "miniatura":
            if rota == "api":
                return self._erro_json(h, 403, "cannotDownloadFile", "The user does not have sufficient permissions.")
            return self._enviar(h, 403, "<html>proibido</html>", "text/html")
        if no.get("so_api") and rota != "api":
            return self._enviar(h, 404, "<html>404</html>", "text/html")
        if rota == "api":
            with self.trava:
                manda_429 = no.get("falhas_429", 0) > 0
                if manda_429:
                    no["falhas_429"] -= 1
                errado = not manda_429 and no.get("tamanho_errado", 0) > 0
                if errado:
                    no["tamanho_errado"] -= 1
            if manda_429:
                return self._erro_json(h, 429, "rateLimitExceeded", "Rate Limit Exceeded",
                                       cabecalhos={"Retry-After": no.get("retry_after", "1")})
            if errado:
                return self._enviar(h, 200, no["dados"] + b"\x00" * 100, "image/jpeg")
        if rota == "usercontent" and no.get("virus") and q.get("uuid") != "uuid-123":
            return self._enviar(h, 200, PAGINA_VIRUS % fid, "text/html; charset=utf-8")
        self._enviar(h, 200, no["dados"], "image/jpeg", pedacos=no.get("pedacos", 1), pausa=no.get("pausa", 0.0))


@pytest.fixture
def servidor(monkeypatch):
    falso = DriveFalso()

    class Manipulador(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_a):
            pass

        def do_GET(self):
            falso.atender(self)

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Manipulador)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % srv.server_address[1]
    falso.base = base
    monkeypatch.setattr(urllib.request, "_opener", urllib.request.build_opener(urllib.request.ProxyHandler({})))
    monkeypatch.setattr(drive, "URL_API", base + "/drive/v3")
    monkeypatch.setattr(drive, "URL_DRIVE", base)
    monkeypatch.setattr(drive, "URL_USERCONTENT", base)
    monkeypatch.setattr(drive, "URL_LH3", base)
    monkeypatch.setattr(drive, "BACKOFF_BASE", 0.02)
    monkeypatch.setattr(drive, "TIMEOUT_LEITURA", 5)
    try:
        yield falso
    finally:
        srv.shutdown()
        srv.server_close()


def _id(prefixo, n):
    return "%s%012d" % (prefixo, n)


@pytest.mark.parametrize(
    "url, esperado",
    [
        ("https://drive.google.com/drive/folders/1AbCdEfGhIjK?usp=sharing", ("pasta", "1AbCdEfGhIjK", None)),
        ("https://drive.google.com/drive/folders/1AbCdEfGhIjK?resourcekey=0-abc", ("pasta", "1AbCdEfGhIjK", "0-abc")),
        ("https://drive.google.com/file/d/1ZyXwVuTsRqP/view?usp=drive_link", ("arquivo", "1ZyXwVuTsRqP", None)),
        ("https://drive.google.com/open?id=1QwErTyUiOp", ("pasta", "1QwErTyUiOp", None)),
        ("1AbCdEfGhIjKlMnOpQrStUv", ("pasta", "1AbCdEfGhIjKlMnOpQrStUv", None)),
        ("https://exemplo.com/foto.jpg", (None, None, None)),
        ("", (None, None, None)),
    ],
)
def test_extrair_id(url, esperado):
    assert drive.extrair_id(url) == esperado


@pytest.mark.parametrize(
    "nome, mime, esperado",
    [
        ("IMG_001.JPG", "", True),
        ("sem_extensao", "image/heic", True),
        ("IMG_002.HEIC", "", True),
        ("video.mp4", "video/mp4", False),
        ("pasta", "application/vnd.google-apps.folder", False),
        ("foto.jpg", "application/vnd.google-apps.shortcut", False),
        ("doc.txt", "text/plain", False),
    ],
)
def test_eh_imagem(nome, mime, esperado):
    assert drive.eh_imagem(nome, mime) is esperado


@pytest.mark.parametrize(
    "cabecalho, esperado",
    [
        (b"\xff\xd8\xff\xe0", True),
        (b"\x89PNG\r\n\x1a\n", True),
        (b"RIFF\x00\x00\x00\x00WEBP", True),
        (b"\x00\x00\x00\x1cftypheic", True),
        (b"\x00\x00\x00\x18ftypmif1", True),
        (b"\x00\x00\x00\x18ftypisom", False),
        (b"<!doctype html><html>", False),
        (b'{"error": {"code": 403}}', False),
    ],
)
def test_eh_conteudo_imagem_confere_a_assinatura(cabecalho, esperado):
    assert drive.eh_conteudo_imagem(cabecalho + b"\x00" * 300) is esperado


def test_eh_conteudo_imagem_recusa_arquivo_pequeno():
    assert drive.eh_conteudo_imagem(b"\xff\xd8\xff" + b"\x00" * 10) is False


def test_chave_api_vem_da_variavel_de_ambiente_ou_do_arquivo(tmp_path, monkeypatch):
    assert drive.chave_api() == ""
    drive.gravar_chave_api("  chave-do-arquivo  ")
    assert drive.chave_api() == "chave-do-arquivo"
    assert (tmp_path / "config" / "drive.json").is_file()

    monkeypatch.setenv(drive.VARIAVEL_CHAVE, "chave-da-variavel")
    assert drive.chave_api() == "chave-da-variavel"


def test_sem_chave_nao_chama_a_api(monkeypatch):
    def proibido(*_a, **_k):
        raise AssertionError("não deveria acessar a rede")

    monkeypatch.setattr(drive.urllib.request, "urlopen", proibido)
    assert drive._requisitar_api_v3("files", {"q": "x"}) is None


@pytest.mark.parametrize(
    "codigo, corpo, trecho, recusa",
    [
        (400, {"error": {"code": 400, "message": "API key not valid. Please pass a valid API key.",
                         "errors": [{"reason": "badRequest"}],
                         "details": [{"reason": "API_KEY_INVALID"}]}}, "chave da API inválida", True),
        (400, {"error": {"code": 400, "message": "Bad key", "errors": [{"reason": "keyInvalid"}]}},
         "chave da API inválida", True),
        (403, {"error": {"code": 403, "message": "Drive API disabled", "errors": [{"reason": "accessNotConfigured"}]}},
         "desativada no projeto do Google Cloud", True),
        (403, {"error": {"code": 403, "message": "", "errors": [{"reason": "dailyLimitExceeded"}]}},
         "cota diária", True),
        (403, {"error": {"code": 403, "message": "", "errors": [{"reason": "forbidden"}],
                         "details": [{"reason": "API_KEY_HTTP_REFERRER_BLOCKED"}]}}, "restrições", True),
        (404, {"error": {"code": 404, "message": "File not found", "errors": [{"reason": "notFound"}]}},
         "sem permissão", False),
        (403, {"error": {"code": 403, "message": "", "errors": [{"reason": "cannotDownloadFile"}]}},
         "bloqueou o download", False),
    ],
)
def test_erro_api_interpreta_o_motivo_do_json(codigo, corpo, trecho, recusa):
    motivo, mensagem = drive._motivo_erro_json(json.dumps(corpo).encode())
    erro = drive.ErroApiDrive(codigo, motivo, mensagem)
    assert trecho in erro.descricao
    assert erro.recusa_chave is recusa
    assert not erro.transitorio


def test_erro_api_429_e_5xx_sao_transitorios():
    assert drive.ErroApiDrive(429, "rateLimitExceeded").transitorio
    assert drive.ErroApiDrive(403, "userRateLimitExceeded").transitorio
    assert drive.ErroApiDrive(503).transitorio
    assert drive.ErroApiDrive(0, "rede", "tempo esgotado").transitorio


def _pasta_com_fotos(servidor, quantidade=3, pid="pastaRaiz00000000"):
    servidor.pasta(pid, "Casamento")
    for i in range(quantidade):
        servidor.arquivo(_id("foto", i), "foto_%03d.jpg" % i, pid, jpeg((i * 40 % 256, 10, 10)))
    return pid


@pytest.mark.parametrize(
    "erro_chave, trecho",
    [("keyInvalid", "chave da API inválida"), ("accessNotConfigured", "desativada no projeto do Google Cloud")],
)
def test_chave_recusada_avisa_o_motivo_e_marca_listagem_limitada(servidor, erro_chave, trecho):
    pid = _pasta_com_fotos(servidor)
    servidor.erro_chave = erro_chave
    lst = drive.listar(pid, chave=CHAVE)
    assert trecho in lst.aviso_api
    assert lst.chave_recusada and lst.limitada and not lst.via_api
    assert len(lst.itens) == 3
    assert lst.titulo == "Casamento"
    assert [c for c, _q, _h in servidor.requisicoes].count("/drive/v3/files") == 0


def test_sem_chave_listagem_fica_marcada_como_limitada(servidor):
    pid = _pasta_com_fotos(servidor)
    lst = drive.listar(pid, chave="")
    assert lst.limitada and lst.aviso_api == ""
    assert len(lst.itens) == 3
    assert lst.pastas_limitadas == [("Casamento", 3)]


def test_listagem_paginada_traz_todas_as_fotos(servidor):
    pid = servidor.pasta("pastaGrande000000", "Formatura")
    for i in range(2500):
        servidor.arquivo(_id("img", i), "IMG_%04d.jpg" % i, pid)
    servidor.arquivo("naLixeira00000000", "apagada.jpg", pid, lixeira=True)
    lst = drive.listar(pid, chave=CHAVE)
    assert lst.via_api and not lst.limitada and not lst.aviso_api
    assert len(lst.itens) == 2500
    assert len({i.id for i in lst.itens}) == 2500
    listas = [q for c, q, _h in servidor.requisicoes if c == "/drive/v3/files"]
    assert len(listas) == 3
    assert sum(1 for q in listas if q.get("pageToken")) == 2
    for q in listas:
        assert "trashed = false" in q["q"]
        assert q["supportsAllDrives"] == "true" and q["includeItemsFromAllDrives"] == "true"
        assert q["pageSize"] == "1000"


def test_selecao_por_atalhos_vai_para_a_pasta_do_evento_pela_internet(servidor):
    aluna = servidor.pasta("aluna000000000000", "Fulana")
    studio = servidor.pasta("studio00000000000", "STUDIO", aluna)
    colacao = servidor.pasta("colacao0000000000", "COLAÇÃO", aluna)
    servidor.arquivo("fotoStudio0000000", "IMG_1.JPG", studio, jpeg())
    servidor.arquivo("fotoColacao000000", "IMG_2.JPG", colacao, jpeg())
    sel = servidor.pasta("selecao0000000000", "Seleções 2026", aluna)
    a20 = servidor.pasta("album2000000000000", "Álbum 20x20", sel)
    a24 = servidor.pasta("album2400000000000", "Álbum 24x34", sel)
    servidor.atalho("at1000000000000000", "Atalho (1)", a20, "fotoStudio0000000", "image/jpeg")
    servidor.atalho("at2000000000000000", "Atalho (2)", a24, "fotoStudio0000000", "image/jpeg")
    servidor.atalho("at3000000000000000", "Atalho (3)", a24, "fotoColacao000000", "image/jpeg")

    lst = drive.listar(sel, chave=CHAVE)
    assert sorted((i.id, i.subpasta) for i in lst.itens) == [
        ("fotoColacao000000", "Álbum 24x34/COLAÇÃO"),
        ("fotoStudio0000000", "Álbum 20x20/STUDIO"),
        ("fotoStudio0000000", "Álbum 24x34/STUDIO"),  # a mesma foto nos dois tamanhos vale nos dois
    ]


def test_login_do_google_lista_pasta_privada_e_baixa_sem_chave(servidor, tmp_path, monkeypatch):
    monkeypatch.setattr(drive, "_autorizacao_oauth", lambda renovar=False: "Bearer " + TOKEN_LOGIN)
    aluna = servidor.pasta("alunaPriv00000000", "Fulana")
    colacao = servidor.pasta("colacaoPriv000000", "COLAÇÃO", aluna)
    servidor.arquivo("fotoPriv000000000", "IMG_9.JPG", colacao, jpeg())
    sel = servidor.pasta("selPriv0000000000", "Seleções", aluna)
    alb = servidor.pasta("albPriv0000000000", "Álbum 20x20", sel)
    servidor.atalho("atPriv00000000000", "Atalho (1)", alb, "fotoPriv000000000", "image/jpeg")

    lst = drive.listar(sel, chave=drive.CHAVE_OAUTH)
    assert lst.via_api and not lst.limitada
    # pasta com um único "Álbum 20x20" dentro: ele vira a pasta principal, e a foto vai para o evento
    assert lst.titulo == "Álbum 20x20"
    assert [(i.id, i.subpasta) for i in lst.itens] == [("fotoPriv000000000", "COLAÇÃO")]
    assert all("key" not in q for c, q, _h in servidor.requisicoes if c.startswith("/drive/v3"))

    sessao = drive.Sessao(chave=drive.CHAVE_OAUTH)
    res = drive.baixar_lote(drive.planejar_destinos(lst.itens, tmp_path), sessao=sessao)
    assert res[0].ok and "/drive/v3/files/fotoPriv000000000" in res[0].url_usada
    assert res[0].caminho.parent.name == "COLAÇÃO"
    assert res[0].caminho.name == "IMG_9.JPG"  # nome original, não "Atalho (1)"


def test_credencial_do_google_dentro_da_pasta_do_programa_e_ignorada(tmp_path, monkeypatch):
    import google_login
    dentro = google_login.PASTA_PROGRAMA / "tests" / "_credencial_teste.json"
    dados = json.dumps({"installed": {"client_id": "x", "client_secret": "y", "auth_uri": "a", "token_uri": "t"}})
    try:
        dentro.write_text(dados, encoding="utf-8")
        monkeypatch.setenv(google_login.VARIAVEL_CREDENCIAIS, str(dentro))
        assert google_login.arquivo_credenciais() is None and not google_login.disponivel()
    finally:
        dentro.unlink()
    fora = tmp_path / "credenciais_google_oauth.json"
    fora.write_text(dados, encoding="utf-8")
    monkeypatch.setenv(google_login.VARIAVEL_CREDENCIAIS, str(fora))
    assert google_login.disponivel()
    assert google_login.arquivo_token() == tmp_path / "token_google_drive.json"
    assert drive.credencial_api() == drive.CHAVE_OAUTH


def test_listagem_recursiva_com_atalhos_resource_key_e_nomes_repetidos(servidor, tmp_path):
    raiz = servidor.pasta("raiz0000000000000", "Evento")
    servidor.arquivo("fotoRaiz000000000", "a.jpg", raiz, jpeg())
    sub = servidor.pasta("sub00000000000000", "Cerimônia", raiz, rk="rk-sub")
    servidor.arquivo("fotoSub0000000000", "a.jpg", sub, jpeg())
    servidor.arquivo("videoSub000000000", "filme.mp4", sub, b"x" * 400, mime="video/mp4")
    servidor.arquivo("alvoArquivo000000", "real.jpg", None, jpeg(), rk="rk-alvo")
    servidor.atalho("atalhoArq00000000", "atalho.jpg", raiz, "alvoArquivo000000", "image/jpeg", "rk-alvo")
    outra = servidor.pasta("outraPasta0000000", "Festa", None)
    servidor.arquivo("fotoFesta00000000", "b.jpg", outra, jpeg())
    servidor.atalho("atalhoPasta000000", "Festa", raiz, outra, "application/vnd.google-apps.folder")

    lst = drive.listar(raiz, chave=CHAVE)
    assert not lst.limitada, lst.aviso_api
    por_id = {i.id: i for i in lst.itens}
    assert set(por_id) == {"fotoRaiz000000000", "fotoSub0000000000", "alvoArquivo000000", "fotoFesta00000000"}
    assert por_id["alvoArquivo000000"].resource_key == "rk-alvo"
    assert por_id["fotoSub0000000000"].subpasta == "Cerimônia"
    assert por_id["fotoFesta00000000"].subpasta == "Festa"
    assert por_id["fotoRaiz000000000"].tamanho == len(jpeg())
    cab_sub = [h for c, q, h in servidor.requisicoes if c == "/drive/v3/files" and "sub0000" in q["q"]]
    assert cab_sub and cab_sub[0]["x-goog-drive-resource-keys"] == "sub00000000000000/rk-sub"

    tarefas = drive.planejar_destinos(lst.itens, tmp_path)
    destinos = {item.id: d for item, d in tarefas}
    assert destinos["fotoRaiz000000000"] == tmp_path / "a.jpg"
    assert destinos["fotoSub0000000000"] == tmp_path / "Cerimônia" / "a.jpg"
    assert len(set(destinos.values())) == 4


def test_planejar_destinos_nao_repete_nomes_e_ajusta_extensao(tmp_path):
    itens = [
        drive.ItemDrive("1" * 20, "IMG.HEIC"),
        drive.ItemDrive("2" * 20, "IMG.jpg"),
        drive.ItemDrive("3" * 20, "img.png"),
        drive.ItemDrive("4" * 20, ""),
        drive.ItemDrive("5" * 20, 'a:b?.jpg'),
    ]
    nomes = [d.name for _i, d in drive.planejar_destinos(itens, tmp_path)]
    assert nomes == ["IMG.jpg", "IMG_2.jpg", "img_3.png", "foto_004.jpg", "a_b_.jpg"]


def test_download_envia_resource_key_e_nao_expoe_a_chave(servidor, tmp_path):
    dados = jpeg()
    servidor.arquivo("arqComRk000000000", "x.jpg", None, dados, rk="rk-1")
    item = drive.ItemDrive("arqComRk000000000", "x.jpg", "rk-1", "image/jpeg", len(dados))
    res = drive.baixar_item(item, tmp_path / "x.jpg", sessao=drive.Sessao(chave=CHAVE))
    assert res.ok and not res.reduzida and res.bytes == len(dados)
    assert res.caminho.read_bytes() == dados
    assert "/drive/v3/files/arqComRk000000000" in res.url_usada
    assert CHAVE not in res.url_usada
    cabecalhos = [h for c, q, h in servidor.requisicoes if q.get("alt") == "media"]
    assert cabecalhos[-1]["x-goog-drive-resource-keys"] == "arqComRk000000000/rk-1"

    sem_chave = drive.baixar_item(item, tmp_path / "y.jpg", sessao=drive.Sessao(chave=""))
    assert sem_chave.ok and "resourcekey=rk-1" in sem_chave.url_usada


def test_heic_e_convertido_para_jpg(servidor, tmp_path):
    pillow_heif = pytest.importorskip("pillow_heif")
    pillow_heif.register_heif_opener()
    saida = io.BytesIO()
    Image.effect_noise((60, 40), 64).convert("RGB").save(saida, "HEIF")
    heic = saida.getvalue()
    servidor.arquivo("arqHeic0000000000", "IMG_0001.HEIC", None, heic, mime="image/heic")
    item = drive.ItemDrive("arqHeic0000000000", "IMG_0001.HEIC", None, "image/heic", len(heic))
    destino = drive.planejar_destinos([item], tmp_path)[0][1]
    assert destino.name == "IMG_0001.jpg"
    res = drive.baixar_item(item, destino, sessao=drive.Sessao(chave=CHAVE))
    assert res.ok, res.motivo
    assert res.caminho == destino
    with Image.open(res.caminho) as im:
        assert im.format == "JPEG" and im.size == (60, 40)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["IMG_0001.jpg"]


def test_heic_sem_pillow_heif_informa_o_motivo(servidor, tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "pillow_heif", None)
    heic = b"\x00\x00\x00\x1cftypheic" + b"\x00" * 500
    servidor.arquivo("arqHeic1111111111", "IMG_0002.HEIC", None, heic, mime="image/heic")
    res = drive.baixar_item("arqHeic1111111111", tmp_path / "IMG_0002.jpg", sessao=drive.Sessao(chave=CHAVE))
    assert not res.ok
    assert res.motivo == drive.MOTIVO_HEIC_SEM_LIB
    assert list(tmp_path.iterdir()) == []


def test_extensao_segue_o_conteudo(servidor, tmp_path):
    saida = io.BytesIO()
    Image.effect_noise((40, 40), 64).convert("RGB").save(saida, "PNG")
    servidor.arquivo("arqPng00000000000", "grafico.jpg", None, saida.getvalue())
    res = drive.baixar_item("arqPng00000000000", tmp_path / "grafico.jpg", sessao=drive.Sessao(chave=CHAVE))
    assert res.ok and res.caminho == tmp_path / "grafico.png"


def test_429_respeita_retry_after_reduz_conexoes_e_depois_baixa(servidor, tmp_path):
    dados = jpeg()
    no = servidor.arquivo("arq42900000000000", "x.jpg", None, dados, so_api=True, falhas_429=1, retry_after="1")
    sessao = drive.Sessao(chave=CHAVE, maximo=8)
    inicio = time.monotonic()
    res = drive.baixar_item(drive.ItemDrive("arq42900000000000", "x.jpg", tamanho=len(dados)), tmp_path / "x.jpg", sessao=sessao)
    duracao = time.monotonic() - inicio
    assert res.ok, res.motivo
    assert 0.9 <= duracao < 2.5
    assert sessao.total_429 == 1 and sessao.limite == 4
    assert no["contagem"]["api"] == 2


def test_tamanho_diferente_do_drive_tenta_de_novo(servidor, tmp_path):
    dados = jpeg()
    no = servidor.arquivo("arqTamanho0000000", "x.jpg", None, dados, so_api=True, tamanho_errado=1)
    item = drive.ItemDrive("arqTamanho0000000", "x.jpg", tamanho=len(dados))
    res = drive.baixar_item(item, tmp_path / "x.jpg", sessao=drive.Sessao(chave=CHAVE))
    assert res.ok, res.motivo
    assert no["contagem"]["api"] == 2
    assert (tmp_path / "x.jpg").read_bytes() == dados


def test_md5_diferente_do_drive_tenta_de_novo(servidor, tmp_path):
    dados = jpeg()
    no = servidor.arquivo("arqMd500000000000", "x.jpg", None, dados, so_api=True, tamanho_errado=1)
    item = drive.ItemDrive("arqMd500000000000", "x.jpg", md5=hashlib.md5(dados).hexdigest())
    res = drive.baixar_item(item, tmp_path / "x.jpg", sessao=drive.Sessao(chave=CHAVE))
    assert res.ok and no["contagem"]["api"] == 2


def test_download_em_blocos_grava_parcial_e_troca_no_fim(servidor, tmp_path, monkeypatch):
    dados = jpeg(tamanho=(800, 600)) + b"\x00" * 20000
    servidor.arquivo("arqBlocos00000000", "x.jpg", None, dados, pedacos=5, pausa=0.02)
    monkeypatch.setattr(drive, "TAMANHO_BLOCO", 4096)
    trocas = []
    replace_original = drive.os.replace

    def espiao(origem, destino):
        trocas.append((Path(origem).name, Path(destino).name, Path(origem).stat().st_size))
        return replace_original(origem, destino)

    monkeypatch.setattr(drive.os, "replace", espiao)
    res = drive.baixar_item("arqBlocos00000000", tmp_path / "x.jpg", sessao=drive.Sessao(chave=CHAVE))
    assert res.ok
    assert trocas == [("x.jpg.parcial", "x.jpg", len(dados))]
    assert (tmp_path / "x.jpg").read_bytes() == dados
    assert not (tmp_path / "x.jpg.parcial").exists()


def test_pagina_de_verificacao_de_virus_e_confirmada(servidor, tmp_path):
    dados = jpeg()
    no = servidor.arquivo("arqVirus000000000", "grande.jpg", None, dados, virus=True)
    res = drive.baixar_item("arqVirus000000000", tmp_path / "grande.jpg", sessao=drive.Sessao(chave=""))
    assert res.ok, res.motivo
    assert "uuid=uuid-123" in res.url_usada
    assert no["contagem"]["usercontent"] == 2
    assert res.caminho.read_bytes() == dados


def test_downloads_paralelos_sao_mais_rapidos_que_sequenciais(servidor, tmp_path):
    atraso, quantidade = 0.25, 12
    itens = []
    for i in range(quantidade):
        dados = jpeg((i * 20, 0, 0))
        servidor.arquivo(_id("par", i), "p%02d.jpg" % i, None, dados, so_api=True, atraso=atraso)
        itens.append(drive.ItemDrive(_id("par", i), "p%02d.jpg" % i, tamanho=len(dados)))
    tarefas = drive.planejar_destinos(itens, tmp_path)
    inicio = time.monotonic()
    resultados = drive.baixar_lote(tarefas, sessao=drive.Sessao(chave=CHAVE, maximo=8))
    duracao = time.monotonic() - inicio
    assert all(r.ok for r in resultados)
    assert duracao < atraso * quantidade * 0.5


def test_relatorio_lista_faltantes_com_motivo_e_reduzidas(servidor, tmp_path):
    import janela_drive

    servidor.arquivo("arqOk000000000000", "ok.jpg", None, jpeg())
    servidor.arquivo("arqSumiu000000000", "sumiu.jpg", None, jpeg(), indisponivel=True)
    servidor.arquivo("arqMini0000000000", "mini.jpg", None, jpeg((5, 5, 5), (32, 24)), so_miniatura=True)
    itens = [drive.ItemDrive(i, n) for i, n in
             (("arqOk000000000000", "ok.jpg"), ("arqSumiu000000000", "sumiu.jpg"), ("arqMini0000000000", "mini.jpg"))]
    tarefas = drive.planejar_destinos(itens, tmp_path)
    resultados = drive.baixar_lote(tarefas, sessao=drive.Sessao(chave=CHAVE))
    rel = drive.Relatorio(tarefas, resultados)
    assert rel.total == 3
    assert [p.name for p in rel.baixadas] == ["ok.jpg", "mini.jpg"]
    assert [(i.nome, r.reduzida) for i, r in rel.reduzidas] == [("mini.jpg", True)]
    faltando = rel.faltando
    assert [i.nome for i, _d, _m in faltando] == ["sumiu.jpg"]
    assert "não encontrado" in faltando[0][2]
    texto = "\n".join(t for t, _tag in janela_drive.linhas_relatorio(rel))
    assert "sumiu.jpg" in texto and "não encontrado" in texto
    assert "mini.jpg" in texto and "resolução reduzida" in texto
    assert janela_drive.precisa_relatorio(rel)


def test_tentar_novamente_baixa_so_as_que_faltaram(servidor, tmp_path):
    servidor.arquivo("arqA0000000000000", "a.jpg", None, jpeg())
    no_b = servidor.arquivo("arqB0000000000000", "b.jpg", None, jpeg(), indisponivel=True)
    servidor.arquivo("arqC0000000000000", "c.jpg", None, jpeg())
    itens = [drive.ItemDrive(i, n) for i, n in
             (("arqA0000000000000", "a.jpg"), ("arqB0000000000000", "b.jpg"), ("arqC0000000000000", "c.jpg"))]
    tarefas = drive.planejar_destinos(itens, tmp_path)
    sessao = drive.Sessao(chave=CHAVE)
    rel = drive.Relatorio(tarefas, drive.baixar_lote(tarefas, sessao=sessao), sessao=sessao)
    assert [i.nome for i, _d, _m in rel.faltando] == ["b.jpg"]

    no_b["indisponivel"] = False
    servidor.requisicoes.clear()
    novos = drive.tentar_novamente(rel)
    assert novos == [tmp_path / "b.jpg"]
    assert servidor.ids_pedidos() == {"arqB0000000000000"}
    assert rel.faltando == [] and len(rel.baixadas) == 3


def test_preparar_e_baixar_com_api_desativada_baixa_pelas_urls_publicas(servidor, tmp_path, monkeypatch):
    import janela_drive

    pid = _pasta_com_fotos(servidor, 4)
    servidor.erro_chave = "accessNotConfigured"
    monkeypatch.setenv(drive.VARIAVEL_CHAVE, CHAVE)
    avisos = []
    rel = janela_drive.preparar_e_baixar("pasta", pid, None, tmp_path, aviso=avisos.append)
    assert rel.limitada and "desativada" in rel.aviso_api
    assert len(rel.baixadas) == 4 and not rel.faltando
    assert all(p.parent == tmp_path / "Casamento" for p in rel.baixadas)
    assert not any(q.get("alt") == "media" for _c, q, _h in servidor.requisicoes)
    assert any("desativada" in a for a in avisos) and any("~50" in a for a in avisos)
    assert "~50 fotos por pasta" in janela_drive.texto_aviso(rel)


def test_cancelar_interrompe_o_lote(servidor, tmp_path):
    itens = []
    for i in range(6):
        servidor.arquivo(_id("can", i), "c%d.jpg" % i, None, jpeg(), so_api=True, atraso=0.3)
        itens.append(drive.ItemDrive(_id("can", i), "c%d.jpg" % i))
    tarefas = drive.planejar_destinos(itens, tmp_path)
    parar = threading.Event()
    threading.Timer(0.1, parar.set).start()
    inicio = time.monotonic()
    resultados = drive.baixar_lote(tarefas, parar=parar.is_set, sessao=drive.Sessao(chave=CHAVE, maximo=2))
    assert time.monotonic() - inicio < 1.5
    assert any(r.motivo == "cancelado" for r in resultados)
    assert not list(tmp_path.glob("*.parcial"))


def test_janela_de_relatorio_constroi_e_tenta_de_novo(tmp_path, monkeypatch):
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("Tk indisponível")
    root.withdraw()
    try:
        import janela_drive
        import tema

        tema.configurar(root)
        itens = [drive.ItemDrive("idOk0000000000000", "a.jpg"), drive.ItemDrive("idFalta0000000000", "b.jpg", subpasta="Sub")]
        tarefas = drive.planejar_destinos(itens, tmp_path)
        tarefas[0][1].write_bytes(jpeg())
        rel = drive.Relatorio(
            tarefas,
            [drive.ResultadoDownload(True, caminho=tarefas[0][1]), drive.ResultadoDownload(False, "sem permissão de acesso")],
            limitada=True,
            aviso_api="A API do Google Drive recusou a listagem: chave da API inválida.",
            pastas_limitadas=[("Evento", 50)],
        )
        adicionados = []
        registros = []

        def executar(_titulo, trabalho, fim):
            t = SimpleNamespace(parar=lambda: False, progresso=lambda n, total: None, aviso=lambda s: None)
            fim(trabalho(t), None)
            return True

        app = SimpleNamespace(
            root=root,
            arranjo={},
            registrar=registros.append,
            executar=executar,
            adicionar_caminhos=lambda caminhos, perguntar_diagramar=False: adicionados.append((caminhos, perguntar_diagramar)),
        )

        def tentar_falso(relatorio, parar=None, progresso=None, aviso=None):
            tarefas[1][1].write_bytes(jpeg())
            relatorio.resultados[1] = drive.ResultadoDownload(True, caminho=tarefas[1][1])
            return [tarefas[1][1]]

        monkeypatch.setattr(drive, "tentar_novamente", tentar_falso)
        janela = janela_drive.abrir_relatorio(app, rel)
        root.update_idletasks()
        assert janela.winfo_exists()
        assert janela.botao_tentar.instate(["!disabled"])
        janela.botao_tentar.invoke()
        assert adicionados == [([str(tarefas[1][1])], False)]
        assert janela.botao_tentar.instate(["disabled"])
        assert any("mais 1 foto" in r for r in registros)
        janela.destroy()
    finally:
        root.destroy()
