# -*- coding: utf-8 -*-
"""
Janela Exportar: formato (JPG, PDF Industrial, PSD), resolução DPI (150/300),
perfil de cor (sRGB/AdobeRGB), marca de registro para corte industrial,
pasta, quais lâminas, nomes dos arquivos, conferência antes de exportar,
andamento e lista do que foi gravado.
"""

import os
import shutil
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

import exportar
import tema
from comum import gravar_json, ler_json, normalizar_caminho
from produtos import lamina_mm

LIMITE_RECENTES = 8
FORMATOS = (
    ("jpg", "JPG (laboratório)"),
    ("pdf", "PDF (impressão / corte industrial)"),
    ("psd", "PSD com camadas (Photoshop)"),
    ("jpg_pdf", "JPG e PDF"),
    ("ambos", "JPG e PSD"),
)
EXISTENTES = (
    ("substituir", "Substituir"),
    ("pular", "Pular (manter o que já existe)"),
    ("perguntar", "Perguntar"),
)


def ler_recentes(arquivo):
    dados = ler_json(arquivo, {})
    return [p for p in (dados.get("recentes") if isinstance(dados, dict) else None) or [] if isinstance(p, str)]


def lembrar_pasta(arquivo, pasta):
    recentes = [str(pasta)] + [p for p in ler_recentes(arquivo) if normalizar_caminho(p) != normalizar_caminho(pasta)]
    try:
        gravar_json(arquivo, {"recentes": recentes[:LIMITE_RECENTES]})
    except OSError:
        pass


def espaco_livre(pasta):
    """Bytes livres no disco da pasta (ou da pasta mais próxima que existe)."""
    caminho = Path(pasta)
    while not caminho.exists() and caminho.parent != caminho:
        caminho = caminho.parent
    try:
        return shutil.disk_usage(str(caminho)).free
    except OSError:
        return None


def pasta_final(opcoes, projeto):
    pasta = Path(opcoes["pasta"])
    if opcoes.get("subpasta"):
        nome = exportar.nome_arquivo("{album}", 1, projeto, None)
        pasta = pasta / nome
    return pasta


def indices_escolhidos(opcoes, projeto, atual):
    total = len(projeto.get("laminas", []))
    if opcoes["laminas"] == "atual":
        if not isinstance(atual, int) or not 0 <= atual < total:
            raise ValueError("Nenhuma lâmina aberta.")
        return [atual]
    if opcoes["laminas"] == "intervalo":
        return exportar.laminas_do_texto(opcoes["intervalo"], total)
    return list(range(total))


def arquivos_previstos(opcoes, projeto, produto, itens):
    """Lista de nomes de arquivos (com extensão) que serão gerados na pasta de destino."""
    fmt = opcoes.get("formato", "jpg")
    pdf_unico = bool(opcoes.get("pdf_unico", True))
    saida = []
    if fmt in ("jpg", "ambos", "jpg_pdf"):
        saida.extend(nome + ".jpg" for nome, _i in itens)
    if fmt in ("psd", "ambos"):
        saida.extend(nome + ".psd" for nome, _i in itens)
    if fmt in ("pdf", "jpg_pdf"):
        if pdf_unico:
            saida.append(exportar.nome_pdf_unico(projeto, produto))
        else:
            saida.extend(nome + ".pdf" for nome, _i in itens)
    return saida


def conferir(projeto, indices):
    """Avisos sobre as lâminas escolhidas: espaços vazios, fotos e fundos que sumiram."""
    vazios, faltam_fotos, faltam_fundos = 0, set(), set()
    for i in indices:
        lamina = projeto["laminas"][i]
        for espaco in lamina.get("espacos", []):
            if not espaco.get("foto"):
                vazios += 1
            elif not Path(espaco["foto"]).is_file():
                faltam_fotos.add(normalizar_caminho(espaco["foto"]))
        for ch_f in ("fundo_imagem", "fundo_imagem_esquerda", "fundo_imagem_direita"):
            fundo = (lamina.get(ch_f) or {}).get("caminho")
            if fundo and not Path(fundo).is_file():
                faltam_fundos.add(normalizar_caminho(fundo))
    avisos = []
    if vazios:
        avisos.append("%s espaço(s) sem foto (saem só com o fundo)." % vazios)
    if faltam_fotos:
        avisos.append("%s foto(s) não encontradas no disco." % len(faltam_fotos))
    if faltam_fundos:
        avisos.append("%s imagem(ns) de fundo não encontradas (sai só a cor)." % len(faltam_fundos))
    return avisos


class JanelaExportar(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.title("Exportar Álbum (JPG · PDF Industrial · PSD)")
        self.configure(bg=tema.PAINEL)
        self.transient(app.root)
        self.minsize(620, 420)
        self.protocol("WM_DELETE_WINDOW", self.fechar)
        self.bind("<Escape>", lambda e: self.fechar())
        self.gravados = []
        self.rodando = False
        self.problemas = []
        self._agendado = None
        op = app.opcoes_exportacao()
        self.vars = {
            "formato": tk.StringVar(value=op.get("formato", "jpg")),
            "qualidade": tk.IntVar(value=int(op.get("qualidade", exportar.QUALIDADE_JPG))),
            "icc": tk.BooleanVar(value=bool(op.get("icc", True))),
            "dpi": tk.IntVar(value=int(op.get("dpi", 300)) if int(op.get("dpi", 300)) in (150, 300) else 300),
            "perfil_cor": tk.StringVar(value=op.get("perfil_cor", "sRGB") if op.get("perfil_cor") in ("sRGB", "AdobeRGB") else "sRGB"),
            "marcas_registro": tk.BooleanVar(value=bool(op.get("marcas_registro", False))),
            "pdf_unico": tk.BooleanVar(value=bool(op.get("pdf_unico", True))),
            "pasta": tk.StringVar(value=op.get("pasta", "")),
            "subpasta": tk.BooleanVar(value=bool(op.get("subpasta", False))),
            "laminas": tk.StringVar(value=op.get("laminas", "todas")),
            "intervalo": tk.StringVar(value=op.get("intervalo", "")),
            "padrao": tk.StringVar(value=op.get("padrao", exportar.PADRAO_NOME)),
            "existentes": tk.StringVar(value=op.get("existentes", "substituir")),
        }
        self._montar()
        for var in self.vars.values():
            var.trace_add("write", lambda *a: self._agendar())
        self.atualizar()
        self.update_idletasks()
        x = app.root.winfo_rootx() + max(0, (app.root.winfo_width() - self.winfo_reqwidth()) // 2)
        y = max(20, app.root.winfo_rooty() + 28)
        self.geometry("+%d+%d" % (x, y))

    def _montar(self):
        quadro = ttk.Frame(self, padding=12)
        quadro.pack(fill="both", expand=True)
        quadro.columnconfigure(0, weight=1)
        quadro.columnconfigure(1, weight=1)

        formato = ttk.LabelFrame(quadro, text="Formato e Impressão Industrial", padding=8)
        formato.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=(0, 6))
        for valor, texto in FORMATOS:
            ttk.Radiobutton(formato, text=texto, value=valor, variable=self.vars["formato"]).pack(anchor="w")

        # Resolução DPI (150 / 300)
        linha_dpi = ttk.Frame(formato)
        linha_dpi.pack(fill="x", pady=(6, 0))
        ttk.Label(linha_dpi, text="Resolução (DPI):").pack(side="left")
        ttk.Radiobutton(linha_dpi, text="300 DPI", value=300, variable=self.vars["dpi"]).pack(side="right", padx=(6, 0))
        ttk.Radiobutton(linha_dpi, text="150 DPI", value=150, variable=self.vars["dpi"]).pack(side="right")

        # Perfil de Cor (sRGB / AdobeRGB)
        linha_cor = ttk.Frame(formato)
        linha_cor.pack(fill="x", pady=(4, 0))
        ttk.Label(linha_cor, text="Perfil de Cor:").pack(side="left")
        ttk.Radiobutton(linha_cor, text="AdobeRGB", value="AdobeRGB", variable=self.vars["perfil_cor"]).pack(side="right", padx=(6, 0))
        ttk.Radiobutton(linha_cor, text="sRGB", value="sRGB", variable=self.vars["perfil_cor"]).pack(side="right")

        # Qualidade do JPG
        linha_qual = ttk.Frame(formato)
        linha_qual.pack(fill="x", pady=(4, 0))
        ttk.Label(linha_qual, text="Qualidade do JPG:").pack(side="left")
        ttk.Spinbox(linha_qual, from_=60, to=100, increment=1, width=5, textvariable=self.vars["qualidade"]).pack(side="right")

        # Opções de ICC, Marca de Registro Industrial e PDF Único
        self.caixa_icc = ttk.Checkbutton(formato, text="Embutir perfil ICC no arquivo", variable=self.vars["icc"])
        self.caixa_icc.pack(anchor="w", pady=(5, 0))

        self.caixa_marcas = ttk.Checkbutton(
            formato,
            text="Marca de registro para corte industrial (guilhotina ⊕)",
            variable=self.vars["marcas_registro"],
        )
        self.caixa_marcas.pack(anchor="w", pady=(2, 0))
        tema.dica(
            self.caixa_marcas,
            "Adiciona margem técnica de 10 mm com marcas de corte nos 4 cantos,\n"
            "marcas de dobra central, alvos ópticos de registro (⊕) e barra de controle CMYK/RGB.",
        )

        self.caixa_pdf_unico = ttk.Checkbutton(
            formato,
            text="PDF em arquivo único multipágina ({album}.pdf)",
            variable=self.vars["pdf_unico"],
        )
        self.caixa_pdf_unico.pack(anchor="w", pady=(2, 0))

        self.rotulo_especificacoes = ttk.Label(formato, text="", style="Fraco.TLabel", wraplength=290, justify="left")
        self.rotulo_especificacoes.pack(anchor="w", pady=(4, 0))

        laminas = ttk.LabelFrame(quadro, text="Quais lâminas", padding=8)
        laminas.grid(row=0, column=1, sticky="nsew", pady=(0, 6))
        self.radio_todas = ttk.Radiobutton(laminas, text="", value="todas", variable=self.vars["laminas"])
        self.radio_todas.pack(anchor="w")
        self.radio_atual = ttk.Radiobutton(laminas, text="", value="atual", variable=self.vars["laminas"])
        self.radio_atual.pack(anchor="w")
        linha = ttk.Frame(laminas)
        linha.pack(fill="x")
        ttk.Radiobutton(linha, text="Estas:", value="intervalo", variable=self.vars["laminas"]).pack(side="left")
        campo = ttk.Entry(linha, textvariable=self.vars["intervalo"], width=14)
        campo.pack(side="left", fill="x", expand=True, padx=(4, 0))
        campo.bind("<FocusIn>", lambda e: self.vars["laminas"].set("intervalo"))
        tema.dica(campo, "Exemplo: 1-5, 8, 10-12")
        self.rotulo_laminas = ttk.Label(laminas, text="", style="Fraco.TLabel")
        self.rotulo_laminas.pack(anchor="w", pady=(4, 0))

        destino = ttk.LabelFrame(quadro, text="Onde salvar", padding=8)
        destino.grid(row=1, column=0, columnspan=2, sticky="nsew", pady=(0, 6))
        linha = ttk.Frame(destino)
        linha.pack(fill="x")
        ttk.Entry(linha, textvariable=self.vars["pasta"]).pack(side="left", fill="x", expand=True)
        ttk.Button(linha, text="Escolher...", style="Compacto.TButton", command=self.escolher_pasta).pack(side="left", padx=(4, 0))
        self.botao_sugestoes = ttk.Menubutton(linha, text="Sugestões", style="Compacto.TMenubutton")
        self.menu_sugestoes = tk.Menu(self.botao_sugestoes, tearoff=0, postcommand=self._montar_sugestoes)
        self.botao_sugestoes["menu"] = self.menu_sugestoes
        self.botao_sugestoes.pack(side="left", padx=(4, 0))
        self.caixa_subpasta = ttk.Checkbutton(destino, text="", variable=self.vars["subpasta"])
        self.caixa_subpasta.pack(anchor="w", pady=(4, 0))
        self.rotulo_destino = ttk.Label(destino, text="", style="Fraco.TLabel", wraplength=580, justify="left")
        self.rotulo_destino.pack(anchor="w", pady=(2, 0))

        nomes = ttk.LabelFrame(quadro, text="Nomes dos arquivos", padding=8)
        nomes.grid(row=2, column=0, columnspan=2, sticky="nsew", pady=(0, 6))
        linha = ttk.Frame(nomes)
        linha.pack(fill="x")
        ttk.Label(linha, text="Padrão").pack(side="left")
        ttk.Entry(linha, textvariable=self.vars["padrao"], width=28).pack(side="left", padx=6)
        ttk.Button(linha, text="Padrão original", style="Compacto.TButton",
                   command=lambda: self.vars["padrao"].set(exportar.PADRAO_NOME)).pack(side="left")
        ttk.Label(nomes, text="Use {n} (número da lâmina), {n:02d} (01, 02...), {album}, {produto}, {total}.",
                  style="Fraco.TLabel").pack(anchor="w", pady=(4, 0))
        self.rotulo_exemplo = ttk.Label(nomes, text="", wraplength=580, justify="left")
        self.rotulo_exemplo.pack(anchor="w", pady=(2, 0))
        linha = ttk.Frame(nomes)
        linha.pack(fill="x", pady=(6, 0))
        ttk.Label(linha, text="Se o arquivo já existir").pack(side="left")
        self.combo_existentes = ttk.Combobox(linha, state="readonly", width=30, values=[t for _v, t in EXISTENTES])
        self.combo_existentes.pack(side="left", padx=6)
        self.combo_existentes.bind("<<ComboboxSelected>>", self._escolheu_existentes)
        self.rotulo_existentes = ttk.Label(linha, text="", style="Fraco.TLabel")
        self.rotulo_existentes.pack(side="left")

        conferencia = ttk.LabelFrame(quadro, text="Antes de exportar", padding=8)
        conferencia.grid(row=3, column=0, columnspan=2, sticky="nsew", pady=(0, 6))
        self.rotulo_conferencia = ttk.Label(conferencia, text="", wraplength=580, justify="left")
        self.rotulo_conferencia.pack(anchor="w")

        andamento = ttk.Frame(quadro)
        andamento.grid(row=4, column=0, columnspan=2, sticky="nsew")
        quadro.rowconfigure(4, weight=1)
        self.barra = ttk.Progressbar(andamento, mode="determinate")
        self.barra.pack(fill="x")
        self.registro = tk.Text(andamento, height=6, bg=tema.PAINEL_2, fg=tema.TEXTO, relief="flat",
                                insertbackground=tema.TEXTO, font=tema.FONTE_PEQUENA, state="disabled")
        self.registro.pack(fill="both", expand=True, pady=(4, 0))

        botoes = ttk.Frame(quadro)
        botoes.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.botao_exportar = ttk.Button(botoes, text="Exportar", style="PrimarioCompacto.TButton", command=self.exportar)
        self.botao_exportar.pack(side="left")
        self.botao_parar = ttk.Button(botoes, text="Parar", style="Compacto.TButton", command=self.app.parar_tarefa,
                                      state="disabled")
        self.botao_parar.pack(side="left", padx=4)
        ttk.Button(botoes, text="Fechar", style="Compacto.TButton", command=self.fechar).pack(side="right")
        self.botao_abrir = ttk.Button(botoes, text="Abrir pasta", style="Compacto.TButton", command=self.abrir_pasta)
        self.botao_abrir.pack(side="right", padx=4)

    # ---------------- leitura das escolhas ----------------

    def opcoes(self):
        op = {chave: var.get() for chave, var in self.vars.items()}
        try:
            op["qualidade"] = max(60, min(100, int(op["qualidade"])))
        except (tk.TclError, ValueError):
            op["qualidade"] = exportar.QUALIDADE_JPG
        try:
            op["dpi"] = 150 if int(op.get("dpi", 300)) == 150 else 300
        except (tk.TclError, ValueError):
            op["dpi"] = 300
        if op.get("perfil_cor") not in ("sRGB", "AdobeRGB"):
            op["perfil_cor"] = "sRGB"
        op["marcas_registro"] = bool(op.get("marcas_registro"))
        op["pdf_unico"] = bool(op.get("pdf_unico", True))
        op["pasta"] = op["pasta"].strip()
        return op

    def _agendar(self):
        if self._agendado:
            self.after_cancel(self._agendado)
        self._agendado = self.after(150, self.atualizar)

    def atualizar(self):
        self._agendado = None
        try:
            op = self.opcoes()
        except tk.TclError:
            return
        projeto, produto = self.app.projeto, self.app.produto
        total = len(projeto["laminas"])
        atual = self.app.atual if isinstance(self.app.atual, int) else None
        self.radio_todas.configure(text="Todas (%s)" % total)
        self.radio_atual.configure(text="Só a aberta (lâmina %s)" % (atual + 1) if atual is not None else "Só a aberta")

        perfil_escolhido = op.get("perfil_cor", "sRGB")
        self.caixa_icc.configure(
            text="Incluir perfil ICC (%s)" % ("Adobe RGB (1998)" if perfil_escolhido == "AdobeRGB" else "sRGB IEC61966-2.1")
        )
        self.caixa_subpasta.configure(text="Criar subpasta com o nome do álbum (%s)" % (projeto.get("nome") or "album"))
        self.combo_existentes.set(dict(EXISTENTES).get(op["existentes"], EXISTENTES[0][1]))

        if op["formato"] in ("pdf", "jpg_pdf"):
            self.caixa_pdf_unico.state(["!disabled"])
        else:
            self.caixa_pdf_unico.state(["disabled"])

        if produto:
            w_mm, h_mm = lamina_mm(produto)
            if op["marcas_registro"]:
                w_mm += 2 * exportar.MARGEM_REGISTRO_MM
                h_mm += 2 * exportar.MARGEM_REGISTRO_MM
            w_px, h_px = exportar.tamanho_final_px(produto, op["dpi"], op["marcas_registro"])
            self.rotulo_especificacoes.configure(
                text="Saída: %dx%d mm (%dx%d px @ %d DPI · %s%s)"
                % (
                    int(round(w_mm)),
                    int(round(h_mm)),
                    w_px,
                    h_px,
                    op["dpi"],
                    perfil_escolhido,
                    " · Corte Industrial" if op["marcas_registro"] else "",
                )
            )

        problemas = []
        try:
            indices = indices_escolhidos(op, projeto, atual)
            self.rotulo_laminas.configure(text="%s lâmina(s) para exportar" % len(indices), foreground=tema.TEXTO_FRACO)
        except ValueError as e:
            indices = []
            self.rotulo_laminas.configure(text=str(e), foreground=tema.ERRO)
            problemas.append(str(e))
        try:
            itens = exportar.itens_exportacao(projeto, indices, op["padrao"], produto)
            nomes = arquivos_previstos(op, projeto, produto, itens)
            exemplo = ", ".join(nomes[:3]) + (" … " + nomes[-1] if len(nomes) > 3 else "")
            self.rotulo_exemplo.configure(text="Exemplo: " + (exemplo or "(nenhuma lâmina)"), foreground=tema.TEXTO)
        except ValueError as e:
            itens, nomes = [], []
            self.rotulo_exemplo.configure(text=str(e), foreground=tema.ERRO)
            problemas.append(str(e))
        if op["pasta"].strip():
            destino = pasta_final(op, projeto)
            livre = espaco_livre(destino)
            existentes = sum(1 for arq in nomes if (destino / arq).exists())
            texto = "Vai para: %s" % destino
            if livre is not None:
                texto += "   ·   livre no disco: %.1f GB" % (livre / 1024.0 ** 3)
            self.rotulo_destino.configure(text=texto, foreground=tema.TEXTO_FRACO)
            self.rotulo_existentes.configure(text="%s já existe(m)" % existentes if existentes else "")
        else:
            self.rotulo_destino.configure(text="Escolha a pasta de saída.", foreground=tema.ERRO)
            self.rotulo_existentes.configure(text="")
            problemas.append("Escolha a pasta de saída.")
        avisos = conferir(projeto, indices) if indices else []
        if not total:
            problemas.append("O álbum não tem lâminas.")
        linhas = ["✕ " + p for p in problemas] + ["! " + a for a in avisos]
        self.rotulo_conferencia.configure(text="\n".join(linhas) if linhas else "✓ Tudo certo para exportar.",
                                          foreground=tema.ERRO if problemas else (tema.ALERTA if avisos else tema.VERDE))
        self.problemas = problemas
        if not self.rodando:
            self.botao_exportar.configure(state="disabled" if problemas else "normal")

    def _escolheu_existentes(self, _evento=None):
        texto = self.combo_existentes.get()
        for valor, rotulo in EXISTENTES:
            if rotulo == texto:
                self.vars["existentes"].set(valor)

    def escolher_pasta(self):
        pasta = filedialog.askdirectory(parent=self, title="Pasta de saída",
                                        initialdir=self.vars["pasta"].get() or self.app.projeto.get("pasta_fotos") or None)
        if pasta:
            self.vars["pasta"].set(str(Path(pasta)))

    def _montar_sugestoes(self):
        menu = self.menu_sugestoes
        menu.delete(0, "end")
        fotos = self.app.projeto.get("pasta_fotos")
        if fotos:
            padrao = str(Path(fotos) / "saida")
            menu.add_command(label="saida ao lado das fotos: %s" % padrao, command=lambda: self.vars["pasta"].set(padrao))
        recentes = ler_recentes(self.app.arquivo_exportacao())
        if recentes:
            menu.add_separator()
            for pasta in recentes:
                menu.add_command(label=pasta, command=lambda p=pasta: self.vars["pasta"].set(p))
        if not fotos and not recentes:
            menu.add_command(label="(sem sugestões)", state="disabled")

    # ---------------- exportação ----------------

    def exportar(self):
        self.atualizar()
        if self.problemas:
            return
        self._limpar_registro()
        self.gravados = []
        if self.app.executar_exportacao(self.opcoes(), janela=self):
            self.rodando = True
            self.botao_exportar.configure(state="disabled")
            self.botao_parar.configure(state="normal")
            self.barra.configure(value=0, maximum=1)

    def aviso(self, texto):
        self.registro.configure(state="normal")
        self.registro.insert("end", texto + "\n")
        self.registro.see("end")
        self.registro.configure(state="disabled")

    def progresso(self, n, total):
        self.barra.configure(maximum=max(1, total), value=n)

    def terminou(self, gravados, erro):
        self.rodando = False
        self.botao_parar.configure(state="disabled")
        self.gravados = list(gravados or [])
        if erro:
            self.aviso("ERRO: %s" % erro)
        self.aviso("Pronto: %s arquivo(s) gravado(s)." % len(self.gravados))
        self.atualizar()

    def _limpar_registro(self):
        self.registro.configure(state="normal")
        self.registro.delete("1.0", "end")
        self.registro.configure(state="disabled")

    def abrir_pasta(self):
        op = self.opcoes()
        if not op["pasta"]:
            return
        destino = pasta_final(op, self.app.projeto)
        if destino.is_dir():
            os.startfile(str(destino))
        else:
            self.aviso("A pasta ainda não existe: %s" % destino)

    def fechar(self):
        if not self.rodando:
            self.app.guardar_opcoes_exportacao(self.opcoes())
        self.app.janela_exportar = None
        self.destroy()
