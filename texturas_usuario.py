# -*- coding: utf-8 -*-
"""
Texturas do usuário para os fundos gerados (no lugar das texturas desenhadas pelo programa).

O fundo gerado continua tendo as CORES do programa (fotos, tema, cor do canudo). A textura passa a vir de:
  - uma imagem qualquer: o programa EXTRAI a textura dela (o relevo/desenho, sem a cor nem a luz geral)
    e aplica esse relevo por cima das cores;
  - um PNG usado como está: com transparência, é colado por cima das cores; sem transparência, o próprio
    desenho dele vira o relevo (modo "png" não passa pelo filtro de extração);
  - nada: degradê só com as cores do programa.

Também reconhece quais imagens da pasta de planos de fundo são texturas:
  "textura"  grão/desenho fino espalhado pela imagem toda (damasco, lona, cortina, concreto)
  "transparente"  PNG com transparência (enfeite/overlay)
  "degrade"  degradê liso, sem grão
  "imagem"   foto de cenário ou imagem com formas grandes e várias cores (usa-se como fundo inteiro)
A classificação automática pode ser corrigida à mão (marcar).

Preferência (texturas.json na pasta de dados):
  "modo": "minhas" (sorteia entre as texturas reconhecidas/importadas) | "uma" (sempre "arquivo")
          | "nenhuma" (só degradê) | "programa" (texturas antigas desenhadas pelo programa)
"""

import hashlib
import threading
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageFilter

from comum import gravar_json, ler_json, normalizar_caminho, pasta_dados

EXTENSOES = (".jpg", ".jpeg", ".jfif", ".png", ".webp", ".tif", ".tiff", ".bmp")
MODOS = ("minhas", "uma", "nenhuma", "programa")
CLASSES = ("textura", "transparente", "degrade", "imagem")
LADO_RELEVO = 4096          # lado maior do relevo guardado (a lâmina 30x22 a 300 dpi tem 3508 px)
LADO_MEDIDA = 512
GRAO_MINIMO = 1.5           # abaixo disto a imagem é lisa (degradê)
FORMA_MAXIMA = 30.0         # acima disto há formas grandes de luz (cenário, brilhos): não é textura
GRAO_IGUAL_MINIMO = 0.15    # o trecho mais liso tem de ter pelo menos 15% do grão do trecho mediano
TRANSPARENCIA_MINIMA = 0.05
AMPLITUDE = 26.0            # níveis (0..255) do relevo na intensidade 1.0

_trava = threading.RLock()
_dados = None
_cache_relevo = {}


def _arquivo():
    return pasta_dados() / "texturas.json"


def _pasta_cache():
    pasta = pasta_dados() / "texturas"
    pasta.mkdir(parents=True, exist_ok=True)
    return pasta


def _ler():
    global _dados
    with _trava:
        if _dados is None:
            bruto = ler_json(_arquivo(), {})
            bruto = bruto if isinstance(bruto, dict) else {}
            _dados = {
                "modo": bruto.get("modo") if bruto.get("modo") in MODOS else "minhas",
                "arquivo": bruto.get("arquivo") or "",
                "arquivo_modo": bruto.get("arquivo_modo") or "relevo",
                "pasta": bruto.get("pasta") or "",
                "intensidade": float(bruto.get("intensidade") or 1.0),
                "marcadas": dict(bruto.get("marcadas") or {}),
                "importadas": list(bruto.get("importadas") or []),
                "medidas": dict(bruto.get("medidas") or {}),
            }
        return _dados


def _gravar():
    with _trava:
        try:
            gravar_json(_arquivo(), _ler())
        except OSError:
            pass


def recarregar():
    """Esquece o que está na memória (para os testes e depois de trocar a pasta de dados)."""
    global _dados
    with _trava:
        _dados = None
        _cache_relevo.clear()


def _chave(caminho):
    try:
        st = Path(caminho).stat()
        return "%s|%s|%s" % (normalizar_caminho(caminho), st.st_mtime_ns, st.st_size)
    except OSError:
        return normalizar_caminho(caminho)


# ---------------------------------------------------------------- reconhecer texturas

def medir(caminho):
    """Medidas que separam textura, degradê e cenário (numa miniatura de 512 px). Guardadas por arquivo."""
    chave = _chave(caminho)
    dados = _ler()
    with _trava:
        guardado = dados["medidas"].get(chave)
    if guardado and "igual" in guardado:
        return guardado
    with Image.open(caminho) as im:
        tem_alfa = im.mode in ("RGBA", "LA") or "transparency" in im.info
        im.draft("RGB", (LADO_MEDIDA * 2, LADO_MEDIDA * 2))
        mini = im.convert("RGBA" if tem_alfa else "RGB")
        mini.thumbnail((LADO_MEDIDA, LADO_MEDIDA))
    transparencia = 0.0
    if tem_alfa:
        alfa = np.asarray(mini.getchannel("A"), dtype=np.float32) / 255.0
        transparencia = float((alfa < 0.95).mean())
    rgb = mini.convert("RGB")
    cinza = rgb.convert("L")
    lum = np.asarray(cinza, dtype=np.float32)
    forma = float(np.asarray(cinza.filter(ImageFilter.GaussianBlur(24)), dtype=np.float32).std())
    fino = lum - np.asarray(cinza.filter(ImageFilter.GaussianBlur(3)), dtype=np.float32)
    grao = float(fino.std())
    # textura tem grão na imagem toda; cenário tem partes lisas e partes cheias (menor bloco / bloco mediano)
    fino = np.abs(fino)
    alto, largo = fino.shape
    blocos = np.array([fino[i * alto // 5:(i + 1) * alto // 5, j * largo // 5:(j + 1) * largo // 5].mean()
                       for i in range(5) for j in range(5)])
    igual = float(blocos.min() / max(1e-6, float(np.median(blocos))))
    hsv = np.asarray(rgb.convert("HSV"), dtype=np.float32)
    vivos = (hsv[..., 1] > 76) & (hsv[..., 2] > 50)  # saturação > 30%, brilho > 20%
    grupos = 0
    if int(vivos.sum()) > vivos.size * 0.04:
        histo = np.bincount((hsv[..., 0][vivos] / 255.0 * 12).astype(int) % 12, minlength=12) / float(vivos.sum())
        fortes = histo >= 0.08
        # grupos de matiz: fatias vizinhas (30° cada) contam como uma cor só
        grupos = int(sum(1 for i in range(12) if fortes[i] and not fortes[i - 1]))
        if fortes.all():
            grupos = 1
    medidas = {"transparencia": round(transparencia, 3), "forma": round(forma, 2), "grao": round(grao, 2),
               "cores": grupos, "luz": round(float(lum.mean()), 1), "igual": round(igual, 3)}
    with _trava:
        dados["medidas"][chave] = medidas
        if len(dados["medidas"]) > 3000:
            for velha in list(dados["medidas"])[:500]:
                dados["medidas"].pop(velha, None)
    return medidas


def ja_medida(caminho):
    """Se a imagem já foi medida (classificar não vai abrir o arquivo: é rápido)."""
    guardado = _ler()["medidas"].get(_chave(caminho))
    return bool(guardado and "igual" in guardado)


def salvar_medidas():
    _gravar()


def classificar(caminho, usar_marcacao=True):
    """'textura' | 'transparente' | 'degrade' | 'imagem' (a marcação à mão manda)."""
    if usar_marcacao:
        marcada = _ler()["marcadas"].get(normalizar_caminho(caminho))
        if marcada in CLASSES:
            return marcada
    try:
        m = medir(caminho)
    except (OSError, ValueError):
        return "imagem"
    if m["transparencia"] >= TRANSPARENCIA_MINIMA:
        return "transparente"
    if m["grao"] < GRAO_MINIMO:
        return "degrade"
    if m["forma"] > FORMA_MAXIMA or m["cores"] >= 2 or m["igual"] < GRAO_IGUAL_MINIMO:
        return "imagem"
    return "textura"


def marcar(caminho, classe):
    """Corrige à mão: classe em CLASSES, ou None para voltar ao reconhecimento automático."""
    dados = _ler()
    chave = normalizar_caminho(caminho)
    with _trava:
        if classe in CLASSES:
            dados["marcadas"][chave] = classe
        else:
            dados["marcadas"].pop(chave, None)
    _gravar()


def eh_textura(caminho):
    return classificar(caminho) in ("textura", "transparente")


def arquivos_da_pasta(pasta):
    pasta = Path(pasta) if pasta else None
    if not pasta or not pasta.is_dir():
        return []
    return sorted((str(p) for p in pasta.rglob("*") if p.is_file() and p.suffix.lower() in EXTENSOES),
                  key=lambda c: c.lower())


def listar_texturas(pasta=None):
    """Texturas disponíveis: as importadas à mão e as reconhecidas na pasta de planos de fundo."""
    dados = _ler()
    pasta = pasta if pasta is not None else dados["pasta"]
    vistas, saida = set(), []
    for caminho in list(dados["importadas"]) + arquivos_da_pasta(pasta):
        chave = normalizar_caminho(caminho)
        if chave in vistas or not Path(caminho).is_file():
            continue
        vistas.add(chave)
        if chave in {normalizar_caminho(c) for c in dados["importadas"]} or eh_textura(caminho):
            saida.append(caminho)
    _gravar()
    return saida


def importar(caminho):
    """Acrescenta uma imagem às texturas (fica valendo mesmo fora da pasta de fundos)."""
    dados = _ler()
    with _trava:
        if normalizar_caminho(caminho) not in {normalizar_caminho(c) for c in dados["importadas"]}:
            dados["importadas"].append(str(caminho))
    _gravar()
    return str(caminho)


# ---------------------------------------------------------------- preferência

def preferencia():
    d = _ler()
    return {"modo": d["modo"], "arquivo": d["arquivo"], "arquivo_modo": d["arquivo_modo"],
            "pasta": d["pasta"], "intensidade": d["intensidade"]}


def definir_preferencia(modo=None, arquivo=None, arquivo_modo=None, pasta=None, intensidade=None):
    dados = _ler()
    with _trava:
        if modo in MODOS:
            dados["modo"] = modo
        if arquivo is not None:
            dados["arquivo"] = str(arquivo)
        if arquivo_modo in ("relevo", "png"):
            dados["arquivo_modo"] = arquivo_modo
        if pasta is not None:
            dados["pasta"] = str(pasta)
        if intensidade is not None:
            dados["intensidade"] = max(0.1, min(3.0, float(intensidade)))
    _gravar()
    return preferencia()


def modo_do_arquivo(caminho):
    """'png' para PNG com transparência (cola como está); 'relevo' para o resto (extrai a textura)."""
    try:
        return "png" if medir(caminho)["transparencia"] >= TRANSPARENCIA_MINIMA else "relevo"
    except (OSError, ValueError):
        return "relevo"


def escolher_para_espec(espec, indice=0):
    """
    Põe na receita do fundo gerado a textura que a preferência manda (a receita guarda o arquivo,
    para o fundo sair igual depois). Devolve a própria receita.
    """
    pref = preferencia()
    modo = pref["modo"]
    if modo == "programa":
        return espec
    espec.pop("textura_arquivo", None)
    espec.pop("textura_modo", None)
    escolhida = None
    if modo == "uma" and pref["arquivo"] and Path(pref["arquivo"]).is_file():
        escolhida, modo_arq = pref["arquivo"], pref["arquivo_modo"]
    elif modo == "minhas":
        lista = listar_texturas()
        if lista:
            escolhida = lista[(int(espec.get("seed") or 0) + int(indice)) % len(lista)]
            modo_arq = modo_do_arquivo(escolhida)
    espec["textura"] = "nenhuma"  # nunca mais a textura desenhada pelo programa
    if escolhida:
        espec["textura_arquivo"] = str(escolhida)
        espec["textura_modo"] = modo_arq
        if abs(pref["intensidade"] - 1.0) > 1e-6:
            espec["textura_intensidade"] = round(pref["intensidade"], 2)
    return espec


# ---------------------------------------------------------------- extrair e aplicar

def extrair_relevo(caminho, filtrar=True):
    """
    O desenho da imagem como relevo (PIL "L": 128 = neutro, mais claro = relevo para cima).
    Com `filtrar`, tira a cor e a luz geral (degradês, vinheta) e deixa só a textura.
    """
    with Image.open(caminho) as im:
        im.draft("L", (LADO_RELEVO, LADO_RELEVO))
        cinza = im.convert("L")
    if max(cinza.size) > LADO_RELEVO:
        escala = LADO_RELEVO / float(max(cinza.size))
        cinza = cinza.resize((max(1, round(cinza.width * escala)), max(1, round(cinza.height * escala))), Image.LANCZOS)
    lum = np.asarray(cinza, dtype=np.float32)
    if filtrar:
        raio = max(4.0, max(cinza.size) * 0.025)
        lum = lum - np.asarray(cinza.filter(ImageFilter.GaussianBlur(raio)), dtype=np.float32)
    else:
        lum = lum - float(lum.mean())
    # piso de 6 níveis: imagem quase lisa não tem o ruído ampliado até virar textura
    teto = max(6.0, float(np.percentile(np.abs(lum), 99.5)))
    relevo = np.clip(lum / teto, -1.0, 1.0)
    return Image.fromarray(np.clip(np.rint(relevo * 127.0 + 128.0), 0, 255).astype(np.uint8), "L")


def caminho_relevo(caminho, filtrar=True):
    """Arquivo PNG do relevo extraído (feito uma vez por imagem e guardado na pasta de dados)."""
    assinatura = hashlib.sha1(("%s|%s" % (_chave(caminho), int(bool(filtrar)))).encode("utf-8")).hexdigest()[:16]
    destino = _pasta_cache() / ("relevo_%s.png" % assinatura)
    if not destino.is_file():
        extrair_relevo(caminho, filtrar).save(destino, compress_level=3)
    return destino


def _relevo_no_tamanho(caminho, tamanho, filtrar):
    """Relevo cobrindo `tamanho` (corta o que sobra, sem deformar)."""
    W, H = tamanho
    chave = (str(caminho), W, H, bool(filtrar))
    with _trava:
        if chave in _cache_relevo:
            return _cache_relevo[chave]
    with Image.open(caminho_relevo(caminho, filtrar)) as im:
        rel = im.convert("L")
    escala = max(W / float(rel.width), H / float(rel.height))
    nw, nh = max(W, int(round(rel.width * escala))), max(H, int(round(rel.height * escala)))
    rel = rel.resize((nw, nh), Image.LANCZOS if escala < 1 else Image.BICUBIC)
    x0, y0 = (nw - W) // 2, (nh - H) // 2
    rel = rel.crop((x0, y0, x0 + W, y0 + H))
    with _trava:
        if W * H <= 4_000_000:
            _cache_relevo[chave] = rel
            while len(_cache_relevo) > 6:
                _cache_relevo.pop(next(iter(_cache_relevo)))
    return rel


def aplicar_relevo(img, caminho, intensidade=1.0, padrao_cor="preto", filtrar=True):
    """Soma o relevo da textura nas cores de `img` (RGB). Em fundo escuro o desenho clareia mais do que escurece."""
    img = img.convert("RGB")
    rel = np.asarray(_relevo_no_tamanho(caminho, img.size, filtrar), dtype=np.float32) - 128.0
    rel *= (AMPLITUDE * float(intensidade)) / 127.0
    if str(padrao_cor).lower() == "preto":
        rel = np.where(rel > 0, rel * 1.35, rel * 0.55)
    canal = Image.fromarray(np.clip(np.rint(rel + 128.0), 0, 255).astype(np.uint8), "L")
    return ImageChops.add(img, Image.merge("RGB", (canal, canal, canal)), scale=1.0, offset=-128)


def aplicar_png(img, caminho):
    """Cola o PNG (com a transparência dele) por cima de `img`, cobrindo tudo sem deformar."""
    base = img.convert("RGBA")
    with Image.open(caminho) as im:
        png = im.convert("RGBA")
    W, H = base.size
    escala = max(W / float(png.width), H / float(png.height))
    nw, nh = max(W, int(round(png.width * escala))), max(H, int(round(png.height * escala)))
    png = png.resize((nw, nh), Image.LANCZOS)
    x0, y0 = (nw - W) // 2, (nh - H) // 2
    base.alpha_composite(png.crop((x0, y0, x0 + W, y0 + H)))
    return base.convert("RGB")


def aplicar(img, espec, padrao_cor="preto"):
    """Aplica em `img` a textura do usuário descrita na receita (`textura_arquivo`, `textura_modo`)."""
    caminho = (espec or {}).get("textura_arquivo")
    if not caminho or not Path(caminho).is_file():
        return img
    intensidade = float(espec.get("textura_intensidade") or 1.0)
    try:
        if espec.get("textura_modo") == "png":
            if medir(caminho)["transparencia"] >= TRANSPARENCIA_MINIMA:
                return aplicar_png(img, caminho)
            return aplicar_relevo(img, caminho, intensidade, padrao_cor, filtrar=False)
        return aplicar_relevo(img, caminho, intensidade, padrao_cor, filtrar=True)
    except (OSError, ValueError):
        return img
