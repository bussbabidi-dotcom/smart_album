# -*- coding: utf-8 -*-
"""
Gera o lançador nativo SmartAlbum.exe (Windows x64 GUI, sem janela de console) na raiz do projeto.

O Album.exe em dist/ é gerado pelo PyInstaller com montar_exe.bat (Album.spec).

  py -3 gerar_lancador.py
"""

import os
import struct
import sys
from pathlib import Path

sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

PASTA_RAIZ = Path(__file__).resolve().parent


def gerar_lancador_nativo_smartalbum_exe(caminho_saida: Path) -> int:
    """
    Gera um executável Windows x64 GUI nativo (PE32+, Subsystem = WINDOWS_GUI = 2) 'SmartAlbum.exe'
    que abre os arquivos/pastas/.album.json passados por linha de comando ("Abrir com...", arrastar e soltar
    ou duplo clique) acionando automaticamente:
      1. dist\\Album\\Album.exe (se existir ao lado)
      2. Album.exe (se _internal\\base_library.zip existir na mesma pasta, ou seja, build do PyInstaller)
      3. pyw.exe -3 "app.pyw" <arquivos>
      4. pythonw.exe "app.pyw" <arquivos>
      5. iniciar.bat <arquivos>
    """
    # Layout de memória (SectionAlignment = 0x1000, FileAlignment = 0x200):
    # RVA 0x0000 .. 0x1000 : Cabeçalhos PE (File 0x0000 .. 0x0400)
    # RVA 0x1000 .. 0x2000 : .text  (Código x86_64, File 0x0400 .. 0x0600)
    # RVA 0x2000 .. 0x3000 : .rdata (Import Table + Strings UTF-16LE, File 0x0600 .. 0x0A00)
    # RVA 0x3000 .. 0x6000 : .data  (Buffers UTF-16LE para caminhos e argumentos, File 0x0A00 .. 0x0C00)
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
    RVA_STR_ALBUM_EXE = 0x21F8
    # Arquivo presente em todo build onedir do PyInstaller, independente da versão do Python.
    str_internal = u16z(r"_internal\base_library.zip")
    assert len(str_internal) <= RVA_STR_ALBUM_EXE - RVA_STR_INTERNAL_DLL
    put_rva(RVA_STR_INTERNAL_DLL, str_internal)

    put_rva(RVA_STR_ALBUM_EXE, u16z("Album.exe"))

    RVA_STR_PYW_EXE = 0x2210
    put_rva(RVA_STR_PYW_EXE, u16z("pyw.exe"))

    RVA_STR_PYTHONW_EXE = 0x2228
    put_rva(RVA_STR_PYTHONW_EXE, u16z("pythonw.exe"))

    RVA_STR_INICIAR_BAT = 0x2248
    put_rva(RVA_STR_INICIAR_BAT, u16z("iniciar.bat"))

    # Prefixo "-3 -B \"app.pyw\" ": os primeiros 3 WCHARs (6 bytes) são "-3 ",
    # então paramBuf + 6 aponta exatamente para "-B \"app.pyw\" <args>"!
    RVA_STR_PYW_PREFIX = 0x2268
    put_rva(RVA_STR_PYW_PREFIX, u16z('-3 -B "app.pyw" '))

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
    # 4. Tier 2: Verifica se _internal\base_library.zip e Album.exe existem na mesma pasta
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
    caminho = PASTA_RAIZ / "SmartAlbum.exe"
    tamanho = gerar_lancador_nativo_smartalbum_exe(caminho)
    print(f"SmartAlbum.exe gerado ({tamanho} bytes): {caminho}")


if __name__ == "__main__":
    main()
