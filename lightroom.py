# -*- coding: utf-8 -*-
"""
Tratamento em lote pelo Lightroom Classic.

O Lightroom não tem COM como o Photoshop, então a conversa é por arquivos
na pasta %LOCALAPPDATA%\\smart_album\\lightroom:
  pedidos/pedido_<id>.json   o programa pede (fotos + preset)
  status/status_<id>.json    o plugin conta o andamento e onde exportou
  pedidos/atualizar_<id>.json o programa avisa que trocou os arquivos
  presets.json, vivo.txt     lista de presets e sinal de que o plugin está rodando

A tratada substitui a original no mesmo caminho e com o mesmo nome, sem
cópia. RAW não vira JPG com o mesmo nome: a tratada fica como .jpg ao lado.
"""

import time
import uuid
from pathlib import Path

from PIL import Image

from comum import (
    EXTENSOES_RAW, apagar_pasta, gravar_json, ler_json, pasta_dados, pasta_recurso, substituir_arquivo,
)
from observador import esperar_pronto

PASTA = pasta_dados() / "lightroom"
PEDIDOS = PASTA / "pedidos"
STATUS = PASTA / "status"
TEMP = PASTA / "temp"
ARQUIVO_PRESETS = PASTA / "presets.json"
ARQUIVO_VIVO = PASTA / "vivo.txt"
VIVO_SEGUNDOS = 30


def preparar_pastas():
    for pasta in (PASTA, PEDIDOS, STATUS, TEMP):
        pasta.mkdir(parents=True, exist_ok=True)


def pasta_plugin():
    return pasta_recurso("lightroom_plugin") / "AlbumPonte.lrplugin"


def plugin_ativo():
    try:
        return time.time() - ARQUIVO_VIVO.stat().st_mtime < VIVO_SEGUNDOS
    except OSError:
        return False


def listar_presets():
    dados = ler_json(ARQUIVO_PRESETS, {}) or {}
    return dados.get("presets") or []


def pedir_presets():
    """Pede para o plugin gravar a lista de presets de novo."""
    preparar_pastas()
    gravar_json(PEDIDOS / ("presets_%s.json" % uuid.uuid4().hex[:8]), {})


def _formato(caminho):
    ext = Path(caminho).suffix.lower()
    if ext in (".tif", ".tiff", ".png"):
        return "TIFF"
    return "JPEG"


def destino_tratada(caminho):
    """Onde a tratada vai ficar: no lugar da original, ou .jpg ao lado do RAW."""
    caminho = Path(caminho)
    if caminho.suffix.lower() in EXTENSOES_RAW:
        return caminho.with_suffix(".jpg")
    return caminho


def enviar_pedido(fotos, preset_uuid, preset_nome=""):
    preparar_pastas()
    id_pedido = time.strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:6]
    pasta_temp = TEMP / id_pedido
    pasta_temp.mkdir(parents=True, exist_ok=True)
    pedido = {
        "id": id_pedido,
        "preset_uuid": preset_uuid,
        "preset_nome": preset_nome,
        "pasta_temp": str(pasta_temp),
        "fotos": [{"caminho": str(Path(f)), "formato": _formato(f)} for f in fotos],
    }
    gravar_json(PEDIDOS / ("pedido_%s.json" % id_pedido), pedido)
    return id_pedido


def _colocar_no_lugar(item):
    """Troca a original pela exportada. PNG volta a ser PNG (o Lightroom exporta TIFF)."""
    original = Path(item["caminho"])
    exportada = Path(item["saida"])
    if not esperar_pronto(exportada, espera_maxima=60):
        raise OSError("o arquivo exportado não ficou pronto: %s" % exportada)
    destino = destino_tratada(original)
    if original.suffix.lower() == ".png":
        convertida = exportada.with_suffix(".convertida.png")
        with Image.open(exportada) as img:
            img.save(convertida, "PNG")
        exportada = convertida
    substituir_arquivo(exportada, destino)
    return destino


def aguardar(id_pedido, aviso=print, parar=lambda: False, ao_progresso=None, ao_trocar=None, espera_maxima=4 * 3600):
    """
    Espera o plugin terminar e troca as originais pelas tratadas.
    ao_trocar(original, final) é chamado para cada foto trocada.
    Devolve (trocadas [(original, final)], erros [(foto, mensagem)]).
    """
    arquivo_status = STATUS / ("status_%s.json" % id_pedido)
    limite = time.time() + espera_maxima
    avisou_parado = False
    status = None
    ultimo = -1
    while time.time() < limite:
        if parar():
            aviso("Parado. O Lightroom pode continuar o pedido; as fotos não foram trocadas.")
            return [], []
        status = ler_json(arquivo_status, None)
        if status:
            if status.get("feitas", 0) != ultimo:
                ultimo = status.get("feitas", 0)
                if ao_progresso:
                    ao_progresso(ultimo, status.get("total", 0))
            if status.get("fim"):
                break
        elif not avisou_parado and not plugin_ativo() and time.time() > limite - espera_maxima + 15:
            aviso("O plugin do Lightroom não respondeu. Abra o Lightroom (ou use Biblioteca > "
                  "Plug-ins extras > Processar pedidos do Álbum).")
            avisou_parado = True
        time.sleep(1.0)
    if not status or not status.get("fim"):
        return [], [("", "O Lightroom não terminou o pedido a tempo.")]
    if status.get("erro"):
        return [], [("", status["erro"])]

    trocadas, erros = [], []
    for item in status.get("fotos", []):
        if not item.get("ok") or not item.get("saida"):
            erros.append((item.get("caminho", ""), item.get("erro") or "não exportou"))
            continue
        try:
            final = _colocar_no_lugar(item)
            trocadas.append((item["caminho"], str(final)))
            aviso("Tratada: %s" % Path(final).name)
            if ao_trocar:
                ao_trocar(item["caminho"], str(final))
        except Exception as e:
            erros.append((item["caminho"], str(e)))
            aviso("ERRO ao trocar %s: %s" % (Path(item["caminho"]).name, e))
    if trocadas:
        gravar_json(PEDIDOS / ("atualizar_%s.json" % id_pedido), {"fotos": [f for _o, f in trocadas]})
    pasta_temp = status.get("pasta_temp") or str(TEMP / id_pedido)
    apagar_pasta(pasta_temp)
    try:
        arquivo_status.unlink()
    except OSError:
        pass
    return trocadas, erros


def tratar(fotos, preset_uuid, preset_nome="", **kwargs):
    return aguardar(enviar_pedido(fotos, preset_uuid, preset_nome), **kwargs)
