# -*- coding: utf-8 -*-
"""
Download de fotos de links públicos do Google Drive ("Qualquer pessoa com o link").

Com uma chave da API do Google (Drive API v3) a listagem é paginada e traz todas
as fotos da pasta. Sem chave, ou se a API recusar a chave, a listagem usa a página
HTML pública da pasta, que o Google limita a cerca de 50 itens por pasta; nesse caso
a Listagem sai marcada como limitada e com o motivo da recusa.

A chave vem da variável de ambiente SMART_ALBUM_DRIVE_CHAVE_API ou de
%LOCALAPPDATA%\\smart_album\\drive.json ({"chave_api": "..."}).
"""

import concurrent.futures
import email.utils
import hashlib
import html
import http.client
import json
import os
import random
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from comum import EXTENSOES, gravar_json, ler_json, pasta_dados

VARIAVEL_CHAVE = "SMART_ALBUM_DRIVE_CHAVE_API"

URL_API = "https://www.googleapis.com/drive/v3"
URL_DRIVE = "https://drive.google.com"
URL_USERCONTENT = "https://drive.usercontent.google.com"
URL_LH3 = "https://lh3.googleusercontent.com"

TIMEOUT_LEITURA = 15
PRAZO_ARQUIVO = 180
RODADAS = 3
TAMANHO_BLOCO = 256 * 1024
LIMITE_PAGINA_HTML = 2 * 1024 * 1024
BACKOFF_BASE = 1.0
ESPERA_MAXIMA = 60.0
TRABALHADORES_COM_CHAVE = 8
TRABALHADORES_SEM_CHAVE = 4
MAX_PAGINAS = 1000
MAX_NIVEL = 20
LIMITE_PASTA_PUBLICA = 50

MOTIVO_HEIC_SEM_LIB = "formato HEIC (instale pillow-heif)"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "Origin": "https://drive.google.com",
    "Referer": "https://drive.google.com/",
}

_CAMPOS_ITEM = "id,name,mimeType,resourceKey,size,md5Checksum,shortcutDetails(targetId,targetMimeType,targetResourceKey)"
_CAMPOS_LISTA = "nextPageToken,files(%s)" % _CAMPOS_ITEM

_MIMES_IGNORADOS = (
    "application/vnd.google-apps.folder",
    "application/vnd.google-apps.shortcut",
    "application/vnd.google-apps.document",
    "application/vnd.google-apps.spreadsheet",
    "application/vnd.google-apps.presentation",
    "application/vnd.google-apps.form",
    "application/pdf",
    "video/mp4",
    "video/quicktime",
)
_MIME_PASTA = "application/vnd.google-apps.folder"
_MIME_ATALHO = "application/vnd.google-apps.shortcut"

_MOTIVOS_CHAVE_INVALIDA = {"keyInvalid", "keyExpired", "API_KEY_INVALID", "API_KEY_EXPIRED"}
_MOTIVOS_API_DESATIVADA = {"accessNotConfigured", "SERVICE_DISABLED"}
_MOTIVOS_CHAVE_RESTRITA = {
    "ipRefererBlocked",
    "API_KEY_HTTP_REFERRER_BLOCKED",
    "API_KEY_IP_ADDRESS_BLOCKED",
    "API_KEY_ANDROID_APP_BLOCKED",
    "API_KEY_IOS_APP_BLOCKED",
    "API_KEY_SERVICE_BLOCKED",
}
_MOTIVOS_COTA_PROJETO = {"dailyLimitExceeded", "dailyLimitExceededUnreg", "quotaExceeded"}
_MOTIVOS_RITMO = {"rateLimitExceeded", "userRateLimitExceeded", "RATE_LIMIT_EXCEEDED", "backendError", "internalError"}

_MARCAS_HEIF = {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"hevm", b"hevs", b"mif1", b"msf1"}
_SUFIXOS_TIPO = {
    "jpg": (".jpg", ".jpeg", ".jfif"),
    "png": (".png",),
    "webp": (".webp",),
    "tif": (".tif", ".tiff"),
    "bmp": (".bmp",),
    "heic": (".jpg", ".jpeg"),
    "gif": (".png",),
}


def _arquivo_config():
    return pasta_dados() / "drive.json"


def chave_api():
    chave = (os.environ.get(VARIAVEL_CHAVE) or "").strip()
    if chave:
        return chave
    dados = ler_json(_arquivo_config(), {}) or {}
    return str(dados.get("chave_api") or "").strip()


def gravar_chave_api(chave):
    dados = ler_json(_arquivo_config(), {}) or {}
    dados["chave_api"] = str(chave or "").strip()
    gravar_json(_arquivo_config(), dados)


def extrair_id(url):
    """Devolve (tipo, id, resourcekey); tipo é "pasta", "arquivo" ou None."""
    url = str(url or "").strip()
    parsed = urllib.parse.urlparse(url)
    qs = urllib.parse.parse_qs(parsed.query)
    resourcekey = qs.get("resourcekey", [None])[0]
    m_pasta = re.search(r"/folders/([a-zA-Z0-9_-]{10,})", url)
    if m_pasta:
        return "pasta", m_pasta.group(1), resourcekey
    m_arq = re.search(r"/file/d/([a-zA-Z0-9_-]{10,})", url)
    if m_arq:
        return "arquivo", m_arq.group(1), resourcekey
    if "id" in qs and qs["id"]:
        return "pasta", qs["id"][0], resourcekey
    if re.fullmatch(r"[a-zA-Z0-9_-]{18,}", url):
        return "pasta", url, None
    return None, None, None


def limpar_nome(nome):
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(nome or "")).strip().rstrip(". ")


@dataclass
class ItemDrive:
    id: str
    nome: str = ""
    resource_key: str = None
    mime: str = ""
    tamanho: int = None
    md5: str = None
    subpasta: str = ""


@dataclass
class Listagem:
    itens: list = field(default_factory=list)
    titulo: str = None
    via_api: bool = False
    limitada: bool = False
    chave_recusada: bool = False
    aviso_api: str = ""
    pastas_limitadas: list = field(default_factory=list)


@dataclass
class ResultadoDownload:
    ok: bool
    motivo: str = ""
    url_usada: str = ""
    reduzida: bool = False
    bytes: int = 0
    caminho: Path = None

    def __bool__(self):
        return self.ok


class ErroApiDrive(Exception):
    """Erro da API do Drive com o código HTTP e o motivo (error.errors[0].reason ou details[].reason)."""

    def __init__(self, codigo, motivo="", mensagem="", retry_after=None):
        self.codigo = int(codigo or 0)
        self.motivo = str(motivo or "")
        self.mensagem = str(mensagem or "")
        self.retry_after = retry_after
        super().__init__(self.descricao)

    def _chave_invalida(self):
        return self.motivo in _MOTIVOS_CHAVE_INVALIDA or (self.codigo == 400 and "api key" in self.mensagem.lower())

    @property
    def recusa_chave(self):
        """A chave não serve para nenhuma pasta: inválida, API desativada, restrita ou sem cota diária."""
        return (
            self._chave_invalida()
            or self.motivo in _MOTIVOS_API_DESATIVADA
            or self.motivo in _MOTIVOS_CHAVE_RESTRITA
            or self.motivo in _MOTIVOS_COTA_PROJETO
        )

    @property
    def transitorio(self):
        return self.codigo in (0, 429, 500, 502, 503, 504) or self.motivo in _MOTIVOS_RITMO

    @property
    def descricao(self):
        m = self.motivo
        if self._chave_invalida():
            return "chave da API inválida"
        if m in _MOTIVOS_API_DESATIVADA:
            return "a API do Google Drive está desativada no projeto do Google Cloud da chave"
        if m in _MOTIVOS_CHAVE_RESTRITA:
            return "a chave tem restrições (site, IP ou API) que bloqueiam este programa"
        if m in _MOTIVOS_COTA_PROJETO:
            return "cota diária da API do Google excedida"
        if m == "downloadQuotaExceeded":
            return "cota de download do arquivo excedida no Google"
        if m == "cannotDownloadFile":
            return "o dono do arquivo bloqueou o download"
        if self.codigo == 429 or m in _MOTIVOS_RITMO:
            return "limite de requisições do Google (HTTP %d)" % (self.codigo or 429)
        if self.codigo == 404 or m == "notFound":
            return "não encontrado ou sem permissão (precisa estar como 'Qualquer pessoa com o link')"
        if self.codigo in (401, 403):
            return "sem permissão de acesso (precisa estar como 'Qualquer pessoa com o link')"
        if self.codigo == 0:
            return self.mensagem or "falha de rede"
        return "erro %d do Google%s" % (self.codigo, (": " + self.mensagem[:160]) if self.mensagem else "")


def _motivo_erro_json(corpo):
    try:
        dados = json.loads(corpo.decode("utf-8", errors="ignore"))
    except (ValueError, AttributeError):
        return "", ""
    erro = dados.get("error") if isinstance(dados, dict) else None
    if not isinstance(erro, dict):
        return "", str(erro or "")
    motivo = ""
    erros = erro.get("errors") or []
    if erros and isinstance(erros[0], dict):
        motivo = str(erros[0].get("reason") or "")
    for det in erro.get("details") or []:
        if isinstance(det, dict) and det.get("reason") and motivo in ("", "badRequest", "forbidden"):
            motivo = str(det["reason"])
    return motivo, str(erro.get("message") or "")


def _retry_after(headers):
    valor = (headers.get("Retry-After") if headers else None) or ""
    valor = valor.strip()
    if not valor:
        return None
    if valor.isdigit():
        return float(valor)
    try:
        data = email.utils.parsedate_to_datetime(valor)
        return max(0.0, data.timestamp() - time.time())
    except (TypeError, ValueError):
        return None


def _erro_de_http(e):
    try:
        corpo = e.read(65536) or b""
    except Exception:
        corpo = b""
    motivo, mensagem = _motivo_erro_json(corpo)
    return ErroApiDrive(e.code, motivo, mensagem, _retry_after(e.headers))


def _motivo_rede(e):
    razao = getattr(e, "reason", e)
    if isinstance(razao, TimeoutError) or "timed out" in str(razao).lower():
        return "tempo esgotado na conexão"
    return "falha de rede (%s)" % razao


def _espera(tentativa, retry_after=None):
    if retry_after:
        return min(ESPERA_MAXIMA, float(retry_after)) + random.uniform(0, 0.1 * BACKOFF_BASE)
    return min(ESPERA_MAXIMA, BACKOFF_BASE * (2 ** tentativa)) * random.uniform(0.5, 1.0)


def _dormir(segundos, parar=None, prazo=None):
    """Dorme em fatias curtas; devolve False se cancelado ou se o prazo acabou."""
    fim = time.monotonic() + max(0.0, segundos)
    if prazo is not None and fim > prazo:
        return False
    while True:
        if parar and parar():
            return False
        resta = fim - time.monotonic()
        if resta <= 0:
            return True
        time.sleep(min(0.2, resta))


def _cabecalho_rk(file_id, resourcekey):
    return "%s/%s" % (file_id, resourcekey) if resourcekey else None


def _sem_chave(url):
    return re.sub(r"([?&])key=[^&]*&?", r"\1", url).rstrip("?&")


def _abrir(url, headers, timeout=None):
    req = urllib.request.Request(url, headers=headers)
    return urllib.request.urlopen(req, timeout=timeout or TIMEOUT_LEITURA)


def _ler_url(url, limite=8 * 1024 * 1024):
    with _abrir(url, _HEADERS) as resp:
        return resp.read(limite)


def _decodificar_html(raw_html):
    s = re.sub(r"\\x([0-9a-fA-F]{2})", lambda m: chr(int(m.group(1), 16)), raw_html)
    s = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), s)
    return s.replace("\\/", "/")


def _requisitar_api_v3(caminho_api, params, resourcekey=None, chave=None, parar=None, tentativas=4):
    """GET na Drive API v3. Sem chave devolve None; erros viram ErroApiDrive (com retentativa nos transitórios)."""
    chave = chave_api() if chave is None else chave
    if not chave:
        return None
    headers = dict(_HEADERS)
    if resourcekey:
        headers["X-Goog-Drive-Resource-Keys"] = resourcekey
    q_params = dict(params)
    q_params["key"] = chave
    url = "%s/%s?%s" % (URL_API, caminho_api.lstrip("/"), urllib.parse.urlencode(q_params))
    ultimo = None
    for tentativa in range(tentativas):
        try:
            with _abrir(url, headers) as resp:
                return json.loads(resp.read().decode("utf-8", errors="ignore"))
        except urllib.error.HTTPError as e:
            ultimo = _erro_de_http(e)
        except ValueError:
            ultimo = ErroApiDrive(0, "resposta", "resposta inválida da API do Google")
        except (urllib.error.URLError, OSError, http.client.HTTPException) as e:
            ultimo = ErroApiDrive(0, "rede", _motivo_rede(e))
        if not ultimo.transitorio or tentativa == tentativas - 1:
            break
        if not _dormir(_espera(tentativa, ultimo.retry_after), parar):
            break
    raise ultimo


def validar_chave(file_id, resourcekey=None, chave=None):
    """Chamada barata (metadados de um item) que confirma se a API aceita a chave. Devolve (metadados, erro)."""
    try:
        meta = _requisitar_api_v3(
            "files/%s" % file_id,
            {"fields": "id,name,mimeType,resourceKey", "supportsAllDrives": "true"},
            resourcekey=_cabecalho_rk(file_id, resourcekey),
            chave=chave,
            tentativas=2,
        )
        return meta, None
    except ErroApiDrive as e:
        return None, e


def eh_imagem(nome, mime):
    mime_l = str(mime or "").lower()
    ext_l = Path(str(nome or "")).suffix.lower()
    if mime_l in _MIMES_IGNORADOS:
        return False
    if mime_l.startswith("video/") or mime_l.startswith("audio/"):
        return False
    if ext_l in EXTENSOES or ext_l in (".heic", ".heif"):
        return True
    if mime_l.startswith("image/"):
        return True
    return False


def _inteiro(valor):
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


def _item_de_meta(meta, resourcekey=None):
    return ItemDrive(
        id=meta["id"],
        nome=(meta.get("name") or "").strip(),
        resource_key=meta.get("resourceKey") or resourcekey,
        mime=meta.get("mimeType") or "",
        tamanho=_inteiro(meta.get("size")),
        md5=meta.get("md5Checksum"),
    )


def resolver_atalho(file_id, resourcekey=None, chave=None):
    """Devolve (id_real, nome, mime) seguindo atalhos do Drive; nome e mime podem ser None."""
    try:
        meta = _requisitar_api_v3(
            "files/%s" % file_id,
            {"fields": "id,name,mimeType,shortcutDetails(targetId,targetMimeType,targetResourceKey)", "supportsAllDrives": "true"},
            resourcekey=_cabecalho_rk(file_id, resourcekey),
            chave=chave,
            tentativas=2,
        )
    except ErroApiDrive:
        meta = None
    if isinstance(meta, dict) and meta.get("id"):
        atalho = meta.get("shortcutDetails") or {}
        if atalho.get("targetId"):
            return atalho["targetId"], meta.get("name"), atalho.get("targetMimeType")
        return meta["id"], meta.get("name"), meta.get("mimeType")

    try:
        raw = _ler_url("%s/file/d/%s/view" % (URL_DRIVE, file_id)).decode("utf-8", errors="ignore")
        m = re.search(
            r'\["([a-zA-Z0-9_-]{15,})",(?:\["[^"]*"\]|null),"([^"]+)","(image/[^"]+)"',
            _decodificar_html(raw),
        )
        if m:
            return m.group(1), m.group(2), m.group(3)
    except Exception:
        pass
    return file_id, None, None


def obter_arquivo(file_id, resourcekey=None, chave=None):
    """Metadados de um arquivo avulso (segue atalhos). Devolve (ItemDrive, ErroApiDrive ou None)."""
    chave = chave_api() if chave is None else chave
    erro = None
    if chave:
        try:
            meta = _requisitar_api_v3(
                "files/%s" % file_id,
                {"fields": _CAMPOS_ITEM, "supportsAllDrives": "true"},
                resourcekey=_cabecalho_rk(file_id, resourcekey),
                chave=chave,
                tentativas=2,
            )
            atalho = meta.get("shortcutDetails") or {}
            if atalho.get("targetId"):
                alvo_rk = atalho.get("targetResourceKey")
                try:
                    alvo = _requisitar_api_v3(
                        "files/%s" % atalho["targetId"],
                        {"fields": _CAMPOS_ITEM, "supportsAllDrives": "true"},
                        resourcekey=_cabecalho_rk(atalho["targetId"], alvo_rk),
                        chave=chave,
                        tentativas=2,
                    )
                    return _item_de_meta(alvo, alvo_rk), None
                except ErroApiDrive:
                    return ItemDrive(atalho["targetId"], meta.get("name") or "", alvo_rk, atalho.get("targetMimeType") or ""), None
            return _item_de_meta(meta, resourcekey), None
        except ErroApiDrive as e:
            erro = e
    real_id, nome, mime = resolver_atalho(file_id, resourcekey, chave="")
    return ItemDrive(real_id, nome or "", resourcekey if real_id == file_id else None, mime or ""), erro


def _classificar_item_api(item, pasta_id, arquivos, subpastas, chave):
    fid = item.get("id")
    if not fid:
        return
    nome = (item.get("name") or "").strip()
    mime = item.get("mimeType") or ""
    if mime == _MIME_PASTA:
        if fid != pasta_id:
            subpastas.append((fid, nome, item.get("resourceKey")))
    elif mime == _MIME_ATALHO:
        atalho = item.get("shortcutDetails") or {}
        alvo_id = atalho.get("targetId")
        alvo_mime = atalho.get("targetMimeType") or ""
        alvo_rk = atalho.get("targetResourceKey")
        if not alvo_id:
            alvo_id, real_nome, alvo_mime = resolver_atalho(fid, item.get("resourceKey"), chave=chave)
            nome = real_nome or nome
            alvo_mime = alvo_mime or ""
        if alvo_mime == _MIME_PASTA:
            if alvo_id != pasta_id:
                subpastas.append((alvo_id, nome, alvo_rk))
        elif alvo_id and eh_imagem(nome, alvo_mime):
            arquivos.append(ItemDrive(alvo_id, nome, alvo_rk, alvo_mime))
    elif eh_imagem(nome, mime):
        arquivos.append(_item_de_meta(item))


def _listar_api(pasta_id, resourcekey, chave, parar, arquivos, subpastas):
    """Lista uma pasta pela API (todas as páginas). Acumula em arquivos/subpastas mesmo se falhar no meio."""
    page_token = None
    for _pagina in range(MAX_PAGINAS):
        params = {
            "q": "'%s' in parents and trashed = false" % pasta_id,
            "fields": _CAMPOS_LISTA,
            "pageSize": "1000",
            "orderBy": "folder,name_natural",
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
        }
        if page_token:
            params["pageToken"] = page_token
        dados = _requisitar_api_v3("files", params, resourcekey=_cabecalho_rk(pasta_id, resourcekey), chave=chave, parar=parar)
        if not isinstance(dados, dict) or "files" not in dados:
            raise ErroApiDrive(0, "resposta", "resposta inesperada da API do Google")
        for item in dados.get("files") or []:
            _classificar_item_api(item, pasta_id, arquivos, subpastas, chave)
        page_token = dados.get("nextPageToken")
        if not page_token or (parar and parar()):
            break


def _listar_html(pasta_id):
    """Página pública da pasta (o Google mostra só ~50 itens). Devolve (arquivos, subpastas, titulo)."""
    arquivos, subpastas, titulo = [], [], None
    ids_vistos = set()
    try:
        raw_html = _ler_url("%s/drive/folders/%s?usp=sharing" % (URL_DRIVE, pasta_id)).decode("utf-8", errors="ignore")
        m_title = re.search(r"<title>([^<]+)</title>", raw_html, re.I)
        if m_title:
            t_limpo = re.sub(r"\s*-\s*Google\s*Drive\s*$", "", html.unescape(m_title.group(1)), flags=re.I).strip()
            if t_limpo and t_limpo.lower() != "google drive":
                titulo = t_limpo
        for fid, nome, mime in re.findall(
            r'\["([a-zA-Z0-9_-]{15,})",(?:\["[^"]*"\]|null),"([^"]+)","([^"]+)"',
            _decodificar_html(raw_html),
        ):
            if fid in ids_vistos or fid == pasta_id:
                continue
            if mime == _MIME_PASTA:
                ids_vistos.add(fid)
                subpastas.append((fid, nome, None))
            elif mime == _MIME_ATALHO:
                ids_vistos.add(fid)
            elif eh_imagem(nome, mime):
                ids_vistos.add(fid)
                arquivos.append(ItemDrive(fid, nome, None, mime))
    except Exception:
        pass

    try:
        html_emb = _ler_url("%s/embeddedfolderview?id=%s#list" % (URL_DRIVE, pasta_id)).decode("utf-8", errors="ignore")
        if not titulo:
            m_t2 = re.search(r"<title>([^<]+)</title>", html_emb, re.I)
            if m_t2 and m_t2.group(1).strip():
                titulo = html.unescape(m_t2.group(1)).strip()
        padrao_entry = re.compile(
            r'id="entry-([a-zA-Z0-9_-]{10,})".*?<a\s+href="([^"]+)".*?class="flip-entry-title">([^<]+)<',
            re.S,
        )
        for m in padrao_entry.finditer(html_emb):
            eid, href, nome = m.group(1), m.group(2), html.unescape(m.group(3)).strip()
            if eid in ids_vistos or eid == pasta_id:
                continue
            ids_vistos.add(eid)
            bloco = m.group(0)
            if "/folders/" in href or "drive-sprite-folder" in bloco:
                subpastas.append((eid, nome, None))
            elif "vnd.google-apps.shortcut" in bloco:
                real_id, real_nome, real_mime = resolver_atalho(eid)
                if real_mime == _MIME_PASTA:
                    subpastas.append((real_id, real_nome or nome, None))
                elif real_id and eh_imagem(real_nome or nome, real_mime or "image/jpeg"):
                    arquivos.append(ItemDrive(real_id, real_nome or nome, None, real_mime or ""))
            elif eh_imagem(nome, ""):
                arquivos.append(ItemDrive(eid, nome))
    except Exception:
        pass
    return arquivos, subpastas, titulo


def listar(pasta_id, resourcekey=None, parar=None, chave=None):
    """Lista recursivamente as fotos da pasta (atalhos, subpastas e drives compartilhados) numa Listagem."""
    chave = chave_api() if chave is None else chave
    lst = Listagem()
    estado = {"usar_api": bool(chave)}
    if chave:
        meta, erro = validar_chave(pasta_id, resourcekey, chave)
        if erro is not None:
            estado["usar_api"] = False
            lst.chave_recusada = erro.recusa_chave
            lst.aviso_api = "A API do Google Drive recusou a listagem: %s." % erro.descricao
        elif isinstance(meta, dict):
            lst.titulo = (meta.get("name") or "").strip() or None
            resourcekey = resourcekey or meta.get("resourceKey")

    visitados = set()
    ids_arquivos = set()

    def visitar(pid, rk, rel, nome_pasta, nivel):
        if pid in visitados or nivel > MAX_NIVEL or (parar and parar()):
            return
        visitados.add(pid)
        arquivos, subpastas = [], []
        listou = False
        if estado["usar_api"]:
            try:
                _listar_api(pid, rk, chave, parar, arquivos, subpastas)
                listou = True
                lst.via_api = True
            except ErroApiDrive as e:
                if not lst.aviso_api:
                    lst.aviso_api = "A API do Google Drive recusou a listagem de '%s': %s." % (nome_pasta or pid, e.descricao)
                if e.recusa_chave:
                    estado["usar_api"] = False
                    lst.chave_recusada = True
        if not listou:
            arq_html, sub_html, titulo_html = _listar_html(pid)
            ids_parciais = {a.id for a in arquivos}
            arquivos.extend(a for a in arq_html if a.id not in ids_parciais)
            ids_sub = {s[0] for s in subpastas}
            subpastas.extend(s for s in sub_html if s[0] not in ids_sub)
            lst.limitada = True
            lst.pastas_limitadas.append((rel or nome_pasta or titulo_html or pid, len(arq_html) + len(sub_html)))
            if nivel == 0 and not lst.titulo and titulo_html:
                lst.titulo = titulo_html

        for item in arquivos:
            if item.id not in ids_arquivos:
                ids_arquivos.add(item.id)
                item.subpasta = rel
                lst.itens.append(item)

        if nivel == 0 and not arquivos and len(subpastas) == 1 and subpastas[0][1]:
            sub_id, sub_nome, sub_rk = subpastas[0]
            lst.titulo = sub_nome.strip()
            visitar(sub_id, sub_rk, rel, sub_nome, nivel + 1)
            return
        for sub_id, sub_nome, sub_rk in subpastas:
            nome_limpo = limpar_nome(sub_nome) or sub_id[:8]
            visitar(sub_id, sub_rk, "%s/%s" % (rel, nome_limpo) if rel else nome_limpo, sub_nome, nivel + 1)

    visitar(pasta_id, resourcekey, "", lst.titulo, 0)
    return lst


def listar_pasta(folder_id, resourcekey=None):
    """Compatibilidade: devolve ([(id, nome), ...], titulo_da_pasta)."""
    lst = listar(folder_id, resourcekey)
    return [(i.id, i.nome) for i in lst.itens], lst.titulo


def tipo_conteudo(dados):
    """Formato pela assinatura: jpg, png, webp, tif, bmp, gif, heic ou None (página de erro, JSON...)."""
    if not dados:
        return None
    if dados[:3] == b"\xff\xd8\xff":
        return "jpg"
    if dados[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if dados[:4] == b"RIFF" and dados[8:12] == b"WEBP":
        return "webp"
    if dados[:4] in (b"II*\x00", b"MM\x00*"):
        return "tif"
    if dados[:2] == b"BM":
        return "bmp"
    if dados[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if dados[4:8] == b"ftyp" and dados[8:12] in _MARCAS_HEIF:
        return "heic"
    return None


def eh_conteudo_imagem(dados):
    """Confere a assinatura do arquivo para não gravar páginas de erro ou de cota do Google como foto."""
    if not dados or len(dados) < 256:
        return False
    return tipo_conteudo(dados) is not None


class Sessao:
    """Estado comum aos downloads paralelos: chave, limite adaptativo de conexões e pausa após HTTP 429."""

    def __init__(self, chave=None, maximo=None):
        self.chave = chave_api() if chave is None else chave
        self.maximo = max(1, int(maximo or (TRABALHADORES_COM_CHAVE if self.chave else TRABALHADORES_SEM_CHAVE)))
        self.limite = self.maximo
        self.ativos = 0
        self.pausa_ate = 0.0
        self.sucessos = 0
        self.total_429 = 0
        self.api_recusada = ""
        self._ultima_reducao = 0.0
        self._cond = threading.Condition()

    def entrar(self, parar=None):
        with self._cond:
            while True:
                if parar and parar():
                    return False
                espera = self.pausa_ate - time.monotonic()
                if espera <= 0 and self.ativos < self.limite:
                    self.ativos += 1
                    return True
                self._cond.wait(min(0.2, espera) if espera > 0 else 0.2)

    def sair(self, sucesso=False):
        with self._cond:
            self.ativos = max(0, self.ativos - 1)
            if sucesso:
                self.sucessos += 1
                if self.sucessos >= 20 and self.limite < self.maximo:
                    self.limite += 1
                    self.sucessos = 0
            self._cond.notify_all()

    def sinalizar_429(self, espera):
        with self._cond:
            agora = time.monotonic()
            self.total_429 += 1
            self.sucessos = 0
            if agora - self._ultima_reducao > 1.0:
                self.limite = max(1, self.limite // 2)
                self._ultima_reducao = agora
            self.pausa_ate = max(self.pausa_ate, agora + max(0.0, espera))
            self._cond.notify_all()


class _Falha(Exception):
    def __init__(self, motivo, transitoria=False, fatal=False, espera=None, prioritaria=False, erro=None):
        super().__init__(motivo)
        self.motivo = motivo
        self.transitoria = transitoria
        self.fatal = fatal
        self.espera = espera
        self.prioritaria = prioritaria
        self.erro = erro


def _parece_html(dados, content_type):
    return "html" in (content_type or "").lower() or re.match(rb"\s*<", dados[:512] or b"") is not None


def _url_confirmacao(pagina, url_atual):
    """Página "não foi possível verificar vírus": monta a URL do botão "Baixar assim mesmo"."""
    texto = pagina.decode("utf-8", errors="ignore")
    m_form = re.search(r'<form[^>]*id="download-form"[^>]*>', texto) or re.search(r'<form[^>]*action="[^"]*download[^"]*"[^>]*>', texto)
    if m_form:
        m_acao = re.search(r'action="([^"]+)"', m_form.group(0))
        campos = {}
        for tag in re.findall(r"<input[^>]+>", texto[m_form.end():]):
            atrib = dict(re.findall(r'(\w+)="([^"]*)"', tag))
            if atrib.get("type", "hidden") == "hidden" and atrib.get("name"):
                campos[atrib["name"]] = html.unescape(atrib.get("value", ""))
        if m_acao and campos:
            acao = urllib.parse.urljoin(url_atual, html.unescape(m_acao.group(1)))
            return "%s?%s" % (acao, urllib.parse.urlencode(campos))
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(url_atual).query)
    file_id = (qs.get("id") or [""])[0]
    rk = (qs.get("resourcekey") or [""])[0]
    rk_q = "&resourcekey=%s" % urllib.parse.quote(rk) if rk else ""
    if not file_id:
        return None
    m_uuid = re.search(r'name="uuid"\s+value="([^"]+)"', texto)
    if m_uuid:
        return "%s/download?id=%s&export=download&confirm=t&uuid=%s%s" % (
            URL_USERCONTENT, file_id, urllib.parse.quote(m_uuid.group(1)), rk_q)
    m_conf = re.search(r"confirm=([0-9A-Za-z_-]+)", texto)
    if m_conf and "confirm=%s" % m_conf.group(1) not in url_atual:
        return "%s/uc?export=download&id=%s&confirm=%s%s" % (URL_DRIVE, file_id, m_conf.group(1), rk_q)
    return None


def _motivo_pagina(pagina):
    texto = pagina.decode("utf-8", errors="ignore").lower()
    if "too many users" in texto or "quota" in texto or "cota" in texto:
        return "cota de download do arquivo excedida no Google (muitos acessos recentes)"
    if "accounts.google.com" in texto or "servicelogin" in texto:
        return "arquivo privado (o Google pediu login)"
    return "o Google devolveu uma página em vez da foto"


def _baixar_url(url, headers, parcial, sessao, parar):
    """Baixa em blocos para parcial. Devolve ("ok", bytes, tipo, md5) ou ("confirmar", nova_url)."""
    if not sessao.entrar(parar):
        raise _Falha("cancelado", fatal=True)
    sucesso = False
    try:
        try:
            resp = _abrir(url, headers)
        except urllib.error.HTTPError as e:
            erro = _erro_de_http(e)
            if erro.codigo == 429 or erro.motivo in _MOTIVOS_RITMO:
                sessao.sinalizar_429(_espera(0, erro.retry_after))
            raise _Falha(erro.descricao, transitoria=erro.transitorio, espera=erro.retry_after, erro=erro)
        except (urllib.error.URLError, OSError, http.client.HTTPException) as e:
            raise _Falha(_motivo_rede(e), transitoria=True)
        with resp:
            primeiro = resp.read(TAMANHO_BLOCO)
            tipo = tipo_conteudo(primeiro)
            if tipo is None or len(primeiro) < 256:
                if _parece_html(primeiro, resp.headers.get("Content-Type")):
                    pagina = primeiro + resp.read(max(0, LIMITE_PAGINA_HTML - len(primeiro)))
                    nova = _url_confirmacao(pagina, url)
                    if nova:
                        return ("confirmar", nova)
                    raise _Falha(_motivo_pagina(pagina))
                raise _Falha("o Google devolveu um conteúdo que não é foto")
            esperado = _inteiro(resp.headers.get("Content-Length"))
            md5 = hashlib.md5()
            total = 0
            with open(parcial, "wb") as f:
                bloco = primeiro
                while bloco:
                    f.write(bloco)
                    md5.update(bloco)
                    total += len(bloco)
                    if parar and parar():
                        raise _Falha("cancelado", fatal=True)
                    bloco = resp.read(TAMANHO_BLOCO)
            if esperado is not None and esperado != total:
                raise _Falha("download incompleto (%d de %d bytes)" % (total, esperado), transitoria=True)
            sucesso = True
            return ("ok", total, tipo, md5.hexdigest())
    except (OSError, http.client.HTTPException) as e:
        raise _Falha("conexão interrompida (%s)" % _motivo_rede(e), transitoria=True)
    finally:
        sessao.sair(sucesso)


def _converter(origem, final, tipo):
    from PIL import Image, ImageOps

    if tipo == "heic":
        try:
            import pillow_heif
        except ImportError:
            raise _Falha(MOTIVO_HEIC_SEM_LIB, prioritaria=True)
        pillow_heif.register_heif_opener()
    temp = final.with_name(final.name + ".conv")
    try:
        with Image.open(origem) as im:
            icc = im.info.get("icc_profile")
            img = ImageOps.exif_transpose(im)
            exif = img.getexif()
            if tipo == "heic":
                if img.mode not in ("RGB", "L"):
                    img = img.convert("RGB")
                formato, opcoes = "JPEG", {"quality": 95}
            else:
                formato, opcoes = "PNG", {}
            if exif:
                opcoes["exif"] = exif.tobytes()
            if icc:
                opcoes["icc_profile"] = icc
            img.save(temp, formato, **opcoes)
        os.replace(temp, final)
    except _Falha:
        raise
    except Exception as e:
        raise _Falha("não foi possível converter o arquivo %s (%s)" % (tipo.upper(), e), prioritaria=True)
    finally:
        if temp.exists():
            try:
                temp.unlink()
            except OSError:
                pass


def _finalizar(parcial, destino, tipo):
    """Troca a extensão para combinar com o conteúdo e converte HEIC (para JPG) e GIF (para PNG)."""
    sufixos = _SUFIXOS_TIPO[tipo]
    final = destino if destino.suffix.lower() in sufixos else destino.with_suffix(sufixos[0])
    if tipo in ("heic", "gif"):
        _converter(parcial, final, tipo)
        parcial.unlink()
    else:
        os.replace(parcial, final)
    return final


def _candidatos(item, sessao):
    rk_q = "&resourcekey=%s" % urllib.parse.quote(item.resource_key) if item.resource_key else ""
    fid = urllib.parse.quote(item.id)
    lista = []
    if sessao.chave and not sessao.api_recusada:
        lista.append({
            "url": "%s/files/%s?alt=media&supportsAllDrives=true&key=%s" % (URL_API, fid, urllib.parse.quote(sessao.chave)),
            "api": True, "original": True,
        })
    lista.append({"url": "%s/download?id=%s&export=download&confirm=t%s" % (URL_USERCONTENT, fid, rk_q), "original": True})
    lista.append({"url": "%s/d/%s=s0" % (URL_LH3, fid), "original": False})
    lista.append({"url": "%s/uc?export=download&id=%s%s" % (URL_DRIVE, fid, rk_q), "original": True})
    return lista


def _tentar_candidato(cand, item, destino, parcial, headers, sessao, parar):
    url = cand["url"]
    for _passo in range(4):
        try:
            r = _baixar_url(url, headers, parcial, sessao, parar)
        except _Falha as f:
            if f.erro is not None and f.erro.motivo == "cannotDownloadAbusiveFile" and "acknowledgeAbuse" not in url:
                url += "&acknowledgeAbuse=true"
                continue
            raise
        if r[0] == "confirmar":
            url = r[1]
            continue
        _ok, total, tipo, md5 = r
        if cand.get("original"):
            if item.tamanho and total != item.tamanho:
                raise _Falha("tamanho diferente do esperado (%d de %d bytes)" % (total, item.tamanho), transitoria=True)
            if item.md5 and md5 != str(item.md5).lower():
                raise _Falha("conteúdo corrompido (MD5 diferente do Drive)", transitoria=True)
        caminho = _finalizar(parcial, destino, tipo)
        return ResultadoDownload(True, "", _sem_chave(url), bool(cand.get("reduzida")), caminho.stat().st_size, caminho)
    raise _Falha("o Google pediu confirmação de download repetidas vezes")


def baixar_item(item, destino, parar=None, sessao=None, permitir_reduzida=True, resource_key=None):
    """
    Baixa uma foto (ItemDrive ou id) para destino e devolve um ResultadoDownload.
    A extensão final segue o conteúdo (HEIC vira JPG); a miniatura (sz=w4096) é só o último recurso.
    """
    if not isinstance(item, ItemDrive):
        item = ItemDrive(str(item), resource_key=resource_key)
    sessao = sessao or Sessao()
    parar = parar or (lambda: False)
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    parcial = destino.with_name(destino.name + ".parcial")
    prazo = time.monotonic() + PRAZO_ARQUIVO
    headers = dict(_HEADERS)
    if item.resource_key:
        headers["X-Goog-Drive-Resource-Keys"] = "%s/%s" % (item.id, item.resource_key)

    motivo, motivo_fixo = "", ""
    candidatos = _candidatos(item, sessao)
    try:
        for rodada in range(RODADAS):
            pendente, fim_espera = False, 0.0
            for cand in candidatos:
                if cand.get("desativado") or (cand.get("api") and sessao.api_recusada):
                    continue
                if parar():
                    return ResultadoDownload(False, "cancelado")
                if time.monotonic() > prazo:
                    motivo = "tempo esgotado"
                    break
                try:
                    return _tentar_candidato(cand, item, destino, parcial, headers, sessao, parar)
                except _Falha as f:
                    motivo = f.motivo
                    if f.prioritaria:
                        motivo_fixo = f.motivo
                    if f.fatal:
                        return ResultadoDownload(False, motivo_fixo or motivo)
                    if cand.get("api") and f.erro is not None:
                        if f.erro.recusa_chave:
                            sessao.api_recusada = f.erro.descricao
                        elif f.erro.codigo == 404:
                            return ResultadoDownload(False, "arquivo " + f.erro.descricao)
                    if f.transitoria:
                        pendente = True
                        if f.espera:
                            fim_espera = max(fim_espera, time.monotonic() + min(ESPERA_MAXIMA, f.espera))
                    else:
                        cand["desativado"] = True
            if not pendente or rodada == RODADAS - 1:
                break
            atraso = max(0.0, fim_espera - time.monotonic()) if fim_espera else _espera(rodada)
            if not _dormir(atraso, parar, prazo):
                motivo = "cancelado" if parar() else (motivo or "tempo esgotado")
                break

        if permitir_reduzida and not parar():
            cand = {
                "url": "%s/thumbnail?id=%s&sz=w4096%s" % (
                    URL_DRIVE, urllib.parse.quote(item.id),
                    "&resourcekey=%s" % urllib.parse.quote(item.resource_key) if item.resource_key else ""),
                "reduzida": True,
            }
            try:
                return _tentar_candidato(cand, item, destino, parcial, headers, sessao, parar)
            except _Falha as f:
                if f.prioritaria:
                    motivo_fixo = f.motivo
                motivo = motivo or f.motivo
        return ResultadoDownload(False, motivo_fixo or motivo or "não foi possível baixar")
    finally:
        if parcial.exists():
            try:
                parcial.unlink()
            except OSError:
                pass


def planejar_destinos(itens, pasta):
    """Monta [(item, destino)] respeitando subpastas, sem repetir nomes (comparando sem extensão, que pode mudar)."""
    pasta = Path(pasta)
    usados = set()
    tarefas = []
    for idx, item in enumerate(itens, start=1):
        nome = limpar_nome(item.nome) or ("foto_%03d" % idx)
        sufixo = Path(nome).suffix.lower()
        if sufixo in (".heic", ".heif"):
            nome = Path(nome).stem + ".jpg"
        elif sufixo == ".gif":
            nome = Path(nome).stem + ".png"
        elif sufixo not in EXTENSOES:
            nome += ".jpg"
        partes = [limpar_nome(p) or "_" for p in str(item.subpasta or "").split("/") if p]
        sub = pasta.joinpath(*partes) if partes else pasta
        base, ext = Path(nome).stem, Path(nome).suffix
        cand, n = nome, 2
        while (str(sub).lower(), Path(cand).stem.lower()) in usados:
            cand = "%s_%d%s" % (base, n, ext)
            n += 1
        usados.add((str(sub).lower(), Path(cand).stem.lower()))
        sub.mkdir(parents=True, exist_ok=True)
        tarefas.append((item, sub / cand))
    return tarefas


def _baixar_seguro(item, destino, parar, sessao, permitir_reduzida):
    try:
        return baixar_item(item, destino, parar=parar, sessao=sessao, permitir_reduzida=permitir_reduzida)
    except Exception as e:
        return ResultadoDownload(False, "erro inesperado (%s)" % e)


def _rodar_pool(tarefas, indices, resultados, trabalhadores, parar, sessao, permitir_reduzida, progresso):
    if not indices:
        return
    feitos = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(trabalhadores, len(indices)))) as pool:
        futuros = {
            pool.submit(_baixar_seguro, tarefas[i][0], tarefas[i][1], parar, sessao, permitir_reduzida): i
            for i in indices
        }
        for fut in concurrent.futures.as_completed(futuros):
            resultados[futuros[fut]] = fut.result()
            feitos += 1
            if progresso:
                progresso(feitos, len(indices))
            if parar():
                for f in futuros:
                    f.cancel()
                break
    for i in indices:
        if resultados[i] is None:
            resultados[i] = ResultadoDownload(False, "cancelado")


def baixar_lote(tarefas, parar=None, progresso=None, sessao=None, aviso=None, segunda_passagem=True):
    """
    Baixa [(ItemDrive, destino), ...] em paralelo e devolve os ResultadoDownload na mesma ordem.
    As que falharem ganham uma segunda passagem (também paralela, com menos conexões), que aceita
    a miniatura como último recurso.
    """
    sessao = sessao or Sessao()
    parar = parar or (lambda: False)
    resultados = [None] * len(tarefas)
    _rodar_pool(tarefas, list(range(len(tarefas))), resultados, sessao.maximo, parar, sessao,
                not segunda_passagem, progresso)
    if segunda_passagem and not parar():
        falhas = [i for i, r in enumerate(resultados) if not r.ok and r.motivo != "cancelado"]
        if falhas:
            if aviso:
                aviso("Google Drive: tentando de novo %d foto(s) que falharam..." % len(falhas))
            if _dormir(_espera(0), parar):
                anteriores = {i: resultados[i] for i in falhas}
                for i in falhas:
                    resultados[i] = None
                _rodar_pool(tarefas, falhas, resultados, max(2, sessao.limite // 2), parar, sessao, True, progresso)
                for i in falhas:
                    if resultados[i].motivo == "cancelado" and anteriores[i].motivo:
                        resultados[i] = ResultadoDownload(False, anteriores[i].motivo)
    return resultados


@dataclass
class Relatorio:
    tarefas: list = field(default_factory=list)
    resultados: list = field(default_factory=list)
    extras: list = field(default_factory=list)
    limitada: bool = False
    aviso_api: str = ""
    pastas_limitadas: list = field(default_factory=list)
    sessao: object = None

    @property
    def total(self):
        return len(self.tarefas) or len(self.extras)

    @property
    def baixadas(self):
        return [r.caminho for r in self.resultados if r and r.ok and r.caminho] + list(self.extras)

    @property
    def reduzidas(self):
        return [(item, r) for (item, _d), r in zip(self.tarefas, self.resultados) if r and r.ok and r.reduzida]

    @property
    def faltando(self):
        return [
            (item, destino, (r.motivo if r is not None else "") or "não foi possível baixar")
            for (item, destino), r in zip(self.tarefas, self.resultados)
            if not (r and r.ok)
        ]


def tentar_novamente(relatorio, parar=None, progresso=None, aviso=None):
    """Baixa de novo só as que faltaram (mesmos destinos); atualiza o relatório e devolve os caminhos novos."""
    indices = [i for i, r in enumerate(relatorio.resultados) if not (r and r.ok)]
    if not indices:
        return []
    sessao = relatorio.sessao if isinstance(relatorio.sessao, Sessao) else Sessao()
    sessao.limite = max(sessao.limite, sessao.maximo // 2, 1)
    parciais = baixar_lote([relatorio.tarefas[i] for i in indices], parar=parar, progresso=progresso,
                           sessao=sessao, aviso=aviso)
    novos = []
    for i, r in zip(indices, parciais):
        if r.ok:
            novos.append(r.caminho)
            relatorio.resultados[i] = r
        elif r.motivo != "cancelado":
            relatorio.resultados[i] = r
    return novos
