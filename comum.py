# -*- coding: utf-8 -*-
"""Caminhos, JSON e cópia de arquivos usados por todo o programa."""

import gc
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

try:
    from PIL import Image as _PIL_Img_Compat
    for _enum_c in (getattr(_PIL_Img_Compat, "Resampling", None), getattr(_PIL_Img_Compat, "Transpose", None)):
        if _enum_c is not None:
            for _k in dir(_enum_c):
                if _k.isupper() and not hasattr(_PIL_Img_Compat, _k):
                    setattr(_PIL_Img_Compat, _k, getattr(_enum_c, _k))
    if not hasattr(_PIL_Img_Compat, "ANTIALIAS") and hasattr(_PIL_Img_Compat, "LANCZOS"):
        setattr(_PIL_Img_Compat, "ANTIALIAS", getattr(_PIL_Img_Compat, "LANCZOS"))
except Exception:
    pass

EXTENSOES = {".jpg", ".jpeg", ".jfif", ".png", ".tif", ".tiff", ".webp", ".bmp"}
EXTENSOES_IMAGEM = EXTENSOES
EXTENSOES_RAW = {".cr2", ".cr3", ".nef", ".arw", ".raf", ".orf", ".rw2", ".dng", ".pef", ".srw"}


def pasta_programa():
    """Pasta do programa: ao lado do exe, ou ao lado deste arquivo."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def pasta_recurso(nome):
    """
    Pasta de recursos (produtos, recursos, lightroom_plugin) somente para leitura.
    NUNCA copia nem cria pastas duplicadas dentro do projeto ou de dist/Album.
    """
    base_prog = pasta_programa()
    ao_lado = base_prog / nome
    if ao_lado.is_dir():
        return ao_lado
    interno = getattr(sys, "_MEIPASS", None)
    candidatos = [
        base_prog.parent / nome,
        base_prog.parent.parent / nome,
        base_prog / "_internal" / nome,
        base_prog / "dist" / "Album" / "_internal" / nome,
    ]
    if interno:
        candidatos.insert(0, Path(interno) / nome)
    for cand in candidatos:
        if cand.is_dir():
            return cand
    return ao_lado


def pasta_dados():
    """Cache, recentes e comunicação com o Lightroom, fora da pasta das fotos."""
    base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    pasta = base / "smart_album"
    pasta.mkdir(parents=True, exist_ok=True)
    return pasta


def pasta_fundos_temporarios():
    """
    Pasta temporária onde os fundos gerados pelo Diagramador Inteligente são salvos como
    arquivos .png reais (prévia e abertura externa). O programa apaga a pasta ao abrir e ao
    fechar; o projeto guarda a receita (fundo_imagem["gerado"]) e o PNG é recriado quando usado.
    """
    pasta = pasta_dados() / "fundos_temp"
    pasta.mkdir(parents=True, exist_ok=True)
    return pasta


def listar_fundos_temporarios():
    """Lista os arquivos de fundos gerados salvos na pasta temporária (mais recentes primeiro)."""
    pasta = pasta_fundos_temporarios()
    arquivos = []
    try:
        for item in pasta.iterdir():
            if item.is_file() and item.suffix.lower() in EXTENSOES:
                try:
                    mtime = item.stat().st_mtime
                except OSError:
                    mtime = 0.0
                arquivos.append((mtime, str(item)))
    except OSError:
        return []
    arquivos.sort(key=lambda x: x[0], reverse=True)
    return [c for _m, c in arquivos]


def limpar_fundos_temporarios(manter_caminhos=None):
    """
    Remove os arquivos de fundos gerados da pasta temporária quando solicitado pelo usuário.
    Se `manter_caminhos` for informado, preserva esses arquivos.
    Devolve `(qtd_removidos, bytes_liberados)`.
    """
    pasta = pasta_fundos_temporarios()
    protegidos = {normalizar_caminho(c) for c in (manter_caminhos or ()) if c}
    removidos = 0
    bytes_liberados = 0
    try:
        for item in list(pasta.iterdir()):
            if not item.is_file():
                continue
            if item.suffix.lower() not in EXTENSOES and not item.name.endswith(".tmp"):
                continue
            if normalizar_caminho(item) in protegidos:
                continue
            try:
                tam = item.stat().st_size
                item.unlink()
                removidos += 1
                bytes_liberados += tam
            except OSError:
                pass
    except OSError:
        pass
    return removidos, bytes_liberados


def modelo_rosto():
    """Modelo YuNet: no programa, em _internal, na raiz do projeto ou no album_molde."""
    nome = "face_detection_yunet_2023mar.onnx"
    base_prog = pasta_programa()
    candidatos = [
        base_prog / "recursos" / nome,
        base_prog / "_internal" / "recursos" / nome,
        base_prog / "dist" / "Album" / "_internal" / "recursos" / nome,
        base_prog.parent / "recursos" / nome,
        base_prog.parent.parent / "recursos" / nome,
        base_prog.parent / "album_molde" / "modelos" / nome,
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
    """Grava num .tmp e troca de uma vez, garantindo remoção do .tmp em caso de falha."""
    caminho = Path(caminho)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    temp = caminho.with_name("%s.%s.tmp" % (caminho.name, threading.get_ident()))
    try:
        temp.write_text(json.dumps(dados, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(temp, caminho)
    finally:
        try:
            if temp.exists():
                temp.unlink()
        except OSError:
            pass


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


def _forcar_remocao_readonly(func, path, _exc_info):
    """Remove atributo somente-leitura no Windows quando shutil.rmtree encontra arquivo bloqueado."""
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        func(path)
    except OSError:
        pass


def apagar_pasta(pasta, tentativas=6, pausa=0.25):
    """Apaga uma pasta e todo seu conteúdo liberando descritores de arquivo e travas readonly no Windows."""
    if not pasta:
        return True
    alvo = Path(pasta)
    if not alvo.exists():
        return True
    gc.collect()
    for tentativa in range(tentativas):
        try:
            shutil.rmtree(alvo, onerror=_forcar_remocao_readonly)
        except Exception:
            pass
        if not alvo.exists():
            return True
        if tentativa == 1:
            gc.collect()
        time.sleep(pausa)
    return not alvo.exists()


def _tamanho_caminho_bytes(caminho):
    p = Path(caminho)
    if not p.exists():
        return 0
    if p.is_file():
        try:
            return p.stat().st_size
        except OSError:
            return 0
    total = 0
    try:
        for arq in p.rglob("*"):
            if arq.is_file():
                try:
                    total += arq.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


def limpar_temporarios_disco(pastas_protegidas=(), limpar_autosave=False):
    """
    Varre e remove arquivos e pastas temporárias que consomem espaço desnecessário no disco:
    1. Pastas órfãs no %TEMP% ('SmartAlbum_Drive_*', 'smart_album_ps_*', 'smart_album_acoes_*')
    2. Pasta antiga de miniaturas em disco (%LOCALAPPDATA%\\smart_album\\miniaturas)
    3. Pasta temporária do Lightroom (%LOCALAPPDATA%\\smart_album\\lightroom\\temp)
    4. Arquivos temporários órfãos (*.tmp, *.parcial, *.psd_tmp) em %LOCALAPPDATA%\\smart_album
    5. Autosaves antigos em %LOCALAPPDATA%\\smart_album\\autosave
    Retorna (qtd_removidos, bytes_liberados).
    """
    protegidas_norm = {normalizar_caminho(p) for p in (pastas_protegidas or ()) if p}
    qtd_removidos = 0
    bytes_liberados = 0

    # 1. Limpa pastas temporárias órfãs no diretório %TEMP% do sistema
    try:
        dir_temp = Path(tempfile.gettempdir())
        prefixos_temp = ("SmartAlbum_Drive_", "smart_album_ps_", "smart_album_acoes_")
        if dir_temp.is_dir():
            for item in dir_temp.iterdir():
                nome = item.name
                if any(nome.startswith(pref) for pref in prefixos_temp):
                    ch_item = normalizar_caminho(item)
                    if ch_item in protegidas_norm or any(
                        p.startswith(ch_item + os.sep) for p in protegidas_norm
                    ):
                        continue
                    tam = _tamanho_caminho_bytes(item)
                    if apagar_pasta(item, tentativas=4, pausa=0.15):
                        qtd_removidos += 1
                        bytes_liberados += tam
    except Exception:
        pass

    # 2. Limpa a pasta de dados do Smart Album (%LOCALAPPDATA%\smart_album)
    try:
        dados_dir = pasta_dados()
        # Miniaturas não ficam mais gravadas em disco (ficam apenas em memória RAM durante o uso)
        pasta_mini = dados_dir / "miniaturas"
        if pasta_mini.exists():
            tam = _tamanho_caminho_bytes(pasta_mini)
            if apagar_pasta(pasta_mini, tentativas=4, pausa=0.15):
                qtd_removidos += 1
                bytes_liberados += tam

        # Temporários antigos da ponte com o Lightroom
        pasta_lr_temp = dados_dir / "lightroom" / "temp"
        if pasta_lr_temp.is_dir():
            for sub in pasta_lr_temp.iterdir():
                tam = _tamanho_caminho_bytes(sub)
                if apagar_pasta(sub, tentativas=3, pausa=0.1):
                    qtd_removidos += 1
                    bytes_liberados += tam

        # Autosaves temporários órfãos (mantém apenas o mais recente se limpar_autosave=False)
        pasta_auto = dados_dir / "autosave"
        if pasta_auto.is_dir():
            arqs_auto = sorted(
                [a for a in pasta_auto.iterdir() if a.is_file()],
                key=lambda a: a.stat().st_mtime if a.exists() else 0,
                reverse=True,
            )
            alvos_apagar = arqs_auto if limpar_autosave else arqs_auto[1:]
            for arq in alvos_apagar:
                try:
                    tam = arq.stat().st_size
                    arq.unlink()
                    qtd_removidos += 1
                    bytes_liberados += tam
                except OSError:
                    pass

        # Arquivos *.tmp / *.parcial / *.psd_tmp esquecidos na pasta de dados
        for arq_tmp in dados_dir.rglob("*"):
            if arq_tmp.is_file() and (
                arq_tmp.name.endswith(".tmp")
                or arq_tmp.name.endswith(".parcial")
                or arq_tmp.name.endswith(".psd_tmp")
            ):
                try:
                    tam = arq_tmp.stat().st_size
                    arq_tmp.unlink()
                    qtd_removidos += 1
                    bytes_liberados += tam
                except OSError:
                    pass
    except Exception:
        pass

    return qtd_removidos, bytes_liberados


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
