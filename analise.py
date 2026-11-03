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
VERSAO_CACHE = 3

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
            for chave in [c for c in self.itens if c[0] == prefixo or c[0].startswith(prefixo + os.sep)]:
                self.itens.pop(chave, None)

    def limpar(self):
        with self.trava:
            self.itens.clear()


_memoria_previa = _Lru(140)
_memoria_miniatura = _Lru(3500)


def miniatura(caminho, lado=LADO_MINIATURA):
    """
    Foto reduzida mantida exclusivamente em memória RAM (LRU) sem gravar
    cópias .jpg no disco (evita consumo desnecessário de espaço em disco).
    lado=LADO_MINIATURA para o painel de fotos, LADO_PREVIA para a lâmina e tela de edição.
    """
    memoria = _memoria_miniatura if lado <= LADO_MINIATURA else _memoria_previa
    ch_norm = normalizar_caminho(caminho)
    chave_rapida = (ch_norm, lado)
    guardada = memoria.pegar(chave_rapida)
    if guardada is not None:
        return guardada
    img = _abrir_reduzida(caminho, lado)
    memoria.por(chave_rapida, img)
    return img


def esquecer_miniaturas(caminho):
    """Depois que a foto foi trocada pela tratada."""
    chave = normalizar_caminho(caminho)
    _memoria_miniatura.esquecer(chave)
    _memoria_previa.esquecer(chave)


def esquecer_pasta(pasta):
    """Remove da memória RAM e do cache de análise todas as fotos de uma pasta (ex.: pasta temporária do Drive)."""
    global _cache_sujo
    if not pasta:
        return
    pref = normalizar_caminho(pasta)
    _memoria_miniatura.esquecer(pref)
    _memoria_previa.esquecer(pref)
    with _trava_cache:
        if _cache is not None and "fotos" in _cache:
            chaves_remover = [
                k for k in _cache["fotos"]
                if k.startswith(pref + os.sep) or k.startswith(pref + "|")
            ]
            for k in chaves_remover:
                _cache["fotos"].pop(k, None)
            if chaves_remover:
                _cache_sujo = True


def imagem_para(caminho, lado_necessario):
    """A menor versão da foto que ainda tem lado_necessario pixels no maior lado."""
    if lado_necessario <= LADO_MINIATURA:
        return miniatura(caminho, LADO_MINIATURA)
    if lado_necessario <= LADO_PREVIA:
        return miniatura(caminho, LADO_PREVIA)
    img, _icc, _dpi = abrir_imagem(caminho)
    return img


# ------------------------------------------------------------
# Rosto (múltiplas pessoas + caixa anti-corte), nitidez, orientação
# ------------------------------------------------------------

def _detector_yunet():
    global _detector
    if _detector is None:
        modelo = modelo_rosto()
        if not modelo.is_file():
            return None
        _detector = cv2.FaceDetectorYN.create(str(modelo), "", (320, 320), 0.58, 0.3, 5000)
    return _detector


def _iou_caixa(a, b):
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    iw, ih = max(0.0, ix1 - ix0), max(0.0, iy1 - iy0)
    inter = iw * ih
    if inter <= 0.0:
        return 0.0
    area_a = max(1e-6, (ax1 - ax0) * (ay1 - ay0))
    area_b = max(1e-6, (bx1 - bx0) * (by1 - by0))
    if inter / min(area_a, area_b) > 0.65:
        return 1.0
    return inter / max(1e-6, area_a + area_b - inter)


def detectar_rosto(img):
    """
    Detecta TODOS os rostos na imagem (YuNet DNN + fallback Haar Cascade),
    conta quantas pessoas existem ('qtd') e calcula a caixa de proteção de todo
    o grupo ('x0', 'y0', 'x1', 'y1' em 0..1, incluindo margem para topo da cabeça,
    cabelo/beca e ombros) para garantir que nenhuma pessoa seja cortada.
    """
    w, h = img.size
    if w < 24 or h < 24:
        return None
    bgr = cv2.cvtColor(np.asarray(img.convert("RGB")), cv2.COLOR_RGB2BGR)
    candidatos = []
    metodo_usado = None

    with _trava_rosto:
        det = _detector_yunet()
        if det is not None:
            try:
                det.setInputSize((w, h))
                _ok, faces = det.detect(bgr)
                if faces is not None and len(faces):
                    metodo_usado = "yunet"
                    for f in faces:
                        fx, fy, fw, fh = (float(v) for v in f[:4])
                        score = float(f[14]) if len(f) > 14 else 0.8
                        if fw < 8 or fh < 8:
                            continue
                        olhos_x = (float(f[4]) + float(f[6])) / 2.0 if len(f) >= 8 else (fx + fw / 2.0)
                        olhos_y = (float(f[5]) + float(f[7])) / 2.0 if len(f) >= 8 else (fy + fh * 0.42)
                        candidatos.append((score, fx, fy, fw, fh, olhos_x, olhos_y))
            except Exception:
                pass

    if not candidatos and hasattr(cv2, "CascadeClassifier"):
        global _haar
        if _haar is None:
            _haar = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        cinza = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        caixas = _haar.detectMultiScale(
            cinza,
            1.1,
            5,
            minSize=(max(22, min(w, h) // 18), max(22, min(w, h) // 18)),
        )
        if len(caixas):
            metodo_usado = "haar"
            for r in caixas:
                fx, fy, fw, fh = (float(v) for v in r)
                candidatos.append((0.68, fx, fy, fw, fh, fx + fw / 2.0, fy + fh * 0.42))

    if not candidatos:
        return None

    # Filtra detecções muito pequenas espúrias (< 8% da área do maior rosto) e aplica NMS
    maior_area = max(c[3] * c[4] for c in candidatos)
    candidatos = [c for c in candidatos if (c[3] * c[4]) >= maior_area * 0.08]
    candidatos.sort(key=lambda c: c[0] * (c[3] * c[4]), reverse=True)

    aceitos = []
    for cand in candidatos:
        _sc, fx, fy, fw, fh, _cx, _cy = cand
        caixa_c = (fx, fy, fx + fw, fy + fh)
        if any(_iou_caixa(caixa_c, (a[1], a[2], a[1] + a[3], a[2] + a[4])) > 0.36 for a in aceitos):
            continue
        aceitos.append(cand)

    if not aceitos:
        return None

    # Ordena os rostos da esquerda para a direita
    aceitos.sort(key=lambda c: c[5])

    lista_rostos = []
    gx0, gy0, gx1, gy1 = 1.0, 1.0, 0.0, 0.0
    soma_peso = 0.0
    soma_cx = 0.0
    soma_cy = 0.0
    maior_h_norm = 0.0

    for _sc, fx, fy, fw, fh, cx, cy in aceitos:
        cx_n = max(0.0, min(1.0, cx / float(w)))
        cy_n = max(0.0, min(1.0, cy / float(h)))
        fw_n = min(1.0, fw / float(w))
        fh_n = min(1.0, fh / float(h))
        maior_h_norm = max(maior_h_norm, fh_n)

        # Margem de segurança de cada pessoa (protege topo da cabeça/cabelo/beca, ombros laterais e queixo/colo)
        pad_x = max(0.035, fw_n * 0.42)
        pad_top = max(0.045, fh_n * 0.52)
        pad_bot = max(0.055, fh_n * 0.65)

        rx0 = max(0.0, (fx / float(w)) - pad_x)
        ry0 = max(0.0, (fy / float(h)) - pad_top)
        rx1 = min(1.0, ((fx + fw) / float(w)) + pad_x)
        ry1 = min(1.0, ((fy + fh) / float(h)) + pad_bot)

        gx0 = min(gx0, rx0)
        gy0 = min(gy0, ry0)
        gx1 = max(gx1, rx1)
        gy1 = max(gy1, ry1)

        peso = max(0.001, fw_n * fh_n)
        soma_peso += peso
        soma_cx += cx_n * peso
        soma_cy += cy_n * peso

        lista_rostos.append({
            "cx": round(cx_n, 4),
            "cy": round(cy_n, 4),
            "w": round(fw_n, 4),
            "h": round(fh_n, 4),
            "x0": round(rx0, 4),
            "y0": round(ry0, 4),
            "x1": round(rx1, 4),
            "y1": round(ry1, 4),
        })

    # Centro do grupo: quando há 2+ pessoas, prioriza o centro da caixa do grupo para não cortar as pessoas das extremidades
    centro_caixa_x = (gx0 + gx1) * 0.5
    centro_caixa_y = (gy0 + gy1) * 0.5
    if len(lista_rostos) >= 2:
        cx_final = centro_caixa_x * 0.78 + (soma_cx / soma_peso) * 0.22
        cy_final = centro_caixa_y * 0.68 + (soma_cy / soma_peso) * 0.32
    else:
        cx_final = soma_cx / soma_peso
        cy_final = soma_cy / soma_peso

    return {
        "cx": round(max(0.0, min(1.0, cx_final)), 4),
        "cy": round(max(0.0, min(1.0, cy_final)), 4),
        "h": round(maior_h_norm, 4),
        "qtd": len(lista_rostos),
        "x0": round(gx0, 4),
        "y0": round(gy0, 4),
        "x1": round(gx1, 4),
        "y1": round(gy1, 4),
        "rostos": lista_rostos,
        "metodo": metodo_usado or "yunet",
    }


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


def _eh_caminho_temporario(caminho_norm):
    c_low = str(caminho_norm or "").lower()
    return (
        "smartalbum_drive_" in c_low
        or "\\appdata\\local\\temp\\" in c_low
        or "/tmp/smartalbum_drive_" in c_low
    )


def gravar_cache():
    global _cache_sujo
    with _trava_cache:
        if _cache is not None and _cache_sujo:
            try:
                fotos_atuais = _cache.get("fotos") or {}
                fotos_filtradas = {}
                for k, v in list(fotos_atuais.items()):
                    cam = k.split("|", 1)[0]
                    if _eh_caminho_temporario(cam):
                        continue
                    if not Path(cam).is_file():
                        continue
                    fotos_filtradas[k] = v
                # Limita a no máximo 600 entradas recentes para não inflar o analise.json no disco
                if len(fotos_filtradas) > 600:
                    itens_rec = list(fotos_filtradas.items())[-600:]
                    fotos_filtradas = dict(itens_rec)
                _cache["fotos"] = fotos_filtradas
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


PASTAS_IGNORADAS = {"saida", "saída", ".git", "__pycache__"}


def listar_imagens(pasta, subpastas=True):
    """Imagens da pasta (e subpastas), sem entrar nas pastas de saída do álbum e ignorando erros de permissão."""
    pasta = Path(pasta)
    if not pasta.is_dir():
        return []
    fotos = []
    if not subpastas:
        try:
            for f in pasta.iterdir():
                if (
                    f.is_file()
                    and f.suffix.lower() in EXTENSOES
                    and not f.name.startswith(".")
                    and not f.name.endswith(".parcial")
                ):
                    fotos.append(f)
        except OSError:
            pass
    else:
        for raiz, dirs, arqs in os.walk(str(pasta), onerror=lambda _e: None):
            dirs[:] = [
                d for d in dirs
                if not d.startswith(".")
                and d.casefold() not in PASTAS_IGNORADAS
            ]
            pasta_raiz = Path(raiz)
            for nome_arq in arqs:
                if nome_arq.startswith(".") or nome_arq.endswith(".parcial"):
                    continue
                if Path(nome_arq).suffix.lower() in EXTENSOES:
                    fotos.append(pasta_raiz / nome_arq)

    def _chave_rel(f):
        try:
            return [chave_natural(p) for p in f.relative_to(pasta).parts]
        except ValueError:
            return [chave_natural(p) for p in f.parts]

    return sorted(fotos, key=_chave_rel)

