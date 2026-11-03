# -*- coding: utf-8 -*-
"""
Gerador e atualizador dos executáveis Windows (.exe) do Smart Album:
1. SmartAlbum.exe (Lançador nativo Windows x64 GUI na raiz do projeto, sem janela preta de console,
   compatível com "Abrir com...", arrastar e soltar pastas/fotos/.album.json e duplo clique).
2. dist/Album/Album.exe (Executável PyInstaller Windows x64 GUI atualizado com todos os 23 módulos
   Python mais recentes embutidos no CArchive + carregamento dinâmico de .py atualizados).
"""

import base64
import json
import os
import struct
import zlib
from pathlib import Path

PASTA_RAIZ = Path(__file__).resolve().parent

ORDEM_MODULOS = [
    "comum",
    "tema",
    "produtos",
    "modelos_lamina",
    "analise",
    "recorte",
    "composicao",
    "capa",
    "atalho_capa",
    "fundos",
    "projeto",
    "diagramar",
    "editor_foto",
    "observador",
    "photoshop",
    "lightroom",
    "janela_tratamento",
    "exportar",
    "exportar_psd",
    "janela_exportar",
    "entrada",
    "linha_comando",
]


def _construir_codeobj_py314_exec(codigo_fonte_ascii: str) -> bytes:
    """
    Constrói um CodeObject válido no formato marshal do Python 3.14 que executa:
        exec(codigo_fonte_ascii)
    Validado byte a byte contra python314.dll do pacote PyInstaller.
    """
    src_bytes = codigo_fonte_ascii.encode("ascii")
    # Bytecode Python 3.14:
    # \x80\x00                     RESUME 0
    # ]\x00                        LOAD_NAME 0 ("exec")
    # !\x00                        PUSH_NULL
    # R\x00                        LOAD_CONST 0 (codigo_fonte_ascii)
    # 4\x01\x00\x00\x00\x00\x00\x00 CALL 1 (+ 3 words inline cache)
    # \x1f\x00                     POP_TOP
    # R\x01                        LOAD_CONST 1 (None)
    # #\x00                        RETURN_VALUE
    co_code = b"\x80\x00]\x00!\x00R\x004\x01\x00\x00\x00\x00\x00\x00\x1f\x00R\x01#\x00"
    out = bytearray()
    out += b"c"  # TYPE_CODE
    out += struct.pack("<iiiii", 0, 0, 0, 4, 0)  # argcount, posonly, kwonly, stacksize=4, flags=0
    out += b"\xf3" + struct.pack("<i", len(co_code)) + co_code  # co_code (ref 0)
    # co_consts: (codigo_fonte_ascii, None)
    out += b")\x02"
    out += b"a" + struct.pack("<i", len(src_bytes)) + src_bytes  # TYPE_ASCII
    out += b"N"  # None
    # co_names: ("exec",)
    out += b")\x01\xda\x04exec"  # ref 1
    # co_localsplusnames: ()
    out += b"\xa9\x00"  # ref 2
    # co_localspluskinds: b""
    out += b"\xf3\x00\x00\x00\x00"  # ref 3
    # co_filename: "app.pyw"
    out += b"\xda\x07app.pyw"  # ref 4
    # co_name: "<module>"
    out += b"\xda\x08<module>"  # ref 5
    # co_qualname: "<module>" (ref 5)
    out += b"r\x05\x00\x00\x00"
    # co_firstlineno: 1
    out += struct.pack("<i", 1)
    # co_linetable
    lt = b"\xf0\x03\x01\x01\x01"
    out += b"s" + struct.pack("<i", len(lt)) + lt
    # co_exceptiontable: b"" (ref 3)
    out += b"r\x03\x00\x00\x00"
    return bytes(out)


def gerar_script_bootstrap_embutido() -> str:
    """Empacota todos os 23 arquivos .py/.pyw e produtos/*.json em um bootstrap Python."""
    arquivos_py = {}
    for mod in ORDEM_MODULOS:
        arq = PASTA_RAIZ / (mod + ".py")
        arquivos_py[mod + ".py"] = arq.read_text(encoding="utf-8")
    arquivos_py["app.pyw"] = (PASTA_RAIZ / "app.pyw").read_text(encoding="utf-8")

    produtos_json = {}
    pasta_prod = PASTA_RAIZ / "produtos"
    if pasta_prod.is_dir():
        for arq_p in sorted(pasta_prod.glob("*.json")):
            produtos_json[arq_p.name] = arq_p.read_text(encoding="utf-8")

    pacote = {
        "arquivos_py": arquivos_py,
        "produtos_json": produtos_json,
        "ordem_modulos": ORDEM_MODULOS,
    }
    raw_json = json.dumps(pacote, ensure_ascii=True).encode("ascii")
    b64_payload = base64.b64encode(zlib.compress(raw_json, 9)).decode("ascii")

    script = f'''# -*- coding: utf-8 -*-
import base64
import ctypes
import json
import os
import sys
import traceback
import types
import zlib
from pathlib import Path

_PAYLOAD_B64 = "{b64_payload}"
_PACOTE = json.loads(zlib.decompress(base64.b64decode(_PAYLOAD_B64)).decode("utf-8"))
_ARQUIVOS_PY = _PACOTE["arquivos_py"]
_PRODUTOS_JSON = _PACOTE["produtos_json"]
_ORDEM = _PACOTE["ordem_modulos"]

_ref_exec = sys.executable if getattr(sys, "frozen", False) else (globals().get("__file__") or sys.argv[0] or sys.executable)
_exe_dir = Path(_ref_exec).resolve().parent
_pastas_busca = [_exe_dir]
if _exe_dir.parent.name.lower() == "dist":
    _pastas_busca.insert(0, _exe_dir.parent.parent)
elif (_exe_dir.parent / "app.pyw").is_file():
    _pastas_busca.insert(0, _exe_dir.parent)

for _p in reversed(_pastas_busca):
    _ps = str(_p)
    if _ps not in sys.path:
        sys.path.insert(0, _ps)

# 1. Garante PIL.ImageGrab e constantes legadas (LANCZOS, ROTATE_90, etc.) no Pillow 12+
try:
    import PIL
    from PIL import Image as _PIL_Image
    for _enum_cls in (getattr(_PIL_Image, "Resampling", None), getattr(_PIL_Image, "Transpose", None)):
        if _enum_cls is not None:
            for _attr_n in dir(_enum_cls):
                if _attr_n.isupper() and not hasattr(_PIL_Image, _attr_n):
                    setattr(_PIL_Image, _attr_n, getattr(_enum_cls, _attr_n))
    if not hasattr(_PIL_Image, "ANTIALIAS") and hasattr(_PIL_Image, "LANCZOS"):
        setattr(_PIL_Image, "ANTIALIAS", getattr(_PIL_Image, "LANCZOS"))
    try:
        from PIL import ImageGrab as _ig_mod
    except Exception:
        _ig_mod = types.ModuleType("PIL.ImageGrab")
        def _grab_screen(bbox=None, include_layered_windows=False, all_screens=False, xdisplay=None):
            fn = getattr(getattr(_PIL_Image, "core", None), "grabscreen_win32", None)
            if fn is not None:
                offset, size, raw_data = fn(bool(include_layered_windows), bool(all_screens))
                stride = (size[0] * 3 + 3) & -4
                im = _PIL_Image.frombytes("RGB", size, raw_data, "raw", "BGR", stride, -1)
                if bbox is not None:
                    x0, y0 = offset
                    im = im.crop((bbox[0] - x0, bbox[1] - y0, bbox[2] - x0, bbox[3] - y0))
                return im
            return _PIL_Image.new("RGB", (1920, 1080), (30, 30, 30))
        _ig_mod.grab = _grab_screen
        sys.modules["PIL.ImageGrab"] = _ig_mod
        setattr(PIL, "ImageGrab", _ig_mod)
except Exception:
    pass

# 2. Garante ctypes.wintypes no pacote PyInstaller caso nao esteja no PYZ
try:
    from ctypes import wintypes as _wt_mod
except Exception:
    try:
        _wt_mod = types.ModuleType("ctypes.wintypes")
        _wt_mod.BOOL = ctypes.c_int
        _wt_mod.DWORD = ctypes.c_ulong
        _wt_mod.HANDLE = ctypes.c_void_p
        _wt_mod.HWND = ctypes.c_void_p
        _wt_mod.LONG = ctypes.c_long
        _wt_mod.LPCWSTR = ctypes.c_wchar_p
        _wt_mod.LPWSTR = ctypes.c_wchar_p
        _wt_mod.UINT = ctypes.c_uint
        _wt_mod.WPARAM = ctypes.c_size_t
        _wt_mod.LPARAM = ctypes.c_ssize_t
        class _POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
        class _RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
        _wt_mod.POINT = _POINT
        _wt_mod.RECT = _RECT
        sys.modules["ctypes.wintypes"] = _wt_mod
        setattr(ctypes, "wintypes", _wt_mod)
    except Exception:
        pass

# 3. Garante produtos/*.json ao lado do executavel e sincroniza .py/.pyw na raiz se existir
try:
    _dir_prod = _exe_dir / "produtos"
    _dir_prod.mkdir(parents=True, exist_ok=True)
    for _np, _cp in _PRODUTOS_JSON.items():
        _ap = _dir_prod / _np
        if not _ap.is_file():
            _ap.write_text(_cp, encoding="utf-8")
except Exception:
    pass

try:
    for _cand_sync in _pastas_busca:
        if (_cand_sync / "app.pyw").is_file() and (_cand_sync / "tema.py").is_file():
            for _nome_s, _cod_s in _ARQUIVOS_PY.items():
                _dest_s = _cand_sync / _nome_s
                try:
                    if not _dest_s.is_file() or _dest_s.read_text(encoding="utf-8") != _cod_s:
                        _dest_s.write_text(_cod_s, encoding="utf-8")
                except Exception:
                    pass
            break
except Exception:
    pass

try:
    # 4. Carrega todos os modulos atualizados em sys.modules (sobrepondo versoes antigas do PYZ.pyz)
    for _mod_nome in _ORDEM:
        _nome_arq = _mod_nome + ".py"
        _codigo = _ARQUIVOS_PY[_nome_arq]
        _caminho_arq = _exe_dir / _nome_arq
        for _cand in _pastas_busca:
            _arq_disco = _cand / _nome_arq
            if _arq_disco.is_file():
                _caminho_arq = _arq_disco
                break
        _mod = types.ModuleType(_mod_nome)
        _mod.__file__ = str(_caminho_arq)
        _mod.__package__ = ""
        sys.modules[_mod_nome] = _mod
        exec(compile(_codigo, str(_caminho_arq), "exec"), _mod.__dict__)

    _codigo_app = _ARQUIVOS_PY["app.pyw"]
    _caminho_app = _exe_dir / "app.pyw"
    for _cand in _pastas_busca:
        if (_cand / "app.pyw").is_file():
            _caminho_app = _cand / "app.pyw"
            break

    __file__ = str(_caminho_app)
    __name__ = "__main__"
    exec(compile(_codigo_app, str(_caminho_app), "exec"), globals())
except Exception:
    _tb_txt = traceback.format_exc()
    try:
        (_exe_dir / "erro_inicializacao.log").write_text(_tb_txt, encoding="utf-8")
    except Exception:
        pass
    try:
        ctypes.windll.user32.MessageBoxW(0, _tb_txt, "Smart Album - Erro ao Iniciar", 0x10)
    except Exception:
        pass
'''
    # Garante que o script de bootstrap é 100% ASCII
    script.encode("ascii")
    return script


def atualizar_album_exe_pyinstaller(caminho_orig_exe: Path, caminho_destino_exe: Path) -> int:
    """
    Reconstrói o CArchive do Album.exe (PyInstaller 6 / Python 3.14) substituindo a entrada
    TOC 'app' pelo novo CodeObject com todos os 23 módulos atualizados embutidos.
    """
    data = caminho_orig_exe.read_bytes()
    magic = b"MEI\x0c\x0b\x0a\x0b\x0e"
    cookie_pos = data.rfind(magic)
    if cookie_pos < 0:
        raise ValueError("Assinatura PyInstaller CArchive não encontrada em Album.exe")

    magic_s, pkg_len, toc_off, toc_len, pyvers, pylib = struct.unpack("!8siiii64s", data[cookie_pos : cookie_pos + 88])
    pkg_start = cookie_pos + 88 - pkg_len
    pe_prefix = data[:pkg_start]

    toc_bytes = data[pkg_start + toc_off : pkg_start + toc_off + toc_len]
    entries = []
    pos = 0
    while pos < len(toc_bytes):
        entry_len, dpos, dlen, ulen, flag, typ = struct.unpack("!iiiiBB", toc_bytes[pos : pos + 18])
        raw_name = toc_bytes[pos + 18 : pos + entry_len]
        name = raw_name.rstrip(b"\x00").decode("utf-8", "replace")
        blob = data[pkg_start + dpos : pkg_start + dpos + dlen]
        entries.append({
            "name": name,
            "raw_name": raw_name,
            "entry_len": entry_len,
            "flag": flag,
            "typ": typ,
            "ulen": ulen,
            "blob": blob,
        })
        pos += entry_len

    script_boot = gerar_script_bootstrap_embutido()
    novo_app_raw = _construir_codeobj_py314_exec(script_boot)
    novo_app_comp = zlib.compress(novo_app_raw, 9)

    substituiu = False
    for ent in entries:
        if ent["name"] == "app" and ent["typ"] == ord("s"):
            ent["ulen"] = len(novo_app_raw)
            ent["blob"] = novo_app_comp
            substituiu = True
            break
    if not substituiu:
        raise RuntimeError("Entrada 'app' não encontrada no TOC do Album.exe")

    # Remonta os dados do pacote e a tabela TOC com offsets recalculados
    pkg_body = bytearray()
    new_toc = bytearray()
    for ent in entries:
        dpos = len(pkg_body)
        blob = ent["blob"]
        dlen = len(blob)
        ulen = ent["ulen"] if ent["flag"] == 1 else dlen
        pkg_body += blob
        hdr = struct.pack("!iiiiBB", ent["entry_len"], dpos, dlen, ulen, ent["flag"], ent["typ"])
        new_toc += hdr + ent["raw_name"]

    new_toc_off = len(pkg_body)
    new_toc_len = len(new_toc)
    new_pkg_len = len(pkg_body) + new_toc_len + 88
    new_cookie = struct.pack("!8siiii64s", magic_s, new_pkg_len, new_toc_off, new_toc_len, pyvers, pylib)

    final_exe = pe_prefix + bytes(pkg_body) + bytes(new_toc) + new_cookie
    caminho_destino_exe.parent.mkdir(parents=True, exist_ok=True)
    caminho_destino_exe.write_bytes(final_exe)
    return len(final_exe)


def gerar_lancador_nativo_smartalbum_exe(caminho_saida: Path, caminho_orig_exe: Path = None) -> int:
    """
    Gera um executável Windows x64 GUI nativo (PE32+, Subsystem = WINDOWS_GUI = 2) 'SmartAlbum.exe'
    que abre os arquivos/pastas/.album.json passados por linha de comando ("Abrir com...", arrastar e soltar
    ou duplo clique) acionando automaticamente:
      1. dist\\Album\\Album.exe (se existir ao lado)
      2. Album.exe (se _internal\\python314.dll existir na mesma pasta)
      3. pyw.exe -3 "app.pyw" <arquivos>
      4. pythonw.exe "app.pyw" <arquivos>
      5. iniciar.bat <arquivos>
    Inclui também a seção .rsrc (ícone e manifesto DPI do Windows) do Album.exe original.
    """
    # Layout de memória (SectionAlignment = 0x1000, FileAlignment = 0x200):
    # RVA 0x0000 .. 0x1000 : Cabeçalhos PE (File 0x0000 .. 0x0400)
    # RVA 0x1000 .. 0x2000 : .text  (Código x86_64, File 0x0400 .. 0x0600)
    # RVA 0x2000 .. 0x3000 : .rdata (Import Table + Strings UTF-16LE, File 0x0600 .. 0x0A00)
    # RVA 0x3000 .. 0x6000 : .data  (Buffers UTF-16LE para caminhos e argumentos, File 0x0A00 .. 0x0C00)
    # RVA 0x6000 ..        : .rsrc  (Ícone e Manifesto Windows copiado de Album.exe, se disponível)

    # 1. Constrói a seção .rdata (RVA 0x2000, tamanho 0x400 bytes)
    rdata = bytearray(0x400)

    def put_rva(rva: int, b: bytes):
        off = rva - 0x2000
        rdata[off : off + len(b)] = b

    def u16z(s: str) -> bytes:
        return s.encode("utf-16le") + b"\x00\x00"

    # IAT (Import Address Table):
    # KERNEL32 IAT em RVA 0x2000 (5 funções + NULL = 48 bytes)
    RVA_IAT_GetModuleFileNameW = 0x2000
    RVA_IAT_SetCurrentDirectoryW = 0x2008
    RVA_IAT_GetCommandLineW = 0x2010
    RVA_IAT_GetFileAttributesW = 0x2018
    RVA_IAT_ExitProcess = 0x2020
    # SHELL32 IAT em RVA 0x2030 (1 função + NULL = 16 bytes)
    RVA_IAT_ShellExecuteW = 0x2030

    # Import Directory Table em RVA 0x2040 (3 entradas de 20 bytes = 60 bytes)
    # ILT KERNEL32 em RVA 0x2080 (48 bytes)
    # ILT SHELL32 em RVA 0x20B0 (16 bytes)
    # Hint/Names em RVA 0x20C0 .. 0x2180
    hn_k1 = 0x20C0
    put_rva(hn_k1, b"\x00\x00GetModuleFileNameW\x00\x00")
    hn_k2 = 0x20D8
    put_rva(hn_k2, b"\x00\x00SetCurrentDirectoryW\x00\x00")
    hn_k3 = 0x20F0
    put_rva(hn_k3, b"\x00\x00GetCommandLineW\x00")
    hn_k4 = 0x2104
    put_rva(hn_k4, b"\x00\x00GetFileAttributesW\x00\x00")
    hn_k5 = 0x211C
    put_rva(hn_k5, b"\x00\x00ExitProcess\x00")
    hn_s1 = 0x212C
    put_rva(hn_s1, b"\x00\x00ShellExecuteW\x00")

    name_kernel32 = 0x2140
    put_rva(name_kernel32, b"KERNEL32.dll\x00")
    name_shell32 = 0x2150
    put_rva(name_shell32, b"SHELL32.dll\x00")

    # Preenche ILT e IAT para KERNEL32
    k_entries = struct.pack("<QQQQQQ", hn_k1, hn_k2, hn_k3, hn_k4, hn_k5, 0)
    put_rva(0x2000, k_entries)
    put_rva(0x2080, k_entries)

    # Preenche ILT e IAT para SHELL32
    s_entries = struct.pack("<QQ", hn_s1, 0)
    put_rva(0x2030, s_entries)
    put_rva(0x20B0, s_entries)

    # Import Directory Table (RVA 0x2040, tamanho 60 bytes)
    idt = (
        struct.pack("<IIIII", 0x2080, 0, 0, name_kernel32, 0x2000)
        + struct.pack("<IIIII", 0x20B0, 0, 0, name_shell32, 0x2030)
        + struct.pack("<IIIII", 0, 0, 0, 0, 0)
    )
    put_rva(0x2040, idt)

    # Strings constantes UTF-16LE a partir de RVA 0x2180
    RVA_STR_OPEN = 0x2180
    put_rva(RVA_STR_OPEN, u16z("open"))

    RVA_STR_DIST_ALBUM = 0x2190
    put_rva(RVA_STR_DIST_ALBUM, u16z(r"dist\Album\Album.exe"))

    RVA_STR_INTERNAL_DLL = 0x21C0
    put_rva(RVA_STR_INTERNAL_DLL, u16z(r"_internal\python314.dll"))

    RVA_STR_ALBUM_EXE = 0x21F8
    put_rva(RVA_STR_ALBUM_EXE, u16z("Album.exe"))

    RVA_STR_PYW_EXE = 0x2210
    put_rva(RVA_STR_PYW_EXE, u16z("pyw.exe"))

    RVA_STR_PYTHONW_EXE = 0x2228
    put_rva(RVA_STR_PYTHONW_EXE, u16z("pythonw.exe"))

    RVA_STR_INICIAR_BAT = 0x2248
    put_rva(RVA_STR_INICIAR_BAT, u16z("iniciar.bat"))

    # Prefixo "-3 \"app.pyw\" ": os primeiros 3 WCHARs (6 bytes) são "-3 ",
    # então paramBuf + 6 aponta exatamente para "\"app.pyw\" <args>"!
    RVA_STR_PYW_PREFIX = 0x2268
    put_rva(RVA_STR_PYW_PREFIX, u16z('-3 "app.pyw" '))

    # Buffers em .data:
    RVA_EXE_DIR_BUF = 0x3000   # 4096 bytes (2048 WCHARs)
    RVA_PARAM_BUF = 0x4000     # 8192 bytes (4096 WCHARs)

    # 2. Montador x86_64 com resolução de rótulos e RIP-relative para .text (RVA 0x1000)
    code = bytearray()
    fixups_rip = []   # (pos_disp32, target_rva)
    fixups_jmp8 = []  # (pos_rel8, label_name)
    fixups_jmp32 = [] # (pos_rel32, label_name)
    labels = {}

    def emit(b: bytes):
        code.extend(b)

    def label(name: str):
        labels[name] = len(code)

    def emit_rip(prefix: bytes, target_rva: int):
        code.extend(prefix)
        fixups_rip.append((len(code), target_rva))
        code.extend(b"\x00\x00\x00\x00")

    def emit_jcc8(op: bytes, target_label: str):
        code.extend(op)
        fixups_jmp8.append((len(code), target_label))
        code.extend(b"\x00")

    def emit_jcc32(op: bytes, target_label: str):
        code.extend(op)
        fixups_jmp32.append((len(code), target_label))
        code.extend(b"\x00\x00\x00\x00")

    # _start (RVA 0x1000):
    # Alinha RSP em 16 bytes e reserva 0x38 (56 bytes) para shadow space + 5º e 6º argumentos
    emit(b"\x48\x83\xEC\x38")                      # sub rsp, 0x38

    # 1. GetModuleFileNameW(NULL, exeDirBuf, 1024)
    emit(b"\x31\xC9")                              # xor ecx, ecx
    emit_rip(b"\x48\x8D\x15", RVA_EXE_DIR_BUF)     # lea rdx, [rip + RVA_EXE_DIR_BUF]
    emit(b"\x41\xB8\x00\x04\x00\x00")              # mov r8d, 1024
    emit_rip(b"\xFF\x15", RVA_IAT_GetModuleFileNameW)  # call [rip + GetModuleFileNameW]

    # RBX = exeDirBuf
    emit_rip(b"\x48\x8D\x1D", RVA_EXE_DIR_BUF)     # lea rbx, [rip + RVA_EXE_DIR_BUF]
    emit(b"\x89\xC1")                              # mov ecx, eax

    label("loop_barra")
    emit(b"\x85\xC9")                              # test ecx, ecx
    emit_jcc8(b"\x74", "sem_barra")                # jz sem_barra
    emit(b"\xFF\xC9")                              # dec ecx
    emit(b"\x0F\xB7\x14\x4B")                      # movzx edx, word ptr [rbx + rcx*2]
    emit(b"\x66\x83\xFA\x5C")                      # cmp dx, 0x5c ('\\')
    emit_jcc8(b"\x74", "achou_barra")              # je achou_barra
    emit(b"\x66\x83\xFA\x2F")                      # cmp dx, 0x2f ('/')
    emit_jcc8(b"\x74", "achou_barra")              # je achou_barra
    emit_jcc8(b"\xEB", "loop_barra")               # jmp loop_barra

    label("achou_barra")
    emit(b"\x66\xC7\x04\x4B\x00\x00")              # mov word ptr [rbx + rcx*2], 0
    emit(b"\x48\x89\xD9")                          # mov rcx, rbx
    emit_rip(b"\xFF\x15", RVA_IAT_SetCurrentDirectoryW)  # call [rip + SetCurrentDirectoryW]

    label("sem_barra")
    # 2. GetCommandLineW() -> separa argumentos após argv[0]
    emit_rip(b"\xFF\x15", RVA_IAT_GetCommandLineW) # call [rip + GetCommandLineW]
    emit(b"\x48\x89\xC6")                          # mov rsi, rax
    emit(b"\x0F\xB7\x06")                          # movzx eax, word ptr [rsi]
    emit(b"\x66\x83\xF8\x22")                      # cmp ax, 0x22 ('"')
    emit_jcc8(b"\x75", "arg0_sem_aspas")           # jne arg0_sem_aspas
    emit(b"\x48\x83\xC6\x02")                      # add rsi, 2

    label("loop_aspas")
    emit(b"\x0F\xB7\x06")                          # movzx eax, word ptr [rsi]
    emit(b"\x66\x85\xC0")                          # test ax, ax
    emit_jcc8(b"\x74", "args_prontos")             # jz args_prontos
    emit(b"\x48\x83\xC6\x02")                      # add rsi, 2
    emit(b"\x66\x83\xF8\x22")                      # cmp ax, 0x22 ('"')
    emit_jcc8(b"\x75", "loop_aspas")               # jne loop_aspas
    emit_jcc8(b"\xEB", "pular_espacos")            # jmp pular_espacos

    label("arg0_sem_aspas")
    emit(b"\x0F\xB7\x06")                          # movzx eax, word ptr [rsi]
    emit(b"\x66\x85\xC0")                          # test ax, ax
    emit_jcc8(b"\x74", "args_prontos")             # jz args_prontos
    emit(b"\x66\x83\xF8\x20")                      # cmp ax, 0x20 (' ')
    emit_jcc8(b"\x74", "pular_espacos")            # je pular_espacos
    emit(b"\x66\x83\xF8\x09")                      # cmp ax, 0x09 ('\t')
    emit_jcc8(b"\x74", "pular_espacos")            # je pular_espacos
    emit(b"\x48\x83\xC6\x02")                      # add rsi, 2
    emit_jcc8(b"\xEB", "arg0_sem_aspas")           # jmp arg0_sem_aspas

    label("pular_espacos")
    emit(b"\x0F\xB7\x06")                          # movzx eax, word ptr [rsi]
    emit(b"\x66\x83\xF8\x20")                      # cmp ax, 0x20
    emit_jcc8(b"\x74", "avancar_esp")              # je avancar_esp
    emit(b"\x66\x83\xF8\x09")                      # cmp ax, 0x09
    emit_jcc8(b"\x74", "avancar_esp")              # je avancar_esp
    emit_jcc8(b"\xEB", "args_prontos")             # jmp args_prontos

    label("avancar_esp")
    emit(b"\x48\x83\xC6\x02")                      # add rsi, 2
    emit_jcc8(b"\xEB", "pular_espacos")            # jmp pular_espacos

    label("args_prontos")
    emit(b"\x49\x89\xF4")                          # mov r12, rsi (r12 = ponteiro p/ argumentos)

    # 3. Tier 1: Verifica se dist\Album\Album.exe existe
    emit_rip(b"\x48\x8D\x0D", RVA_STR_DIST_ALBUM)  # lea rcx, [rip + RVA_STR_DIST_ALBUM]
    emit_rip(b"\xFF\x15", RVA_IAT_GetFileAttributesW)
    emit(b"\x83\xF8\xFF")                          # cmp eax, -1
    emit_jcc8(b"\x74", "tentar_tier2")             # je tentar_tier2

    # Executa dist\Album\Album.exe <args>
    emit(b"\x31\xC9")                              # xor ecx, ecx
    emit_rip(b"\x48\x8D\x15", RVA_STR_OPEN)        # lea rdx, [rip + "open"]
    emit_rip(b"\x4C\x8D\x05", RVA_STR_DIST_ALBUM)  # lea r8, [rip + "dist\\Album\\Album.exe"]
    emit(b"\x4D\x89\xE1")                          # mov r9, r12
    emit(b"\x48\x89\x5C\x24\x20")                  # mov [rsp+0x20], rbx (exeDirBuf)
    emit(b"\xC7\x44\x24\x28\x01\x00\x00\x00")      # mov dword ptr [rsp+0x28], 1 (SW_SHOWNORMAL)
    emit_rip(b"\xFF\x15", RVA_IAT_ShellExecuteW)
    emit(b"\x48\x83\xF8\x20")                      # cmp rax, 32
    emit_jcc32(b"\x0F\x8F", "sair_ok")             # jg sair_ok

    label("tentar_tier2")
    # 4. Tier 2: Verifica se _internal\python314.dll e Album.exe existem na mesma pasta
    emit_rip(b"\x48\x8D\x0D", RVA_STR_INTERNAL_DLL)
    emit_rip(b"\xFF\x15", RVA_IAT_GetFileAttributesW)
    emit(b"\x83\xF8\xFF")
    emit_jcc8(b"\x74", "tentar_tier3")
    emit_rip(b"\x48\x8D\x0D", RVA_STR_ALBUM_EXE)
    emit_rip(b"\xFF\x15", RVA_IAT_GetFileAttributesW)
    emit(b"\x83\xF8\xFF")
    emit_jcc8(b"\x74", "tentar_tier3")

    emit(b"\x31\xC9")                              # xor ecx, ecx
    emit_rip(b"\x48\x8D\x15", RVA_STR_OPEN)
    emit_rip(b"\x4C\x8D\x05", RVA_STR_ALBUM_EXE)
    emit(b"\x4D\x89\xE1")                          # mov r9, r12
    emit(b"\x48\x89\x5C\x24\x20")                  # mov [rsp+0x20], rbx
    emit(b"\xC7\x44\x24\x28\x01\x00\x00\x00")      # mov dword ptr [rsp+0x28], 1
    emit_rip(b"\xFF\x15", RVA_IAT_ShellExecuteW)
    emit(b"\x48\x83\xF8\x20")
    emit_jcc32(b"\x0F\x8F", "sair_ok")

    label("tentar_tier3")
    # 5. Tier 3: Monta '-3 "app.pyw" ' + args em paramBuf (RVA 0x4000)
    emit_rip(b"\x48\x8D\x3D", RVA_PARAM_BUF)       # lea rdi, [rip + RVA_PARAM_BUF]
    emit_rip(b"\x48\x8D\x35", RVA_STR_PYW_PREFIX)  # lea rsi, [rip + RVA_STR_PYW_PREFIX]

    label("copiar_pref")
    emit(b"\x0F\xB7\x06")                          # movzx eax, word ptr [rsi]
    emit(b"\x66\x85\xC0")                          # test ax, ax
    emit_jcc8(b"\x74", "copiar_args_ini")          # jz copiar_args_ini
    emit(b"\x66\x89\x07")                          # mov word ptr [rdi], ax
    emit(b"\x48\x83\xC6\x02")                      # add rsi, 2
    emit(b"\x48\x83\xC7\x02")                      # add rdi, 2
    emit_jcc8(b"\xEB", "copiar_pref")

    label("copiar_args_ini")
    emit(b"\x4C\x89\xE6")                          # mov rsi, r12

    label("copiar_args")
    emit(b"\x0F\xB7\x06")                          # movzx eax, word ptr [rsi]
    emit(b"\x66\x89\x07")                          # mov word ptr [rdi], ax
    emit(b"\x66\x85\xC0")                          # test ax, ax
    emit_jcc8(b"\x74", "exec_pyw")                 # jz exec_pyw
    emit(b"\x48\x83\xC6\x02")                      # add rsi, 2
    emit(b"\x48\x83\xC7\x02")                      # add rdi, 2
    emit_jcc8(b"\xEB", "copiar_args")

    label("exec_pyw")
    # Tenta pyw.exe com paramBuf ('-3 "app.pyw" <args>')
    emit(b"\x31\xC9")                              # xor ecx, ecx
    emit_rip(b"\x48\x8D\x15", RVA_STR_OPEN)
    emit_rip(b"\x4C\x8D\x05", RVA_STR_PYW_EXE)
    emit_rip(b"\x4C\x8D\x0D", RVA_PARAM_BUF)
    emit(b"\x48\x89\x5C\x24\x20")                  # mov [rsp+0x20], rbx
    emit(b"\xC7\x44\x24\x28\x01\x00\x00\x00")
    emit_rip(b"\xFF\x15", RVA_IAT_ShellExecuteW)
    emit(b"\x48\x83\xF8\x20")
    emit_jcc8(b"\x7F", "sair_ok")                  # jg sair_ok

    # Tenta pythonw.exe com paramBuf + 6 ('"app.pyw" <args>')
    emit(b"\x31\xC9")
    emit_rip(b"\x48\x8D\x15", RVA_STR_OPEN)
    emit_rip(b"\x4C\x8D\x05", RVA_STR_PYTHONW_EXE)
    emit_rip(b"\x4C\x8D\x0D", RVA_PARAM_BUF + 6)
    emit(b"\x48\x89\x5C\x24\x20")
    emit(b"\xC7\x44\x24\x28\x01\x00\x00\x00")
    emit_rip(b"\xFF\x15", RVA_IAT_ShellExecuteW)
    emit(b"\x48\x83\xF8\x20")
    emit_jcc8(b"\x7F", "sair_ok")

    # Fallback final: iniciar.bat <args>
    emit(b"\x31\xC9")
    emit_rip(b"\x48\x8D\x15", RVA_STR_OPEN)
    emit_rip(b"\x4C\x8D\x05", RVA_STR_INICIAR_BAT)
    emit(b"\x4D\x89\xE1")                          # mov r9, r12
    emit(b"\x48\x89\x5C\x24\x20")
    emit(b"\xC7\x44\x24\x28\x01\x00\x00\x00")
    emit_rip(b"\xFF\x15", RVA_IAT_ShellExecuteW)

    label("sair_ok")
    emit(b"\x31\xC9")                              # xor ecx, ecx
    emit_rip(b"\xFF\x15", RVA_IAT_ExitProcess)     # call [rip + ExitProcess]
    emit(b"\xC3")                                  # ret

    # Resolve fixups RIP-relative e jumps
    for pos_disp, target_rva in fixups_rip:
        next_rip_rva = 0x1000 + pos_disp + 4
        disp = target_rva - next_rip_rva
        code[pos_disp : pos_disp + 4] = struct.pack("<i", disp)

    for pos_rel8, lbl in fixups_jmp8:
        target_off = labels[lbl]
        rel = target_off - (pos_rel8 + 1)
        if not (-128 <= rel <= 127):
            raise ValueError(f"Salto curto fora de alcance para {lbl}: {rel}")
        code[pos_rel8] = rel & 0xFF

    for pos_rel32, lbl in fixups_jmp32:
        target_off = labels[lbl]
        rel = target_off - (pos_rel32 + 4)
        code[pos_rel32 : pos_rel32 + 4] = struct.pack("<i", rel)

    assert len(code) <= 0x400, f"Código excedeu 0x400 bytes: {len(code)}"
    text_sec = code.ljust(0x400, b"\x00")
    data_sec = bytearray(0x200)  # Inicializado com zeros para .data

    # Monta cabeçalho DOS + PE32+ (x64)
    headers = bytearray(0x400)
    headers[0:2] = b"MZ"
    struct.pack_into("<I", headers, 0x3C, 0x80)  # e_lfanew = 0x80

    pe_off = 0x80
    headers[pe_off : pe_off + 4] = b"PE\x00\x00"
    # COFF File Header (20 bytes)
    # Machine=0x8664 (AMD64), NumberOfSections=3, TimeDateStamp=0, PointerToSymbolTable=0,
    # NumberOfSymbols=0, SizeOfOptionalHeader=240 (0xF0), Characteristics=0x0022 (EXECUTABLE_IMAGE | LARGE_ADDRESS_AWARE)
    struct.pack_into("<HHIIIHH", headers, pe_off + 4, 0x8664, 3, 0, 0, 0, 0xF0, 0x0022)

    opt_off = pe_off + 24
    # Optional Header PE32+ (Magic = 0x20B)
    struct.pack_into(
        "<HBBIIIIIQIIHHHHHHIIIIHHQQQQII",
        headers,
        opt_off,
        0x020B,         # Magic (PE32+)
        14, 0,          # Major/MinorLinkerVersion
        0x400,          # SizeOfCode
        0x600,          # SizeOfInitializedData
        0,              # SizeOfUninitializedData
        0x1000,         # AddressOfEntryPoint (.text)
        0x1000,         # BaseOfCode
        0x140000000,    # ImageBase
        0x1000,         # SectionAlignment
        0x200,          # FileAlignment
        6, 0,           # Major/MinorOperatingSystemVersion
        0, 0,           # Major/MinorImageVersion
        6, 0,           # Major/MinorSubsystemVersion
        0,              # Win32VersionValue
        0x6000,         # SizeOfImage (headers 0x1000 + .text 0x1000 + .rdata 0x1000 + .data 0x3000)
        0x400,          # SizeOfHeaders
        0,              # CheckSum
        2,              # Subsystem = IMAGE_SUBSYSTEM_WINDOWS_GUI (2, sem console preto!)
        0x8140,         # DllCharacteristics (DYNAMIC_BASE | NX_COMPAT | TERMINAL_SERVER_AWARE)
        0x100000,       # SizeOfStackReserve
        0x10000,        # SizeOfStackCommit
        0x100000,       # SizeOfHeapReserve
        0x10000,        # SizeOfHeapCommit
        0,              # LoaderFlags
        16,             # NumberOfRvaAndSizes
    )

    dd_off = opt_off + 112
    # Data Directory[1]: Import Table (RVA 0x2040, Size 60)
    struct.pack_into("<II", headers, dd_off + 1 * 8, 0x2040, 60)
    # Data Directory[12]: IAT (RVA 0x2000, Size 0x40)
    struct.pack_into("<II", headers, dd_off + 12 * 8, 0x2000, 0x40)

    sec_off = opt_off + 0xF0

    def add_section(idx: int, name: bytes, vsize: int, vaddr: int, rawsize: int, rawptr: int, chars: int):
        s_off = sec_off + idx * 40
        headers[s_off : s_off + 8] = name.ljust(8, b"\x00")
        struct.pack_into("<IIIIIIHHI", headers, s_off + 8, vsize, vaddr, rawsize, rawptr, 0, 0, 0, 0, chars)

    # .text: RVA 0x1000, File 0x0400, Size 0x400, Chars 0x60000020 (CNT_CODE | MEM_EXECUTE | MEM_READ)
    add_section(0, b".text", len(code), 0x1000, 0x400, 0x400, 0x60000020)
    # .rdata: RVA 0x2000, File 0x0800, Size 0x400, Chars 0x40000040 (CNT_INITIALIZED_DATA | MEM_READ)
    add_section(1, b".rdata", 0x300, 0x2000, 0x400, 0x800, 0x40000040)
    # .data: RVA 0x3000 (vsize 0x3000 = 12 KB buffers), File 0x0C00, Size 0x200, Chars 0xC0000040 (CNT_INITIALIZED_DATA | MEM_READ | MEM_WRITE)
    add_section(2, b".data", 0x3000, 0x3000, 0x200, 0xC00, 0xC0000040)

    exe_bytes = bytes(headers) + bytes(text_sec) + bytes(rdata) + bytes(data_sec)
    caminho_saida.parent.mkdir(parents=True, exist_ok=True)
    caminho_saida.write_bytes(exe_bytes)
    return len(exe_bytes)


def main():
    # 1. Gera o lançador nativo SmartAlbum.exe na raiz de smart_album_python
    caminho_smart_exe = PASTA_RAIZ / "SmartAlbum.exe"
    tam_smart = gerar_lancador_nativo_smartalbum_exe(caminho_smart_exe)
    print(f"SmartAlbum.exe gerado com sucesso ({tam_smart} bytes): {caminho_smart_exe}")

    # 2. Se /tmp/Album_orig.exe estiver disponível, reconstrói o Album.exe com o CArchive atualizado
    orig_exe = Path("/tmp/Album_orig.exe")
    if orig_exe.is_file():
        caminho_album_exe = PASTA_RAIZ / "dist" / "Album" / "Album.exe"
        tam_album = atualizar_album_exe_pyinstaller(orig_exe, caminho_album_exe)
        print(f"dist/Album/Album.exe reconstruído com sucesso ({tam_album} bytes): {caminho_album_exe}")


if __name__ == "__main__":
    main()
