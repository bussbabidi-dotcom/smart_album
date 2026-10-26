# -*- coding: utf-8 -*-
"""
Atalho para o programa de capas (pasta capas, ao lado do smart_album).

A capa não é mais feita aqui: o botão "Capa" abre o programa de capas e o
trabalho continua lá. Os arquivos da pasta capas nunca são alterados.
  - 1 faz capas_ui.pyw: janela principal (não recebe argumentos);
  - AUTOMAÇAO-PASTA.py --ui: a janela mínima da automação.
O caminho do programa e do Python podem ser trocados (capa_programa.json).
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

from comum import gravar_json, ler_json, pasta_programa

NOMES_PROGRAMA = ("1 faz capas_ui.pyw", "AUTOMAÇAO-PASTA.py")
PROGRAMA_AUTOMACAO = "AUTOMAÇAO-PASTA.py"
PASTAS_CANDIDATAS = tuple(pasta / "capas" for pasta in list(pasta_programa().parents)[:4])


def ler_config(arquivo):
    dados = ler_json(arquivo, {})
    return dados if isinstance(dados, dict) else {}


def gravar_config(arquivo, **valores):
    dados = ler_config(arquivo)
    dados.update({k: str(v) if v else "" for k, v in valores.items()})
    try:
        gravar_json(arquivo, dados)
    except OSError:
        pass
    return dados


def programa_padrao():
    """O programa de capas que existir primeiro (a janela principal tem preferência)."""
    for pasta in PASTAS_CANDIDATAS:
        for nome in NOMES_PROGRAMA:
            if (pasta / nome).is_file():
                return pasta / nome
    return None


def programa_configurado(arquivo):
    caminho = ler_config(arquivo).get("programa")
    if caminho and Path(caminho).is_file():
        return Path(caminho)
    return programa_padrao()


def achar_python(arquivo=None):
    """pythonw para abrir a janela sem console: o configurado, o deste Python ou o do PATH."""
    configurado = ler_config(arquivo).get("python") if arquivo else None
    if configurado and Path(configurado).is_file():
        return configurado
    if not getattr(sys, "frozen", False):
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        return str(pythonw if pythonw.is_file() else sys.executable)
    for nome in ("pyw", "pythonw", "py", "python"):
        achado = shutil.which(nome)
        if achado:
            return achado
    return None


def montar_comando(programa, python):
    """Lista de argumentos para abrir o programa de capas."""
    programa = Path(programa)
    if programa.suffix.lower() == ".exe":
        return [str(programa)]
    if not python:
        raise FileNotFoundError("Não achei o Python para abrir %s. Indique o pythonw.exe." % programa.name)
    comando = [str(python)]
    if Path(python).stem.lower() in ("py", "pyw"):
        comando.append("-3")
    comando.append(str(programa))
    if programa.name == PROGRAMA_AUTOMACAO:
        comando.append("--ui")
    return comando


def abrir(arquivo_config, programa=None):
    """Abre o programa de capas na pasta dele. Devolve (processo, comando)."""
    programa = Path(programa) if programa else programa_configurado(arquivo_config)
    if not programa or not programa.is_file():
        raise FileNotFoundError("Programa de capas não encontrado.")
    comando = montar_comando(programa, achar_python(arquivo_config))
    criacao = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    processo = subprocess.Popen(comando, cwd=str(programa.parent), creationflags=criacao)
    return processo, comando
