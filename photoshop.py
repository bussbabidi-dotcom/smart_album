# -*- coding: utf-8 -*-
"""
Pontes opcionais com Adobe Photoshop e Adobe Lightroom Classic no Windows:
- Photoshop: usa COM (win32com.client, do pacote pywin32) para ler os conjuntos e ações
  instalados no Photoshop, rodá-los nas fotos escolhidas, ou abrir fotos para edição
  com troca automática de tela (foco para o Photoshop ao abrir, e retorno automático
  da tela para o Smart Album ao salvar com Ctrl+S ou fechar a foto).
- Lightroom: abre as fotos no Lightroom e vigia uma pasta até as fotos tratadas saírem.
"""

import ctypes
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
try:
    import winreg
except ImportError:
    winreg = None

from comum import EXTENSOES as EXTENSOES_IMAGEM, normalizar_caminho


FORMATOS_SAIDA = ("jpg", "tif", "psd", "mesmo")


class ErroPhotoshop(RuntimeError):
    pass


JSX_COMUM = r"""
app.displayDialogs = DialogModes.NO;

function salvarComo(doc, arquivo, formato, qualidade) {
    try {
        if (doc.bitsPerChannel !== BitsPerChannelType.EIGHT && formato === "jpg") {
            doc.bitsPerChannel = BitsPerChannelType.EIGHT;
        }
    } catch (eBits) {}
    if (formato === "jpg") {
        var jpg = new JPEGSaveOptions();
        jpg.quality = qualidade || 12;
        jpg.embedColorProfile = true;
        jpg.formatOptions = FormatOptions.STANDARDBASELINE;
        doc.saveAs(arquivo, jpg, true, Extension.LOWERCASE);
    } else if (formato === "tif") {
        var tif = new TiffSaveOptions();
        tif.imageCompression = TIFFEncoding.TIFFLZW;
        tif.layers = false;
        tif.embedColorProfile = true;
        doc.saveAs(arquivo, tif, true, Extension.LOWERCASE);
    } else if (formato === "psd") {
        var psd = new PhotoshopSaveOptions();
        psd.layers = true;
        psd.embedColorProfile = true;
        psd.alphaChannels = true;
        psd.annotations = true;
        psd.spotColors = true;
        doc.saveAs(arquivo, psd, true, Extension.LOWERCASE);
    }
}
"""

JSX_LISTAR_ACOES = r"""
function s2t(s) { return app.stringIDToTypeID(s); }
function c2t(c) { return app.charIDToTypeID(c); }

var conjuntos = [];
var i = 1;
while (true) {
    var ref = new ActionReference();
    ref.putIndex(c2t("ASet"), i);
    var desc;
    try { desc = executeActionGet(ref); } catch (e) { break; }
    var nome = desc.getString(c2t("Nm  "));
    var total = desc.getInteger(c2t("NmbC"));
    var acoes = [];
    for (var j = 1; j <= total; j++) {
        var r2 = new ActionReference();
        r2.putIndex(c2t("Actn"), j);
        r2.putIndex(c2t("ASet"), i);
        acoes.push(executeActionGet(r2).getString(c2t("Nm  ")));
    }
    conjuntos.push({nome: nome, acoes: acoes});
    i++;
}

var arq = new File(JOB.saida);
arq.encoding = "UTF-8";
arq.open("w");
arq.write( JSON.stringify(conjuntos) );
arq.close();
"""

JSX_RODAR_ACAO = r"""
for (var i = 0; i < JOB.arquivos.length; i++) {
    var item = JOB.arquivos[i];
    var doc = app.open(new File(item.entrada));
    app.doAction(JOB.acao, JOB.conjunto);
    if (JOB.formato === "mesmo") {
        doc.close(SaveOptions.SAVECHANGES);
    } else {
        salvarComo(doc, new File(item.saida), JOB.formato, JOB.qualidade);
        doc.close(SaveOptions.DONOTSAVECHANGES);
    }
}
"""


# ======================================================================
# Troca de Janela (Win32 Foreground Focus) entre Smart Album e Photoshop
# ======================================================================

def _forcar_janela_frente_hwnd(hwnd, maximizar=None):
    """
    Traz a janela `hwnd` para o primeiro plano no Windows 10/11 contornando
    o bloqueio de ForegroundLockTimeout (usando AttachThreadInput + tecla Alt).
    """
    if not hwnd or os.name != "nt":
        return False
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        # Se for uma janela filha (como o frame interno do Tkinter), sobe até a janela raiz de topo
        GA_ROOT = 2
        raiz = user32.GetAncestor(int(hwnd), GA_ROOT)
        if raiz:
            hwnd = raiz

        SW_RESTORE = 9
        SW_SHOWMAXIMIZED = 3
        SW_SHOW = 5

        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_SHOWMAXIMIZED if maximizar else SW_RESTORE)
        elif maximizar is True:
            user32.ShowWindow(hwnd, SW_SHOWMAXIMIZED)
        elif maximizar is False:
            user32.ShowWindow(hwnd, SW_RESTORE)
        else:
            user32.ShowWindow(hwnd, SW_SHOW)

        fg_hwnd = user32.GetForegroundWindow()
        tid_atual = kernel32.GetCurrentThreadId()
        tid_fg = user32.GetWindowThreadProcessId(fg_hwnd, None) if fg_hwnd else 0
        tid_alvo = user32.GetWindowThreadProcessId(hwnd, None)

        anexado_fg = False
        anexado_alvo = False
        try:
            if tid_fg and tid_fg != tid_atual:
                anexado_fg = bool(user32.AttachThreadInput(tid_fg, tid_atual, True))
            if tid_alvo and tid_alvo != tid_atual:
                anexado_alvo = bool(user32.AttachThreadInput(tid_alvo, tid_atual, True))

            # Simula toque neutro na tecla Alt (VK_MENU = 0x12) para liberar SetForegroundWindow no Windows
            VK_MENU = 0x12
            KEYEVENTF_KEYUP = 0x0002
            user32.keybd_event(VK_MENU, 0, 0, 0)
            user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)

            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
            user32.SetFocus(hwnd)
        finally:
            if anexado_fg:
                user32.AttachThreadInput(tid_fg, tid_atual, False)
            if anexado_alvo:
                user32.AttachThreadInput(tid_alvo, tid_atual, False)
        return True
    except Exception:
        return False


def _encontrar_hwnd_photoshop():
    """Localiza o HWND da janela principal do Adobe Photoshop no Windows."""
    if os.name != "nt":
        return None
    encontrados = []
    try:
        user32 = ctypes.windll.user32
        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def callback(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            buf_cls = ctypes.create_unicode_buffer(128)
            user32.GetClassNameW(hwnd, buf_cls, 128)
            cls = (buf_cls.value or "").strip()

            buf_tit = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(hwnd, buf_tit, 512)
            titulo = (buf_tit.value or "").strip()

            if cls == "Photoshop" or "adobe photoshop" in titulo.lower() or (
                cls.startswith("OWL.") and "photoshop" in titulo.lower()
            ):
                encontrados.append((0 if cls == "Photoshop" else 1, hwnd))
            elif "@" in titulo and ("rgb/" in titulo.lower() or "cmyk/" in titulo.lower()) and cls == "Photoshop":
                encontrados.append((0, hwnd))
            return True

        user32.EnumWindows(WNDENUMPROC(callback), 0)
    except Exception:
        pass
    if not encontrados:
        return None
    encontrados.sort(key=lambda par: par[0])
    return encontrados[0][1]


def trazer_janela_para_frente(hwnd_ou_id, maximizar=None):
    """Traz a janela do Smart Album (ou qualquer HWND) para frente do Photoshop."""
    return _forcar_janela_frente_hwnd(hwnd_ou_id, maximizar=maximizar)


# ======================================================================
# Descoberta de programas instalados no Windows
# ======================================================================

def _procurar_executavel(pastas, nomes):
    candidatos = []
    for base in pastas:
        if base and Path(base).is_dir():
            for padrao in nomes:
                candidatos.extend(Path(base).glob(padrao))
    candidatos = [p for p in candidatos if p.is_file()]
    candidatos.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidatos[0] if candidatos else None


def _registro_app_path(executavel):
    for raiz in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(raiz, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\\" + executavel) as ch:
                valor, _ = winreg.QueryValueEx(ch, "")
                if valor and Path(valor).is_file():
                    return Path(valor)
        except OSError:
            pass
    return None


def achar_photoshop():
    achado = _registro_app_path("Photoshop.exe")
    if achado:
        return achado
    pastas = [os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")]
    return _procurar_executavel(pastas, ["Adobe/Adobe Photoshop*/Photoshop.exe"])


def achar_lightroom():
    for exe in ("lightroom.exe", "Lightroom.exe"):
        achado = _registro_app_path(exe)
        if achado:
            return achado
    pastas = [os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")]
    return _procurar_executavel(pastas, ["Adobe/Adobe Lightroom Classic*/Lightroom.exe",
                                         "Adobe/Adobe Photoshop Lightroom*/lightroom.exe"])


# ======================================================================
# Photoshop via COM + JSX
# ======================================================================

def _com():
    try:
        import pythoncom
        import win32com.client
        return pythoncom, win32com.client
    except ImportError:
        raise ErroPhotoshop(
            "Para conversar com o Photoshop automaticamente é preciso instalar o pywin32:\n\n"
            "Abra o Prompt de Comando e rode:\n"
            "pip install pywin32"
        )


def conectar():
    """Devolve o objeto Photoshop.Application (abre o Photoshop se estiver fechado)."""
    pythoncom, client = _com()
    pythoncom.CoInitialize()
    try:
        return client.Dispatch("Photoshop.Application")
    except Exception as e:
        exe = achar_photoshop()
        if not exe:
            raise ErroPhotoshop("Não encontrei o Adobe Photoshop instalado neste computador.") from e
        raise ErroPhotoshop("Não consegui abrir o Photoshop via automação (%s)." % e) from e


def _json_jsx():
    """ExtendScript antigo (ES3) não tem JSON.stringify: inclui um pequenino."""
    return r"""
if (typeof JSON === "undefined") { JSON = {}; }
JSON.stringify = function (v) {
    if (v === null || v === undefined) return "null";
    if (typeof v === "number" || typeof v === "boolean") return String(v);
    if (typeof v === "string") {
        return '"' + v.replace(/\\/g, "\\\\").replace(/"/g, '\\"')
                      .replace(/\n/g, "\\n").replace(/\r/g, "\\r") + '"';
    }
    if (v instanceof Array) {
        var a = [];
        for (var i = 0; i < v.length; i++) a.push(JSON.stringify(v[i]));
        return "[" + a.join(",") + "]";
    }
    var p = [];
    for (var k in v) if (v.hasOwnProperty(k)) p.push(JSON.stringify(String(k)) + ":" + JSON.stringify(v[k]));
    return "{" + p.join(",") + "}";
};
"""


def rodar_jsx(corpo, job):
    """Grava um .jsx temporário com `JOB = ...` no topo e manda o Photoshop executar."""
    app = conectar()
    pasta = Path(tempfile.mkdtemp(prefix="smart_album_ps_"))
    try:
        arquivo_jsx = pasta / "job.jsx"
        codigo = "var JOB = " + json.dumps(job, ensure_ascii=True) + ";\n" + _json_jsx() + JSX_COMUM + corpo
        arquivo_jsx.write_text(codigo, encoding="utf-8")
        try:
            return app.DoJavaScriptFile(str(arquivo_jsx), [], 1)
        except Exception:
            return app.DoJavaScript(codigo, [], 1)
    except ErroPhotoshop:
        raise
    except Exception as e:
        raise ErroPhotoshop("O Photoshop devolveu um erro ao rodar o script:\n%s" % e) from e
    finally:
        shutil.rmtree(pasta, ignore_errors=True)


def aplicar_acao_em_arquivo(caminho_arquivo, conjunto, acao):
    """Abre um arquivo (ex.: PSD recém-gravado) no Photoshop, roda a ação e salva."""
    caminho_arquivo = str(Path(caminho_arquivo).resolve())
    rodar_jsx(
        JSX_RODAR_ACAO,
        {
            "conjunto": conjunto,
            "acao": acao,
            "formato": "mesmo",
            "qualidade": 12,
            "arquivos": [{"entrada": caminho_arquivo, "saida": caminho_arquivo}],
        },
    )


def listar_acoes():
    """Devolve `[{'nome': conjunto, 'acoes': [acao, ...]}, ...]` lidos direto do Photoshop."""
    pasta = Path(tempfile.mkdtemp(prefix="smart_album_acoes_"))
    try:
        saida = pasta / "acoes.json"
        rodar_jsx(JSX_LISTAR_ACOES, {"saida": str(saida)})
        if not saida.is_file():
            raise ErroPhotoshop("O Photoshop não devolveu a lista de ações.")
        return json.loads(saida.read_text(encoding="utf-8-sig"))
    finally:
        shutil.rmtree(pasta, ignore_errors=True)


def destino_tratada(caminho, formato, pasta_saida=None, sufixo="_tratada"):
    origem = Path(caminho)
    if formato == "mesmo":
        return origem
    ext = "." + formato.lower().lstrip(".")
    pasta = Path(pasta_saida) if pasta_saida else origem.parent / "_tratadas"
    pasta.mkdir(parents=True, exist_ok=True)
    return pasta / (origem.stem + sufixo + ext)


def rodar_acao_em_fotos(caminhos, conjunto, acao, formato="mesmo", pasta_saida=None,
                        qualidade_jpg=12, ao_progresso=None, parar=None):
    """
    Abre cada foto no Photoshop, roda `acao` do `conjunto` e salva.
    Devolve `[(caminho_original, caminho_final), ...]`.
    """
    formato = formato.lower()
    if formato not in FORMATOS_SAIDA:
        raise ValueError("Formato desconhecido: %s" % formato)
    pares = []
    total = len(caminhos)
    for n, c in enumerate(caminhos, start=1):
        if parar and parar():
            break
        original = Path(c)
        if not original.is_file():
            continue
        saida = destino_tratada(original, formato, pasta_saida)
        rodar_jsx(JSX_RODAR_ACAO, {
            "conjunto": conjunto,
            "acao": acao,
            "formato": formato,
            "qualidade": int(qualidade_jpg),
            "arquivos": [{"entrada": str(original), "saida": str(saida)}],
        })
        pares.append((str(original), str(saida)))
        if ao_progresso:
            ao_progresso(n, total, original)
    return pares


def acao_em_lote(caminhos, conjunto, acao, aviso=None, parar=None, ao_progresso=None, ao_trocar=None):
    """Roda `acao` do `conjunto` no próprio arquivo original de cada foto e devolve `(trocadas, erros)`."""
    trocadas, erros = [], []
    total = len(caminhos)
    for n, c in enumerate(caminhos, start=1):
        if parar and parar():
            break
        original = Path(c)
        if not original.is_file():
            continue
        if aviso:
            aviso("Photoshop (%s / %s): %s" % (conjunto, acao, original.name))
        try:
            rodar_jsx(
                JSX_RODAR_ACAO,
                {
                    "conjunto": conjunto,
                    "acao": acao,
                    "formato": "mesmo",
                    "qualidade": 12,
                    "arquivos": [{"entrada": str(original), "saida": str(original)}],
                },
            )
            trocadas.append(str(original))
            if ao_trocar:
                ao_trocar(str(original))
        except Exception as e:
            erros.append((str(original), str(e)))
        if ao_progresso:
            ao_progresso(n, total)
    return trocadas, erros


def ativar_janela_photoshop():
    """Coloca a janela do Adobe Photoshop em primeiro plano na tela do Windows."""
    hwnd = _encontrar_hwnd_photoshop()
    if hwnd and _forcar_janela_frente_hwnd(hwnd, maximizar=True):
        return True
    try:
        app = conectar()
        try:
            app.Visible = True
        except Exception:
            pass
        try:
            app.BringToFront()
        except Exception:
            pass
    except Exception:
        pass
    hwnd = _encontrar_hwnd_photoshop()
    if hwnd and _forcar_janela_frente_hwnd(hwnd, maximizar=True):
        return True
    try:
        _, client = _com()
        shell = client.Dispatch("WScript.Shell")
        shell.AppActivate("Adobe Photoshop")
        return True
    except Exception:
        return False


def abrir_para_editar(caminhos):
    """Abre as fotos no Photoshop para edição manual e ativa a janela do Photoshop."""
    caminhos_validos = [str(Path(c).resolve()) for c in caminhos if c and Path(c).is_file()]
    if not caminhos_validos:
        return
    try:
        app = conectar()
        try:
            app.Visible = True
        except Exception:
            pass
        for c in caminhos_validos:
            app.Open(c)
        ativar_janela_photoshop()
        return
    except ErroPhotoshop:
        exe = achar_photoshop()
        if not exe:
            raise
        subprocess.Popen([str(exe)] + caminhos_validos)
        for _ in range(16):
            time.sleep(0.35)
            if _encontrar_hwnd_photoshop():
                ativar_janela_photoshop()
                break


def _normalizar_caminho_ps(texto_caminho):
    """Converte caminhos devolvidos pelo Photoshop (inclusive formato POSIX '/c/Users/...') para formato comparável."""
    if not texto_caminho:
        return ""
    s = str(texto_caminho).strip()
    # Formato ExtendScript URI/POSIX no Windows: /c/Users/... ou ~/...
    m = re.match(r"^/([a-zA-Z])/(.*)$", s)
    if m:
        s = "%s:\\%s" % (m.group(1).upper(), m.group(2).replace("/", "\\"))
    try:
        return normalizar_caminho(str(Path(s).resolve()))
    except Exception:
        return normalizar_caminho(s)


def _estado_documentos_no_photoshop(alvos_norm, nomes_alvos):
    """
    Inspeciona os documentos abertos no Photoshop via COM.
    Devolve `(algum_aberto, lista_salvos_pelo_ps)` ou `(None, [])` se o COM estiver ocupado (ex.: diálogo modal).
    """
    try:
        pythoncom, client = _com()
        pythoncom.CoInitialize()
        app = client.GetActiveObject("Photoshop.Application")
        docs = app.Documents
        qtd = int(docs.Count)
        if qtd == 0:
            return False, []
        algum_aberto = False
        estado_saved = {}
        for i in range(1, qtd + 1):
            try:
                doc = docs.Item(i)
                nome_doc = ""
                try:
                    nome_doc = str(doc.Name).strip().casefold()
                except Exception:
                    pass
                caminho_raw = ""
                try:
                    fn = doc.FullName
                    caminho_raw = getattr(fn, "fsName", None) or str(fn)
                except Exception:
                    pass
                caminho_norm = _normalizar_caminho_ps(caminho_raw)
                bateu_caminho = bool(caminho_norm and caminho_norm in alvos_norm)
                bateu_nome = bool(nome_doc and nome_doc in nomes_alvos)
                if bateu_caminho or bateu_nome:
                    algum_aberto = True
                    chave = caminho_norm if bateu_caminho else nome_doc
                    try:
                        estado_saved[chave] = bool(doc.Saved)
                    except Exception:
                        pass
            except Exception:
                # Se um documento específico falhar ao ler FullName mas o nome bater, não ignora
                continue
        return algum_aberto, estado_saved
    except Exception:
        return None, {}


def monitorar_edicao_photoshop(caminhos, ao_salvar=None, ao_terminar=None, intervalo=0.55):
    """
    Monitora em background as fotos abertas no Photoshop:
    - Sempre que o usuário pressiona Ctrl+S no Photoshop (mudança de mtime/tamanho ou transição doc.Saved False -> True),
      dispara `ao_salvar(caminho)` e `ao_terminar(modificadas, 'salvo')` para devolver a tela imediatamente ao Smart Album.
    - Quando o usuário fecha a(s) foto(s) no Photoshop depois de terem sido abertas,
      dispara `ao_terminar(modificadas, 'fechado')` para restaurar a janela do Smart Album automaticamente.
    """
    arquivos = [str(Path(c).resolve()) for c in caminhos if c and Path(c).is_file()]
    if not arquivos:
        return

    alvos_norm = {_normalizar_caminho_ps(c): c for c in arquivos}
    nomes_alvos = {Path(c).name.strip().casefold(): c for c in arquivos}
    assinaturas_iniciais = {}
    for c in arquivos:
        try:
            st = Path(c).stat()
            assinaturas_iniciais[c] = (st.st_mtime_ns, st.st_size)
        except OSError:
            assinaturas_iniciais[c] = None

    def loop():
        modificadas = set()
        viu_aberto = False
        sujou_no_ps = set()
        inicio = time.time()
        contagem_fechado_seguido = 0

        # Garante que a janela do Photoshop receba o foco assim que o monitoramento começa
        time.sleep(0.25)
        ativar_janela_photoshop()

        while True:
            time.sleep(intervalo)
            salvou_agora = False

            # 1) Verifica alteração física no arquivo em disco (Ctrl+S)
            for c in arquivos:
                try:
                    st = Path(c).stat()
                    atual = (st.st_mtime_ns, st.st_size)
                except OSError:
                    continue
                ant = assinaturas_iniciais.get(c)
                if ant is not None and atual != ant:
                    assinaturas_iniciais[c] = atual
                    modificadas.add(c)
                    salvou_agora = True
                    if ao_salvar:
                        try:
                            ao_salvar(c)
                        except Exception:
                            pass

            # 2) Verifica estado dos documentos abertos no Photoshop via COM
            estado_abertos, mapa_saved = _estado_documentos_no_photoshop(set(alvos_norm.keys()), set(nomes_alvos.keys()))
            if estado_abertos is True:
                viu_aberto = True
                contagem_fechado_seguido = 0
                for ch_doc, is_saved in (mapa_saved or {}).items():
                    if not is_saved:
                        sujou_no_ps.add(ch_doc)
                    elif ch_doc in sujou_no_ps and is_saved:
                        # O documento estava modificado no Photoshop e acabou de ser salvo (Ctrl+S)!
                        sujou_no_ps.discard(ch_doc)
                        cam_real = alvos_norm.get(ch_doc) or nomes_alvos.get(ch_doc) or arquivos[0]
                        modificadas.add(cam_real)
                        salvou_agora = True
                        if ao_salvar:
                            try:
                                ao_salvar(cam_real)
                            except Exception:
                                pass

            if salvou_agora:
                if ao_terminar:
                    try:
                        ao_terminar(list(modificadas), "salvo")
                    except Exception:
                        pass
                break

            if estado_abertos is False:
                if viu_aberto:
                    contagem_fechado_seguido += 1
                    # Confirma por 2 leituras seguidas que o documento foi fechado para evitar falso positivo durante salvamento
                    if contagem_fechado_seguido >= 2:
                        if ao_terminar:
                            try:
                                ao_terminar(list(modificadas), "fechado")
                            except Exception:
                                pass
                        break
                elif (time.time() - inicio) > 35.0:
                    # Timeout de segurança caso o Photoshop tenha sido fechado antes de abrir a foto
                    if ao_terminar:
                        try:
                            ao_terminar(list(modificadas), "fechado")
                        except Exception:
                            pass
                    break

            if (time.time() - inicio) > 3600:
                break

    threading.Thread(target=loop, daemon=True).start()


# ======================================================================
# Lightroom Classic
# ======================================================================

def enviar_para_lightroom(caminhos, pasta_Copia=None):
    """
    Abre o Lightroom Classic com as fotos (ele mostra a tela de importação
    já com essas fotos marcadas). Se `pasta_Copia` for dada, copia antes
    para lá e abre as cópias.
    """
    exe = achar_lightroom()
    if not exe:
        raise FileNotFoundError("Não encontrei o Adobe Lightroom Classic instalado neste computador.")
    alvos = []
    for c in caminhos:
        origem = Path(c)
        if not origem.is_file():
            continue
        if pasta_Copia:
            Path(pasta_Copia).mkdir(parents=True, exist_ok=True)
            destino = Path(pasta_Copia) / origem.name
            if normalizar_caminho(origem) != normalizar_caminho(destino):
                shutil.copy2(origem, destino)
            alvos.append(str(destino))
        else:
            alvos.append(str(origem))
    if not alvos:
        return []
    subprocess.Popen([str(exe)] + alvos)
    return alvos


class VigiaPasta:
    """
    Vigia uma pasta de saída do Lightroom: quando aparece uma imagem nova ou
    mais recente com o mesmo nome-base de uma foto do álbum, avisa `ao_achar(original, nova)`.
    """

    def __init__(self, ao_achar, intervalo=1.5):
        self.ao_achar = ao_achar
        self.intervalo = intervalo
        self._alvo = None
        self._mapa = {}
        self._vistos = {}
        self._parar = threading.Event()
        self._thread = None

    def iniciar(self, pasta_saida, caminhos_originais):
        self.parar()
        pasta = Path(pasta_saida)
        pasta.mkdir(parents=True, exist_ok=True)
        self._alvo = pasta
        self._mapa = {Path(c).stem.casefold(): str(c) for c in caminhos_originais}
        self._vistos = {normalizar_caminho(p): p.stat().st_mtime_ns
                        for p in pasta.iterdir() if p.is_file()}
        self._parar.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def parar(self):
        self._parar.set()

    def ativo(self):
        return self._thread is not None and self._thread.is_alive() and not self._parar.is_set()

    def _loop(self):
        while not self._parar.wait(self.intervalo):
            try:
                for arq in self._alvo.iterdir():
                    if not arq.is_file() or arq.suffix.lower() not in EXTENSOES_IMAGEM:
                        continue
                    chave = normalizar_caminho(arq)
                    mtime = arq.stat().st_mtime_ns
                    if self._vistos.get(chave) == mtime:
                        continue
                    tamanho_antes = arq.stat().st_size
                    time.sleep(0.3)
                    if not arq.is_file() or arq.stat().st_size != tamanho_antes:
                        continue
                    self._vistos[chave] = mtime
                    original = self._mapa.get(arq.stem.casefold())
                    if original:
                        self.ao_achar(original, str(arq))
            except OSError:
                pass
