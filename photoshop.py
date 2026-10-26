# -*- coding: utf-8 -*-
"""
Conversa com o Photoshop, do mesmo jeito da automação das capas:
COM (DoJavaScriptFile), pasta temporária local, arquivo de status para
trazer o erro de volta e cópia com nova tentativa para pastas de rede.
Se o COM não responder, abre o Photoshop.exe com o .jsx e espera o status.
"""

import glob
import json
import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from comum import apagar_pasta, normalizar_caminho, substituir_arquivo

_local = threading.local()

JSX_COMUM = r"""
function gravarStatus(texto) {
    try {
        var f = new File(JOB.arquivo_status);
        f.encoding = "UTF-8";
        if (f.open("w")) { f.write(texto); f.close(); }
    } catch (e) {}
}
function guardarPreferencias() {
    return {
        regua: app.preferences.rulerUnits,
        tipo: app.preferences.typeUnits,
        dialogos: app.displayDialogs
    };
}
function prepararPreferencias() {
    app.preferences.rulerUnits = Units.PIXELS;
    app.preferences.typeUnits = TypeUnits.POINTS;
    app.displayDialogs = DialogModes.NO;
}
function restaurarPreferencias(p) {
    try { app.preferences.rulerUnits = p.regua; } catch (e) {}
    try { app.preferences.typeUnits = p.tipo; } catch (e) {}
    try { app.displayDialogs = p.dialogos; } catch (e) {}
}
function corSolida(rgb) {
    var c = new SolidColor();
    c.rgb.red = rgb[0];
    c.rgb.green = rgb[1];
    c.rgb.blue = rgb[2];
    return c;
}
function salvarComo(doc, caminho, formato) {
    var arquivo = new File(caminho);
    if (formato == "psd") {
        var psd = new PhotoshopSaveOptions();
        psd.layers = true;
        psd.embedColorProfile = true;
        doc.saveAs(arquivo, psd, true, Extension.LOWERCASE);
    } else if (formato == "tif") {
        var tif = new TiffSaveOptions();
        tif.embedColorProfile = true;
        tif.imageCompression = TIFFEncoding.NONE;
        tif.layers = false;
        doc.saveAs(arquivo, tif, true, Extension.LOWERCASE);
    } else if (formato == "png") {
        var png = new PNGSaveOptions();
        doc.saveAs(arquivo, png, true, Extension.LOWERCASE);
    } else {
        if (doc.bitsPerChannel != BitsPerChannelType.EIGHT) { doc.bitsPerChannel = BitsPerChannelType.EIGHT; }
        var jpg = new JPEGSaveOptions();
        jpg.quality = 12;
        jpg.embedColorProfile = true;
        jpg.formatOptions = FormatOptions.STANDARDBASELINE;
        doc.saveAs(arquivo, jpg, true, Extension.LOWERCASE);
    }
}
"""

JSX_ACAO = r"""
(function () {
    var prefs = guardarPreferencias();
    var etapa = "abrir";
    var doc = null;
    try {
        prepararPreferencias();
        doc = app.open(new File(JOB.entrada));
        etapa = "acao";
        app.doAction(JOB.acao, JOB.conjunto);
        doc = app.activeDocument;
        etapa = "salvar";
        salvarComo(doc, JOB.saida, JOB.formato);
        doc.close(SaveOptions.DONOTSAVECHANGES);
        doc = null;
        gravarStatus("OK|" + JOB.saida);
    } catch (e) {
        gravarStatus("ERRO|" + etapa + "|" + e.message + " (linha " + e.line + ")");
        try { if (doc) { doc.close(SaveOptions.DONOTSAVECHANGES); } } catch (e2) {}
    } finally {
        restaurarPreferencias(prefs);
    }
})();
"""

JSX_LISTAR_ACOES = r"""
(function () {
    var linhas = ["OK"];
    try {
        var s = 1;
        while (true) {
            var ref = new ActionReference();
            ref.putIndex(charIDToTypeID("ASet"), s);
            var desc;
            try { desc = executeActionGet(ref); } catch (e) { break; }
            linhas.push("C\t" + desc.getString(charIDToTypeID("Nm  ")));
            var n = desc.hasKey(charIDToTypeID("NmbC")) ? desc.getInteger(charIDToTypeID("NmbC")) : 0;
            for (var a = 1; a <= n; a++) {
                var r2 = new ActionReference();
                r2.putIndex(charIDToTypeID("Actn"), a);
                r2.putIndex(charIDToTypeID("ASet"), s);
                linhas.push("A\t" + executeActionGet(r2).getString(charIDToTypeID("Nm  ")));
            }
            s++;
        }
        gravarStatus(linhas.join("\n"));
    } catch (e) {
        gravarStatus("ERRO|listar ações|" + e.message + " (linha " + e.line + ")");
    }
})();
"""

FORMATOS = {".jpg": "jpg", ".jpeg": "jpg", ".tif": "tif", ".tiff": "tif", ".png": "png", ".psd": "psd"}


class ErroPhotoshop(Exception):
    pass


def achar_photoshop_exe():
    """Photoshop.exe mais novo instalado, procurando nas pastas da Adobe."""
    candidatos = []
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramW6432"), r"C:\Program Files"):
        if base:
            candidatos += glob.glob(os.path.join(base, "Adobe", "Adobe Photoshop*", "Photoshop.exe"))
    candidatos = sorted(set(candidatos), key=lambda c: os.path.getmtime(c), reverse=True)
    return candidatos[0] if candidatos else None


def conectar():
    """Photoshop por COM, um por thread (o COM exige CoInitialize em cada thread)."""
    app = getattr(_local, "app", None)
    if app is not None:
        try:
            _ = app.Version
            return app
        except Exception:
            _local.app = None
    import pythoncom
    import win32com.client
    if not getattr(_local, "com_iniciado", False):
        pythoncom.CoInitialize()
        _local.com_iniciado = True
    app = win32com.client.Dispatch("Photoshop.Application")
    try:
        app.Visible = True
    except Exception:
        pass
    _local.app = app
    return app


def ler_status(arquivo):
    try:
        if Path(arquivo).is_file():
            return Path(arquivo).read_text(encoding="utf-8-sig", errors="replace").strip()
    except OSError:
        pass
    return ""


def rodar_jsx(job, corpo, pasta_temp, espera_maxima=900):
    """
    Grava var JOB = {...} + JSX numa pasta temporária, roda e devolve o status.
    Levanta ErroPhotoshop quando o status começa com ERRO.
    """
    pasta_temp = Path(pasta_temp)
    pasta_temp.mkdir(parents=True, exist_ok=True)
    arquivo_status = pasta_temp / ("_status_%s.txt" % threading.get_ident())
    if arquivo_status.exists():
        arquivo_status.unlink()
    job = dict(job)
    job["arquivo_status"] = str(arquivo_status)
    arquivo_jsx = pasta_temp / ("_album_%s.jsx" % threading.get_ident())
    codigo = "#target photoshop\nvar JOB = " + json.dumps(job, ensure_ascii=True) + ";\n" + JSX_COMUM + corpo
    arquivo_jsx.write_text(codigo, encoding="utf-8")

    retorno = None
    try:
        app = conectar()
    except Exception as e:
        app = None
        erro_com = e
    if app is not None:
        try:
            retorno = app.DoJavaScriptFile(str(arquivo_jsx), [], 1)
        except Exception as e:
            status = ler_status(arquivo_status)
            raise ErroPhotoshop(
                "Erro na chamada ao Photoshop (DoJavaScriptFile): %s%s\n\nSe aparecer 'acesso negado', "
                "o Photoshop e este programa precisam rodar com o mesmo nível de permissão." % (
                    e, ("\nStatus do JSX: " + status) if status else "")
            )
    else:
        exe = achar_photoshop_exe()
        if not exe:
            raise ErroPhotoshop("Photoshop não encontrado (COM: %s)." % erro_com)
        subprocess.Popen([exe, str(arquivo_jsx)])
        limite = time.time() + espera_maxima
        while time.time() < limite and not ler_status(arquivo_status):
            time.sleep(1.0)

    status = ler_status(arquivo_status) or (str(retorno) if retorno else "")
    if not status:
        raise ErroPhotoshop("O Photoshop não devolveu status (veja se apareceu algum aviso nele).")
    if status.startswith("ERRO|"):
        partes = status.split("|", 2)
        raise ErroPhotoshop("Erro no Photoshop\nEtapa: %s\nMensagem: %s" % (
            partes[1] if len(partes) > 1 else "?", partes[2] if len(partes) > 2 else status))
    return status


def fechar_documentos_da_pasta(pasta):
    """Fecha sem salvar os documentos abertos a partir da pasta temporária."""
    try:
        app = conectar()
        prefixo = normalizar_caminho(pasta)
        documentos = app.Documents
        for i in range(documentos.Count, 0, -1):
            try:
                doc = documentos.Item(i)
                if normalizar_caminho(str(doc.FullName)).startswith(prefixo):
                    doc.Close(2)
            except Exception:
                continue
    except Exception:
        pass


# ------------------------------------------------------------
# Editar no Photoshop
# ------------------------------------------------------------

def abrir_para_editar(caminhos):
    """Abre as próprias fotos no Photoshop. Salvar (Ctrl+S) grava por cima delas."""
    caminhos = [str(c) for c in caminhos]
    try:
        app = conectar()
        for caminho in caminhos:
            app.Open(caminho)
        return
    except Exception:
        pass
    exe = achar_photoshop_exe()
    if not exe:
        raise ErroPhotoshop("Photoshop não encontrado.")
    subprocess.Popen([exe] + caminhos)


def listar_acoes():
    """{conjunto: [ações]} do painel Ações do Photoshop aberto (pelo ActionManager)."""
    pasta_temp = Path(tempfile.mkdtemp(prefix="album_acoes_"))
    try:
        status = rodar_jsx({}, JSX_LISTAR_ACOES, pasta_temp, espera_maxima=60)
    finally:
        apagar_pasta(pasta_temp)
    conjuntos = {}
    atual = None
    for linha in status.splitlines()[1:]:
        tipo, _sep, nome = linha.partition("\t")
        if tipo == "C":
            atual = nome
            conjuntos.setdefault(atual, [])
        elif tipo == "A" and atual is not None:
            conjuntos[atual].append(nome)
    return conjuntos


def acao_em_lote(fotos, conjunto, acao, aviso=print, parar=lambda: False, ao_progresso=None, ao_trocar=None):
    """
    Roda a Ação em cada foto e substitui a original pelo resultado (mesmo
    caminho, mesmo nome, sem cópia). Devolve (trocadas, erros).
    ao_trocar(caminho) é chamado depois que cada original foi substituída.
    """
    if not conjunto or not acao:
        raise ValueError("Informe o conjunto e o nome da Ação do Photoshop.")
    pasta_temp = Path(tempfile.mkdtemp(prefix="album_acao_"))
    trocadas, erros = [], []
    try:
        for n, foto in enumerate(fotos, start=1):
            if parar():
                aviso("Parado.")
                break
            foto = Path(foto)
            formato = FORMATOS.get(foto.suffix.lower(), "jpg")
            local_entrada = pasta_temp / ("entrada_%s%s" % (n, foto.suffix.lower()))
            local_saida = pasta_temp / ("saida_%s%s" % (n, foto.suffix.lower()))
            try:
                substituir_arquivo(foto, local_entrada)
                rodar_jsx(
                    {"entrada": str(local_entrada), "saida": str(local_saida), "conjunto": conjunto,
                     "acao": acao, "formato": formato},
                    JSX_ACAO, pasta_temp,
                )
                if not local_saida.is_file() or local_saida.stat().st_size == 0:
                    raise ErroPhotoshop("O Photoshop não gravou o resultado.")
                substituir_arquivo(local_saida, foto)
                trocadas.append(str(foto))
                aviso("Tratada: %s" % foto.name)
                if ao_trocar:
                    ao_trocar(str(foto))
            except Exception as e:
                erros.append((str(foto), str(e)))
                aviso("ERRO em %s: %s" % (foto.name, e))
            finally:
                for temp in (local_entrada, local_saida):
                    try:
                        if temp.exists():
                            temp.unlink()
                    except OSError:
                        pass
            if ao_progresso:
                ao_progresso(n, len(fotos))
    finally:
        fechar_documentos_da_pasta(pasta_temp)
        apagar_pasta(pasta_temp)
    return trocadas, erros
