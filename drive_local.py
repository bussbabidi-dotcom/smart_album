# -*- coding: utf-8 -*-
"""
Fotos de uma pasta do Google Drive pelo "Google Drive para computador" instalado (unidade G:),
sem baixar pela internet.

O Drive instalado guarda um índice (metadata_sqlite_db) com todas as pastas, arquivos e atalhos.
Ele é lido em modo só leitura (abre na hora, não precisa copiar os ~2 GB) para:
  - achar a pasta pelo ID do link;
  - percorrer as subpastas;
  - trocar cada atalho ("Atalho (12)") pela foto real, com o nome original (IMG_1668.JPG);
  - montar o caminho da foto no G:.
Depois as fotos são copiadas do G: em paralelo. Se a foto ainda não estiver no PC, o próprio
Drive instalado a busca na hora. O que falhar volta para o download pela internet (drive.py),
porque cada item continua com o ID da nuvem.
"""

import concurrent.futures
import os
import shutil
import sqlite3
import string
import threading
from dataclasses import dataclass, field
from pathlib import Path

import drive

MIME_PASTA = "application/vnd.google-apps.folder"
MIME_ATALHO = "application/vnd.google-apps.shortcut"
NOMES_RAIZ = {"my drive", "meu drive", "root"}
PASTA_ATALHOS = ".shortcut-targets-by-id"
TRABALHADORES = 8
MAX_NIVEL = 20


def bancos_drivefs():
    """metadata_sqlite_db de cada conta do Drive instalado, o mais recente primeiro."""
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "DriveFS"
    if not base.is_dir():
        return []
    achados = [p for p in base.glob("*/metadata_sqlite_db") if p.is_file()]
    return sorted(achados, key=lambda p: p.stat().st_mtime, reverse=True)


def unidades_drive():
    """Letras de unidade que são do Drive instalado (têm 'Meu Drive' ou a pasta de atalhos)."""
    unidades = []
    for letra in string.ascii_uppercase[3:]:
        raiz = Path("%s:\\" % letra)
        try:
            if (raiz / "Meu Drive").is_dir() or (raiz / "My Drive").is_dir() or (raiz / PASTA_ATALHOS).is_dir():
                unidades.append(raiz)
        except OSError:
            continue
    return unidades


@dataclass
class FotoLocal:
    item: drive.ItemDrive
    caminho: Path


@dataclass
class ListagemLocal:
    titulo: str = ""
    fotos: list = field(default_factory=list)
    sem_caminho: list = field(default_factory=list)  # ItemDrive que o G: não mostra (vão pela internet)


class Indice:
    """Leitura do índice do Drive instalado (formato 2026: items.id = ID da nuvem, stable_parents)."""

    def __init__(self, banco, unidades=None):
        self.banco = Path(banco)
        self.conn = sqlite3.connect("file:%s?mode=ro" % self.banco.as_posix(), uri=True, timeout=10,
                                    check_same_thread=False)
        self.unidades = list(unidades if unidades is not None else unidades_drive())
        self._trava = threading.Lock()
        self._caminhos = {}
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(items)")}
        tabelas = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"stable_id", "id", "local_title", "mime_type"} <= cols or "stable_parents" not in tabelas:
            self.conn.close()
            raise RuntimeError("formato do índice do Google Drive desconhecido")
        self._filtro = " AND COALESCE(i.trashed, 0) = 0" + (" AND COALESCE(i.is_tombstone, 0) = 0" if "is_tombstone" in cols else "")
        self._tem_atalhos = "shortcut_details" in tabelas

    def fechar(self):
        try:
            self.conn.close()
        except Exception:
            pass

    def _um(self, sql, args):
        with self._trava:
            return self.conn.execute(sql, args).fetchone()

    def _todos(self, sql, args):
        with self._trava:
            return self.conn.execute(sql, args).fetchall()

    def por_id_nuvem(self, id_nuvem):
        return self._um(
            "SELECT i.stable_id, i.id, i.local_title, i.mime_type, i.file_size FROM items i WHERE i.id = ?" + self._filtro,
            (id_nuvem,),
        )

    def por_stable(self, stable_id):
        return self._um(
            "SELECT i.stable_id, i.id, i.local_title, i.mime_type, i.file_size FROM items i WHERE i.stable_id = ?" + self._filtro,
            (stable_id,),
        )

    def filhos(self, stable_id):
        return self._todos(
            "SELECT i.stable_id, i.id, i.local_title, i.mime_type, i.file_size FROM items i "
            "JOIN stable_parents p ON p.item_stable_id = i.stable_id WHERE p.parent_stable_id = ?" + self._filtro
            + " ORDER BY i.local_title",
            (stable_id,),
        )

    def alvo_do_atalho(self, stable_id):
        if not self._tem_atalhos:
            return None
        linha = self._um("SELECT target_stable_id FROM shortcut_details WHERE shortcut_stable_id = ?", (stable_id,))
        return self.por_stable(linha[0]) if linha else None

    def caminho(self, stable_id):
        """Caminho do item no G: (ou None). Sobe pelas pastas-pai e testa 'Meu Drive' e a pasta de atalhos."""
        if stable_id in self._caminhos:
            return self._caminhos[stable_id]
        cadeia = []  # do item até o topo: (stable_id, id_nuvem, titulo)
        atual, vistos = stable_id, set()
        for _ in range(60):
            if atual in vistos:
                break
            vistos.add(atual)
            linha = self._um("SELECT stable_id, id, local_title FROM items WHERE stable_id = ?", (atual,))
            if not linha:
                break
            cadeia.append(linha)
            pai = self._um("SELECT parent_stable_id FROM stable_parents WHERE item_stable_id = ? LIMIT 1", (atual,))
            if not pai:
                break
            atual = pai[0]
        if not cadeia:
            self._caminhos[stable_id] = None
            return None
        titulos = [str(c[2] or "") for c in reversed(cadeia)]
        candidatos = []
        for unidade in self.unidades:
            sem_raiz = titulos[1:] if titulos and titulos[0].strip().lower() in NOMES_RAIZ else titulos
            for nome_raiz in ("Meu Drive", "My Drive"):
                candidatos.append(unidade.joinpath(nome_raiz, *sem_raiz))
            candidatos.append(unidade.joinpath(*titulos))
            # Pasta compartilhada: aparece em G:\.shortcut-targets-by-id\<ID de algum ancestral>\<nome>\...
            for pos, (_sid, id_nuvem, titulo) in enumerate(reversed(cadeia)):
                if id_nuvem:
                    candidatos.append(unidade.joinpath(PASTA_ATALHOS, id_nuvem, *titulos[pos:]))
        achado = None
        for candidato in candidatos:
            try:
                if candidato.exists():
                    achado = candidato
                    break
            except OSError:
                continue
        self._caminhos[stable_id] = achado
        return achado


def _eh_pasta(mime):
    return (mime or "") == MIME_PASTA


def listar(indice, id_nuvem, parar=None):
    """Fotos da pasta (com subpastas e atalhos) pelo índice. Devolve ListagemLocal ou None se a pasta não estiver no Drive do PC."""
    parar = parar or (lambda: False)
    raiz = indice.por_id_nuvem(id_nuvem)
    if not raiz:
        return None
    lst = ListagemLocal(titulo=str(raiz[2] or ""))
    if not _eh_pasta(raiz[3]):
        alvo = indice.alvo_do_atalho(raiz[0]) if raiz[3] == MIME_ATALHO else raiz
        if alvo:
            _adicionar_foto(indice, lst, alvo, "", set())
        return lst
    vistos_pastas, vistos_fotos = set(), set()

    def visitar(stable_id, rel, nivel):
        if stable_id in vistos_pastas or nivel > MAX_NIVEL or parar():
            return
        vistos_pastas.add(stable_id)
        filhos = indice.filhos(stable_id)
        subpastas = []
        for filho in filhos:
            sid, _id, titulo, mime, _tam = filho
            if mime == MIME_ATALHO:
                filho = indice.alvo_do_atalho(sid)
                if not filho:
                    continue
                sid, _id, titulo, mime, _tam = filho
            if _eh_pasta(mime):
                subpastas.append((sid, titulo))
            else:
                _adicionar_foto(indice, lst, filho, rel, vistos_fotos)
        for sid, titulo in subpastas:
            nome = drive.limpar_nome(titulo) or str(sid)
            visitar(sid, "%s/%s" % (rel, nome) if rel else nome, nivel + 1)

    visitar(raiz[0], "", 0)
    return lst


def _adicionar_foto(indice, lst, linha, rel, vistos):
    sid, id_nuvem, titulo, mime, tamanho = linha
    if sid in vistos or not drive.eh_imagem(titulo, mime):
        return
    vistos.add(sid)
    item = drive.ItemDrive(id=id_nuvem, nome=str(titulo or "").strip(), mime=mime or "",
                           tamanho=drive._inteiro(tamanho) or None, subpasta=rel)
    caminho = indice.caminho(sid)
    if caminho is not None:
        lst.fotos.append(FotoLocal(item, caminho))
    else:
        lst.sem_caminho.append(item)


def abrir_indice():
    """Primeiro índice do Drive instalado que abre, ou None."""
    unidades = unidades_drive()
    if not unidades:
        return None
    for banco in bancos_drivefs():
        try:
            return Indice(banco, unidades)
        except Exception:
            continue
    return None


def _copiar(origem, destino, tamanho_esperado, parar):
    if parar():
        return drive.ResultadoDownload(False, "cancelado")
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    parcial = destino.with_name(destino.name + ".parcial")
    try:
        shutil.copyfile(origem, parcial)
        with open(parcial, "rb") as f:
            inicio = f.read(4096)
        tipo = drive.tipo_conteudo(inicio)
        tamanho = parcial.stat().st_size
        if tipo is None:
            raise OSError("o arquivo no Drive do PC não é uma foto")
        if tamanho_esperado and tamanho != tamanho_esperado:
            raise OSError("cópia incompleta do Drive do PC (%d de %d bytes)" % (tamanho, tamanho_esperado))
        final = drive._finalizar(parcial, destino, tipo)
        return drive.ResultadoDownload(True, "", str(origem), False, final.stat().st_size, final)
    except Exception as e:
        return drive.ResultadoDownload(False, "Drive do PC: %s" % getattr(e, "motivo", e))
    finally:
        if parcial.exists():
            try:
                parcial.unlink()
            except OSError:
                pass


def copiar_lote(tarefas_locais, parar=None, progresso=None, trabalhadores=TRABALHADORES):
    """[(origem_no_G, destino, tamanho)] copiados em paralelo; devolve ResultadoDownload na mesma ordem."""
    parar = parar or (lambda: False)
    resultados = [None] * len(tarefas_locais)
    if not tarefas_locais:
        return resultados
    feitos = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(trabalhadores, len(tarefas_locais)))) as pool:
        futuros = {pool.submit(_copiar, o, d, t, parar): i for i, (o, d, t) in enumerate(tarefas_locais)}
        for fut in concurrent.futures.as_completed(futuros):
            resultados[futuros[fut]] = fut.result()
            feitos += 1
            if progresso:
                progresso(feitos, len(tarefas_locais))
    return resultados
