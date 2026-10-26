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
import tema

ALTURA_MINIATURA = 46
MINIATURAS_MAXIMO = 40


class JanelaTratamento(tk.Toplevel):
    def __init__(self, app, fotos=None):
        super().__init__(app.root)
        self.app = app
        self.title("Tratamento de fotos")
        self.configure(bg=tema.PAINEL)
        self.transient(app.root)
        self.minsize(600, 400)
        self.protocol("WM_DELETE_WINDOW", self.fechar)
        self.bind("<Escape>", lambda e: self.fechar())
        self.fotos_escolhidas = [f for f in (fotos or []) if f]
        self.escopo = tk.StringVar(value="escolhidas" if self.fotos_escolhidas else "selecionadas")
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
        self.update_idletasks()
        x = app.root.winfo_rootx() + max(0, (app.root.winfo_width() - self.winfo_reqwidth()) // 2)
        self.geometry("+%d+%d" % (x, app.root.winfo_rooty() + 30))

    # ---------------- montagem ----------------

    def _montar(self):
        quadro = ttk.Frame(self, padding=12)
        quadro.pack(fill="both", expand=True)
        quadro.columnconfigure(0, weight=1)
        quadro.columnconfigure(1, weight=1)

        quais = ttk.LabelFrame(quadro, text="Quais fotos", padding=8)
        quais.grid(row=0, column=0, columnspan=2, sticky="nsew", pady=(0, 6))
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
        self.rotulo_fotos.pack(anchor="w", pady=(4, 2))
        self.faixa = tk.Canvas(quais, height=ALTURA_MINIATURA + 6, bg=tema.PAINEL_2, highlightthickness=0)
        self.faixa.pack(fill="x")

        ps = ttk.LabelFrame(quadro, text="Photoshop", padding=8)
        ps.grid(row=1, column=0, sticky="nsew", padx=(0, 6), pady=(0, 6))
        botao = ttk.Button(ps, text="Abrir no Photoshop para editar", style="Compacto.TButton", command=self.editar_no_photoshop)
        botao.pack(fill="x")
        tema.dica(botao, "Abre as próprias fotos. Edite e salve com Ctrl+S: o álbum atualiza sozinho.")
        ttk.Label(ps, text="Conjunto da Ação", style="Fraco.TLabel").pack(anchor="w", pady=(8, 0))
        self.combo_conjunto = ttk.Combobox(ps, textvariable=self.conjunto)
        self.combo_conjunto.pack(fill="x")
        ttk.Label(ps, text="Ação", style="Fraco.TLabel").pack(anchor="w", pady=(4, 0))
        self.combo_acao = ttk.Combobox(ps, textvariable=self.acao)
        self.combo_acao.pack(fill="x")
        linha = ttk.Frame(ps)
        linha.pack(fill="x", pady=(6, 0))
        self.botao_carregar = ttk.Button(linha, text="Carregar ações do Photoshop", style="Compacto.TButton",
                                         command=self.carregar_acoes)
        self.botao_carregar.pack(side="left")
        self.botao_acao = ttk.Button(ps, text="Rodar Ação nas fotos", style="PrimarioCompacto.TButton", command=self.rodar_acao)
        self.botao_acao.pack(fill="x", pady=(8, 0))

        lr = ttk.LabelFrame(quadro, text="Lightroom Classic", padding=8)
        lr.grid(row=1, column=1, sticky="nsew", pady=(0, 6))
        self.rotulo_lr = ttk.Label(lr, text="", style="Fraco.TLabel", wraplength=260, justify="left")
        self.rotulo_lr.pack(anchor="w")
        ttk.Label(lr, text="Preset de revelação", style="Fraco.TLabel").pack(anchor="w", pady=(6, 0))
        self.combo_preset = ttk.Combobox(lr, textvariable=self.preset, state="readonly")
        self.combo_preset.pack(fill="x")
        linha = ttk.Frame(lr)
        linha.pack(fill="x", pady=(6, 0))
        ttk.Button(linha, text="Atualizar presets", style="Compacto.TButton", command=self.atualizar_presets).pack(
            side="left", fill="x", expand=True, padx=(0, 2))
        ttk.Button(linha, text="Pasta do plugin", style="Compacto.TButton", command=self.abrir_pasta_plugin).pack(
            side="left", fill="x", expand=True)
        ttk.Label(lr, text="Instalar: no Lightroom Classic, Arquivo > Gerenciador de plug-ins > Adicionar, e escolha "
                  "a pasta AlbumPonte.lrplugin.", style="Fraco.TLabel", wraplength=260, justify="left").pack(anchor="w", pady=(6, 0))
        self.botao_lr = ttk.Button(lr, text="Tratar no Lightroom", style="PrimarioCompacto.TButton", command=self.tratar_lightroom)
        self.botao_lr.pack(fill="x", pady=(8, 0))

        aviso = ttk.LabelFrame(quadro, text="Atenção", padding=8)
        aviso.grid(row=2, column=0, columnspan=2, sticky="nsew", pady=(0, 6))
        ttk.Label(aviso, text="Rodar a Ação ou o Lightroom SUBSTITUI as fotos originais pelas tratadas, no mesmo lugar e "
                  "com o mesmo nome, sem cópia. Não dá para voltar pelo programa.", foreground=tema.ALERTA,
                  wraplength=560, justify="left").pack(anchor="w")
        ttk.Checkbutton(aviso, text="Entendi: pode substituir as originais", variable=self.entendi).pack(anchor="w", pady=(4, 0))

        andamento = ttk.Frame(quadro)
        andamento.grid(row=3, column=0, columnspan=2, sticky="nsew")
        quadro.rowconfigure(3, weight=1)
        self.barra = ttk.Progressbar(andamento, mode="determinate")
        self.barra.pack(fill="x")
        self.registro = tk.Text(andamento, height=6, bg=tema.PAINEL_2, fg=tema.TEXTO, relief="flat",
                                insertbackground=tema.TEXTO, font=tema.FONTE_PEQUENA, state="disabled")
        self.registro.pack(fill="both", expand=True, pady=(4, 0))
        self.rotulo_resumo = ttk.Label(andamento, text="", wraplength=560, justify="left")
        self.rotulo_resumo.pack(anchor="w", pady=(4, 0))

        botoes = ttk.Frame(quadro)
        botoes.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.botao_parar = ttk.Button(botoes, text="Parar", style="Compacto.TButton", command=self.app.parar_tarefa,
                                      state="disabled")
        self.botao_parar.pack(side="left")
        ttk.Button(botoes, text="Fechar", style="Compacto.TButton", command=self.fechar).pack(side="right")

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
        self.app.editar_no_photoshop(fotos)
        self.aviso("Abrindo %s foto(s) no Photoshop. Salve com Ctrl+S: o álbum atualiza sozinho." % len(fotos))

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
            messagebox.showwarning("Tratamento", "Marque 'Entendi: pode substituir as originais' para tratar no %s."
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
        for botao in (self.botao_acao, self.botao_lr):
            botao.configure(state="disabled")
        self.rotulo_resumo.configure(text="")
        self.aviso(texto + "...")

    def _parou_antes(self):
        self.rodando = False
        self.botao_parar.configure(state="disabled")
        for botao in (self.botao_acao, self.botao_lr):
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
