# -*- coding: utf-8 -*-
"""
Janela Tratamento de fotos: quais fotos, Photoshop (editar ou rodar Ação),
Lightroom (preset pelo plugin), aviso de que as originais são substituídas,
andamento, registro e resumo.
"""

import os
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from PIL import ImageTk

import analise
import lightroom
import photoshop
import recorte
import tema
from comum import normalizar_caminho

ALTURA_MINIATURA = 46
MINIATURAS_MAXIMO = 40

SLIDERS_LOTE = (
    ("exposicao", "Exposição (Luz)", -100, 100, "Clareia ou escurece a exposição geral em lote"),
    ("brilho", "Brilho", -100, 100, "Ajusta os meios-tons de todas as fotos do lote"),
    ("contraste", "Contraste", -100, 100, "Aumenta ou suaviza o contraste geral"),
    ("sombras", "Sombras", -100, 100, "Recupera detalhes nas partes escuras das fotos"),
    ("realces", "Realces", -100, 100, "Protege ou recupera brilhos intensos e roupas claras"),
    ("temperatura", "Temperatura (Cor)", -100, 100, "Corrige fotos frias/azuladas (-) ou quentes/amareladas (+)"),
    ("matiz", "Matiz (Verde/Magenta)", -100, 100, "Ajuste fino de tinta de pele e iluminação ambiente"),
    ("vibracao", "Vibração", -100, 100, "Realça cores apagadas protegendo tons de pele"),
    ("saturacao", "Saturação", -100, 100, "Intensidade global de todas as cores"),
    ("nitidez", "Nitidez", 0, 100, "Realça detalhes e foco para impressão"),
)


class JanelaTratamento(tk.Toplevel):
    def __init__(self, app, fotos=None):
        super().__init__(app.root)
        self.app = app
        self.title("Edição em Lote Inteligente (Luz, Cor e Saturação) · Photoshop & Lightroom")
        self.configure(bg=tema.PAINEL)
        self.transient(app.root)
        self.minsize(560, 420)
        self.protocol("WM_DELETE_WINDOW", self.fechar)
        self.bind("<Escape>", lambda e: self.fechar())
        self.fotos_escolhidas = [f for f in (fotos or []) if f]
        if self.fotos_escolhidas:
            escopo_ini = "escolhidas"
        elif app._fotos_escolhidas():
            escopo_ini = "selecionadas"
        elif app._fotos_do_album():
            escopo_ini = "album"
        else:
            escopo_ini = "projeto"
        self.escopo = tk.StringVar(value=escopo_ini)
        self.nivel_auto_lote = tk.StringVar(value="medio")
        self.usar_ia_lote = tk.BooleanVar(value=False)
        self.pb_lote = tk.BooleanVar(value=False)
        self.vars_lote = {}
        self.rotulos_lote = {}
        for chave, *_ in SLIDERS_LOTE:
            self.vars_lote[chave] = tk.DoubleVar(value=0.0)
        ps = app.projeto.get("photoshop") or {}
        lr = app.projeto.get("lightroom") or {}
        self.conjunto = tk.StringVar(value=ps.get("conjunto", ""))
        self.acao = tk.StringVar(value=ps.get("acao", ""))
        self.preset = tk.StringVar(value=lr.get("preset_nome", ""))
        self.entendi = tk.BooleanVar(value=False)
        self.acoes = {}
        self.rodando = False
        self._tk_miniaturas = []
        self._montar()
        self.escopo.trace_add("write", lambda *a: self.atualizar_fotos())
        for var in (self.conjunto, self.acao, self.preset):
            var.trace_add("write", lambda *a: self._guardar())
        self.conjunto.trace_add("write", lambda *a: self._listar_acoes_do_conjunto())
        self.atualizar_fotos()
        self.atualizar_lightroom()
        tema.configurar_janela_secundaria(
            self,
            pai=app.root,
            chave="janela_tratamento",
            largura=780,
            altura=680,
            min_largura=560,
            min_altura=420,
        )

    # ---------------- montagem ----------------

    def _montar(self):
        tema.criar_cabecalho_janela(
            self,
            "Edição em Lote Inteligente (1 Clique) + Photoshop & Lightroom",
            "Melhore fotos escuras, lavadas ou sem cor em 1 clique (Leve, Médio e Pesado) com filtros de Luz, Cor e Saturação",
            icone="✨",
        )

        rodape_fixo = tk.Frame(self, bg=tema.CABECALHO, highlightthickness=1, highlightbackground=tema.BORDA, padx=12, pady=8)
        rodape_fixo.pack(side="bottom", fill="x")
        self._rodape_fixo = rodape_fixo

        quadro, _container_rol, _canvas_rol = tema.criar_corpo_rolavel(self, padding=12)
        quadro.columnconfigure(0, weight=1, uniform="col_trat")
        quadro.columnconfigure(1, weight=1, uniform="col_trat")

        quais = ttk.LabelFrame(quadro, text="1. Escopo das Fotos para Edição em Lote", padding=8)
        quais.grid(row=0, column=0, columnspan=2, sticky="nsew", pady=(0, 8))
        linha = ttk.Frame(quais)
        linha.pack(fill="x")
        self.radios = {}
        opcoes = (("escolhidas", ""), ("selecionadas", ""), ("lamina", ""), ("album", ""), ("projeto", ""))
        for valor, _texto in opcoes:
            if valor == "escolhidas" and not self.fotos_escolhidas:
                continue
            radio = ttk.Radiobutton(linha, text="", value=valor, variable=self.escopo)
            radio.pack(side="left", padx=(0, 10))
            self.radios[valor] = radio
        self.rotulo_fotos = ttk.Label(quais, text="", style="Fraco.TLabel")
        self.rotulo_fotos.pack(anchor="w", pady=(4, 4))
        self.faixa = tk.Canvas(
            quais, height=ALTURA_MINIATURA + 8, bg=tema.FUNDO,
            highlightthickness=1, highlightbackground=tema.BORDA
        )
        self.faixa.pack(fill="x")

        # ---------------- SEÇÃO NOVA: EDIÇÃO EM LOTE INTELIGENTE (LUZ, COR E SATURAÇÃO) ----------------
        lote_box = ttk.LabelFrame(
            quadro,
            text="2. Melhoria Rápida em 1 Clique (Leve, Médio e Pesado) + Filtros de Luz, Cor e Saturação",
            padding=10,
        )
        lote_box.grid(row=1, column=0, columnspan=2, sticky="nsew", pady=(0, 8))

        lbl_explica = ttk.Label(
            lote_box,
            text="O modo Inteligente analisa cada foto individualmente (recupera fotos escuras, estouradas, lavadas ou com cor fria/amarelada) na intensidade escolhida:",
            style="Fraco.TLabel",
            wraplength=700,
            justify="left",
        )
        lbl_explica.pack(anchor="w", pady=(0, 6))
        self._lbl_explica_lote = lbl_explica

        # Botões de ação imediata em 1 clique
        barra_1clique = ttk.Frame(lote_box)
        barra_1clique.pack(fill="x", pady=(0, 8))
        for col_idx in range(4):
            barra_1clique.columnconfigure(col_idx, weight=1, uniform="btn1c")

        btn_1c_leve = ttk.Button(
            barra_1clique,
            text="✨ 1-Clique Leve",
            style="Compacto.TButton",
            command=lambda: self.aplicar_1clique_rapido("leve"),
        )
        btn_1c_leve.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        tema.dica(btn_1c_leve, "Correção sutil (~45%): melhora luz, cor e nitidez mantendo o aspecto natural da foto.")

        btn_1c_medio = ttk.Button(
            barra_1clique,
            text="✨ 1-Clique Médio (Ideal)",
            style="PrimarioCompacto.TButton",
            command=lambda: self.aplicar_1clique_rapido("medio"),
        )
        btn_1c_medio.grid(row=0, column=1, sticky="ew", padx=2)
        tema.dica(btn_1c_medio, "Equilíbrio ideal (~80%): corrige fotos escuras, sem contraste ou desbotadas em 1 clique.")

        btn_1c_pesado = ttk.Button(
            barra_1clique,
            text="🔥 1-Clique Pesado",
            style="Compacto.TButton",
            command=lambda: self.aplicar_1clique_rapido("pesado"),
        )
        btn_1c_pesado.grid(row=0, column=2, sticky="ew", padx=2)
        tema.dica(btn_1c_pesado, "Recuperação intensa (100%+): resgata fotos ruins, muito escuras, contraluz forte ou cores apagadas.")

        btn_1c_zerar = ttk.Button(
            barra_1clique,
            text="↺ Restaurar Original",
            style="Compacto.TButton",
            command=self.restaurar_original_lote,
        )
        btn_1c_zerar.grid(row=0, column=3, sticky="ew", padx=(4, 0))
        tema.dica(btn_1c_zerar, "Remove todos os filtros de luz, cor e saturação das fotos do escopo selecionado (suporta Ctrl+Z).")

        linha_ia = ttk.Frame(lote_box)
        linha_ia.pack(fill="x", pady=(0, 8))
        btn_auto_ia = ttk.Button(
            linha_ia,
            text="🤖 Auto IA no escopo",
            style="PrimarioCompacto.TButton",
            command=lambda: self._executar_lote_no_projeto(nivel="medio", usar_ia=True),
        )
        btn_auto_ia.pack(side="left")
        tema.dica(btn_auto_ia, "Edição automática por IA (3D LUT adaptativa + alvo por cena e pelos rostos), "
                               "calculada neste PC em segundo plano. Suporta Ctrl+Z.")
        caixa_ia = ttk.Checkbutton(
            linha_ia,
            text="🤖 Usar IA nos botões Leve / Médio / Pesado e no 'Modo base'",
            variable=self.usar_ia_lote,
        )
        caixa_ia.pack(side="left", padx=(10, 0))
        tema.dica(caixa_ia, "Ligado: Leve/Médio/Pesado usam o estilo da IA a 40/70/100% + correções por regras.")

        # Seletor do modo base + Sliders de ajuste fino em lote
        modo_linha = ttk.Frame(lote_box)
        modo_linha.pack(fill="x", pady=(2, 6))
        ttk.Label(modo_linha, text="Modo base ao aplicar:", style="Fraco.TLabel").pack(side="left", padx=(0, 6))
        for val_n, txt_n in (
            ("leve", "Automático Leve"),
            ("medio", "Automático Médio"),
            ("pesado", "Automático Pesado"),
            ("manual", "Somente Filtros Manuais abaixo"),
        ):
            ttk.Radiobutton(
                modo_linha,
                text=txt_n,
                value=val_n,
                variable=self.nivel_auto_lote,
            ).pack(side="left", padx=(0, 10))

        grade_sliders = ttk.Frame(lote_box)
        grade_sliders.pack(fill="x", pady=(2, 6))
        grade_sliders.columnconfigure(0, weight=1, uniform="col_sl")
        grade_sliders.columnconfigure(1, weight=1, uniform="col_sl")

        for idx_s, (chave, nome_s, vmin, vmax, dica_s) in enumerate(SLIDERS_LOTE):
            col_s = idx_s % 2
            row_s = idx_s // 2
            celula = ttk.Frame(grade_sliders)
            celula.grid(row=row_s, column=col_s, sticky="ew", padx=(0, 8) if col_s == 0 else (8, 0), pady=2)
            topo_s = ttk.Frame(celula)
            topo_s.pack(fill="x")
            lbl_n = ttk.Label(topo_s, text=nome_s, style="Fraco.TLabel")
            lbl_n.pack(side="left")
            tema.dica(lbl_n, dica_s + " (Duplo-clique na barra para zerar)")
            lbl_v = ttk.Label(topo_s, text="0", style="Fraco.TLabel", width=5, anchor="e")
            lbl_v.pack(side="right")
            self.rotulos_lote[chave] = lbl_v
            var_s = self.vars_lote[chave]
            escala_s = ttk.Scale(
                celula,
                from_=vmin,
                to=vmax,
                variable=var_s,
                command=lambda v, c=chave, r=lbl_v: self._ao_mover_slider_lote(c, v, r),
            )
            escala_s.pack(fill="x")
            escala_s.bind("<Double-Button-1>", lambda e, c=chave: self._zerar_slider_lote(c))

        linha_opcoes_lote = ttk.Frame(lote_box)
        linha_opcoes_lote.pack(fill="x", pady=(4, 6))
        ttk.Checkbutton(
            linha_opcoes_lote,
            text="Converter lote para Preto e Branco (P&B)",
            variable=self.pb_lote,
        ).pack(side="left")
        ttk.Button(
            linha_opcoes_lote,
            text="↺ Zerar Barras Manuais",
            style="Compacto.TButton",
            command=self.zerar_barras_lote,
        ).pack(side="right")

        linha_acoes_lote = ttk.Frame(lote_box)
        linha_acoes_lote.pack(fill="x", pady=(4, 0))
        linha_acoes_lote.columnconfigure(0, weight=3)
        linha_acoes_lote.columnconfigure(1, weight=2)

        self.botao_lote_projeto = ttk.Button(
            linha_acoes_lote,
            text="✨ Aplicar no Projeto / Álbum (Com Ctrl+Z, sem alterar originais)",
            style="PrimarioCompacto.TButton",
            command=self.aplicar_lote_no_projeto,
        )
        self.botao_lote_projeto.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        tema.dica(
            self.botao_lote_projeto,
            "Aplica o nível inteligente + filtros manuais em todas as fotos do escopo dentro do projeto. Pode desfazer com Ctrl+Z.",
        )

        self.botao_lote_disco = ttk.Button(
            linha_acoes_lote,
            text="💾 Gravar nas Fotos Originais do Disco",
            style="Compacto.TButton",
            command=self.gravar_lote_nas_originais,
        )
        self.botao_lote_disco.grid(row=0, column=1, sticky="ew")
        tema.dica(
            self.botao_lote_disco,
            "Salva os ajustes de Luz, Cor e Saturação diretamente nos arquivos originais no computador (requer marcar a confirmação abaixo).",
        )

        # ---------------- SEÇÃO EXTERNA: PHOTOSHOP & LIGHTROOM ----------------
        ps = ttk.LabelFrame(quadro, text="3. Adobe Photoshop CC (Edição & Actions)", padding=10)
        ps.grid(row=2, column=0, sticky="nsew", padx=(0, 6), pady=(0, 8))
        botao = ttk.Button(ps, text="Ps  Abrir no Photoshop para editar", style="Compacto.TButton", command=self.editar_no_photoshop)
        botao.pack(fill="x")
        tema.dica(botao, "Abre as próprias fotos. Edite e salve com Ctrl+S: o álbum atualiza sozinho.")
        ttk.Label(ps, text="Conjunto da Ação (Action Set)", style="Fraco.TLabel").pack(anchor="w", pady=(8, 2))
        self.combo_conjunto = ttk.Combobox(ps, textvariable=self.conjunto)
        self.combo_conjunto.pack(fill="x")
        ttk.Label(ps, text="Ação (Action)", style="Fraco.TLabel").pack(anchor="w", pady=(6, 2))
        self.combo_acao = ttk.Combobox(ps, textvariable=self.acao)
        self.combo_acao.pack(fill="x")
        linha = ttk.Frame(ps)
        linha.pack(fill="x", pady=(8, 0))
        self.botao_carregar = ttk.Button(linha, text="↻ Ler Ações do Photoshop", style="Compacto.TButton",
                                         command=self.carregar_acoes)
        self.botao_carregar.pack(side="left", fill="x", expand=True)
        self.botao_acao = ttk.Button(ps, text="▶ Rodar Ação nas Fotos", style="PrimarioCompacto.TButton", command=self.rodar_acao)
        self.botao_acao.pack(fill="x", pady=(8, 0))

        lr = ttk.LabelFrame(quadro, text="4. Adobe Lightroom Classic (Plugin Ponte)", padding=10)
        lr.grid(row=2, column=1, sticky="nsew", pady=(0, 8))
        self.rotulo_lr = ttk.Label(lr, text="", style="Fraco.TLabel", wraplength=280, justify="left")
        self.rotulo_lr.pack(anchor="w")
        ttk.Label(lr, text="Preset de Revelação", style="Fraco.TLabel").pack(anchor="w", pady=(8, 2))
        self.combo_preset = ttk.Combobox(lr, textvariable=self.preset, state="readonly")
        self.combo_preset.pack(fill="x")
        linha = ttk.Frame(lr)
        linha.pack(fill="x", pady=(8, 0))
        ttk.Button(linha, text="↻ Atualizar Presets", style="Compacto.TButton", command=self.atualizar_presets).pack(
            side="left", fill="x", expand=True, padx=(0, 3))
        ttk.Button(linha, text="📂 Pasta do Plugin", style="Compacto.TButton", command=self.abrir_pasta_plugin).pack(
            side="left", fill="x", expand=True)
        lbl_instalar_lr = ttk.Label(
            lr,
            text="Instalar: no Lightroom Classic, Arquivo > Gerenciador de plug-ins > Adicionar, e escolha a pasta AlbumPonte.lrplugin.",
            style="Fraco.TLabel",
            wraplength=280,
            justify="left",
        )
        lbl_instalar_lr.pack(anchor="w", pady=(6, 0))
        self.botao_lr = ttk.Button(lr, text="▶ Tratar no Lightroom", style="PrimarioCompacto.TButton", command=self.tratar_lightroom)
        self.botao_lr.pack(fill="x", pady=(8, 0))

        aviso = ttk.LabelFrame(quadro, text="Confirmação para Substituir Arquivos Originais no Disco", padding=8)
        aviso.grid(row=3, column=0, columnspan=2, sticky="nsew", pady=(0, 8))
        lbl_aviso_sub = ttk.Label(
            aviso,
            text="Atenção: 'Aplicar no Projeto / Álbum' NÃO altera os arquivos do PC e aceita Ctrl+Z. Já 'Gravar nas Fotos Originais', 'Rodar Ação' ou 'Lightroom' substituem os arquivos originais no disco.",
            foreground=tema.ALERTA,
            wraplength=600,
            justify="left",
        )
        lbl_aviso_sub.pack(anchor="w")
        ttk.Checkbutton(aviso, text="Entendi: autorizo substituir os arquivos originais no disco", variable=self.entendi).pack(anchor="w", pady=(4, 0))

        andamento = ttk.Frame(quadro)
        andamento.grid(row=4, column=0, columnspan=2, sticky="nsew")
        quadro.rowconfigure(4, weight=1)
        self.barra = ttk.Progressbar(andamento, mode="determinate")
        self.barra.pack(fill="x")
        self.registro = tk.Text(andamento, height=5, bg=tema.FUNDO, fg=tema.TEXTO, relief="solid", bd=1,
                                insertbackground=tema.TEXTO, font=tema.FONTE_MONO, state="disabled")
        self.registro.pack(fill="both", expand=True, pady=(4, 0))
        self.rotulo_resumo = ttk.Label(andamento, text="", wraplength=600, justify="left")
        self.rotulo_resumo.pack(anchor="w", pady=(4, 0))

        botoes = ttk.Frame(self._rodape_fixo)
        botoes.pack(fill="x")
        self.botao_parar = ttk.Button(botoes, text="Parar", style="Compacto.TButton", command=self.app.parar_tarefa,
                                      state="disabled")
        self.botao_parar.pack(side="left")
        ttk.Button(botoes, text="Fechar", style="Compacto.TButton", command=self.fechar).pack(side="right", padx=(0, 10))

        def _ajustar_wraps_tratamento(evento):
            larg_total = max(460, int(evento.width) - 48)
            larg_col = max(210, larg_total // 2 - 24)
            self.rotulo_lr.configure(wraplength=larg_col)
            lbl_instalar_lr.configure(wraplength=larg_col)
            lbl_aviso_sub.configure(wraplength=larg_total)
            self._lbl_explica_lote.configure(wraplength=larg_total)
            self.rotulo_resumo.configure(wraplength=larg_total)

        quadro.bind("<Configure>", _ajustar_wraps_tratamento, add="+")

    # ---------------- fotos ----------------

    def listas(self):
        app = self.app
        return {
            "escolhidas": list(self.fotos_escolhidas),
            "selecionadas": app._fotos_escolhidas(),
            "lamina": app._fotos_da_lamina(),
            "album": app._fotos_do_album(),
            "projeto": [f["caminho"] for f in app.projeto["fotos"]],
        }

    def fotos(self):
        return self.listas().get(self.escopo.get(), [])

    def atualizar_fotos(self):
        listas = self.listas()
        textos = {"escolhidas": "Escolhidas", "selecionadas": "Selecionadas", "lamina": "Lâmina aberta",
                  "album": "Álbum inteiro", "projeto": "Todas do projeto"}
        for valor, radio in self.radios.items():
            radio.configure(text="%s (%s)" % (textos[valor], len(listas[valor])))
        fotos = listas.get(self.escopo.get(), [])
        self.rotulo_fotos.configure(text="%s foto(s) vão ser tratadas." % len(fotos) if fotos else
                                    "Nenhuma foto nesta escolha. Selecione fotos no painel ou escolha outra opção.")
        self._desenhar_faixa(fotos)

    def _desenhar_faixa(self, fotos):
        c = self.faixa
        c.delete("all")
        self._tk_miniaturas = []
        x = 4
        for caminho in fotos[:MINIATURAS_MAXIMO]:
            try:
                img = analise.miniatura(caminho).copy()
            except OSError:
                continue
            img.thumbnail((ALTURA_MINIATURA * 2, ALTURA_MINIATURA))
            foto = ImageTk.PhotoImage(img)
            self._tk_miniaturas.append(foto)
            c.create_image(x, 3, image=foto, anchor="nw")
            x += img.width + 4
        if len(fotos) > MINIATURAS_MAXIMO:
            c.create_text(x + 4, ALTURA_MINIATURA // 2 + 3, anchor="w", fill=tema.TEXTO_FRACO,
                          text="+ %s" % (len(fotos) - MINIATURAS_MAXIMO))

    # ---------------- Edição em Lote Inteligente (1 Clique + Luz, Cor e Saturação) ----------------

    def _ao_mover_slider_lote(self, chave, valor, rotulo):
        num = int(round(float(valor)))
        rotulo.configure(text="%d" % num)

    def _zerar_slider_lote(self, chave):
        if chave in self.vars_lote:
            self.vars_lote[chave].set(0.0)
        if chave in self.rotulos_lote:
            self.rotulos_lote[chave].configure(text="0")

    def zerar_barras_lote(self):
        for chave, var_s in self.vars_lote.items():
            var_s.set(0.0)
            if chave in self.rotulos_lote:
                self.rotulos_lote[chave].configure(text="0")
        self.pb_lote.set(False)
        self.aviso("Barras manuais de Luz, Cor e Saturação zeradas.")

    def _obter_extras_lote(self):
        extras = {}
        for chave, var_s in self.vars_lote.items():
            val = int(round(float(var_s.get() or 0)))
            if val != 0:
                extras[chave] = val
        if self.pb_lote.get():
            extras["pb"] = True
        return extras

    def aplicar_1clique_rapido(self, nivel):
        """Aplica imediatamente o 1-Clique (Leve, Médio ou Pesado) nas fotos do escopo escolhido no projeto."""
        self.nivel_auto_lote.set(nivel)
        self._executar_lote_no_projeto(nivel=nivel)

    def restaurar_original_lote(self):
        """Remove todos os ajustes de Luz, Cor e Saturação das fotos do escopo escolhido (com Ctrl+Z)."""
        fotos = self.fotos()
        if not fotos:
            messagebox.showinfo("Edição em Lote", "Nenhuma foto escolhida neste escopo.", parent=self)
            return
        qtd = self.app.aplicar_lote_inteligente(
            fotos_alvo=fotos,
            escopo=self.escopo.get(),
            nivel="original",
            extras={},
            janela_pai=self,
            silencioso=True,
        )
        msg = "Ajustes restaurados ao original em %d foto(s) (use Ctrl+Z se quiser desfazer)." % qtd
        self.aviso(msg)
        self.rotulo_resumo.configure(text=msg, foreground=tema.VERDE)

    def aplicar_lote_no_projeto(self):
        """Aplica o modo selecionado (Leve, Médio, Pesado ou Manual) + barras manuais no projeto com Ctrl+Z."""
        nivel = self.nivel_auto_lote.get()
        self._executar_lote_no_projeto(nivel=nivel)

    def _executar_lote_no_projeto(self, nivel, usar_ia=None):
        fotos = self.fotos()
        if not fotos:
            messagebox.showinfo("Edição em Lote", "Nenhuma foto escolhida neste escopo.", parent=self)
            return
        extras = self._obter_extras_lote()
        if usar_ia is None:
            usar_ia = bool(self.usar_ia_lote.get())
        if usar_ia and nivel in recorte.FORCA_IA_NIVEL:
            qtd = self.app.aplicar_lote_inteligente(
                fotos_alvo=fotos, escopo=self.escopo.get(), nivel=nivel, extras=extras,
                janela_pai=self, silencioso=True, usar_ia=True,
            )
            if qtd:
                msg = "🤖 Auto IA (%s) calculando %d foto(s) em segundo plano... (Suporta Ctrl+Z)" % (nivel, qtd)
                self.aviso(msg)
                self.rotulo_resumo.configure(text=msg, foreground=tema.VERDE)
            return
        if nivel == "manual" and not extras:
            messagebox.showinfo(
                "Edição em Lote",
                "Você escolheu 'Somente Filtros Manuais', mas todas as barras estão em zero.\n"
                "Mova alguma barra de Luz, Cor ou Saturação, ou escolha 1-Clique Leve, Médio ou Pesado.",
                parent=self,
            )
            return
        qtd = self.app.aplicar_lote_inteligente(
            fotos_alvo=fotos,
            escopo=self.escopo.get(),
            nivel=nivel,
            extras=extras,
            janela_pai=self,
            silencioso=True,
        )
        nomes_n = {
            "leve": "1-Clique Leve",
            "medio": "1-Clique Médio",
            "pesado": "1-Clique Pesado",
            "manual": "Filtros Manuais",
        }
        resumo = "%s aplicado em %d foto(s) no projeto! (Suporta Ctrl+Z)" % (
            nomes_n.get(nivel, nivel),
            qtd,
        )
        self.aviso(resumo)
        self.rotulo_resumo.configure(text=resumo, foreground=tema.VERDE)
        self.atualizar_fotos()

    def gravar_lote_nas_originais(self):
        """Grava o ajuste inteligente + filtros manuais diretamente nos arquivos originais no disco."""
        fotos = self._pode_trocar("Arquivos Originais do Disco")
        if not fotos:
            return
        nivel = self.nivel_auto_lote.get()
        extras = self._obter_extras_lote()
        if nivel == "manual" and not extras:
            messagebox.showinfo(
                "Gravar nas Fotos Originais",
                "Escolha um nível 1-Clique (Leve, Médio ou Pesado) ou ajuste as barras de Luz, Cor e Saturação antes de gravar.",
                parent=self,
            )
            return
        nomes_n = {
            "leve": "1-Clique Leve",
            "medio": "1-Clique Médio",
            "pesado": "1-Clique Pesado",
            "manual": "Filtros Manuais",
        }
        if not messagebox.askyesno(
            "Gravar Edição em Lote nas Fotos Originais",
            "Confirma gravar %s diretamente em %d arquivo(s) original(is) no disco?\n\n"
            "Os arquivos serão atualizados permanentemente e os ajustes do projeto nessas fotos serão zerados para não duplicar o efeito."
            % (nomes_n.get(nivel, nivel), len(fotos)),
            parent=self,
        ):
            return
        app = self.app
        usar_ia = bool(self.usar_ia_lote.get()) and nivel in recorte.FORCA_IA_NIVEL
        bases = {caminho: app.obter_edicao_foto(caminho) for caminho in dict.fromkeys(fotos)}

        def trabalho(t):
            trocadas, erros = [], []
            unicas = list(dict.fromkeys(fotos))
            total = len(unicas)
            for idx, caminho in enumerate(unicas, 1):
                if t.parar():
                    break
                nome_arq = Path(caminho).name
                t.aviso("Gravando melhoria (%d/%d): %s" % (idx, total, nome_arq))
                try:
                    ed_atual = bases.get(caminho) or {}
                    if usar_ia:
                        mapa_ia, _sem = app.calcular_edicoes_ia([caminho], nivel, extras, {normalizar_caminho(caminho): ed_atual})
                        ed_final = next(iter(mapa_ia.values()))
                    else:
                        ed_final = recorte.calcular_ajuste_inteligente(
                            caminho,
                            nivel=nivel,
                            extras=extras,
                            base_edicao=ed_atual,
                        )
                    if extras.get("pb"):
                        ed_final["pb"] = True
                    recorte.gravar_ajustes_no_arquivo_original(caminho, ed_final)
                    app.fila.put(("chamar", lambda c=caminho: app.zerar_ajustes_luz_cor_foto(c)))
                    app.fila.put(("foto_trocada", caminho, caminho))
                    trocadas.append(caminho)
                except Exception as e:
                    erros.append((caminho, str(e)))
                t.progresso(idx, total)
            return trocadas, erros

        self._comecar("Gravando %s nas originais (%d foto(s))" % (nomes_n.get(nivel, nivel), len(fotos)))
        if not app.executar("Gravando lote nas originais", trabalho, self._terminou, janela=self):
            self._parou_antes()

    # ---------------- Photoshop ----------------

    def _guardar(self):
        self.app.projeto["photoshop"] = {"conjunto": self.conjunto.get().strip(), "acao": self.acao.get().strip()}
        preset = self._preset()
        self.app.projeto["lightroom"] = {"preset_uuid": preset["uuid"] if preset else "", "preset_nome": self.preset.get()}
        self.app._marcar_modificado()

    def editar_no_photoshop(self):
        fotos = self.fotos()
        if not fotos:
            self.aviso("Nenhuma foto escolhida.")
            return
        self.aviso("Alternando para o Photoshop (%s foto(s)). Ao salvar (Ctrl+S) ou fechar, o Smart Album volta sozinho." % len(fotos))
        self.app.editar_no_photoshop(fotos)

    def carregar_acoes(self):
        self.botao_carregar.configure(state="disabled")
        self.aviso("Lendo as ações do Photoshop...")

        def trabalho():
            try:
                resultado, erro = photoshop.listar_acoes(), None
            except Exception as e:
                resultado, erro = None, e
            self.app.fila.put(("chamar", lambda: self._acoes_carregadas(resultado, erro)))

        threading.Thread(target=trabalho, daemon=True).start()

    def _acoes_carregadas(self, acoes, erro):
        if not self.winfo_exists():
            return
        self.botao_carregar.configure(state="normal")
        if erro is not None:
            self.aviso("Não deu para ler as ações (%s). Escreva o conjunto e a Ação." % erro)
            return
        self.acoes = acoes or {}
        self.combo_conjunto.configure(values=list(self.acoes))
        self._listar_acoes_do_conjunto()
        self.aviso("%s conjunto(s) de ações carregados." % len(self.acoes))

    def _listar_acoes_do_conjunto(self):
        self.combo_acao.configure(values=self.acoes.get(self.conjunto.get(), []))

    def _pode_trocar(self, onde):
        if self.rodando or self.app.tarefa is not None:
            messagebox.showinfo("Tratamento", "Espere a tarefa atual terminar.", parent=self)
            return None
        fotos = self.fotos()
        if not fotos:
            messagebox.showinfo("Tratamento", "Nenhuma foto escolhida.", parent=self)
            return None
        if not self.entendi.get():
            messagebox.showwarning("Tratamento", "Marque 'Entendi: autorizo substituir os arquivos originais no disco' para continuar em: %s."
                                   % onde, parent=self)
            return None
        return fotos

    def rodar_acao(self):
        conjunto, acao = self.conjunto.get().strip(), self.acao.get().strip()
        if not conjunto or not acao:
            messagebox.showinfo("Ação do Photoshop", "Escolha (ou escreva) o conjunto e a Ação.", parent=self)
            return
        fotos = self._pode_trocar("Photoshop")
        if not fotos:
            return
        app = self.app

        def trabalho(t):
            return photoshop.acao_em_lote(fotos, conjunto, acao, aviso=t.aviso, parar=t.parar, ao_progresso=t.progresso,
                                          ao_trocar=lambda c: app.fila.put(("foto_trocada", c, c)))

        self._comecar("Ação %s / %s em %s foto(s)" % (conjunto, acao, len(fotos)))
        if not app.executar("Ação do Photoshop", trabalho, self._terminou, janela=self):
            self._parou_antes()

    # ---------------- Lightroom ----------------

    def _preset(self):
        for preset in lightroom.listar_presets():
            if preset["nome"] == self.preset.get():
                return preset
        return None

    def atualizar_lightroom(self):
        presets = lightroom.listar_presets()
        self.combo_preset.configure(values=[p["nome"] for p in presets])
        if lightroom.plugin_ativo():
            texto = "Plugin ativo no Lightroom. %s preset(s)." % len(presets)
        elif presets:
            texto = "Lightroom fechado (o plugin responde quando ele abrir). %s preset(s)." % len(presets)
        else:
            texto = "Plugin ainda não instalado (veja abaixo) ou o Lightroom nunca foi aberto com ele."
        self.rotulo_lr.configure(text=texto)

    def atualizar_presets(self):
        lightroom.pedir_presets()
        self.aviso("Pedido de presets enviado ao Lightroom.")
        self.after(5000, lambda: self.winfo_exists() and self.atualizar_lightroom())
        self.atualizar_lightroom()

    def abrir_pasta_plugin(self):
        pasta = lightroom.pasta_plugin()
        if pasta.is_dir():
            os.startfile(str(pasta.parent))
        self.aviso("Plugin: %s" % pasta)

    def tratar_lightroom(self):
        preset = self._preset()
        if not preset:
            messagebox.showinfo("Lightroom", "Escolha o preset de revelação. Se a lista estiver vazia, instale o plugin "
                                "e clique em 'Atualizar presets' com o Lightroom aberto.", parent=self)
            return
        fotos = self._pode_trocar("Lightroom")
        if not fotos:
            return
        if not lightroom.plugin_ativo() and not messagebox.askyesno(
                "Lightroom", "O plugin do Lightroom não está respondendo agora. Mandar o pedido mesmo assim? "
                "Ele é feito quando o Lightroom abrir.", parent=self):
            return
        app = self.app

        def trabalho(t):
            return lightroom.tratar(fotos, preset["uuid"], preset["nome"], aviso=t.aviso, parar=t.parar,
                                    ao_progresso=t.progresso, ao_trocar=lambda o, f: app.fila.put(("foto_trocada", o, f)))

        self._comecar("Lightroom (preset %s) em %s foto(s)" % (preset["nome"], len(fotos)))
        if not app.executar("Tratando no Lightroom", trabalho, self._terminou, janela=self):
            self._parou_antes()

    # ---------------- andamento ----------------

    def _comecar(self, texto):
        self.rodando = True
        self.barra.configure(value=0, maximum=1)
        self.botao_parar.configure(state="normal")
        for botao in (self.botao_acao, self.botao_lr, self.botao_lote_disco):
            botao.configure(state="disabled")
        self.rotulo_resumo.configure(text="")
        self.aviso(texto + "...")

    def _parou_antes(self):
        self.rodando = False
        self.botao_parar.configure(state="disabled")
        for botao in (self.botao_acao, self.botao_lr, self.botao_lote_disco):
            botao.configure(state="normal")

    def aviso(self, texto):
        self.registro.configure(state="normal")
        self.registro.insert("end", texto + "\n")
        self.registro.see("end")
        self.registro.configure(state="disabled")

    def progresso(self, n, total):
        self.barra.configure(maximum=max(1, total), value=n)

    def _terminou(self, resultado, erro):
        self._parou_antes()
        if erro:
            self.rotulo_resumo.configure(text="Erro: %s" % erro, foreground=tema.ERRO)
            self.aviso("ERRO: %s" % erro)
            return
        trocadas, erros = resultado
        texto = "%s foto(s) tratadas." % len(trocadas)
        if erros:
            texto += " %s com erro:\n" % len(erros) + "\n".join(
                "%s: %s" % (Path(f).name if f else "", m) for f, m in erros[:8])
        self.rotulo_resumo.configure(text=texto, foreground=tema.ALERTA if erros else tema.VERDE)
        self.app.registrar(texto.splitlines()[0])
        self.atualizar_fotos()

    def fechar(self):
        self.app.janela_tratamento = None
        self.destroy()
