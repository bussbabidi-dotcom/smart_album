# -*- coding: utf-8 -*-
"""
Facilidades para trazer as fotos: arrastar e soltar, Ctrl+V, abrir com a
pasta, "Enviar para", pastas recentes e conserto de caminhos.
"""

import os
import re
import sys
import time
from pathlib import Path

import analise
import projeto as proj
from comum import EXTENSOES, chave_natural, gravar_json, ler_json, normalizar_caminho, pasta_dados, pasta_programa
from produtos import adivinhar_produto as _adivinhar

ARQUIVO_RECENTES = pasta_dados() / "recentes.json"
LIMITE_RECENTES = 12


def _limpar_caminho_individual(item):
    """Converte item bruto de drop/texto (incluindo file://, chaves ou barras mistas) em caminho válido."""
    c = str(item or "").strip().strip("{}").strip('"').strip("'")
    if not c:
        return None
    if c.lower().startswith("file://"):
        import urllib.parse
        parsed = urllib.parse.urlparse(c)
        caminho_url = urllib.parse.unquote(parsed.path or "")
        if os.name == "nt":
            if re.match(r"^/[a-zA-Z]:", caminho_url):
                caminho_url = caminho_url[1:]
            elif parsed.netloc:
                caminho_url = "//" + parsed.netloc + caminho_url
        c = caminho_url
    if os.name == "nt":
        c = c.replace("/", "\\")
    c_norm = os.path.normpath(c)
    if os.path.exists(c_norm):
        return c_norm
    if os.path.exists(c):
        return c
    return None


def caminhos_do_drop(widget, dados):
    """
    Converte os dados recebidos no evento de arrastar e soltar (tkinterdnd2 ou Win32)
    em uma lista de caminhos reais (pastas ou arquivos), sem corromper barras invertidas '\\' do Windows.
    """
    if isinstance(dados, (list, tuple)):
        saida = []
        for item in dados:
            limpo = _limpar_caminho_individual(item)
            if limpo and limpo not in saida:
                saida.append(limpo)
        return saida

    texto = str(dados or "").strip()
    if not texto:
        return []

    # 1. Caso seja uma única pasta/arquivo (mesmo com espaços e sem chaves {})
    unico = _limpar_caminho_individual(texto)
    if unico:
        return [unico]

    candidatos = []
    # 2. Substitui '\' por '/' ANTES do splitlist do Tcl para que o Tcl não engula '\n', '\t', '\r', '\b' etc.
    if widget is not None:
        try:
            protegido = texto.replace("\\", "/")
            candidatos.extend([c for c in widget.tk.splitlist(protegido) if c])
        except Exception:
            pass

    # 3. Extrai blocos entre chaves {...}, aspas "..." ou linhas
    for m in re.finditer(r'\{([^}]+)\}|"([^"]+)"', texto):
        val = m.group(1) or m.group(2)
        if val:
            candidatos.append(val)

    # 4. Padrão específico para múltiplos caminhos do Windows com espaços mas sem chaves (ex.: C:\Pasta 1 C:\Pasta 2)
    for m_win in re.finditer(
        r'(?:[A-Za-z]:[\\/]|\\\\)[^\r\n{}"]*?(?=\s+(?:[A-Za-z]:[\\/]|\\\\)|$)',
        texto,
    ):
        val_w = m_win.group(0).strip()
        if val_w:
            candidatos.append(val_w)

    saida = []
    for item in candidatos:
        limpo = _limpar_caminho_individual(item)
        if limpo and limpo not in saida:
            saida.append(limpo)

    if saida:
        return saida
    return caminhos_do_texto(texto)


def caminhos_do_texto(texto):
    """Caminhos colados como texto: um por linha, entre chaves ou entre aspas."""
    saida = []
    for linha in str(texto or "").splitlines():
        linha = linha.strip()
        if not linha:
            continue
        direto = _limpar_caminho_individual(linha)
        if direto:
            if direto not in saida:
                saida.append(direto)
            continue
        for m in re.finditer(r'\{([^}]+)\}|"([^"]+)"|(\S+)', linha):
            cand = m.group(1) or m.group(2) or m.group(3)
            limpo = _limpar_caminho_individual(cand)
            if limpo and limpo not in saida:
                saida.append(limpo)
    return saida


def desbloquear_uipi_drop_windows(root):
    """
    Libera WM_DROPFILES (0x0233), WM_COPYDATA (0x004A) e WM_COPYGLOBALDATA (0x0049) no filtro UIPI do Windows,
    permitindo arrastar pastas do Windows Explorer mesmo quando o programa roda como Administrador.
    """
    if os.name != "nt":
        return
    try:
        import ctypes
        user32 = ctypes.windll.user32
        root.update_idletasks()
        hwnd_child = root.winfo_id()
        hwnd_top = user32.GetParent(hwnd_child) or hwnd_child
        MSGFLT_ALLOW = 1
        mensagens = (0x0233, 0x004A, 0x0049)
        if hasattr(user32, "ChangeWindowMessageFilter"):
            for msg in mensagens:
                try:
                    user32.ChangeWindowMessageFilter(msg, MSGFLT_ALLOW)
                except Exception:
                    pass
        if hasattr(user32, "ChangeWindowMessageFilterEx"):
            for hw in {hwnd_child, hwnd_top}:
                if not hw:
                    continue
                for msg in mensagens:
                    try:
                        user32.ChangeWindowMessageFilterEx(ctypes.c_void_p(hw), msg, MSGFLT_ALLOW, None)
                    except Exception:
                        pass
    except Exception:
        pass


def registrar_drop_nativo_windows(root, ao_soltar):
    """
    Ativa o suporte nativo a Arrastar e Soltar pastas/arquivos do Windows Explorer (WM_DROPFILES via ctypes)
    sem depender do pacote externo tkinterdnd2.
    Chama `ao_soltar(caminhos, x_root, y_root)` quando o usuário solta pastas ou fotos na janela.
    """
    if os.name != "nt":
        return False

    # Tenta windnd se estiver instalado, senão usa hook ctypes nativo 32/64 bits
    try:
        import ctypes
        from ctypes import wintypes

        shell32 = ctypes.windll.shell32
        user32 = ctypes.windll.user32

        desbloquear_uipi_drop_windows(root)
        root.update_idletasks()

        WM_DROPFILES = 0x0233
        GWLP_WNDPROC = -4

        LRESULT = ctypes.c_ssize_t
        HWND = ctypes.c_void_p
        UINT = ctypes.c_uint
        WPARAM = ctypes.c_size_t
        LPARAM = ctypes.c_ssize_t

        WNDPROC = ctypes.WINFUNCTYPE(LRESULT, HWND, UINT, WPARAM, LPARAM)

        set_window_long = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
        set_window_long.argtypes = [HWND, ctypes.c_int, ctypes.c_void_p]
        set_window_long.restype = ctypes.c_void_p

        call_window_proc = user32.CallWindowProcW
        call_window_proc.argtypes = [ctypes.c_void_p, HWND, UINT, WPARAM, LPARAM]
        call_window_proc.restype = LRESULT

        shell32.DragAcceptFiles.argtypes = [HWND, wintypes.BOOL]
        shell32.DragAcceptFiles.restype = None

        shell32.DragQueryFileW.argtypes = [ctypes.c_void_p, UINT, wintypes.LPWSTR, UINT]
        shell32.DragQueryFileW.restype = UINT

        shell32.DragFinish.argtypes = [ctypes.c_void_p]
        shell32.DragFinish.restype = None

        hwnd_child = root.winfo_id()
        hwnd_top = user32.GetParent(hwnd_child) or hwnd_child

        if not hasattr(root, "_win32_wndprocs"):
            root._win32_wndprocs = []

        alvos_hwnd = []
        for hw in (hwnd_top, hwnd_child):
            if hw and hw not in alvos_hwnd:
                alvos_hwnd.append(hw)

        for hw in alvos_hwnd:
            shell32.DragAcceptFiles(ctypes.c_void_p(hw), True)

            proc_original = [None]

            def _criar_proc(orig_ref):
                def _wnd_proc(hwnd, msg, wparam, lparam):
                    if msg == WM_DROPFILES:
                        try:
                            hdrop = ctypes.c_void_p(wparam)
                            qtd = shell32.DragQueryFileW(hdrop, 0xFFFFFFFF, None, 0)
                            caminhos = []
                            for i in range(qtd):
                                tam = shell32.DragQueryFileW(hdrop, i, None, 0)
                                buf = ctypes.create_unicode_buffer(tam + 2)
                                shell32.DragQueryFileW(hdrop, i, buf, tam + 2)
                                if buf.value:
                                    caminhos.append(buf.value)
                            pt = wintypes.POINT()
                            try:
                                user32.GetCursorPos(ctypes.byref(pt))
                                x_root, y_root = int(pt.x), int(pt.y)
                            except Exception:
                                x_root, y_root = int(root.winfo_rootx() + 100), int(root.winfo_rooty() + 100)
                            shell32.DragFinish(hdrop)
                            if caminhos:
                                root.after(10, lambda c=caminhos, xr=x_root, yr=y_root: ao_soltar(c, xr, yr))
                            return 0
                        except Exception:
                            pass
                    return call_window_proc(orig_ref[0], hwnd, msg, wparam, lparam)

                return WNDPROC(_wnd_proc)

            cb = _criar_proc(proc_original)
            antigo = set_window_long(ctypes.c_void_p(hw), GWLP_WNDPROC, ctypes.cast(cb, ctypes.c_void_p))
            if antigo:
                proc_original[0] = antigo
                root._win32_wndprocs.append((hw, cb, antigo))

        return bool(root._win32_wndprocs)
    except Exception:
        return False


def caminhos_da_area_de_transferencia(root):
    """Arquivos copiados no Explorer (Ctrl+C) ou caminhos copiados como texto."""
    try:
        import win32clipboard
        win32clipboard.OpenClipboard()
        try:
            if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_HDROP):
                return list(win32clipboard.GetClipboardData(win32clipboard.CF_HDROP))
        finally:
            win32clipboard.CloseClipboard()
    except Exception:
        pass
    try:
        return caminhos_do_texto(root.clipboard_get())
    except Exception:
        return []


def expandir(caminhos):
    """
    (fotos, projetos): pastas viram as fotos de dentro (com subpastas),
    arquivos que não são imagem ficam de fora, repetidos são ignorados.
    """
    fotos, projetos, vistos = [], [], set()
    for caminho in caminhos:
        caminho = Path(caminho)
        if caminho.is_dir():
            achadas = analise.listar_imagens(caminho)
        elif caminho.is_file() and caminho.name.endswith(proj.EXTENSAO):
            projetos.append(caminho)
            continue
        elif caminho.is_file() and caminho.suffix.lower() in EXTENSOES:
            achadas = [caminho]
        else:
            achadas = []
        for foto in achadas:
            chave = normalizar_caminho(foto)
            if chave not in vistos:
                vistos.add(chave)
                fotos.append(foto)
    return fotos, projetos


def pasta_comum(fotos):
    if not fotos:
        return None
    try:
        return Path(os.path.commonpath([str(Path(f).parent) for f in fotos]))
    except ValueError:
        return Path(fotos[0]).parent


def sugerir_saida(pasta_fotos):
    """Usa a própria pasta das fotos como base padrão (sem criar subpasta 'saida')."""
    if not pasta_fotos:
        return ""
    p = Path(pasta_fotos)
    if p.name.lower() in ("saida", "saída") and not p.exists():
        p = p.parent
    return str(p)


def adivinhar_produto(pasta, fotos):
    textos = [str(pasta)] if pasta else []
    textos += [Path(f).name for f in list(fotos)[:30]]
    return _adivinhar(textos)


# ------------------------------------------------------------
# Recentes
# ------------------------------------------------------------

def listar_recentes():
    dados = ler_json(ARQUIVO_RECENTES, []) or []
    return [r for r in dados if Path(r.get("caminho", "")).exists()]


def adicionar_recente(tipo, caminho):
    """tipo: "pasta" ou "projeto"."""
    caminho = str(caminho)
    dados = [r for r in (ler_json(ARQUIVO_RECENTES, []) or [])
             if normalizar_caminho(r.get("caminho", "")) != normalizar_caminho(caminho)]
    dados.insert(0, {"tipo": tipo, "caminho": caminho, "quando": time.time()})
    try:
        gravar_json(ARQUIVO_RECENTES, dados[:LIMITE_RECENTES])
    except OSError:
        pass


# ------------------------------------------------------------
# Foto que mudou de lugar
# ------------------------------------------------------------

def consertar_caminhos(projeto, nova_pasta, forcar_todas=False):
    """
    Procura as fotos dentro da nova pasta (pelo nome; com nomes repetidos,
    vence a que tem mais pastas iguais no caminho) e corrige o projeto inteiro.
    Se `forcar_todas=True` (ou se não houver fotos faltando mas o usuário pediu
    para relinkar a pasta), tenta relinkar todas as fotos e fundos do projeto.
    Devolve (consertadas, ainda faltando).
    """
    por_nome = {}
    for foto in analise.listar_imagens(nova_pasta):
        por_nome.setdefault(foto.name.casefold(), []).append(foto)

    alvos = list(proj.faltando(projeto))
    if forcar_todas or not alvos:
        todos = [f["caminho"] for f in projeto.get("fotos", []) if f.get("caminho")]
        todos += [e["foto"] for e in proj.todos_espacos(projeto) if e.get("foto")]
        todos += [d["caminho"] for d in proj.fundos_imagem(projeto) if d.get("caminho")]
        vistos = set()
        alvos = []
        for c in todos:
            ch = normalizar_caminho(c)
            if ch not in vistos:
                vistos.add(ch)
                alvos.append(c)

    consertadas = 0
    for antigo in alvos:
        candidatos = por_nome.get(Path(antigo).name.casefold())
        if not candidatos:
            continue
        partes_antigas = [p.casefold() for p in Path(antigo).parts[::-1]]

        def parecidas(candidato):
            n = 0
            for a, b in zip(partes_antigas, [p.casefold() for p in candidato.parts[::-1]]):
                if a != b:
                    break
                n += 1
            return n

        melhor = max(candidatos, key=parecidas)
        proj.trocar_caminho(projeto, antigo, melhor)
        consertadas += 1
    if consertadas > 0:
        projeto["pasta_fotos"] = str(nova_pasta)
    return consertadas, proj.faltando(projeto)


# ------------------------------------------------------------
# Atalho em "Enviar para"
# ------------------------------------------------------------

def comando_programa():
    """(executável, argumentos) para abrir o programa."""
    if getattr(sys, "frozen", False):
        return sys.executable, ""
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    exe = str(pythonw if pythonw.is_file() else sys.executable)
    return exe, '"%s"' % (pasta_programa() / "app.pyw")


def instalar_enviar_para(nome="Álbum - diagramar"):
    """Cria o atalho no menu do botão direito > Enviar para."""
    import win32com.client
    pasta = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "SendTo"
    atalho = pasta / (nome + ".lnk")
    exe, argumentos = comando_programa()
    shell = win32com.client.Dispatch("WScript.Shell")
    lnk = shell.CreateShortcut(str(atalho))
    lnk.TargetPath = exe
    lnk.Arguments = argumentos
    lnk.WorkingDirectory = str(pasta_programa())
    lnk.Description = "Abre a pasta no programa de álbum"
    lnk.Save()
    return atalho


def ordenar(fotos):
    return sorted(fotos, key=lambda f: [chave_natural(p) for p in Path(f).parts])
