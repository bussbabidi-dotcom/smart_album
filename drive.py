# -*- coding: utf-8 -*-
"""
Download de fotos de links públicos do Google Drive ("Qualquer pessoa com o link").

Com uma chave da API do Google (Drive API v3) a listagem é paginada e traz todas
as fotos da pasta. Sem chave, a listagem usa a página HTML pública da pasta, que
o Google limita a cerca de 50 itens por pasta.

A chave vem da variável de ambiente SMART_ALBUM_DRIVE_CHAVE_API ou de
%LOCALAPPDATA%\\smart_album\\drive.json ({"chave_api": "..."}).
"""

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from comum import EXTENSOES, gravar_json, ler_json, pasta_dados

VARIAVEL_CHAVE = "SMART_ALBUM_DRIVE_CHAVE_API"

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

_CAMPOS_LISTA = "nextPageToken,files(id,name,mimeType,resourceKey,shortcutDetails(targetId,targetMimeType,targetResourceKey))"

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


def _decodificar_html(raw_html):
    s = re.sub(r"\\x([0-9a-fA-F]{2})", lambda m: chr(int(m.group(1), 16)), raw_html)
    s = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), s)
    return s.replace("\\/", "/")


def _ler_url(url, timeout):
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _requisitar_api_v3(caminho_api, params, resourcekey=None):
    chave = chave_api()
    if not chave:
        return None
    headers = dict(_HEADERS)
    if resourcekey:
        headers["X-Goog-Drive-Resource-Keys"] = resourcekey
    q_params = dict(params)
    q_params["key"] = chave
    url = "https://www.googleapis.com/drive/v3/%s?%s" % (
        caminho_api.lstrip("/"),
        urllib.parse.urlencode(q_params),
    )
    for tentativa in range(3):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=25) as resp:
                return json.loads(resp.read().decode("utf-8", errors="ignore"))
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and tentativa < 2:
                time.sleep(0.6 * (tentativa + 1))
                continue
            return None
        except Exception:
            if tentativa < 2:
                time.sleep(0.5 * (tentativa + 1))
                continue
            return None
    return None


def resolver_atalho(file_id, resourcekey=None):
    """Devolve (id_real, nome, mime) seguindo atalhos do Drive; nome e mime podem ser None."""
    meta = _requisitar_api_v3(
        "files/%s" % file_id,
        {
            "fields": "id,name,mimeType,shortcutDetails(targetId,targetMimeType,targetResourceKey)",
            "supportsAllDrives": "true",
        },
        resourcekey=resourcekey,
    )
    if isinstance(meta, dict) and meta.get("id"):
        atalho = meta.get("shortcutDetails") or {}
        if atalho.get("targetId"):
            return atalho["targetId"], meta.get("name"), atalho.get("targetMimeType")
        return meta["id"], meta.get("name"), meta.get("mimeType")

    try:
        raw = _ler_url("https://drive.google.com/file/d/%s/view" % file_id, 20).decode("utf-8", errors="ignore")
        s = _decodificar_html(raw)
        m = re.search(
            r'\["([a-zA-Z0-9_-]{15,})",(?:\["[^"]*"\]|null),"([^"]+)","(image/[^"]+)"',
            s,
        )
        if m:
            return m.group(1), m.group(2), m.group(3)
    except Exception:
        pass
    return file_id, None, None


def eh_imagem(nome, mime):
    mime_l = str(mime or "").lower()
    ext_l = Path(str(nome or "")).suffix.lower()
    if mime_l in _MIMES_IGNORADOS:
        return False
    if mime_l.startswith("video/") or mime_l.startswith("audio/"):
        return False
    if ext_l in EXTENSOES:
        return True
    if mime_l.startswith("image/"):
        return True
    return False


def listar_pasta(folder_id, visitados=None, nivel=0, resourcekey=None):
    """Lista recursivamente as fotos da pasta. Devolve ([(id, nome), ...], titulo_da_pasta)."""
    if visitados is None:
        visitados = set()
    if folder_id in visitados or nivel > 6:
        return [], None
    visitados.add(folder_id)

    arquivos = []
    subpastas = []
    ids_subpastas = set()
    ids_atalhos = set()
    ids_arquivos = set()
    titulo_pasta = None
    rk_header = "%s/%s" % (folder_id, resourcekey) if resourcekey else None

    meta_pasta = _requisitar_api_v3(
        "files/%s" % folder_id,
        {"fields": "id,name,mimeType", "supportsAllDrives": "true"},
        resourcekey=rk_header,
    )
    if isinstance(meta_pasta, dict) and meta_pasta.get("name"):
        titulo_pasta = meta_pasta["name"].strip()

    page_token = None
    listou_via_api = False
    for _pagina in range(50):
        params_lista = {
            "q": "'%s' in parents and trashed = false" % folder_id,
            "fields": _CAMPOS_LISTA,
            "pageSize": "1000",
            "orderBy": "folder,name_natural",
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
        }
        if page_token:
            params_lista["pageToken"] = page_token
        dados_pag = _requisitar_api_v3("files", params_lista, resourcekey=rk_header)
        if not isinstance(dados_pag, dict) or "files" not in dados_pag:
            break
        listou_via_api = True
        for item in dados_pag.get("files") or []:
            fid = item.get("id")
            nome = (item.get("name") or "").strip()
            mime = item.get("mimeType") or ""
            if not fid:
                continue
            if mime == "application/vnd.google-apps.folder":
                if fid != folder_id and fid not in visitados and fid not in ids_subpastas:
                    ids_subpastas.add(fid)
                    subpastas.append((fid, nome, item.get("resourceKey")))
            elif mime == "application/vnd.google-apps.shortcut":
                atalho = item.get("shortcutDetails") or {}
                alvo_id = atalho.get("targetId")
                alvo_mime = atalho.get("targetMimeType") or ""
                alvo_rk = atalho.get("targetResourceKey")
                if not alvo_id:
                    alvo_id, real_nome, alvo_mime = resolver_atalho(fid)
                    nome = real_nome or nome
                if alvo_mime == "application/vnd.google-apps.folder":
                    if alvo_id and alvo_id != folder_id and alvo_id not in visitados and alvo_id not in ids_subpastas:
                        ids_subpastas.add(alvo_id)
                        subpastas.append((alvo_id, nome, alvo_rk))
                elif alvo_id and eh_imagem(nome, alvo_mime):
                    if alvo_id not in ids_arquivos:
                        ids_arquivos.add(alvo_id)
                        arquivos.append((alvo_id, nome))
            elif eh_imagem(nome, mime):
                if fid not in ids_arquivos:
                    ids_arquivos.add(fid)
                    arquivos.append((fid, nome))
        page_token = dados_pag.get("nextPageToken")
        if not page_token:
            break

    if not listou_via_api:
        try:
            url_folder = "https://drive.google.com/drive/folders/%s?usp=sharing" % folder_id
            raw_html = _ler_url(url_folder, 25).decode("utf-8", errors="ignore")
            if not titulo_pasta:
                m_title = re.search(r"<title>([^<]+)</title>", raw_html, re.I)
                if m_title:
                    t_limpo = re.sub(r"\s*-\s*Google\s*Drive\s*$", "", m_title.group(1), flags=re.I).strip()
                    if t_limpo and t_limpo.lower() != "google drive":
                        titulo_pasta = t_limpo

            s = _decodificar_html(raw_html)
            for fid, nome, mime in re.findall(
                r'\["([a-zA-Z0-9_-]{15,})",(?:\["[^"]*"\]|null),"([^"]+)","([^"]+)"',
                s,
            ):
                if mime == "application/vnd.google-apps.folder":
                    if fid != folder_id and fid not in visitados and fid not in ids_subpastas:
                        ids_subpastas.add(fid)
                        subpastas.append((fid, nome, None))
                elif mime == "application/vnd.google-apps.shortcut":
                    ids_atalhos.add(fid)
                elif eh_imagem(nome, mime):
                    if fid not in ids_arquivos:
                        ids_arquivos.add(fid)
                        arquivos.append((fid, nome))
        except Exception:
            pass

        try:
            url_lista = "https://drive.google.com/embeddedfolderview?id=%s#list" % folder_id
            html_emb = _ler_url(url_lista, 25).decode("utf-8", errors="ignore")
            if not titulo_pasta:
                m_t2 = re.search(r"<title>([^<]+)</title>", html_emb, re.I)
                if m_t2 and m_t2.group(1).strip():
                    titulo_pasta = m_t2.group(1).strip()

            padrao_entry = re.compile(
                r'id="entry-([a-zA-Z0-9_-]{10,})".*?<a\s+href="([^"]+)".*?class="flip-entry-title">([^<]+)<',
                re.S,
            )
            for m in padrao_entry.finditer(html_emb):
                eid, href, titulo = m.group(1), m.group(2), m.group(3).strip()
                bloco = m.group(0)
                if "/folders/" in href or "drive-sprite-folder" in bloco:
                    if eid != folder_id and eid not in visitados and eid not in ids_subpastas:
                        ids_subpastas.add(eid)
                        subpastas.append((eid, titulo, None))
                elif "vnd.google-apps.shortcut" in bloco:
                    if eid not in ids_atalhos:
                        ids_atalhos.add(eid)
                        real_id, real_nome, real_mime = resolver_atalho(eid)
                        if real_id and real_id not in ids_arquivos and eh_imagem(real_nome or titulo, real_mime or "image/jpeg"):
                            ids_arquivos.add(real_id)
                            arquivos.append((real_id, real_nome or titulo))
                elif eh_imagem(titulo, ""):
                    if eid not in ids_arquivos and eid not in ids_atalhos:
                        ids_arquivos.add(eid)
                        arquivos.append((eid, titulo))
        except Exception:
            pass

    for sub_id, sub_nome, sub_rk in subpastas:
        sub_arqs, _ = listar_pasta(sub_id, visitados, nivel + 1, sub_rk)
        if sub_arqs and len(subpastas) == 1 and not arquivos and sub_nome:
            titulo_pasta = sub_nome
        for fid, nome in sub_arqs:
            if fid not in ids_arquivos:
                ids_arquivos.add(fid)
                arquivos.append((fid, nome))

    return arquivos, titulo_pasta


def eh_conteudo_imagem(dados):
    """Confere a assinatura do arquivo para não gravar páginas de erro ou de cota do Google como foto."""
    if not dados or len(dados) < 256:
        return False
    if dados[:3] == b"\xff\xd8\xff":
        return True
    if dados[:8] == b"\x89PNG\r\n\x1a\n":
        return True
    if dados[:4] == b"RIFF" and dados[8:12] == b"WEBP":
        return True
    if dados[:4] in (b"II*\x00", b"MM\x00*"):
        return True
    if dados[:2] == b"BM":
        return True
    if dados[:6] in (b"GIF87a", b"GIF89a"):
        return True
    return False


def baixar_item(file_id, destino_arq):
    urls_tentativa = []
    chave = chave_api()
    if chave:
        urls_tentativa.append(
            "https://www.googleapis.com/drive/v3/files/%s?alt=media&supportsAllDrives=true&key=%s"
            % (file_id, chave)
        )
    urls_tentativa.extend([
        "https://lh3.googleusercontent.com/d/%s=s0" % file_id,
        "https://drive.usercontent.google.com/download?id=%s&export=download&confirm=t" % file_id,
        "https://drive.google.com/uc?export=download&id=%s" % file_id,
        "https://drive.google.com/thumbnail?id=%s&sz=w4096" % file_id,
    ])

    conteudo = None
    for rodada in range(3):
        for url_dl in urls_tentativa:
            try:
                dados = _ler_url(url_dl, 35)
                if not eh_conteudo_imagem(dados):
                    # Página de confirmação de download ("arquivo grande demais para verificar vírus")
                    m_conf = re.search(rb"confirm=([0-9A-Za-z_-]+)", dados)
                    m_uuid = re.search(rb'name="uuid"\s+value="([^"]+)"', dados)
                    if m_uuid:
                        uuid_val = m_uuid.group(1).decode("utf-8", errors="ignore")
                        dados = _ler_url(
                            "https://drive.usercontent.google.com/download?id=%s&export=download&confirm=t&uuid=%s"
                            % (file_id, uuid_val),
                            35,
                        )
                    elif m_conf:
                        token = m_conf.group(1).decode("utf-8", errors="ignore")
                        dados = _ler_url(
                            "https://drive.google.com/uc?export=download&id=%s&confirm=%s" % (file_id, token),
                            35,
                        )
                if eh_conteudo_imagem(dados):
                    conteudo = dados
                    break
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504):
                    time.sleep(0.4 * (rodada + 1))
                continue
            except Exception:
                continue
        if conteudo:
            break
        time.sleep(0.7 * (rodada + 1))

    if not conteudo:
        return False

    with open(destino_arq, "wb") as f:
        f.write(conteudo)
    return True
