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


def caminhos_do_drop(widget, dados):
    """O texto que o tkinterdnd2 entrega (com chaves em nomes com espaço) em lista."""
    try:
        return [c for c in widget.tk.splitlist(dados) if c]
    except Exception:
        return caminhos_do_texto(dados)


def caminhos_do_texto(texto):
    """Caminhos colados como texto: um por linha, ou entre aspas."""
    saida = []
    for linha in str(texto or "").splitlines():
        linha = linha.strip()
        if not linha:
            continue
        entre_aspas = re.findall(r'"([^"]+)"', linha)
        saida.extend(entre_aspas if entre_aspas else [linha.strip("'")])
    return [c for c in saida if os.path.exists(c)]


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
    return str(Path(pasta_fotos) / "saida") if pasta_fotos else ""


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

def consertar_caminhos(projeto, nova_pasta):
    """
    Procura as fotos que faltam dentro da nova pasta (pelo nome; com nomes
    repetidos, vence a que tem mais pastas iguais no caminho) e corrige o
    projeto inteiro. Devolve (consertadas, ainda faltando).
    """
    por_nome = {}
    for foto in analise.listar_imagens(nova_pasta):
        por_nome.setdefault(foto.name.casefold(), []).append(foto)
    consertadas = 0
    for antigo in proj.faltando(projeto):
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
