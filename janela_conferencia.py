# -*- coding: utf-8 -*-
"""
Janela "Conferir álbum": mostra os problemas achados por conferencia.py, um por linha.
Duplo clique (ou Enter) leva direto à lâmina e ao espaço do problema; a janela fica aberta ao lado
para ir corrigindo e conferir de novo.
"""

import tkinter as tk
from tkinter import ttk

import conferencia
import tema

ICONES = {"erro": "✖", "aviso": "⚠", "info": "ℹ"}
CORES = {"erro": tema.ERRO, "aviso": tema.ALERTA, "info": tema.TEXTO_FRACO}


class JanelaConferencia(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.problemas = []
        self.var_so_atual = tk.BooleanVar(value=False)
        self.var_ocultar_info = tk.BooleanVar(value=False)
        self.title("Conferir álbum")
        self.configure(bg=tema.PAINEL)
        self.transient(app.root)
        self.protocol("WM_DELETE_WINDOW", self.fechar)
        self.bind("<Escape>", lambda e: self.fechar())
        tema.configurar_janela_secundaria(self, pai=app.root, chave="janela_conferencia", largura=720, altura=520,
                                          min_largura=520, min_altura=320)
        tema.criar_cabecalho_janela(self, "Conferir álbum",
                                    "Dê um duplo clique num problema para ir até ele", icone="✔")

        corpo = ttk.Frame(self, padding=(10, 8, 10, 4))
        corpo.pack(fill="both", expand=True)
        self.rotulo_resumo = ttk.Label(corpo, text="", font=("Segoe UI", 10, "bold"))
        self.rotulo_resumo.pack(anchor="w", pady=(0, 6))

        estilo = ttk.Style(self)
        estilo.configure("Conferencia.Treeview", background=tema.FUNDO, fieldbackground=tema.FUNDO,
                         foreground=tema.TEXTO, rowheight=24, borderwidth=0)
        estilo.map("Conferencia.Treeview", background=[("selected", tema.DESTAQUE_ESCURO)],
                   foreground=[("selected", "#ffffff")])
        estilo.configure("Conferencia.Treeview.Heading", background=tema.CABECALHO, foreground=tema.TEXTO)

        quadro = ttk.Frame(corpo)
        quadro.pack(fill="both", expand=True)
        self.lista = ttk.Treeview(quadro, columns=("lamina", "problema"), show="tree headings",
                                  style="Conferencia.Treeview", selectmode="browse")
        self.lista.heading("#0", text="")
        self.lista.heading("lamina", text="Lâmina")
        self.lista.heading("problema", text="O que achei")
        self.lista.column("#0", width=34, stretch=False, anchor="center")
        self.lista.column("lamina", width=80, stretch=False, anchor="center")
        self.lista.column("problema", width=520, anchor="w")
        barra = ttk.Scrollbar(quadro, orient="vertical", command=self.lista.yview)
        self.lista.configure(yscrollcommand=barra.set)
        self.lista.pack(side="left", fill="both", expand=True)
        barra.pack(side="right", fill="y")
        for nivel, cor in CORES.items():
            self.lista.tag_configure(nivel, foreground=cor)
        self.lista.bind("<Double-1>", self._ir)
        self.lista.bind("<Return>", self._ir)

        opcoes = ttk.Frame(corpo)
        opcoes.pack(fill="x", pady=(6, 0))
        c1 = ttk.Checkbutton(opcoes, text="Só a lâmina que estou vendo", variable=self.var_so_atual,
                             command=self.conferir)
        c1.pack(side="left")
        tema.dica(c1, "Confere apenas a lâmina aberta na tela, em vez do álbum inteiro.")
        c2 = ttk.Checkbutton(opcoes, text="Esconder observações", variable=self.var_ocultar_info,
                             command=self._preencher)
        c2.pack(side="left", padx=(12, 0))
        tema.dica(c2, "Esconde as linhas só informativas (como fotos que ficaram sem lâmina).")

        rodape = ttk.Frame(corpo)
        rodape.pack(fill="x", pady=(8, 4))
        ttk.Button(rodape, text="↻ Conferir de novo", command=self.conferir).pack(side="left")
        ttk.Button(rodape, text="Fechar", command=self.fechar).pack(side="right")
        self.botao_ir = ttk.Button(rodape, text="Ir até o problema", command=self._ir)
        self.botao_ir.pack(side="right", padx=6)
        self.conferir()

    def conferir(self):
        app = self.app
        indices = [app.atual] if self.var_so_atual.get() and isinstance(app.atual, int) else None
        self.problemas = conferencia.conferir(app.projeto, app.produto, app.info, indices)
        self._preencher()

    def _preencher(self):
        self.lista.delete(*self.lista.get_children())
        mostrados = [p for p in self.problemas if not (self.var_ocultar_info.get() and p["nivel"] == "info")]
        for i, p in enumerate(mostrados):
            lamina = "—" if p["lamina"] is None else str(p["lamina"] + 1)
            self.lista.insert("", "end", iid=str(i), text=ICONES[p["nivel"]], values=(lamina, p["texto"]),
                              tags=(p["nivel"],))
        self._mostrados = mostrados
        self.rotulo_resumo.configure(text=conferencia.resumo(self.problemas))
        if mostrados:
            self.lista.selection_set("0")
            self.lista.focus("0")

    def _ir(self, _evento=None):
        selecao = self.lista.selection()
        if not selecao:
            return
        p = self._mostrados[int(selecao[0])]
        if p["lamina"] is None:
            return
        app = self.app
        app.selecionar_lamina(p["lamina"])
        if p["espaco"] is not None:
            app.espaco_sel = p["espaco"]
            app._definir_selecao_espacos({p["espaco"]}, principal=p["espaco"])
            app.desenhar_lamina()

    def fechar(self):
        if getattr(self.app, "janela_conferencia", None) is self:
            self.app.janela_conferencia = None
        self.destroy()
