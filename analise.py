# -*- coding: utf-8 -*-
"""
Análise das fotos: tamanho, orientação, nitidez, rosto e data.

O resultado fica num cache em disco (fora da pasta das fotos), pela chave
caminho + data de modificação + tamanho: se a foto for trocada pela tratada,
ela é analisada de novo.
"""

import hashlib
import os
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageFile, ImageOps

ImageFile.LOAD_TRUNCATED_IMAGES = True

from comum import (
    EXTENSOES, chave_natural, gravar_json, ler_json, modelo_rosto, normalizar_caminho, pasta_dados,
)

LADO_ANALISE = 1024
LADO_PREVIA = 1600
LADO_MINIATURA = 220
TOLERANCIA_QUADRADO = 0.08
VERSAO_CACHE = 2

PASTA_MINIATURAS = pasta_dados() / "miniaturas"
ARQUIVO_CACHE = pasta_dados() / "analise.json"

_detector = None
_haar = None
_trava_rosto = threading.Lock()
_trava_cache = threading.Lock()
_cache = None
_cache_sujo = False


# ------------------------------------------------------------
# Abrir imagens
# ------------------------------------------------------------

def abrir_imagem(caminho):
    """Imagem inteira em RGB, já na orientação do EXIF. Devolve (img, icc, dpi)."""
    with Image.open(caminho) as raw:
        icc = raw.info.get("icc_profile")
        dpi = raw.info.get("dpi")
        img = ImageOps.exif_transpose(raw)
        if img.mode != "RGB":
            img = img.convert("RGB")
        else:
            img = img.copy()
    return img, icc, dpi


def _abrir_reduzida(caminho, lado):
    """Decodifica o JPEG já reduzido (draft), bem mais rápido que abrir inteiro."""
    with Image.open(caminho) as raw:
        try:
            raw.draft("RGB", (lado, lado))
        except Exception:
            pass
        img = ImageOps.exif_transpose(raw)
        if img.mode != "RGB":
            img = img.convert("RGB")
        else:
            img = img.copy()
    if max(img.size) > lado:
        escala = lado / float(max(img.size))
        filtro = Image.BILINEAR if lado <= LADO_MINIATURA else Image.LANCZOS
        img = img.resize((max(1, round(img.width * escala)), max(1, round(img.height * escala))), filtro)
    return img


def _arquivo_miniatura(caminho, lado):
    st = Path(caminho).stat()
    chave = "%s|%s|%s|%s" % (normalizar_caminho(caminho), st.st_mtime_ns, st.st_size, lado)
    return PASTA_MINIATURAS / (hashlib.sha1(chave.encode("utf-8")).hexdigest() + ".jpg")


class _Lru:
    def __init__(self, limite):
        self.limite = limite
        self.itens = OrderedDict()
        self.trava = threading.Lock()

    def pegar(self, chave):
        with self.trava:
            if chave in self.itens:
                self.itens.move_to_end(chave)
                return self.itens[chave]
        return None

    def por(self, chave, valor):
        with self.trava:
            self.itens[chave] = valor
            self.itens.move_to_end(chave)
            while len(self.itens) > self.limite:
                self.itens.popitem(last=False)

    def esquecer(self, prefixo):
        with self.trava:
            for chave in [c for c in self.itens if c[0] == prefixo]:
                self.itens.pop(chave, None)


_memoria_previa = _Lru(140)
_memoria_miniatura = _Lru(3500)


def miniatura(caminho, lado=LADO_MINIATURA):
    """
    Foto reduzida guardada em disco e na memória. lado=LADO_MINIATURA para o
    painel de fotos, LADO_PREVIA para a lâmina e a tela de edição.
    Consulta primeiro a memória RAM sem I/O de disco (stat) para resposta instantânea.
    """
    memoria = _memoria_miniatura if lado <= LADO_MINIATURA else _memoria_previa
    ch_norm = normalizar_caminho(caminho)
    chave_rapida = (ch_norm, lado)
    guardada = memoria.pegar(chave_rapida)
    if guardada is not None:
        return guardada
    try:
        destino = _arquivo_miniatura(caminho, lado)
    except OSError:
        destino = None
    img = None
    if destino is not None and destino.is_file():
        try:
            with Image.open(destino) as arquivo:
                img = arquivo.convert("RGB")
        except OSError:
            img = None
    if img is None:
        img = _abrir_reduzida(caminho, lado)
        if destino is not None:
            try:
                PASTA_MINIATURAS.mkdir(parents=True, exist_ok=True)
                temp = destino.with_name("%s.%s.tmp" % (destino.stem, threading.get_ident()))
                img.save(temp, "JPEG", quality=88)
                os.replace(temp, destino)
            except OSError:
                pass
    memoria.por(chave_rapida, img)
    return img


def esquecer_miniaturas(caminho):
    """Depois que a foto foi trocada pela tratada."""
    chave = normalizar_caminho(caminho)
    _memoria_miniatura.esquecer(chave)
    _memoria_previa.esquecer(chave)


def imagem_para(caminho, lado_necessario):
    """A menor versão da foto que ainda tem lado_necessario pixels no maior lado."""
    if lado_necessario <= LADO_MINIATURA:
        return miniatura(caminho, LADO_MINIATURA)
    if lado_necessario <= LADO_PREVIA:
        return miniatura(caminho, LADO_PREVIA)
    img, _icc, _dpi = abrir_imagem(caminho)
    return img


# ------------------------------------------------------------
# Rosto, nitidez, orientação
# ------------------------------------------------------------

def _detector_yunet():
    global _detector
    if _detector is None:
        modelo = modelo_rosto()
        if not modelo.is_file():
            return None
        _detector = cv2.FaceDetectorYN.create(str(modelo), "", (320, 320), 0.7, 0.3, 5000)
    return _detector


def detectar_rosto(img):
    """Maior rosto, em fração da imagem: {cx, cy (altura dos olhos), h}, ou None."""
    w, h = img.size
    bgr = cv2.cvtColor(np.asarray(img.convert("RGB")), cv2.COLOR_RGB2BGR)
    with _trava_rosto:
        det = _detector_yunet()
        if det is not None:
            det.setInputSize((w, h))
            _ok, faces = det.detect(bgr)
            if faces is not None and len(faces):
                f = max(faces, key=lambda r: r[2] * r[3])
                fx, fy, fw, fh = (float(v) for v in f[:4])
                olhos_y = (float(f[5]) + float(f[7])) / 2
                return {"cx": (fx + fw / 2) / w, "cy": olhos_y / h, "h": fh / h, "metodo": "yunet"}
    global _haar
    if not hasattr(cv2, "CascadeClassifier"):
        return None
    if _haar is None:
        _haar = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    cinza = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    caixas = _haar.detectMultiScale(cinza, 1.1, 6, minSize=(30, 30))
    if len(caixas):
        fx, fy, fw, fh = (float(v) for v in max(caixas, key=lambda r: r[2] * r[3]))
        return {"cx": (fx + fw / 2) / w, "cy": (fy + fh * 0.4) / h, "h": fh / h, "metodo": "haar"}
    return None


def nitidez(img):
    pequena = img.convert("L")
    escala = min(1.0, 480 / float(max(pequena.size)))
    if escala < 1.0:
        pequena = pequena.resize((max(1, round(pequena.width * escala)), max(1, round(pequena.height * escala))))
    return float(cv2.Laplacian(np.asarray(pequena), cv2.CV_64F).var())


def assinatura_cenario(img):
    """
    Assinatura visual de cenário (128 bins) combinando:
    - Anel externo do fundo (ignora o centro onde ficam roupas/pessoas)
    - Faixa superior (céu/iluminação/teto) e inferior (piso/gramado)
    - Histograma HSV global
    """
    pequena = img.convert("RGB").resize((96, 96), Image.Resampling.BILINEAR)
    hsv = cv2.cvtColor(np.asarray(pequena), cv2.COLOR_RGB2HSV)

    # Máscara do anel externo (fundo real ao redor dos assuntos)
    mascara_fundo = np.ones((96, 96), dtype=np.uint8) * 255
    mascara_fundo[22:78, 22:74] = 0

    hist_fundo = cv2.calcHist([hsv], [0, 1, 2], mascara_fundo, [8, 3, 2], [0, 180, 0, 256, 0, 256])
    cv2.normalize(hist_fundo, hist_fundo, alpha=0.50, norm_type=cv2.NORM_L1)

    # Faixa superior (topo 35% da imagem: luz ambiente / céu / fundo)
    topo_hsv = hsv[:34, :, :]
    hist_topo = cv2.calcHist([topo_hsv], [0, 1, 2], None, [8, 2, 2], [0, 180, 0, 256, 0, 256])
    cv2.normalize(hist_topo, hist_topo, alpha=0.25, norm_type=cv2.NORM_L1)

    # Histograma global complementar
    hist_global = cv2.calcHist([hsv], [0, 1, 2], None, [8, 3, 2], [0, 180, 0, 256, 0, 256])
    cv2.normalize(hist_global, hist_global, alpha=0.25, norm_type=cv2.NORM_L1)

    vetor = np.concatenate([hist_fundo.reshape(-1), hist_topo.reshape(-1), hist_global.reshape(-1)])
    soma = float(np.sum(vetor))
    if soma > 1e-9:
        vetor = vetor / soma
    return vetor.astype(float).tolist()


def hash_perceptual(caminho):
    """Hash visual para reconhecer cópias iguais com nomes ou compressão diferentes."""
    img = miniatura(caminho, 128).convert("L").resize((32, 32), Image.Resampling.LANCZOS)
    coef = cv2.dct(np.asarray(img, dtype=np.float32))[:8, :8].reshape(-1)
    mediana = float(np.median(coef[1:]))
    valor = 0
    for coeficiente in coef:
        valor = (valor << 1) | int(coeficiente > mediana)
    return valor


def orientacao(w, h):
    proporcao = w / float(h)
    if abs(proporcao - 1.0) <= TOLERANCIA_QUADRADO:
        return "quadrado"
    return "horizontal" if w > h else "vertical"


def _data_foto(caminho):
    """Data em que a foto foi tirada (EXIF), ou a data do arquivo."""
    try:
        with Image.open(caminho) as img:
            exif = img.getexif()
            texto = exif.get_ifd(0x8769).get(36867) or exif.get(306)
        if texto:
            return datetime.strptime(str(texto).strip()[:19], "%Y:%m:%d %H:%M:%S").timestamp()
    except Exception:
        pass
    try:
        return Path(caminho).stat().st_mtime
    except OSError:
        return 0.0


def _tamanho_real(caminho):
    """Largura e altura já na orientação do EXIF, sem decodificar a imagem."""
    with Image.open(caminho) as img:
        w, h = img.size
        try:
            if img.getexif().get(0x0112) in (5, 6, 7, 8):
                w, h = h, w
        except Exception:
            pass
    return w, h


# ------------------------------------------------------------
# Cache
# ------------------------------------------------------------

def _carregar_cache():
    global _cache
    if _cache is None:
        dados = ler_json(ARQUIVO_CACHE, {}) or {}
        if dados.get("versao") != VERSAO_CACHE:
            dados = {"versao": VERSAO_CACHE, "fotos": {}}
        _cache = dados
    return _cache


def gravar_cache():
    global _cache_sujo
    with _trava_cache:
        if _cache is not None and _cache_sujo:
            try:
                gravar_json(ARQUIVO_CACHE, _cache)
                _cache_sujo = False
            except OSError:
                pass


def _chave_cache(caminho):
    st = Path(caminho).stat()
    return "%s|%s|%s" % (normalizar_caminho(caminho), st.st_mtime_ns, st.st_size)


def analisar_foto(caminho):
    """Dados de uma foto, do cache ou calculados agora."""
    global _cache_sujo
    chave = _chave_cache(caminho)
    with _trava_cache:
        guardado = _carregar_cache()["fotos"].get(chave)
    if guardado:
        if not guardado.get("cenario"):
            assinatura = assinatura_cenario(miniatura(caminho, LADO_PREVIA))
            guardado["cenario"] = assinatura
            with _trava_cache:
                _cache_sujo = True
        return guardado
    w, h = _tamanho_real(caminho)
    img = miniatura(caminho, LADO_PREVIA)
    analise = img
    if max(img.size) > LADO_ANALISE:
        escala = LADO_ANALISE / float(max(img.size))
        analise = img.resize((max(1, round(img.width * escala)), max(1, round(img.height * escala))))
    miniatura(caminho, LADO_MINIATURA)
    dados = {
        "w": w,
        "h": h,
        "proporcao": w / float(h),
        "orientacao": orientacao(w, h),
        "nitidez": nitidez(analise),
        "rosto": detectar_rosto(analise),
        "cenario": assinatura_cenario(analise),
        "data": _data_foto(caminho),
    }
    with _trava_cache:
        _carregar_cache()["fotos"][chave] = dados
        _cache_sujo = True
    return dados


def analisar_fotos(caminhos, ao_progresso=None, parar=lambda: False, trabalhadores=None):
    """{caminho normalizado: dados}. Fotos com erro ficam de fora (e vão para erros)."""
    caminhos = list(caminhos)
    if not caminhos:
        return {}, {}
    if trabalhadores is None:
        trabalhadores = min(6, max(4, (os.cpu_count() or 4)))
    info = {}
    erros = {}
    feitos = [0]
    trava = threading.Lock()

    def um(caminho):
        if parar():
            return
        try:
            dados = analisar_foto(caminho)
            with trava:
                info[normalizar_caminho(caminho)] = dados
        except Exception as e:
            with trava:
                erros[str(caminho)] = str(e)
        with trava:
            feitos[0] += 1
            n = feitos[0]
        if ao_progresso:
            ao_progresso(n, len(caminhos), caminho)

    with ThreadPoolExecutor(max_workers=trabalhadores) as pool:
        list(pool.map(um, caminhos))
    gravar_cache()
    return info, erros


PASTAS_IGNORADAS = {"saida", "saída"}


def listar_imagens(pasta, subpastas=True):
    """Imagens da pasta (e subpastas), sem entrar nas pastas de saída do álbum."""
    pasta = Path(pasta)
    if not pasta.is_dir():
        return []
    iterador = pasta.rglob("*") if subpastas else pasta.iterdir()
    fotos = [
        f for f in iterador
        if f.is_file() and f.suffix.lower() in EXTENSOES and not f.name.startswith(".")
        and not f.name.endswith(".parcial")
        and not any(p.casefold() in PASTAS_IGNORADAS for p in f.relative_to(pasta).parts[:-1])
    ]
    return sorted(fotos, key=lambda f: [chave_natural(p) for p in f.relative_to(pasta).parts])
