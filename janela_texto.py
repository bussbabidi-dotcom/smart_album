# -*- coding: utf-8 -*-
"""
Janela do bloco de texto: escreve o texto e escolhe a fonte (qualquer uma instalada no PC), o tamanho,
a cor, o alinhamento, o espaçamento, a sombra e a opacidade. Cada mudança aparece na lâmina na hora
e pode ser desfeita com Ctrl+Z (um passo só por vez que a janela é aberta).
"""

import tkinter as tk
from tkinter import ttk

import tema
import texto_bloco

ATRASO_MS = 140


class JanelaTexto(tk.Toplevel):
    def __init__(self, app, indice_lamina, indice_texto):
        super().__init__(app.root)
        self.app = app
        self.indice_lamina = indice_lamina
        self.indice_texto = indice_texto
        self._carregando = True
        self._historico_marcado = False
        self._agendado = None
        self.title("Bloco de texto")
        self.configure(bg=tema.PAINEL)
        self.transient(app.root)
        self.minsize(380, 480)
        self.protocol("WM_DELETE_WINDOW", self.fechar)
        self.bind("<Escape>", lambda e: self.fechar())
        tema.configurar_janela_secundaria(self, pai=app.root, chave="janela_texto", largura=440, altura=660,
                                          min_largura=380, min_altura=480)
        tema.criar_cabecalho_janela(self, "Bloco de texto",
                                    "Transparente, por cima da lâmina · usa as fontes instaladas no PC", icone="T")
        quadro, _container, _canvas = tema.criar_corpo_rolavel(self, padding=12)
        quadro.columnconfigure(1, weight=1)
        bloco = self._bloco()
        if bloco is None:
            self.destroy()
            return
        b = texto_bloco.completar(bloco)

        linha = 0
        ttk.Label(quadro, text="Texto").grid(row=linha, column=0, sticky="nw", pady=(0, 6))
        self.caixa = tk.Text(quadro, height=4, width=30, wrap="word", undo=True, bg=tema.FUNDO, fg=tema.TEXTO,
                             insertbackground=tema.TEXTO, relief="flat", font=("Segoe UI", 10))
        self.caixa.grid(row=linha, column=1, sticky="ew", pady=(0, 6))
        self.caixa.insert("1.0", b["texto"])
        self.caixa.bind("<KeyRelease>", lambda e: self._alterou())

        linha += 1
        ttk.Label(quadro, text="Fonte").grid(row=linha, column=0, sticky="w", pady=3)
        self._todas_fontes = list(texto_bloco.fontes_instaladas())
        self.var_fonte = tk.StringVar(value=b["fonte"])
        self.combo_fonte = ttk.Combobox(quadro, textvariable=self.var_fonte, values=self._todas_fontes)
        self.combo_fonte.grid(row=linha, column=1, sticky="ew", pady=3)
        self.combo_fonte.bind("<<ComboboxSelected>>", lambda e: self._alterou())
        self.combo_fonte.bind("<KeyRelease>", self._filtrar_fontes)
        self.combo_fonte.bind("<Return>", lambda e: self._alterou())
        self.rotulo_fonte = ttk.Label(quadro, text="", style="TLabel")
        linha += 1
        self.rotulo_fonte.grid(row=linha, column=1, sticky="w")
        tema.dica(self.combo_fonte, "Digite parte do nome para filtrar (ex.: aston, montserrat).\n"
                                    "Aparecem todas as fontes instaladas no Windows.")

        linha += 1
        ttk.Label(quadro, text="Tamanho").grid(row=linha, column=0, sticky="w", pady=3)
        quadro_tam = ttk.Frame(quadro)
        quadro_tam.grid(row=linha, column=1, sticky="w", pady=3)
        self.var_modo = tk.StringVar(value="fixo" if b["tamanho_pt"] else "auto")
        ttk.Radiobutton(quadro_tam, text="Encaixar no bloco", variable=self.var_modo, value="auto",
                        command=self._alterou).pack(side="left")
        ttk.Radiobutton(quadro_tam, text="Fixo:", variable=self.var_modo, value="fixo",
                        command=self._alterou).pack(side="left", padx=(10, 2))
        self.var_pt = tk.StringVar(value=str(int(b["tamanho_pt"] or 24)))
        self.spin_pt = ttk.Spinbox(quadro_tam, from_=4, to=600, width=5, textvariable=self.var_pt, command=self._alterou)
        self.spin_pt.pack(side="left")
        self.spin_pt.bind("<KeyRelease>", lambda e: self._alterou())
        ttk.Label(quadro_tam, text="pt").pack(side="left", padx=2)

        linha += 1
        ttk.Label(quadro, text="Cor").grid(row=linha, column=0, sticky="w", pady=3)
        self.var_cor = tk.StringVar(value=b["cor"])
        quadro_cor = ttk.Frame(quadro)
        quadro_cor.grid(row=linha, column=1, sticky="w", pady=3)
        self.amostra_cor = tk.Label(quadro_cor, width=4, bg=b["cor"], relief="solid", bd=1)
        self.amostra_cor.pack(side="left")
        self.amostra_cor.bind("<Button-1>", lambda e: self._escolher_cor())
        ttk.Button(quadro_cor, text="Escolher cor...", command=self._escolher_cor).pack(side="left", padx=6)

        linha += 1
        ttk.Label(quadro, text="Opacidade").grid(row=linha, column=0, sticky="w", pady=3)
        self.var_opacidade = tk.IntVar(value=int(b["opacidade"]))
        ttk.Scale(quadro, from_=0, to=100, variable=self.var_opacidade, command=lambda v: self._alterou()).grid(
            row=linha, column=1, sticky="ew", pady=3)

        linha += 1
        ttk.Label(quadro, text="Alinhar").grid(row=linha, column=0, sticky="w", pady=3)
        quadro_al = ttk.Frame(quadro)
        quadro_al.grid(row=linha, column=1, sticky="w", pady=3)
        self.var_alinhar = tk.StringVar(value=b["alinhar"])
        for rotulo, valor in (("Esquerda", "esquerda"), ("Centro", "centro"), ("Direita", "direita")):
            ttk.Radiobutton(quadro_al, text=rotulo, variable=self.var_alinhar, value=valor,
                            command=self._alterou).pack(side="left", padx=(0, 8))

        linha += 1
        ttk.Label(quadro, text="Vertical").grid(row=linha, column=0, sticky="w", pady=3)
        quadro_v = ttk.Frame(quadro)
        quadro_v.grid(row=linha, column=1, sticky="w", pady=3)
        self.var_alinhar_v = tk.StringVar(value=b["alinhar_v"])
        for rotulo, valor in (("Topo", "topo"), ("Meio", "meio"), ("Base", "base")):
            ttk.Radiobutton(quadro_v, text=rotulo, variable=self.var_alinhar_v, value=valor,
                            command=self._alterou).pack(side="left", padx=(0, 8))

        linha += 1
        ttk.Label(quadro, text="Entre letras (%)").grid(row=linha, column=0, sticky="w", pady=3)
        self.var_letras = tk.StringVar(value=str(int(round(float(b["espaco_letras"]) * 100))))
        sp1 = ttk.Spinbox(quadro, from_=-10, to=100, width=6, textvariable=self.var_letras, command=self._alterou)
        sp1.grid(row=linha, column=1, sticky="w", pady=3)
        sp1.bind("<KeyRelease>", lambda e: self._alterou())

        linha += 1
        ttk.Label(quadro, text="Entre linhas (%)").grid(row=linha, column=0, sticky="w", pady=3)
        self.var_linhas = tk.StringVar(value=str(int(round(float(b["espaco_linhas"]) * 100))))
        sp2 = ttk.Spinbox(quadro, from_=40, to=250, width=6, textvariable=self.var_linhas, command=self._alterou)
        sp2.grid(row=linha, column=1, sticky="w", pady=3)
        sp2.bind("<KeyRelease>", lambda e: self._alterou())

        linha += 1
        self.var_maiusculas = tk.BooleanVar(value=bool(b["maiusculas"]))
        self.var_sombra = tk.BooleanVar(value=bool(b["sombra"]))
        quadro_ck = ttk.Frame(quadro)
        quadro_ck.grid(row=linha, column=0, columnspan=2, sticky="w", pady=6)
        ttk.Checkbutton(quadro_ck, text="MAIÚSCULAS", variable=self.var_maiusculas, command=self._alterou).pack(
            side="left", padx=(0, 14))
        ttk.Checkbutton(quadro_ck, text="Sombra suave", variable=self.var_sombra, command=self._alterou).pack(side="left")

        linha += 1
        quadro_bt = ttk.Frame(quadro)
        quadro_bt.grid(row=linha, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        ttk.Button(quadro_bt, text="Apagar bloco", command=self._apagar).pack(side="left")
        ttk.Button(quadro_bt, text="Fechar", command=self.fechar).pack(side="right")

        self._carregando = False
        self._mostrar_arquivo()
        self.caixa.focus_set()

    # ---------------------------------------------------------------- dados

    def _bloco(self):
        lamina = self.app._lamina(self.indice_lamina)
        textos = (lamina or {}).get("textos") or []
        if 0 <= self.indice_texto < len(textos):
            return textos[self.indice_texto]
        return None

    def _filtrar_fontes(self, evento=None):
        if evento is not None and evento.keysym in ("Up", "Down", "Return", "Escape", "Tab"):
            return
        digitado = self.var_fonte.get().strip().lower()
        achadas = [n for n in self._todas_fontes if digitado in n.lower()] if digitado else self._todas_fontes
        self.combo_fonte.configure(values=achadas or self._todas_fontes)
        if self.var_fonte.get() in self._todas_fontes:
            self._alterou()

    def _mostrar_arquivo(self):
        nome = self.var_fonte.get()
        arquivo = texto_bloco.fontes_instaladas().get(nome)
        self.rotulo_fonte.configure(text="" if arquivo else "Fonte não encontrada — usando Arial")

    def _escolher_cor(self):
        nova = tema.escolher_cor_photoshop(self, self.var_cor.get(), "Cor do texto",
                                           ao_mudar=lambda c: self._definir_cor(c))
        if nova:
            self._definir_cor(nova)

    def _definir_cor(self, cor):
        self.var_cor.set(cor)
        self.amostra_cor.configure(bg=cor)
        self._alterou()

    @staticmethod
    def _numero(variavel, padrao, minimo, maximo):
        try:
            valor = float(str(variavel.get()).replace(",", "."))
        except ValueError:
            return padrao
        return max(minimo, min(maximo, valor))

    def _alterou(self, *_):
        if self._carregando:
            return
        if self._agendado is not None:
            self.after_cancel(self._agendado)
        self._agendado = self.after(ATRASO_MS, self._aplicar)

    def _aplicar(self):
        self._agendado = None
        bloco = self._bloco()
        if bloco is None:
            return
        if not self._historico_marcado:
            self.app.marcar_historico()  # um passo de desfazer para tudo o que for mudado nesta janela
            self._historico_marcado = True
        fonte = self.var_fonte.get().strip() or texto_bloco.FONTE_PADRAO
        arquivo = texto_bloco.fontes_instaladas().get(fonte, "")
        bloco.update({
            "texto": self.caixa.get("1.0", "end-1c"),
            "fonte": fonte,
            "arquivo": arquivo or bloco.get("arquivo", ""),
            "tamanho_pt": None if self.var_modo.get() == "auto" else self._numero(self.var_pt, 24, 4, 600),
            "cor": self.var_cor.get(),
            "opacidade": int(self.var_opacidade.get()),
            "alinhar": self.var_alinhar.get(),
            "alinhar_v": self.var_alinhar_v.get(),
            "espaco_letras": self._numero(self.var_letras, 0, -10, 100) / 100.0,
            "espaco_linhas": self._numero(self.var_linhas, 95, 40, 250) / 100.0,
            "maiusculas": bool(self.var_maiusculas.get()),
            "sombra": bool(self.var_sombra.get()),
        })
        self._mostrar_arquivo()
        self.app.atualizar_lamina_atual()

    def _apagar(self):
        self.app.apagar_texto(self.indice_lamina, self.indice_texto)
        self.fechar()

    def fechar(self):
        if self._agendado is not None:
            self.after_cancel(self._agendado)
            self._aplicar()
        self.app.janela_texto = None
        self.destroy()
