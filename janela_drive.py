# -*- coding: utf-8 -*-
"""
Janela "Importar do Google Drive": pede o link (e a chave da API, opcional) e baixa as
fotos numa pasta temporária que o programa apaga ao fechar. A rede fica em drive.py.
No fim mostra um relatório com as que faltaram (e o motivo) e permite tentar de novo.
"""

import tempfile
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


def _baixar_com_gdown(drive_id, pasta_destino):
    try:
        import gdown
    except ImportError:
        return []
    try:
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
    except Exception:
        return []
    return [Path(a) for a in arqs if Path(a).suffix.lower() in EXTENSOES and Path(a).is_file()]


def _tentar_drive_do_pc(drive_id, pasta_base, parar, progresso, avisar, sessao):
    """
    Se a pasta está no Google Drive para computador (G:), lista pelo índice dele e copia do G:
    (bem mais rápido e sem os limites do Google). O que não der pelo G: vai pela internet.
    Devolve drive.Relatorio ou None quando a pasta não está no Drive do PC.
    """
    try:
        import drive_local
    except Exception:
        return None
    indice = None
    try:
        indice = drive_local.abrir_indice()
        if indice is None:
            return None
        listagem = drive_local.listar(indice, drive_id, parar=parar)
    except Exception as e:
        avisar("Google Drive: não deu para usar o Drive do PC (%s); baixando pela internet." % e)
        return None
    finally:
        if indice is not None:
            indice.fechar()
    if listagem is None or not (listagem.fotos or listagem.sem_caminho):
        return None

    pasta = pasta_base / (drive.limpar_nome(listagem.titulo) or ("Drive_%s" % drive_id[:6]))
    pasta.mkdir(parents=True, exist_ok=True)
    itens = [f.item for f in listagem.fotos] + list(listagem.sem_caminho)
    rel = drive.Relatorio(sessao=sessao)
    rel.tarefas = drive.planejar_destinos(itens, pasta)
    origem = {id(f.item): f for f in listagem.fotos}
    avisar("Google Drive: %d foto(s) encontrada(s) no Drive do PC (G:), copiando..." % len(itens))

    locais = [i for i, (item, _d) in enumerate(rel.tarefas) if id(item) in origem]
    copias = drive_local.copiar_lote(
        [(origem[id(rel.tarefas[i][0])].caminho, rel.tarefas[i][1], rel.tarefas[i][0].tamanho) for i in locais],
        parar=parar,
        progresso=(lambda feitos, _total: progresso(feitos, len(itens))) if progresso else None,
    )
    rel.resultados = [None] * len(rel.tarefas)
    for i, resultado in zip(locais, copias):
        rel.resultados[i] = resultado

    pela_internet = [i for i, r in enumerate(rel.resultados) if not (r and r.ok) and not parar()]
    if pela_internet:
        avisar("Google Drive: %d foto(s) não vieram pelo Drive do PC; baixando pela internet..." % len(pela_internet))
        web = drive.baixar_lote([rel.tarefas[i] for i in pela_internet], parar=parar, sessao=sessao, aviso=avisar)
        for i, resultado in zip(pela_internet, web):
            rel.resultados[i] = resultado
    for i, resultado in enumerate(rel.resultados):
        if resultado is None:
            rel.resultados[i] = drive.ResultadoDownload(False, "cancelado")
    return rel


def preparar_e_baixar(tipo_id, drive_id, resourcekey, pasta_base, parar=None, progresso=None, aviso=None):
    """Lista (ou resolve o arquivo avulso), baixa em paralelo e devolve um drive.Relatorio. Roda fora da janela."""
    parar = parar or (lambda: False)
    avisar = aviso or (lambda _texto: None)
    pasta_base = Path(pasta_base)
    sessao = drive.Sessao()

    if tipo_id == "arquivo":
        item, erro = drive.obter_arquivo(drive_id, resourcekey, chave=sessao.chave)
        rel = drive.Relatorio(sessao=sessao)
        if erro is not None:
            rel.aviso_api = "A API do Google Drive recusou a chave: %s." % erro.descricao
            if erro.recusa_chave:
                sessao.api_recusada = erro.descricao
            avisar("Google Drive: " + rel.aviso_api)
        item.nome = item.nome or ("foto_%s" % drive_id[:8])
        rel.tarefas = drive.planejar_destinos([item], pasta_base / ("Drive_%s" % drive_id[:6]))
        rel.resultados = drive.baixar_lote(rel.tarefas, parar=parar, progresso=progresso, sessao=sessao, aviso=avisar)
        return rel

    rel_local = _tentar_drive_do_pc(drive_id, pasta_base, parar, progresso, avisar, sessao)
    if rel_local is not None:
        return rel_local

    listagem = drive.listar(drive_id, resourcekey, parar=parar, chave=sessao.chave)
    rel = drive.Relatorio(
        limitada=listagem.limitada,
        aviso_api=listagem.aviso_api,
        pastas_limitadas=listagem.pastas_limitadas,
        sessao=sessao,
    )
    if listagem.aviso_api:
        avisar("Google Drive: " + listagem.aviso_api)
        if listagem.chave_recusada:
            sessao.api_recusada = listagem.aviso_api
    if listagem.limitada:
        avisar("Google Drive: usando a página pública da pasta, que mostra no máximo ~%d fotos por pasta; podem faltar fotos."
               % drive.LIMITE_PASTA_PUBLICA)
    pasta = pasta_base / (drive.limpar_nome(listagem.titulo or "") or ("Drive_%s" % drive_id[:6]))
    pasta.mkdir(parents=True, exist_ok=True)
    if listagem.itens:
        avisar("Google Drive: %d foto(s) encontrada(s), baixando..." % len(listagem.itens))
        rel.tarefas = drive.planejar_destinos(listagem.itens, pasta)
        rel.resultados = drive.baixar_lote(rel.tarefas, parar=parar, progresso=progresso, sessao=sessao, aviso=avisar)
        return rel
    if not parar():
        rel.extras = _baixar_com_gdown(drive_id, pasta)
    return rel


def precisa_relatorio(relatorio):
    return bool(relatorio.faltando or relatorio.reduzidas or relatorio.limitada or relatorio.aviso_api)


def texto_aviso(relatorio):
    if relatorio.limitada:
        motivo = relatorio.aviso_api or "Sem chave da API do Google."
        return (
            "%s A listagem usou a página pública do Google Drive, que mostra no máximo ~%d fotos por pasta: "
            "podem existir mais fotos no link. Confira a chave da API (com a Google Drive API ativada no "
            "Google Cloud) e importe de novo para listar tudo." % (motivo, drive.LIMITE_PASTA_PUBLICA)
        )
    return relatorio.aviso_api or ""


def linhas_relatorio(relatorio):
    """[(texto, tag)] do quadro do relatório; tag é "titulo", "erro", "alerta" ou ""."""
    linhas = []
    faltando = relatorio.faltando
    if faltando:
        linhas.append(("Faltaram (%d):" % len(faltando), "titulo"))
        for item, destino, motivo in faltando:
            nome = "%s/%s" % (item.subpasta, item.nome or destino.name) if item.subpasta else (item.nome or destino.name)
            linhas.append(("  • %s — %s" % (nome, motivo), "erro"))
        linhas.append(("", ""))
    reduzidas = relatorio.reduzidas
    if reduzidas:
        linhas.append(("Baixadas em resolução reduzida (%d):" % len(reduzidas), "titulo"))
        for item, res in reduzidas:
            nome = res.caminho.name if res.caminho else item.nome
            linhas.append(("  • %s — baixada em resolução reduzida (o Google só liberou a miniatura)" % nome, "alerta"))
        linhas.append(("", ""))
    if relatorio.pastas_limitadas:
        linhas.append(("Pastas listadas pela página pública (máx. ~%d itens cada):" % drive.LIMITE_PASTA_PUBLICA, "titulo"))
        for nome, qtd in relatorio.pastas_limitadas:
            extra = " — provavelmente incompleta" if qtd >= drive.LIMITE_PASTA_PUBLICA - 5 else ""
            linhas.append(("  • %s (%d itens)%s" % (nome or "(pasta principal)", qtd, extra), "alerta" if extra else ""))
    if not linhas:
        linhas.append(("Todas as fotos encontradas foram baixadas em resolução original.", ""))
    return linhas


def _existe(janela):
    try:
        return bool(janela.winfo_exists())
    except tk.TclError:
        return False


def abrir_relatorio(app, relatorio):
    janela = tk.Toplevel(app.root)
    janela.title("Relatório do Google Drive")
    janela.configure(bg=tema.PAINEL)
    janela.transient(app.root)
    tema.criar_cabecalho_janela(
        janela,
        "Relatório do Download do Google Drive",
        "Fotos que faltaram, baixadas em resolução reduzida e limites da listagem",
        icone="☁",
    )

    rodape = tk.Frame(janela, bg=tema.CABECALHO, highlightthickness=1, highlightbackground=tema.BORDA, padx=14, pady=8)
    rodape.pack(side="bottom", fill="x")
    quadro = ttk.Frame(janela, padding=14)
    quadro.pack(fill="both", expand=True)

    lbl_resumo = ttk.Label(quadro, style="Titulo.TLabel")
    lbl_resumo.pack(anchor="w")
    lbl_aviso = tk.Label(quadro, bg=tema.PAINEL, fg=tema.ALERTA, font=tema.FONTE, justify="left", anchor="w")
    quadro.bind("<Configure>", lambda e: lbl_aviso.configure(wraplength=max(320, e.width - 28)), add="+")

    area = tk.Frame(quadro, bg=tema.PAINEL)
    area.pack(fill="both", expand=True, pady=(8, 0))
    texto = tk.Text(
        area, bg=tema.FUNDO, fg=tema.TEXTO, insertbackground=tema.TEXTO, relief="flat", wrap="word",
        font=tema.FONTE, highlightthickness=1, highlightbackground=tema.BORDA, padx=8, pady=6, height=12,
    )
    rolagem = ttk.Scrollbar(area, orient="vertical", command=texto.yview)
    texto.configure(yscrollcommand=rolagem.set)
    rolagem.pack(side="right", fill="y")
    texto.pack(side="left", fill="both", expand=True)
    texto.tag_configure("titulo", foreground=tema.DESTAQUE, font=tema.FONTE_FORTE)
    texto.tag_configure("erro", foreground=tema.ERRO)
    texto.tag_configure("alerta", foreground=tema.ALERTA)

    botoes = ttk.Frame(rodape)
    botoes.pack(fill="x")
    ttk.Button(botoes, text="Fechar", command=janela.destroy).pack(side="right", padx=(0, 10))
    botao_tentar = ttk.Button(botoes, text="Tentar de novo as que faltaram", style="Primario.TButton")
    botao_tentar.pack(side="right", padx=(0, 6))

    def atualizar():
        if not _existe(janela):
            return
        lbl_resumo.configure(
            text="Encontradas no link: %d   ·   Baixadas: %d   ·   Resolução reduzida: %d   ·   Faltaram: %d"
            % (relatorio.total, len(relatorio.baixadas), len(relatorio.reduzidas), len(relatorio.faltando))
        )
        aviso = texto_aviso(relatorio)
        lbl_aviso.configure(text=aviso)
        if aviso:
            lbl_aviso.pack(anchor="w", fill="x", pady=(6, 0), after=lbl_resumo)
        else:
            lbl_aviso.pack_forget()
        texto.configure(state="normal")
        texto.delete("1.0", "end")
        for linha, tag in linhas_relatorio(relatorio):
            texto.insert("end", linha + "\n", tag or ())
        texto.configure(state="disabled")
        botao_tentar.state(["!disabled"] if relatorio.faltando else ["disabled"])

    def tentar_de_novo():
        if not relatorio.faltando:
            return
        ja_tinha_fotos = bool(relatorio.baixadas)
        botao_tentar.state(["disabled"])

        def trabalho(t):
            return drive.tentar_novamente(relatorio, parar=t.parar, progresso=t.progresso, aviso=getattr(t, "aviso", None))

        def fim(novos, erro):
            atualizar()
            if erro:
                app.registrar("Google Drive: erro ao tentar de novo (%s)." % erro)
                return
            if novos:
                app.registrar("Google Drive: mais %d foto(s) baixada(s) na nova tentativa." % len(novos))
                app.adicionar_caminhos([str(p) for p in novos], perguntar_diagramar=not ja_tinha_fotos)
            else:
                app.registrar("Google Drive: nenhuma das fotos que faltaram pôde ser baixada.")

        if app.executar("Tentando de novo as fotos do Google Drive", trabalho, fim) is False:
            atualizar()

    botao_tentar.configure(command=tentar_de_novo)
    janela.bind("<Escape>", lambda _e: janela.destroy())
    atualizar()
    tema.configurar_janela_secundaria(
        janela,
        pai=app,
        chave="janela_drive_relatorio",
        largura=640,
        altura=440,
        min_largura=460,
        min_altura=300,
    )
    janela.atualizar = atualizar
    janela.botao_tentar = botao_tentar
    return janela


def baixar(app, url):
    tipo_id, drive_id, resourcekey = drive.extrair_id(url)
    if not drive_id:
        messagebox.showwarning("Google Drive", "Link inválido. Cole um link de pasta ou arquivo do Google Drive.", parent=app.root)
        return

    pasta_base = tempfile.mkdtemp(prefix="SmartAlbum_Drive_")
    app._pastas_temp_drive.append(pasta_base)

    def trabalho(t):
        return preparar_e_baixar(
            tipo_id, drive_id, resourcekey, pasta_base,
            parar=t.parar, progresso=t.progresso, aviso=getattr(t, "aviso", None),
        )

    def fim(relatorio, erro):
        if erro or relatorio is None or not (relatorio.baixadas or relatorio.faltando):
            try:
                if pasta_base in app._pastas_temp_drive:
                    app._pastas_temp_drive.remove(pasta_base)
                apagar_pasta(pasta_base, tentativas=5, pausa=0.15)
            except Exception:
                pass
            mensagem = (
                "Não foi possível baixar fotos desse link.\n"
                "Verifique se a pasta do Google Drive está com acesso 'Qualquer pessoa com o link'."
            )
            if relatorio is not None and relatorio.aviso_api:
                mensagem += "\n\n" + relatorio.aviso_api
            messagebox.showwarning("Google Drive", mensagem, parent=app.root)
            return
        baixadas = relatorio.baixadas
        app.registrar(
            "Google Drive: %d de %d foto(s) baixada(s) na pasta temporária (auto-excluída ao fechar)."
            % (len(baixadas), relatorio.total)
        )
        if relatorio.faltando:
            app.registrar("Google Drive: faltaram %d foto(s); veja o relatório." % len(relatorio.faltando))
        if baixadas:
            app.adicionar_caminhos([str(p) for p in baixadas], perguntar_diagramar=True)
        if precisa_relatorio(relatorio):
            abrir_relatorio(app, relatorio)

    app.executar("Baixando fotos do Google Drive", trabalho, fim)
