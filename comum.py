# -*- coding: utf-8 -*-
"""Caminhos, JSON e cópia de arquivos usados por todo o programa."""

import json
import os
import re
import shutil
import sys
import threading
import time
from pathlib import Path

EXTENSOES = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}
EXTENSOES_IMAGEM = EXTENSOES
EXTENSOES_RAW = {".cr2", ".cr3", ".nef", ".arw", ".raf", ".orf", ".rw2", ".dng", ".pef", ".srw"}


def pasta_programa():
    """Pasta do programa: ao lado do exe, ou ao lado deste arquivo."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def pasta_recurso(nome):
    """
    Pasta de recursos (produtos, modelos, plugin) ao lado do programa. No exe,
    a primeira vez copia a que veio dentro dele, para os modelos novos ficarem salvos ali.
    """
    ao_lado = pasta_programa() / nome
    if ao_lado.is_dir():
        return ao_lado
    interno = getattr(sys, "_MEIPASS", None)
    if interno and (Path(interno) / nome).is_dir():
        try:
            shutil.copytree(Path(interno) / nome, ao_lado)
            return ao_lado
        except OSError:
            return Path(interno) / nome
    return ao_lado


def pasta_dados():
    """Cache, recentes e comunicação com o Lightroom, fora da pasta das fotos."""
    base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    pasta = base / "smart_album"
    pasta.mkdir(parents=True, exist_ok=True)
    return pasta


def modelo_rosto():
    """Modelo YuNet: no programa, ou o que já está no album_molde."""
    nome = "face_detection_yunet_2023mar.onnx"
    candidatos = [
        pasta_programa() / "recursos" / nome,
        pasta_programa().parent / "album_molde" / "modelos" / nome,
    ]
    interno = getattr(sys, "_MEIPASS", None)
    if interno:
        candidatos.insert(0, Path(interno) / "recursos" / nome)
    for caminho in candidatos:
        if caminho.is_file():
            return caminho
    return candidatos[0]


def chave_natural(caminho):
    partes = re.split(r"(\d+)", Path(caminho).name)
    return [int(p) if p.isdigit() else p.casefold() for p in partes]


def normalizar_caminho(caminho):
    return os.path.normcase(os.path.abspath(str(caminho)))


def mm_para_px(mm, dpi):
    return mm * dpi / 25.4


def ler_json(caminho, padrao=None):
    caminho = Path(caminho)
    if not caminho.is_file():
        return padrao
    try:
        return json.loads(caminho.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return padrao


def gravar_json(caminho, dados):
    """Grava num .tmp e troca de uma vez, para nunca deixar o arquivo pela metade."""
    caminho = Path(caminho)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    temp = caminho.with_name("%s.%s.tmp" % (caminho.name, threading.get_ident()))
    temp.write_text(json.dumps(dados, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, caminho)


def substituir_arquivo(origem, destino, tentativas=5, pausa=2.0):
    """
    Coloca origem no lugar de destino. Copia para um .parcial ao lado do
    destino e troca com os.replace: o destino nunca fica meio gravado.
    Tenta de novo quando o arquivo está travado ou a rede falha.
    """
    origem = Path(origem)
    destino = Path(destino)
    parcial = destino.with_name(destino.name + ".parcial")
    ultimo_erro = None
    for tentativa in range(1, tentativas + 1):
        try:
            destino.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(origem, parcial)
            if parcial.stat().st_size != origem.stat().st_size:
                raise OSError("cópia incompleta (tamanho diferente)")
            os.replace(parcial, destino)
            return
        except OSError as e:
            ultimo_erro = e
            try:
                if parcial.exists():
                    parcial.unlink()
            except OSError:
                pass
            if tentativa < tentativas:
                time.sleep(pausa)
    raise OSError("Não foi possível gravar %s depois de %s tentativas: %s" % (destino, tentativas, ultimo_erro))


def apagar_pasta(pasta, tentativas=5, pausa=1.0):
    for _ in range(tentativas):
        shutil.rmtree(pasta, ignore_errors=True)
        if not Path(pasta).exists():
            return True
        time.sleep(pausa)
    return False


def cor_rgb(valor, padrao=(255, 255, 255)):
    """'#rrggbb' ou [r, g, b] para tupla."""
    if isinstance(valor, (list, tuple)) and len(valor) >= 3:
        return tuple(int(v) for v in valor[:3])
    if isinstance(valor, str):
        texto = valor.strip().lstrip("#")
        if len(texto) == 6:
            try:
                return tuple(int(texto[i:i + 2], 16) for i in (0, 2, 4))
            except ValueError:
                pass
    return padrao


def cor_hex(valor):
    r, g, b = cor_rgb(valor)
    return "#%02x%02x%02x" % (r, g, b)
