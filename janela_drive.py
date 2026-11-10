# -*- coding: utf-8 -*-
"""
Janela "Importar do Google Drive": pede o link (e a chave da API, opcional) e baixa as
fotos numa pasta temporária que o programa apaga ao fechar. A rede fica em drive.py.
"""

import concurrent.futures
import re
import tempfile
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

import drive
import tema
from comum import EXTENSOES, apagar_pasta


def abrir(app):
    janela = tk.Toplevel(app.root)
    janela.title("Importar Fotos do Google Drive")
    janela.configure(bg=tema.PAINEL)
    janela.transient(app.root)

    tema.criar_cabecalho_janela(
        janela,
        "Importar Fotos ou Pastas do Google Drive",
        "Baixa todas as fotos do link em pasta temporária com limpeza automática ao fechar",
        icone="☁",
    )

    rodape = tk.Frame(janela, bg=tema.CABECALHO, highlightthickness=1, highlightbackground=tema.BORDA, padx=14, pady=8)
    rodape.pack(side="bottom", fill="x")

    quadro = ttk.Frame(janela, padding=14)
    quadro.pack(fill="both", expand=True)
    lbl_t = ttk.Label(quadro, text="Cole o link público da pasta ou foto do Google Drive:", style="Titulo.TLabel")
    lbl_t.pack(anchor="w")
    lbl_sub = ttk.Label(
        quadro,
        text="As fotos serão baixadas em uma pasta temporária e apagadas ao fechar o programa ou atingir o limite de memória.",
        style="Fraco.TLabel",
        justify="left",
    )
    lbl_sub.pack(anchor="w", pady=(2, 8))
    quadro.bind("<Configure>", lambda e: lbl_sub.configure(wraplength=max(320, e.width - 28)), add="+")

    linha = ttk.Frame(quadro)
    linha.pack(fill="x", pady=(0, 6))
    link_var = tk.StringVar()
    try:
        clip = app.root.clipboard_get().strip()
        if "drive.google.com" in clip:
            link_var.set(clip)
    except tk.TclError:
        pass

    campo = ttk.Entry(linha, textvariable=link_var, width=54)
    campo.pack(side="left", fill="x", expand=True, padx=(0, 6))
    campo.focus_set()

    def colar_clip():
        try:
            link_var.set(app.root.clipboard_get().strip())
        except tk.TclError:
            pass

    ttk.Button(linha, text="Colar", command=colar_clip).pack(side="right")

    ttk.Label(
        quadro,
        text="Chave da API do Google (opcional; sem ela o Drive lista só ~50 fotos por pasta):",
        style="Fraco.TLabel",
    ).pack(anchor="w", pady=(6, 2))
    chave_inicial = drive.chave_api()
    chave_var = tk.StringVar(value=chave_inicial)
    ttk.Entry(quadro, textvariable=chave_var, width=54, show="•").pack(fill="x")

    botoes = ttk.Frame(rodape)
    botoes.pack(fill="x")

    def confirmar():
        url = link_var.get().strip()
        if not url:
            return
        chave = chave_var.get().strip()
        if chave != chave_inicial:
            try:
                drive.gravar_chave_api(chave)
            except OSError as e:
                app.registrar("Google Drive: não foi possível salvar a chave da API (%s)." % e)
        try:
            janela.grab_release()
        except tk.TclError:
            pass
        janela.destroy()
        baixar(app, url)

    ttk.Button(botoes, text="Cancelar", command=janela.destroy).pack(side="right", padx=(0, 10))
    ttk.Button(botoes, text="Baixar Fotos", style="Primario.TButton", command=confirmar).pack(side="right", padx=(0, 6))
    janela.bind("<Return>", lambda _e: confirmar())
    janela.bind("<Escape>", lambda _e: janela.destroy())
    tema.configurar_janela_secundaria(
        janela,
        pai=app,
        chave="janela_drive",
        largura=600,
        altura=290,
        min_largura=440,
        min_altura=270,
    )
    janela.grab_set()


def baixar(app, url):
    tipo_id, drive_id, resourcekey = drive.extrair_id(url)
    if not drive_id:
        messagebox.showwarning("Google Drive", "Link inválido. Cole um link de pasta ou arquivo do Google Drive.", parent=app.root)
        return

    pasta_base = tempfile.mkdtemp(prefix="SmartAlbum_Drive_")
    app._pastas_temp_drive.append(pasta_base)
    estado_destino = {"pasta": Path(pasta_base) / ("Drive_%s" % drive_id[:6]), "esperadas": 0}
    estado_destino["pasta"].mkdir(parents=True, exist_ok=True)

    def trabalho(t):
        baixados = []
        pasta_destino = estado_destino["pasta"]

        if tipo_id == "arquivo":
            real_id, real_nome, _real_mime = drive.resolver_atalho(drive_id, resourcekey=resourcekey)
            nome_arq = re.sub(r'[<>:"/\\|?*]', "_", (real_nome or ("foto_%s.jpg" % drive_id[:8])).strip())
            if Path(nome_arq).suffix.lower() not in EXTENSOES:
                nome_arq += ".jpg"
            dest = pasta_destino / nome_arq
            estado_destino["esperadas"] = 1
            if drive.baixar_item(real_id or drive_id, dest) and dest.is_file():
                baixados.append(dest)
            return baixados

        # 1. Lista recursivamente as fotos (com chave da API, sem o limite de ~50 por pasta da página pública)
        achados, titulo_pasta = drive.listar_pasta(drive_id, resourcekey=resourcekey)
        if titulo_pasta:
            nome_pasta_limpo = re.sub(r'[<>:"/\\|?*]', "_", titulo_pasta).strip()
            if nome_pasta_limpo:
                nova_pasta = Path(pasta_base) / nome_pasta_limpo
                nova_pasta.mkdir(parents=True, exist_ok=True)
                pasta_destino = nova_pasta
                estado_destino["pasta"] = nova_pasta

        if achados:
            total = len(achados)
            estado_destino["esperadas"] = total
            nomes_usados = set()
            tarefas_dl = []
            for idx, (fid, nome_arq) in enumerate(achados, start=1):
                nome_limpo = re.sub(r'[<>:"/\\|?*]', "_", (nome_arq or ("foto_%03d.jpg" % idx)).strip())
                if Path(nome_limpo).suffix.lower() not in EXTENSOES:
                    nome_limpo += ".jpg"
                base_stem = Path(nome_limpo).stem
                ext_s = Path(nome_limpo).suffix
                cand = nome_limpo
                contador_dup = 2
                while cand.lower() in nomes_usados:
                    cand = "%s_%d%s" % (base_stem, contador_dup, ext_s)
                    contador_dup += 1
                nomes_usados.add(cand.lower())
                tarefas_dl.append((fid, pasta_destino / cand))

            concluidos = 0
            faltaram = []
            # Usa 4 workers paralelos para evitar bloqueio de taxa (HTTP 429) do Google Drive
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                futuros = {
                    pool.submit(drive.baixar_item, fid, dest): (fid, dest)
                    for fid, dest in tarefas_dl
                }
                for fut in concurrent.futures.as_completed(futuros):
                    if t.parar():
                        break
                    fid, dest = futuros[fut]
                    concluidos += 1
                    ok = False
                    try:
                        if fut.result() and dest.is_file() and dest.stat().st_size > 256:
                            baixados.append(dest)
                            ok = True
                    except Exception:
                        ok = False
                    if not ok:
                        faltaram.append((fid, dest))
                    t.progresso(concluidos, total)

            # Segunda passagem sequencial de recuperação para fotos que sofreram rate-limit momentâneo
            if faltaram and not t.parar():
                time.sleep(1.0)
                for fid, dest in faltaram:
                    if t.parar():
                        break
                    try:
                        if drive.baixar_item(fid, dest) and dest.is_file() and dest.stat().st_size > 256:
                            baixados.append(dest)
                    except Exception:
                        pass

            if baixados:
                # Ordena na mesma ordem exata da listagem da pasta
                ordem_map = {dest: i for i, (_fid, dest) in enumerate(tarefas_dl)}
                baixados.sort(key=lambda p: ordem_map.get(p, 999999))
                return baixados

        # 2. Fallback com gdown (com remaining_ok=True para não parar em 50 arquivos)
        try:
            import gdown
            try:
                arqs = gdown.download_folder(
                    id=drive_id,
                    output=str(pasta_destino),
                    quiet=True,
                    use_cookies=False,
                    remaining_ok=True,
                ) or []
            except TypeError:
                arqs = gdown.download_folder(
                    id=drive_id,
                    output=str(pasta_destino),
                    quiet=True,
                    use_cookies=False,
                ) or []
            baixados = [Path(a) for a in arqs if Path(a).suffix.lower() in EXTENSOES and Path(a).is_file()]
        except Exception:
            pass
        return baixados

    def fim(baixados, erro):
        if erro or not baixados:
            try:
                if pasta_base in app._pastas_temp_drive:
                    app._pastas_temp_drive.remove(pasta_base)
                apagar_pasta(pasta_base, tentativas=5, pausa=0.15)
            except Exception:
                pass
            messagebox.showwarning(
                "Google Drive",
                "Não foi possível baixar fotos desse link.\nVerifique se a pasta do Google Drive está com acesso 'Qualquer pessoa com o link'.",
                parent=app.root,
            )
            return
        esperadas = estado_destino.get("esperadas") or len(baixados)
        app.registrar(
            "Google Drive: %d de %d foto(s) baixada(s) na pasta temporária (auto-excluída ao fechar)." % (len(baixados), esperadas)
        )
        app.adicionar_caminhos([str(p) for p in baixados], perguntar_diagramar=True)

    app.executar("Baixando fotos do Google Drive", trabalho, fim)
