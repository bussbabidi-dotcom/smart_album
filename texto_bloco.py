# -*- coding: utf-8 -*-
"""
Bloco de texto da lâmina.

Funciona como um espaço de foto (tem x, y, w, h em fração da lâmina; é selecionado, movido,
redimensionado, duplicado e apagado no editor), mas é TRANSPARENTE: só o texto aparece, por cima
das fotos e do fundo. Usa as fontes instaladas no Windows.

Fica em lamina["textos"] = [bloco, ...]. Um bloco:
    {"x", "y", "w", "h",            # área do bloco (fração da lâmina)
     "texto": "Nome\\nCurso",       # várias linhas; quebra por palavra dentro da largura do bloco
     "fonte": "Montserrat Medium",  # nome da fonte instalada (como o Windows registra)
     "arquivo": "C:\\...\\x.ttf",    # arquivo da fonte (reserva, se o nome não for achado)
     "tamanho_pt": None,            # None = encaixar no bloco (maior tamanho que cabe); ou nº em pontos
     "cor": "#ffffff", "opacidade": 100,
     "alinhar": "centro" | "esquerda" | "direita",
     "alinhar_v": "meio" | "topo" | "base",
     "espaco_letras": 0.0,          # fração do tamanho da letra (0.1 = 10%)
     "espaco_linhas": 0.95,         # múltiplo da altura natural da linha
     "maiusculas": False, "sombra": False}
"""

import functools
import os
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from comum import cor_rgb
from produtos import caixa_px

ALINHAMENTOS = ("esquerda", "centro", "direita")
ALINHAMENTOS_V = ("topo", "meio", "base")
FONTE_PADRAO = "Arial"
PADRAO = {
    "x": 0.55, "y": 0.35, "w": 0.35, "h": 0.30,
    "texto": "Texto",
    "fonte": FONTE_PADRAO,
    "arquivo": "",
    "tamanho_pt": None,
    "cor": "#ffffff",
    "opacidade": 100,
    "alinhar": "centro",
    "alinhar_v": "meio",
    "espaco_letras": 0.0,
    "espaco_linhas": 0.95,
    "maiusculas": False,
    "sombra": False,
}

_FONTES = None


def completar(bloco):
    """Bloco com todos os campos (os que faltam vêm do padrão)."""
    dados = dict(PADRAO)
    dados.update(bloco or {})
    return dados


def novo_bloco(x, y, w, h, **extra):
    bloco = dict(PADRAO)
    bloco.update({"x": float(x), "y": float(y), "w": float(w), "h": float(h)})
    bloco.update(extra)
    if bloco["fonte"] and not bloco["arquivo"]:
        bloco["arquivo"] = arquivo_da_fonte(bloco["fonte"]) or ""
    return bloco


# ---------------------------------------------------------------- fontes instaladas

def _ler_registro():
    fontes = {}
    try:
        import winreg
    except ImportError:
        return fontes
    pasta_sistema = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    locais = (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"),
    )
    for raiz, caminho in locais:
        try:
            chave = winreg.OpenKey(raiz, caminho)
        except OSError:
            continue
        with chave:
            i = 0
            while True:
                try:
                    nome, valor, _tipo = winreg.EnumValue(chave, i)
                except OSError:
                    break
                i += 1
                arquivo = Path(str(valor))
                if not arquivo.is_absolute():
                    arquivo = pasta_sistema / arquivo
                if arquivo.suffix.lower() not in (".ttf", ".otf", ".ttc") or not arquivo.is_file():
                    continue
                nome = re.sub(r"\s*\((TrueType|OpenType|All res)\)\s*$", "", str(nome)).strip()
                if nome:
                    fontes[nome] = str(arquivo)
    return fontes


def _ler_pastas():
    """Reserva (sem registro do Windows): lê as pastas de fontes e usa o nome do arquivo."""
    fontes = {}
    pastas = [Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        pastas.append(Path(local) / "Microsoft" / "Windows" / "Fonts")
    for pasta in pastas:
        if pasta.is_dir():
            for arquivo in pasta.iterdir():
                if arquivo.suffix.lower() in (".ttf", ".otf", ".ttc"):
                    fontes[arquivo.stem] = str(arquivo)
    return fontes


def fontes_instaladas(recarregar=False):
    """{nome da fonte: arquivo}, ordenado sem ligar para maiúsculas. Guarda em memória."""
    global _FONTES
    if _FONTES is None or recarregar:
        dados = _ler_registro() or _ler_pastas()
        _FONTES = dict(sorted(dados.items(), key=lambda par: par[0].lower()))
        _fonte_pil.cache_clear()
    return _FONTES


def arquivo_da_fonte(nome, reserva=None):
    """Arquivo da fonte pelo nome (sem ligar para maiúsculas); senão o arquivo reserva; senão o da Arial."""
    fontes = fontes_instaladas()
    if nome in fontes:
        return fontes[nome]
    por_minuscula = {n.lower(): a for n, a in fontes.items()}
    if str(nome or "").lower() in por_minuscula:
        return por_minuscula[str(nome).lower()]
    if reserva and Path(reserva).is_file():
        return str(reserva)
    return por_minuscula.get(FONTE_PADRAO.lower())


@functools.lru_cache(maxsize=96)
def _fonte_pil(arquivo, tamanho):
    tamanho = max(1, int(tamanho))
    try:
        if arquivo:
            return ImageFont.truetype(arquivo, tamanho)
    except OSError:
        pass
    try:
        return ImageFont.load_default(size=tamanho)
    except TypeError:
        return ImageFont.load_default()


# ---------------------------------------------------------------- medidas e quebra de linha

def _largura(linha, fonte, track):
    if not linha:
        return 0.0
    if track:
        return sum(fonte.getlength(c) for c in linha) + track * (len(linha) - 1)
    return fonte.getlength(linha)


def _passo(fonte, fator):
    try:
        asc, desc = fonte.getmetrics()
    except AttributeError:
        asc, desc = fonte.size, 0
    return max(1.0, (asc + desc) * fator)


def _quebrar(texto, fonte, largura_max, track):
    linhas = []
    for paragrafo in str(texto).split("\n"):
        atual = ""
        for palavra in paragrafo.split(" "):
            teste = palavra if not atual else atual + " " + palavra
            if not atual or _largura(teste, fonte, track) <= largura_max:
                atual = teste
            else:
                linhas.append(atual)
                atual = palavra
        linhas.append(atual)
    return linhas


def _cabe(bloco, arquivo, tamanho, w, h):
    fonte = _fonte_pil(arquivo, tamanho)
    track = tamanho * float(bloco["espaco_letras"])
    linhas = _quebrar(bloco["_texto"], fonte, w, track)
    alto = len(linhas) * _passo(fonte, float(bloco["espaco_linhas"]))
    largo = max((_largura(l, fonte, track) for l in linhas), default=0.0)
    return largo <= w and alto <= h


def tamanho_que_cabe(bloco, arquivo, w, h):
    """Maior corpo (px) em que o texto, quebrado em linhas, cabe em w x h."""
    lo, hi, melhor = 4, max(8, int(h)), 4
    while lo <= hi:
        meio = (lo + hi) // 2
        if _cabe(bloco, arquivo, meio, w, h):
            melhor, lo = meio, meio + 1
        else:
            hi = meio - 1
    return melhor


# ---------------------------------------------------------------- desenho

def renderizar(bloco, tamanho_px, px_por_mm):
    """RGBA transparente do tamanho do bloco, com só o texto desenhado."""
    bloco = completar(bloco)
    w, h = max(1, int(tamanho_px[0])), max(1, int(tamanho_px[1]))
    camada = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    texto = str(bloco.get("texto") or "")
    if not texto.strip():
        return camada
    if bloco.get("maiusculas"):
        texto = texto.upper()
    bloco["_texto"] = texto
    arquivo = arquivo_da_fonte(bloco.get("fonte"), bloco.get("arquivo"))
    if bloco.get("tamanho_pt"):
        tamanho = max(4, int(round(float(bloco["tamanho_pt"]) * 25.4 / 72.0 * px_por_mm)))
    else:
        tamanho = tamanho_que_cabe(bloco, arquivo, w * 0.98, h * 0.98)
    fonte = _fonte_pil(arquivo, tamanho)
    track = tamanho * float(bloco["espaco_letras"])
    passo = _passo(fonte, float(bloco["espaco_linhas"]))
    linhas = _quebrar(texto, fonte, w, track)
    alto = passo * len(linhas)
    y = {"topo": 0.0, "meio": (h - alto) / 2.0, "base": h - alto}.get(bloco["alinhar_v"], (h - alto) / 2.0)
    r, g, b = cor_rgb(bloco["cor"], (255, 255, 255))
    opacidade = max(0.0, min(100.0, float(bloco["opacidade"]))) / 100.0
    d = ImageDraw.Draw(camada)
    for linha in linhas:
        largo = _largura(linha, fonte, track)
        x = {"esquerda": 0.0, "centro": (w - largo) / 2.0, "direita": w - largo}.get(bloco["alinhar"], (w - largo) / 2.0)
        if track:
            for letra in linha:
                d.text((x, y), letra, font=fonte, fill=(r, g, b, 255), anchor="la")
                x += fonte.getlength(letra) + track
        elif linha:
            d.text((x, y), linha, font=fonte, fill=(r, g, b, 255), anchor="la")
        y += passo
    if bloco.get("sombra"):
        raio = max(2.0, tamanho * 0.035)
        sombra = Image.new("RGBA", (w, h), (0, 0, 0, 255))
        sombra.putalpha(camada.split()[3].filter(ImageFilter.GaussianBlur(raio)).point(lambda v: int(v * 0.6)))
        solta = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        solta.paste(sombra, (int(round(tamanho * 0.02)), int(round(tamanho * 0.04))))
        camada = Image.alpha_composite(solta, camada)
    if opacidade < 1.0:  # opacidade da camada inteira (texto + sombra juntos)
        camada.putalpha(camada.getchannel("A").point(lambda v: int(round(v * opacidade))))
    return camada


def colar_textos(base, textos, px_por_mm=None):
    """Cola os blocos de texto (RGBA transparente) por cima de `base`, cada um na sua área."""
    for bloco in textos or []:
        x, y, w, h = caixa_px(bloco, base.size)
        camada = Image.new("RGBA", base.size, (0, 0, 0, 0))
        camada.paste(renderizar(bloco, (w, h), px_por_mm or base.size[0] / 297.0), (x, y))
        base.alpha_composite(camada)
    return base
