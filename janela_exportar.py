# -*- coding: utf-8 -*-
"""
Janela Exportar (estilo Adobe Photoshop CC):
- Escolha múltipla apenas entre JPG, PSD e PDF (caixas de seleção independentes)
- Ao salvar, cria a pasta principal chamada 'Album <TAMANHO>' (ex.: 'Album 30X20')
  e dentro dela cria apenas as subpastas dos formatos marcados ('JPG', 'PSD', 'PDF')
- Resolução DPI (150/300), perfil de cor (sRGB/AdobeRGB), marcas de registro para corte industrial,
  quais lâminas exportar, conferência prévia e barra de progresso em tempo real.
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
SUBPASTAS_FORMATO = {
    "jpg": "JPG",
    "psd": "PSD",
    "pdf": "PDF",
}
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


def _limpar_pasta_saida_legada(caminho_str, projeto=None):
    """Remove subpasta 'saida' automática de projetos antigos e usa a pasta das fotos diretamente."""
    texto = (caminho_str or "").strip()
    if texto:
        p = Path(texto)
        if p.name.lower() in ("saida", "saída") and not p.exists():
            p = p.parent
        return str(p)
    pasta_fotos = (projeto or {}).get("pasta_fotos") or ""
    if pasta_fotos:
        return str(Path(pasta_fotos))
    return str(Path.home())


def pasta_final(opcoes, projeto=None, produto=None):
    """
    Pasta raiz do álbum exportado: '<pasta_base>/Album <TAMANHO>' (ex.: 'Album 30X20').
    Se a pasta escolhida já terminar com 'Album <TAMANHO>', não duplica.
    """
    base = Path(_limpar_pasta_saida_legada((opcoes or {}).get("pasta"), projeto))
    nome_album = exportar.nome_pasta_album(produto)
    if base.name.casefold() == nome_album.casefold():
        return base
    return base / nome_album


def pasta_do_formato(pasta_album, formato):
    """Retorna a subpasta ('JPG', 'PSD' ou 'PDF') dentro da pasta 'Album <TAMANHO>'."""
    sub = SUBPASTAS_FORMATO.get(str(formato).lower(), str(formato).upper())
    return Path(pasta_album) / sub


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
    """
    Lista de caminhos relativos (ex.: 'JPG/01 - lamina.jpg', 'PSD/01 - lamina.psd', 'PDF/album.pdf')
    que serão gravados dentro da pasta 'Album <TAMANHO>', apenas para os formatos marcados.
    """
    ativos = exportar.formatos_ativos(opcoes)
    pdf_unico = bool(opcoes.get("pdf_unico", True))
    saida = []
    if "jpg" in ativos:
        saida.extend("JPG/%s.jpg" % nome for nome, _i in itens)
    if "psd" in ativos:
        saida.extend("PSD/%s.psd" % nome for nome, _i in itens)
    if "pdf" in ativos:
        if pdf_unico:
            saida.append("PDF/%s" % exportar.nome_pdf_unico(projeto, produto))
        else:
            saida.extend("PDF/%s.pdf" % nome for nome, _i in itens)
    saida.append(exportar.nome_json_projeto(projeto, produto))
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
        self.title("Exportar Álbum · Photoshop CC (JPG · PSD · PDF)")
        self.configure(bg=tema.PAINEL)
        self.transient(app.root)
        self.minsize(680, 520)
        self.protocol("WM_DELETE_WINDOW", self.fechar)
        self.bind("<Escape>", lambda e: self.fechar())
        self.gravados = []
        self.rodando = False
        self.problemas = []
        self._agendado = None

        op = app.opcoes_exportacao()
        ativos = exportar.formatos_ativos(op)
        if not ativos:
            ativos = ["jpg"]

        self.vars = {
            "exportar_jpg": tk.BooleanVar(value=("jpg" in ativos)),
            "exportar_psd": tk.BooleanVar(value=("psd" in ativos)),
            "exportar_pdf": tk.BooleanVar(value=("pdf" in ativos)),
            "qualidade": tk.IntVar(value=int(op.get("qualidade", exportar.QUALIDADE_JPG))),
            "icc": tk.BooleanVar(value=bool(op.get("icc", True))),
            "dpi": tk.IntVar(value=int(op.get("dpi", 300)) if int(op.get("dpi", 300)) in (150, 300) else 300),
            "perfil_cor": tk.StringVar(
                value=op.get("perfil_cor", "sRGB") if op.get("perfil_cor") in ("sRGB", "AdobeRGB") else "sRGB"
            ),
            "marcas_registro": tk.BooleanVar(value=bool(op.get("marcas_registro", False))),
            "pdf_unico": tk.BooleanVar(value=bool(op.get("pdf_unico", True))),
            "pasta": tk.StringVar(value=_limpar_pasta_saida_legada(op.get("pasta"), app.projeto)),
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
        y = max(20, app.root.winfo_rooty() + 24)
        self.geometry("+%d+%d" % (x, y))

    def _montar(self):
        tema.criar_cabecalho_janela(
            self,
            "Exportação de Produção Industrial (JPG · PSD · PDF)",
            "Cria automaticamente a pasta 'Album <TAMANHO>' com subpastas apenas para os formatos marcados",
            icone="⇪",
        )

        quadro = ttk.Frame(self, padding=12)
        quadro.pack(fill="both", expand=True)
        quadro.columnconfigure(0, weight=1)
        quadro.columnconfigure(1, weight=1)

        # --- Coluna 1: Formatos (Escolha Múltipla: JPG, PSD, PDF) e Impressão ---
        formato = ttk.LabelFrame(quadro, text="Formatos de Saída (Escolha Múltipla)", padding=10)
        formato.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=(0, 8))

        ttk.Label(
            formato,
            text="Marque um ou mais formatos (cria subpasta por formato marcado):",
            style="Fraco.TLabel",
        ).pack(anchor="w", pady=(0, 6))

        grade_fmt = ttk.Frame(formato)
        grade_fmt.pack(fill="x", pady=(0, 6))
        for col_i, (ch_var, rot_curto, dica_fmt) in enumerate((
            ("exportar_jpg", "☑  JPG (Laboratório)", "Grava as lâminas em alta resolução na subpasta JPG/"),
            ("exportar_psd", "☑  PSD (Camadas)", "Grava arquivos do Photoshop com camadas editáveis na subpasta PSD/"),
            ("exportar_pdf", "☑  PDF (Industrial)", "Grava arquivo(s) PDF para impressão/corte na subpasta PDF/"),
        )):
            cb = ttk.Checkbutton(
                grade_fmt,
                text=rot_curto,
                variable=self.vars[ch_var],
                style="Alternar.Toolbutton",
            )
            cb.grid(row=0, column=col_i, sticky="ew", padx=(0 if col_i == 0 else 4, 0))
            grade_fmt.columnconfigure(col_i, weight=1)
            tema.dica(cb, dica_fmt)

        # Resolução DPI (150 / 300)
        linha_dpi = ttk.Frame(formato)
        linha_dpi.pack(fill="x", pady=(4, 0))
        ttk.Label(linha_dpi, text="Resolução (DPI):").pack(side="left")
        ttk.Radiobutton(linha_dpi, text="300 DPI", value=300, variable=self.vars["dpi"]).pack(side="right", padx=(6, 0))
        ttk.Radiobutton(linha_dpi, text="150 DPI", value=150, variable=self.vars["dpi"]).pack(side="right")

        # Perfil de Cor (sRGB / AdobeRGB)
        linha_cor = ttk.Frame(formato)
        linha_cor.pack(fill="x", pady=(4, 0))
        ttk.Label(linha_cor, text="Perfil de Cor:").pack(side="left")
        ttk.Radiobutton(linha_cor, text="AdobeRGB", value="AdobeRGB", variable=self.vars["perfil_cor"]).pack(
            side="right", padx=(6, 0)
        )
        ttk.Radiobutton(linha_cor, text="sRGB", value="sRGB", variable=self.vars["perfil_cor"]).pack(side="right")

        # Qualidade do JPG
        linha_qual = ttk.Frame(formato)
        linha_qual.pack(fill="x", pady=(4, 0))
        ttk.Label(linha_qual, text="Qualidade do JPG:").pack(side="left")
        ttk.Spinbox(linha_qual, from_=60, to=100, increment=1, width=5, textvariable=self.vars["qualidade"]).pack(
            side="right"
        )

        # Opções de ICC, Marca de Registro Industrial e PDF Único
        self.caixa_icc = ttk.Checkbutton(formato, text="Embutir perfil ICC no arquivo", variable=self.vars["icc"])
        self.caixa_icc.pack(anchor="w", pady=(6, 0))

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

        self.rotulo_especificacoes = ttk.Label(formato, text="", style="Fraco.TLabel", wraplength=320, justify="left")
        self.rotulo_especificacoes.pack(anchor="w", pady=(4, 0))

        # --- Coluna 2: Quais lâminas e Nomes dos arquivos ---
        col_dir = ttk.Frame(quadro)
        col_dir.grid(row=0, column=1, sticky="nsew", pady=(0, 8))

        laminas = ttk.LabelFrame(col_dir, text="Quais Lâminas", padding=10)
        laminas.pack(fill="x", pady=(0, 8))
        self.radio_todas = ttk.Radiobutton(laminas, text="", value="todas", variable=self.vars["laminas"])
        self.radio_todas.pack(anchor="w")
        self.radio_atual = ttk.Radiobutton(laminas, text="", value="atual", variable=self.vars["laminas"])
        self.radio_atual.pack(anchor="w")
        linha = ttk.Frame(laminas)
        linha.pack(fill="x", pady=(2, 0))
        ttk.Radiobutton(linha, text="Intervalo:", value="intervalo", variable=self.vars["laminas"]).pack(side="left")
        campo = ttk.Entry(linha, textvariable=self.vars["intervalo"], width=14)
        campo.pack(side="left", fill="x", expand=True, padx=(4, 0))
        campo.bind("<FocusIn>", lambda e: self.vars["laminas"].set("intervalo"))
        tema.dica(campo, "Exemplo: 1-5, 8, 10-12")
        self.rotulo_laminas = ttk.Label(laminas, text="", style="Fraco.TLabel")
        self.rotulo_laminas.pack(anchor="w", pady=(4, 0))

        nomes = ttk.LabelFrame(col_dir, text="Padrão de Nomes e Arquivos Existentes", padding=10)
        nomes.pack(fill="both", expand=True)
        linha = ttk.Frame(nomes)
        linha.pack(fill="x")
        ttk.Label(linha, text="Padrão:").pack(side="left")
        ttk.Entry(linha, textvariable=self.vars["padrao"], width=20).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(
            linha,
            text="Padrão",
            style="Compacto.TButton",
            command=lambda: self.vars["padrao"].set(exportar.PADRAO_NOME),
        ).pack(side="left")
        ttk.Label(
            nomes,
            text="Variáveis: {n}, {n:02d}, {album}, {produto}, {total}.",
            style="Fraco.TLabel",
        ).pack(anchor="w", pady=(4, 0))
        self.rotulo_exemplo = ttk.Label(nomes, text="", wraplength=300, justify="left", font=tema.FONTE_PEQUENA)
        self.rotulo_exemplo.pack(anchor="w", pady=(2, 0))

        linha_ex = ttk.Frame(nomes)
        linha_ex.pack(fill="x", pady=(6, 0))
        ttk.Label(linha_ex, text="Se existir:").pack(side="left")
        self.combo_existentes = ttk.Combobox(linha_ex, state="readonly", width=24, values=[t for _v, t in EXISTENTES])
        self.combo_existentes.pack(side="left", fill="x", expand=True, padx=(6, 0))
        self.combo_existentes.bind("<<ComboboxSelected>>", self._escolheu_existentes)
        self.rotulo_existentes = ttk.Label(nomes, text="", style="Fraco.TLabel")
        self.rotulo_existentes.pack(anchor="w", pady=(2, 0))

        # --- Linha 1: Onde salvar + Estrutura automática Album <TAMANHO> / [JPG, PSD, PDF] ---
        destino = ttk.LabelFrame(quadro, text="Pasta de Saída e Estrutura Automática por Tamanho", padding=10)
        destino.grid(row=1, column=0, columnspan=2, sticky="nsew", pady=(0, 8))
        linha = ttk.Frame(destino)
        linha.pack(fill="x")
        ttk.Entry(linha, textvariable=self.vars["pasta"]).pack(side="left", fill="x", expand=True)
        ttk.Button(linha, text="Escolher pasta...", style="Compacto.TButton", command=self.escolher_pasta).pack(
            side="left", padx=(6, 0)
        )
        self.botao_sugestoes = ttk.Menubutton(linha, text="Recentes", style="Compacto.TMenubutton")
        self.menu_sugestoes = tk.Menu(self.botao_sugestoes, tearoff=0, postcommand=self._montar_sugestoes)
        self.botao_sugestoes["menu"] = self.menu_sugestoes
        self.botao_sugestoes.pack(side="left", padx=(4, 0))

        self.rotulo_estrutura = ttk.Label(destino, text="", foreground=tema.DESTAQUE, font=tema.FONTE_FORTE)
        self.rotulo_estrutura.pack(anchor="w", pady=(6, 2))
        self.rotulo_destino = ttk.Label(destino, text="", style="Fraco.TLabel", wraplength=640, justify="left")
        self.rotulo_destino.pack(anchor="w")

        # --- Linha 2: Conferência antes de exportar ---
        conferencia = ttk.LabelFrame(quadro, text="Diagnóstico Pré-Exportação", padding=8)
        conferencia.grid(row=2, column=0, columnspan=2, sticky="nsew", pady=(0, 8))
        self.rotulo_conferencia = ttk.Label(conferencia, text="", wraplength=640, justify="left")
        self.rotulo_conferencia.pack(anchor="w")

        # --- Linha 3: Barra de Progresso e Console ---
        andamento = ttk.Frame(quadro)
        andamento.grid(row=3, column=0, columnspan=2, sticky="nsew")
        quadro.rowconfigure(3, weight=1)
        self.barra = ttk.Progressbar(andamento, mode="determinate")
        self.barra.pack(fill="x")
        self.registro = tk.Text(
            andamento,
            height=5,
            bg=tema.FUNDO,
            fg=tema.TEXTO,
            relief="solid",
            bd=1,
            insertbackground=tema.TEXTO,
            font=tema.FONTE_MONO,
            state="disabled",
        )
        self.registro.pack(fill="both", expand=True, pady=(4, 0))

        # --- Linha 4: Rodapé de botões ---
        botoes = ttk.Frame(quadro)
        botoes.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        self.botao_exportar = ttk.Button(
            botoes, text="⇪ Exportar Álbum", style="Primario.TButton", command=self.exportar
        )
        self.botao_exportar.pack(side="left")
        self.botao_parar = ttk.Button(
            botoes, text="Parar", style="Compacto.TButton", command=self.app.parar_tarefa, state="disabled"
        )
        self.botao_parar.pack(side="left", padx=6)
        ttk.Button(botoes, text="Fechar", style="Compacto.TButton", command=self.fechar).pack(side="right")
        self.botao_abrir = ttk.Button(
            botoes, text="📂 Abrir Pasta do Álbum", style="Compacto.TButton", command=self.abrir_pasta
        )
        self.botao_abrir.pack(side="right", padx=6)

    # ---------------- leitura das escolhas ----------------

    def opcoes(self):
        op = {chave: var.get() for chave, var in self.vars.items()}
        op["exportar_jpg"] = bool(op.get("exportar_jpg"))
        op["exportar_psd"] = bool(op.get("exportar_psd"))
        op["exportar_pdf"] = bool(op.get("exportar_pdf"))
        ativos = exportar.formatos_ativos(op)
        # Mantém chave 'formato' sincronizada para compatibilidade
        if ativos == ["jpg", "psd"]:
            op["formato"] = "ambos"
        elif ativos == ["jpg", "pdf"]:
            op["formato"] = "jpg_pdf"
        elif len(ativos) == 1:
            op["formato"] = ativos[0]
        elif len(ativos) > 1:
            op["formato"] = "todos"
        else:
            op["formato"] = "nenhum"
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
        op["pasta"] = _limpar_pasta_saida_legada(op.get("pasta"), self.app.projeto)
        op["subpasta"] = True
        return op

    def _agendar(self):
        if self._agendado:
            self.after_cancel(self._agendado)
        self._agendado = self.after(120, self.atualizar)

    def atualizar(self):
        self._agendado = None
        try:
            op = self.opcoes()
        except tk.TclError:
            return
        projeto, produto = self.app.projeto, self.app.produto
        total = len(projeto["laminas"])
        atual = self.app.atual if isinstance(self.app.atual, int) else None
        self.radio_todas.configure(text="Todas as lâminas (%s)" % total)
        self.radio_atual.configure(
            text="Somente a aberta (lâmina %s)" % (atual + 1) if atual is not None else "Somente a aberta"
        )

        perfil_escolhido = op.get("perfil_cor", "sRGB")
        self.caixa_icc.configure(
            text="Incluir perfil ICC (%s)"
            % ("Adobe RGB (1998)" if perfil_escolhido == "AdobeRGB" else "sRGB IEC61966-2.1")
        )
        self.combo_existentes.set(dict(EXISTENTES).get(op["existentes"], EXISTENTES[0][1]))

        ativos = exportar.formatos_ativos(op)
        if "pdf" in ativos:
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

        nome_pasta_alb = exportar.nome_pasta_album(produto)
        partes_est = ["📂 %s/" % SUBPASTAS_FORMATO[f] for f in ativos]
        if ativos:
            partes_est.append("📄 %s" % exportar.nome_json_projeto(projeto, produto))
        subpastas_txt = "  +  ".join(partes_est) if partes_est else "(nenhum formato marcado)"
        self.rotulo_estrutura.configure(
            text="📁 %s   →   %s" % (nome_pasta_alb, subpastas_txt)
        )

        problemas = []
        if not ativos:
            problemas.append("Marque pelo menos um formato de saída (JPG, PSD ou PDF).")

        try:
            indices = indices_escolhidos(op, projeto, atual)
            self.rotulo_laminas.configure(
                text="%s lâmina(s) selecionada(s) para exportar" % len(indices), foreground=tema.TEXTO_FRACO
            )
        except ValueError as e:
            indices = []
            self.rotulo_laminas.configure(text=str(e), foreground=tema.ERRO)
            problemas.append(str(e))
        try:
            itens = exportar.itens_exportacao(projeto, indices, op["padrao"], produto)
            nomes = arquivos_previstos(op, projeto, produto, itens)
            exemplo = ", ".join(nomes[:3]) + (" … " + nomes[-1] if len(nomes) > 3 else "")
            self.rotulo_exemplo.configure(text="Saída: " + (exemplo or "(nenhum arquivo)"), foreground=tema.TEXTO)
        except ValueError as e:
            itens, nomes = [], []
            self.rotulo_exemplo.configure(text=str(e), foreground=tema.ERRO)
            problemas.append(str(e))

        if op["pasta"].strip():
            destino = pasta_final(op, projeto, produto)
            livre = espaco_livre(destino)
            existentes = sum(1 for arq in nomes if (destino / arq).exists())
            texto = "Caminho completo: %s" % destino
            if livre is not None:
                texto += "   ·   Livre no disco: %.1f GB" % (livre / 1024.0 ** 3)
            self.rotulo_destino.configure(text=texto, foreground=tema.TEXTO_FRACO)
            self.rotulo_existentes.configure(
                text="%s arquivo(s) já existe(m) nas subpastas selecionadas" % existentes if existentes else ""
            )
        else:
            self.rotulo_destino.configure(text="Escolha a pasta base onde a pasta '%s' será criada." % nome_pasta_alb, foreground=tema.ERRO)
            self.rotulo_existentes.configure(text="")
            problemas.append("Escolha a pasta de saída.")

        avisos = conferir(projeto, indices) if indices else []
        if not total:
            problemas.append("O álbum não tem lâminas.")
        linhas = ["✕ " + p for p in problemas] + ["! " + a for a in avisos]
        self.rotulo_conferencia.configure(
            text="\n".join(linhas) if linhas else "✓ Pronto para gravar em '%s' (%s)." % (nome_pasta_alb, ", ".join(f.upper() for f in ativos)),
            foreground=tema.ERRO if problemas else (tema.ALERTA if avisos else tema.VERDE),
        )
        self.problemas = problemas
        if not self.rodando:
            self.botao_exportar.configure(state="disabled" if problemas else "normal")

    def _escolheu_existentes(self, _evento=None):
        texto = self.combo_existentes.get()
        for valor, rotulo in EXISTENTES:
            if rotulo == texto:
                self.vars["existentes"].set(valor)

    def escolher_pasta(self):
        pasta = filedialog.askdirectory(
            parent=self,
            title="Pasta onde será criada a pasta do álbum",
            initialdir=self.vars["pasta"].get() or self.app.projeto.get("pasta_fotos") or None,
        )
        if pasta:
            self.vars["pasta"].set(str(Path(pasta)))

    def _montar_sugestoes(self):
        menu = self.menu_sugestoes
        menu.delete(0, "end")
        fotos = self.app.projeto.get("pasta_fotos")
        if fotos:
            padrao = str(Path(fotos))
            menu.add_command(
                label="Mesma pasta das fotos: %s" % padrao, command=lambda: self.vars["pasta"].set(padrao)
            )
        recentes = ler_recentes(self.app.arquivo_exportacao())
        if recentes:
            menu.add_separator()
            for pasta in recentes:
                menu.add_command(label=pasta, command=lambda p=pasta: self.vars["pasta"].set(p))
        if not fotos and not recentes:
            menu.add_command(label="(sem pastas recentes)", state="disabled")

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
        self.aviso("Concluído: %s arquivo(s) gravado(s)." % len(self.gravados))
        self.atualizar()

    def _limpar_registro(self):
        self.registro.configure(state="normal")
        self.registro.delete("1.0", "end")
        self.registro.configure(state="disabled")

    def abrir_pasta(self):
        op = self.opcoes()
        if not op["pasta"]:
            return
        destino = pasta_final(op, self.app.projeto, self.app.produto)
        if destino.is_dir():
            os.startfile(str(destino))
        elif Path(op["pasta"]).is_dir():
            os.startfile(str(Path(op["pasta"])))
        else:
            self.aviso("A pasta ainda não existe: %s" % destino)

    def fechar(self):
        if not self.rodando:
            self.app.guardar_opcoes_exportacao(self.opcoes())
        self.app.janela_exportar = None
        self.destroy()
