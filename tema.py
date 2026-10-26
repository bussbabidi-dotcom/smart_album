# -*- coding: utf-8 -*-
"""Tema escuro inspirado no Adobe Photoshop CC (grafite #181818 / #252526 com seletor de cores HSB/RGB/Hex e conta-gotas de tela)."""

import colorsys
import tkinter as tk
from tkinter import colorchooser, ttk

from PIL import Image, ImageDraw, ImageGrab, ImageTk

FUNDO = "#181818"
PAINEL = "#252526"
PAINEL_2 = "#323233"
PAINEL_3 = "#3e3e42"
CABECALHO = "#1e1e1e"
BORDA = "#3c3c3c"
BORDA_CLARA = "#505054"
TEXTO = "#e6e6e6"
TEXTO_FRACO = "#969696"
DESTAQUE = "#0d99ff"
DESTAQUE_ESCURO = "#007acc"
MULTI_SEL = "#8b5cf6"
MULTI_SEL_ESCURO = "#4c1d95"
VERDE = "#4cc38a"
ALERTA = "#e0a040"
ERRO = "#e05555"

FONTE = ("Segoe UI", 9)
FONTE_FORTE = ("Segoe UI Semibold", 9)
FONTE_TITULO = ("Segoe UI Semibold", 10)
FONTE_CABECALHO = ("Segoe UI Semibold", 11)
FONTE_PEQUENA = ("Segoe UI", 8)
FONTE_MONO = ("Consolas", 9)
FONTE_ICONE = ("Segoe UI Symbol", 10)


def configurar(root):
    root.configure(bg=FUNDO)
    root.option_add("*Font", FONTE)
    root.option_add("*Menu.background", PAINEL)
    root.option_add("*Menu.foreground", TEXTO)
    root.option_add("*Menu.activeBackground", DESTAQUE_ESCURO)
    root.option_add("*Menu.activeForeground", "#ffffff")
    root.option_add("*Menu.relief", "flat")
    root.option_add("*TCombobox*Listbox.background", PAINEL)
    root.option_add("*TCombobox*Listbox.foreground", TEXTO)
    root.option_add("*TCombobox*Listbox.selectBackground", DESTAQUE_ESCURO)

    estilo = ttk.Style(root)
    estilo.theme_use("clam")

    estilo.configure(".", background=PAINEL, foreground=TEXTO, fieldbackground=PAINEL_2,
                     bordercolor=BORDA, lightcolor=PAINEL_2, darkcolor=PAINEL, troughcolor=FUNDO,
                     focuscolor=DESTAQUE, selectbackground=DESTAQUE_ESCURO, selectforeground="#ffffff",
                     insertcolor=TEXTO, font=FONTE)

    estilo.configure("TFrame", background=PAINEL)
    estilo.configure("Fundo.TFrame", background=FUNDO)
    estilo.configure("Cabecalho.TFrame", background=CABECALHO)
    estilo.configure("Card.TFrame", background=PAINEL_2)
    estilo.configure("Barra.TFrame", background=PAINEL)

    estilo.configure("TLabel", background=PAINEL, foreground=TEXTO)
    estilo.configure("Fraco.TLabel", foreground=TEXTO_FRACO)
    estilo.configure("Fundo.TLabel", background=FUNDO, foreground=TEXTO_FRACO)
    estilo.configure("Cabecalho.TLabel", background=CABECALHO, foreground=TEXTO, font=FONTE_CABECALHO)
    estilo.configure("SubCabecalho.TLabel", background=CABECALHO, foreground=TEXTO_FRACO, font=FONTE_PEQUENA)
    estilo.configure("Titulo.TLabel", font=FONTE_TITULO)
    estilo.configure("Secao.TLabel", foreground=DESTAQUE, font=FONTE_FORTE)
    estilo.configure("Logo.TLabel", font=("Segoe UI Black", 12), foreground=DESTAQUE)

    estilo.configure("TButton", background=PAINEL_2, foreground=TEXTO, borderwidth=1, bordercolor=BORDA,
                     padding=(9, 4), relief="flat")
    estilo.map("TButton", background=[("pressed", DESTAQUE_ESCURO), ("active", PAINEL_3), ("disabled", PAINEL)],
               foreground=[("pressed", "#ffffff"), ("disabled", TEXTO_FRACO)],
               bordercolor=[("active", BORDA_CLARA), ("pressed", DESTAQUE)])

    estilo.configure("Primario.TButton", background=DESTAQUE, foreground="#ffffff", font=FONTE_FORTE,
                     borderwidth=0, padding=(10, 5))
    estilo.map("Primario.TButton", background=[("pressed", DESTAQUE_ESCURO), ("active", "#33adff"),
                                               ("disabled", PAINEL_3)])

    estilo.configure("Icone.TButton", padding=(6, 4), font=FONTE_ICONE, anchor="center", width=0)
    estilo.configure("Compacto.TButton", padding=(7, 3), width=0)
    estilo.configure("Mini.TButton", padding=(6, 3), anchor="center", font=FONTE_ICONE, width=0)
    estilo.configure("PrimarioCompacto.TButton", background=DESTAQUE, foreground="#ffffff", font=FONTE_FORTE,
                     borderwidth=0, padding=(8, 4), width=0)
    estilo.map("PrimarioCompacto.TButton", background=[("pressed", DESTAQUE_ESCURO), ("active", "#33adff"),
                                                       ("disabled", PAINEL_3)])

    estilo.configure("Compacto.TMenubutton", background=PAINEL_2, foreground=TEXTO, borderwidth=1, bordercolor=BORDA,
                     padding=(7, 3), relief="flat", arrowcolor=TEXTO_FRACO, width=0)
    estilo.map("Compacto.TMenubutton", background=[("active", PAINEL_3)])
    estilo.configure("Compacto.TCheckbutton", padding=(4, 2))
    estilo.configure("PrimarioCompacto.TMenubutton", background=DESTAQUE, foreground="#ffffff", font=FONTE_FORTE,
                     arrowcolor="#ffffff", borderwidth=0, relief="flat", padding=(8, 3), width=0)
    estilo.map("PrimarioCompacto.TMenubutton", background=[("active", "#33adff")])

    estilo.configure("Alternar.Toolbutton", background=PAINEL_2, foreground=TEXTO, borderwidth=1, bordercolor=BORDA,
                     padding=(8, 4), relief="flat", anchor="center", width=0)
    estilo.map("Alternar.Toolbutton", background=[("selected", DESTAQUE), ("active", PAINEL_3)],
               foreground=[("selected", "#ffffff")],
               bordercolor=[("selected", DESTAQUE)])

    estilo.configure("TMenubutton", background=PAINEL_2, foreground=TEXTO, borderwidth=1, bordercolor=BORDA,
                     padding=(8, 4), relief="flat", arrowcolor=TEXTO_FRACO)
    estilo.map("TMenubutton", background=[("active", PAINEL_3)])
    estilo.configure("Primario.TMenubutton", background=DESTAQUE, foreground="#ffffff", font=FONTE_FORTE,
                     arrowcolor="#ffffff")
    estilo.map("Primario.TMenubutton", background=[("active", "#33adff")])

    estilo.configure("TCheckbutton", background=PAINEL, foreground=TEXTO, indicatorbackground=FUNDO,
                     indicatorforeground=DESTAQUE)
    estilo.map("TCheckbutton", background=[("active", PAINEL)], indicatorbackground=[("selected", FUNDO)])
    estilo.configure("TRadiobutton", background=PAINEL, foreground=TEXTO, indicatorbackground=FUNDO,
                     indicatorforeground=DESTAQUE)
    estilo.map("TRadiobutton", background=[("active", PAINEL)])
    estilo.configure("Filtro.TRadiobutton", background=PAINEL_2, foreground=TEXTO_FRACO, padding=(7, 3),
                     indicatorsize=0, indicatormargin=0)
    estilo.map("Filtro.TRadiobutton", background=[("selected", DESTAQUE_ESCURO), ("active", PAINEL_3)],
               foreground=[("selected", "#ffffff")])

    estilo.configure("TEntry", fieldbackground=FUNDO, foreground=TEXTO, borderwidth=1, bordercolor=BORDA)
    estilo.configure("TSpinbox", fieldbackground=FUNDO, foreground=TEXTO, arrowcolor=TEXTO_FRACO, background=PAINEL_2)
    estilo.configure("TCombobox", fieldbackground=FUNDO, foreground=TEXTO, arrowcolor=TEXTO_FRACO, background=PAINEL_2)
    estilo.map("TCombobox", fieldbackground=[("readonly", FUNDO)], foreground=[("readonly", TEXTO)],
               selectbackground=[("readonly", FUNDO)], selectforeground=[("readonly", TEXTO)])

    estilo.configure("TNotebook", background=PAINEL, borderwidth=0, tabmargins=(2, 2, 2, 0))
    estilo.configure("TNotebook.Tab", background=FUNDO, foreground=TEXTO_FRACO, padding=(10, 4), borderwidth=0)
    estilo.map("TNotebook.Tab", background=[("selected", PAINEL)], foreground=[("selected", TEXTO)])

    estilo.configure("TLabelframe", background=PAINEL, bordercolor=BORDA, borderwidth=1, relief="solid")
    estilo.configure("TLabelframe.Label", background=PAINEL, foreground=DESTAQUE, font=FONTE_FORTE)

    estilo.configure("Horizontal.TProgressbar", background=DESTAQUE, troughcolor=FUNDO, borderwidth=0,
                     lightcolor=DESTAQUE, darkcolor=DESTAQUE)
    estilo.configure("Horizontal.TScale", background=PAINEL, troughcolor=FUNDO)
    estilo.configure("TScrollbar", background=PAINEL_2, troughcolor=FUNDO, arrowcolor=TEXTO_FRACO, borderwidth=0)
    estilo.map("TScrollbar", background=[("active", PAINEL_3)])
    estilo.configure("TPanedwindow", background=FUNDO)
    estilo.configure("Sash", sashthickness=3)
    return estilo


def criar_cabecalho_janela(pai, titulo, subtitulo=None, icone="◆"):
    """Cabeçalho superior escuro no estilo Adobe Photoshop CC para janelas flutuantes."""
    faixa = tk.Frame(pai, bg=CABECALHO, highlightthickness=1, highlightbackground=BORDA)
    faixa.pack(side="top", fill="x")
    interno = tk.Frame(faixa, bg=CABECALHO, padx=14, pady=9)
    interno.pack(fill="x")
    linha_t = tk.Frame(interno, bg=CABECALHO)
    linha_t.pack(fill="x")
    tk.Label(linha_t, text=icone, bg=CABECALHO, fg=DESTAQUE, font=("Segoe UI Symbol", 11, "bold")).pack(side="left", padx=(0, 7))
    lbl_titulo = tk.Label(linha_t, text=titulo, bg=CABECALHO, fg=TEXTO, font=FONTE_CABECALHO)
    lbl_titulo.pack(side="left")
    lbl_sub = None
    if subtitulo is not None:
        lbl_sub = tk.Label(interno, text=subtitulo, bg=CABECALHO, fg=TEXTO_FRACO, font=FONTE_PEQUENA, anchor="w", justify="left")
        lbl_sub.pack(fill="x", padx=(22, 0), pady=(2, 0))
    return faixa, lbl_titulo, lbl_sub


class Dica:
    """Tooltip estilo Photoshop: aparece rapidamente ao pousar o mouse sobre o ícone."""

    ATRASO_MS = 220

    def __init__(self, widget, texto):
        self.widget = widget
        self.texto = texto
        self.janela = None
        self.agendado = None
        widget.bind("<Enter>", self._entrou, add="+")
        widget.bind("<Leave>", self._saiu, add="+")
        widget.bind("<ButtonPress>", self._saiu, add="+")

    def _entrou(self, _evento=None):
        self._cancelar()
        self.agendado = self.widget.after(self.ATRASO_MS, self.mostrar)

    def _saiu(self, _evento=None):
        self._cancelar()
        if self.janela is not None:
            self.janela.destroy()
            self.janela = None

    def _cancelar(self):
        if self.agendado:
            self.widget.after_cancel(self.agendado)
            self.agendado = None

    def mostrar(self):
        self.agendado = None
        if self.janela is not None or not self.widget.winfo_ismapped() or not self.texto:
            return
        self.janela = tk.Toplevel(self.widget)
        self.janela.overrideredirect(True)
        self.janela.attributes("-topmost", True)
        borda = tk.Frame(self.janela, bg="#525257", padx=1, pady=1)
        borda.pack()
        tk.Label(borda, text=self.texto, bg="#181818", fg="#f0f0f0", padx=8, pady=4, font=FONTE,
                 justify="left").pack()
        self.janela.update_idletasks()
        x = self.widget.winfo_rootx()
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 5
        largura_tela = self.widget.winfo_screenwidth()
        x = max(4, min(x, largura_tela - self.janela.winfo_reqwidth() - 4))
        if y + self.janela.winfo_reqheight() > self.widget.winfo_screenheight():
            y = self.widget.winfo_rooty() - self.janela.winfo_reqheight() - 5
        self.janela.geometry("+%d+%d" % (x, y))


def dica(widget, texto):
    widget._dica = Dica(widget, texto)
    return widget


def _hex_para_rgb(texto):
    s = str(texto or "#ffffff").strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) != 6:
        return (255, 255, 255)
    try:
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except ValueError:
        return (255, 255, 255)


def _rgb_para_hex(r, g, b):
    return "#%02x%02x%02x" % (
        max(0, min(255, int(round(r)))),
        max(0, min(255, int(round(g)))),
        max(0, min(255, int(round(b)))),
    )


class ContaGotasTela(tk.Toplevel):
    """
    Conta-gotas em tela cheia estilo Photoshop: captura a tela atual e exibe uma lupa
    ampliada (9x9 pixels) acompanhando o cursor para capturar qualquer cor visível na tela.
    """

    def __init__(self, pai, ao_escolher):
        super().__init__(pai)
        self.ao_escolher = ao_escolher
        self.resultado = None
        self.withdraw()
        self.update_idletasks()

        try:
            self._captura = ImageGrab.grab(all_screens=True).convert("RGB")
        except Exception:
            try:
                self._captura = ImageGrab.grab().convert("RGB")
            except Exception:
                self._captura = Image.new("RGB", (max(800, pai.winfo_screenwidth()), max(600, pai.winfo_screenheight())), (30, 30, 30))

        self.overrideredirect(True)
        self.attributes("-topmost", True)
        largura = self.winfo_screenwidth()
        altura = self.winfo_screenheight()
        self.geometry("%dx%d+0+0" % (largura, altura))
        self.configure(bg="#000000", cursor="crosshair")

        img_exibida = self._captura
        if img_exibida.size != (largura, altura):
            img_exibida = self._captura.crop((0, 0, largura, altura))
        self._tk_fundo = ImageTk.PhotoImage(img_exibida)
        self._tk_lupa = None

        self.canvas = tk.Canvas(self, width=largura, height=altura, highlightthickness=0, bd=0, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.create_image(0, 0, image=self._tk_fundo, anchor="nw")

        # Faixa de instrução no topo
        self.canvas.create_rectangle(
            largura // 2 - 220, 14, largura // 2 + 220, 44,
            fill="#181818", outline=DESTAQUE, width=1, tags="ui"
        )
        self.canvas.create_text(
            largura // 2, 29,
            text="🎯 Clique em qualquer ponto da tela para capturar a cor  ·  ESC para cancelar",
            fill="#ffffff", font=FONTE_FORTE, tags="ui"
        )

        self.canvas.bind("<Motion>", self._mover)
        self.canvas.bind("<ButtonPress-1>", self._confirmar)
        self.canvas.bind("<ButtonPress-3>", lambda _e: self.cancelar())
        self.bind("<Escape>", lambda _e: self.cancelar())

        self.deiconify()
        self.lift()
        self.focus_force()
        self.grab_set()

    def _cor_em(self, x, y):
        w, h = self._captura.size
        px = max(0, min(w - 1, int(x)))
        py = max(0, min(h - 1, int(y)))
        return self._captura.getpixel((px, py))

    def _mover(self, evento):
        x, y = int(evento.x), int(evento.y)
        r, g, b = self._cor_em(x, y)
        hex_cor = _rgb_para_hex(r, g, b)

        # Recorte 9x9 ao redor do cursor ampliado para 108x108
        raio = 4
        lado_grade = 9
        escala = 12
        lado_lupa = lado_grade * escala
        recorte = Image.new("RGB", (lado_grade, lado_grade), (24, 24, 24))
        w, h = self._captura.size
        for dy in range(-raio, raio + 1):
            for dx in range(-raio, raio + 1):
                sx = max(0, min(w - 1, x + dx))
                sy = max(0, min(h - 1, y + dy))
                recorte.putpixel((dx + raio, dy + raio), self._captura.getpixel((sx, sy)))
        ampliada = recorte.resize((lado_lupa, lado_lupa), Image.NEAREST)
        draw = ImageDraw.Draw(ampliada)
        # Pixel central destacado
        c0 = raio * escala
        c1 = c0 + escala
        draw.rectangle((c0, c0, c1, c1), outline="#ffffff", width=2)
        draw.rectangle((c0 - 1, c0 - 1, c1 + 1, c1 + 1), outline="#000000", width=1)
        self._tk_lupa = ImageTk.PhotoImage(ampliada)

        c = self.canvas
        c.delete("lupa")
        lx = x + 22
        ly = y + 22
        if lx + lado_lupa + 16 > c.winfo_width():
            lx = x - lado_lupa - 22
        if ly + lado_lupa + 46 > c.winfo_height():
            ly = y - lado_lupa - 52

        c.create_rectangle(lx - 2, ly - 2, lx + lado_lupa + 2, ly + lado_lupa + 38,
                           fill="#181818", outline=DESTAQUE, width=2, tags="lupa")
        c.create_image(lx, ly, image=self._tk_lupa, anchor="nw", tags="lupa")
        c.create_rectangle(lx + 6, ly + lado_lupa + 6, lx + 24, ly + lado_lupa + 30,
                           fill=hex_cor, outline="#ffffff", width=1, tags="lupa")
        c.create_text(lx + 30, ly + lado_lupa + 18, anchor="w",
                      text="%s  (%d,%d,%d)" % (hex_cor.upper(), r, g, b),
                      fill="#ffffff", font=FONTE_MONO, tags="lupa")

    def _confirmar(self, evento):
        r, g, b = self._cor_em(evento.x, evento.y)
        self.resultado = _rgb_para_hex(r, g, b)
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()
        if self.ao_escolher:
            self.ao_escolher(self.resultado)

    def cancelar(self):
        self.resultado = None
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()


class DialogoCorPhotoshop(tk.Toplevel):
    """
    Seletor de Cores estilo Adobe Photoshop CC:
    - Campo 2D Saturação x Brilho + Barra vertical de Matiz (Hue)
    - Comparador Nova Cor / Cor Atual
    - Botão Conta-Gotas de Tela Cheia (captura qualquer pixel da tela)
    - Campos HSB, RGB e Hex (#RRGGBB) sincronizados em tempo real
    """

    LADO_SV = 210
    LARG_H = 22

    def __init__(self, pai, cor_inicial="#c08a4a", titulo="Seletor de Cores (Photoshop)", ao_mudar=None):
        super().__init__(pai)
        self.pai = pai
        self.ao_mudar = ao_mudar
        self.cor_inicial = _rgb_para_hex(*_hex_para_rgb(cor_inicial))
        self.resultado = None
        self._sincronizando = False
        self._matiz_cache_img = None
        self._matiz_cache_val = None
        self._tk_sv = None
        self._tk_hue = None

        r0, g0, b0 = _hex_para_rgb(self.cor_inicial)
        h0, s0, v0 = colorsys.rgb_to_hsv(r0 / 255.0, g0 / 255.0, b0 / 255.0)
        self.h = h0 * 360.0
        self.s = s0 * 100.0
        self.v = v0 * 100.0

        self.title(titulo)
        self.configure(bg=PAINEL)
        self.transient(pai)
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self.cancelar)
        self.bind("<Escape>", lambda _e: self.cancelar())
        self.bind("<Return>", lambda _e: self.confirmar())

        self._montar()
        self._gerar_barra_hue()
        self._atualizar_tudo()

        self.update_idletasks()
        px = pai.winfo_rootx() + max(20, (pai.winfo_width() - self.winfo_reqwidth()) // 2)
        py = pai.winfo_rooty() + max(20, (pai.winfo_height() - self.winfo_reqheight()) // 2)
        self.geometry("+%d+%d" % (px, py))
        self.grab_set()
        self.focus_set()

    def _montar(self):
        criar_cabecalho_janela(
            self,
            "Seletor de Cores (Photoshop)",
            "Escolha no espectro HSB/RGB ou capture qualquer pixel da tela com o conta-gotas",
            icone="🎨",
        )

        corpo = ttk.Frame(self, padding=14)
        corpo.pack(fill="both", expand=True)

        grade = ttk.Frame(corpo)
        grade.pack(fill="both", expand=True)

        # 1. Campo 2D Saturação / Brilho
        quadro_sv = tk.Frame(grade, bg=BORDA, padx=1, pady=1)
        quadro_sv.grid(row=0, column=0, sticky="nw")
        self.canvas_sv = tk.Canvas(
            quadro_sv, width=self.LADO_SV, height=self.LADO_SV,
            bg="#000000", highlightthickness=0, cursor="crosshair"
        )
        self.canvas_sv.pack()
        self.canvas_sv.bind("<ButtonPress-1>", self._evento_sv)
        self.canvas_sv.bind("<B1-Motion>", self._evento_sv)

        # 2. Barra vertical de Matiz (Hue)
        quadro_h = tk.Frame(grade, bg=BORDA, padx=1, pady=1)
        quadro_h.grid(row=0, column=1, sticky="nw", padx=(10, 14))
        self.canvas_h = tk.Canvas(
            quadro_h, width=self.LARG_H, height=self.LADO_SV,
            bg="#000000", highlightthickness=0, cursor="sb_v_double_arrow"
        )
        self.canvas_h.pack()
        self.canvas_h.bind("<ButtonPress-1>", self._evento_h)
        self.canvas_h.bind("<B1-Motion>", self._evento_h)

        # 3. Coluna de Amostras (Nova / Atual) + Conta-gotas + Entradas HSB / RGB / Hex
        col_dir = ttk.Frame(grade)
        col_dir.grid(row=0, column=2, sticky="nsew")

        topo_dir = ttk.Frame(col_dir)
        topo_dir.pack(fill="x", pady=(0, 8))

        # Comparador Nova / Atual estilo Photoshop
        bloco_amostra = tk.Frame(topo_dir, bg=BORDA, padx=1, pady=1)
        bloco_amostra.pack(side="left")
        tk.Label(bloco_amostra, text="nova", bg=CABECALHO, fg=TEXTO_FRACO, font=FONTE_PEQUENA).pack(fill="x")
        self.lbl_nova = tk.Label(bloco_amostra, width=9, height=2, bg=self.cor_inicial, relief="flat")
        self.lbl_nova.pack(fill="x")
        self.lbl_atual = tk.Label(bloco_amostra, width=9, height=2, bg=self.cor_inicial, relief="flat", cursor="hand2")
        self.lbl_atual.pack(fill="x")
        self.lbl_atual.bind("<Button-1>", lambda _e: self._definir_hex(self.cor_inicial))
        tk.Label(bloco_amostra, text="atual", bg=CABECALHO, fg=TEXTO_FRACO, font=FONTE_PEQUENA).pack(fill="x")
        dica(self.lbl_atual, "Clique para voltar à cor original (%s)" % self.cor_inicial)

        # Botões de ação à direita da amostra
        col_botoes = ttk.Frame(topo_dir)
        col_botoes.pack(side="left", fill="both", expand=True, padx=(10, 0))
        ttk.Button(col_botoes, text="OK", style="Primario.TButton", command=self.confirmar).pack(fill="x", pady=(0, 4))
        ttk.Button(col_botoes, text="Cancelar", command=self.cancelar).pack(fill="x", pady=(0, 6))
        bt_gotas = ttk.Button(
            col_botoes,
            text="🎯 Capturar da Tela",
            style="Compacto.TButton",
            command=self.iniciar_conta_gotas,
        )
        bt_gotas.pack(fill="x")
        dica(bt_gotas, "Ativa o conta-gotas com lupa para escolher qualquer cor visível na tela")

        # Entradas numéricas HSB e RGB lado a lado
        grade_num = ttk.LabelFrame(col_dir, text="Valores HSB / RGB", padding=6)
        grade_num.pack(fill="x", pady=(0, 6))

        self.vars_num = {}
        definicoes = (
            ("H", "°", 0, 360, 0, 0),
            ("S", "%", 0, 100, 1, 0),
            ("B", "%", 0, 100, 2, 0),
            ("R", "", 0, 255, 0, 1),
            ("G", "", 0, 255, 1, 1),
            ("B_rgb", "", 0, 255, 2, 1),
        )
        for chave, sufixo, minimo, maximo, lin, col in definicoes:
            rot_txt = "B:" if chave == "B_rgb" else ("%s:" % chave)
            var = tk.IntVar(value=0)
            self.vars_num[chave] = var
            px_col = col * 3
            ttk.Label(grade_num, text=rot_txt, width=2).grid(row=lin, column=px_col, sticky="w", pady=1)
            sp = ttk.Spinbox(
                grade_num, from_=minimo, to=maximo, increment=1, width=5,
                textvariable=var, command=lambda c=chave: self._digitou_num(c)
            )
            sp.grid(row=lin, column=px_col + 1, sticky="w", padx=(2, 2), pady=1)
            sp.bind("<Return>", lambda _e, c=chave: self._digitou_num(c))
            sp.bind("<FocusOut>", lambda _e, c=chave: self._digitou_num(c))
            ttk.Label(grade_num, text=sufixo, style="Fraco.TLabel", width=2).grid(
                row=lin, column=px_col + 2, sticky="w", padx=(0, 8 if col == 0 else 0)
            )

        # Campo Hexadecimal + Botão sistema
        linha_hex = ttk.Frame(col_dir)
        linha_hex.pack(fill="x", pady=(2, 0))
        ttk.Label(linha_hex, text="#").pack(side="left")
        self.var_hex = tk.StringVar(value=self.cor_inicial.lstrip("#").upper())
        ent_hex = ttk.Entry(linha_hex, textvariable=self.var_hex, width=9, font=FONTE_MONO)
        ent_hex.pack(side="left", padx=(2, 8))
        ent_hex.bind("<Return>", lambda _e: self._digitou_hex())
        ent_hex.bind("<FocusOut>", lambda _e: self._digitou_hex())
        ttk.Button(
            linha_hex, text="Paleta Windows...", style="Compacto.TButton",
            command=self._abrir_paleta_sistema
        ).pack(side="right")

        # Paleta rápida de tons clássicos de álbum (sépia, âmbar, azul petróleo, creme, grafite...)
        faixa_presets = ttk.LabelFrame(corpo, text="Amostras Rápidas de Estúdio", padding=6)
        faixa_presets.pack(fill="x", pady=(10, 0))
        cores_rapidas = (
            "#ffffff", "#f5f0e6", "#ead7c3", "#d4a373", "#c08a4a", "#8d5b4c",
            "#5c4033", "#2b2d42", "#1d3557", "#457b9d", "#2a9d8f", "#6b705c",
            "#3a3a3c", "#181818",
        )
        for c_hex in cores_rapidas:
            am = tk.Label(faixa_presets, bg=c_hex, width=3, height=1, relief="solid", bd=1, cursor="hand2")
            am.pack(side="left", padx=2)
            am.bind("<Button-1>", lambda _e, c=c_hex: self._definir_hex(c))

    def _gerar_barra_hue(self):
        alt = self.LADO_SV
        larg = self.LARG_H
        img = Image.new("RGB", (larg, alt))
        px = img.load()
        for y in range(alt):
            h = 1.0 - (y / float(max(1, alt - 1)))
            r, g, b = colorsys.hsv_to_rgb(h, 1.0, 1.0)
            cor = (int(round(r * 255)), int(round(g * 255)), int(round(b * 255)))
            for x in range(larg):
                px[x, y] = cor
        self._tk_hue = ImageTk.PhotoImage(img)

    def _gerar_campo_sv(self):
        h_norm = (self.h % 360.0) / 360.0
        if self._matiz_cache_img is not None and self._matiz_cache_val == round(h_norm, 4):
            return self._matiz_cache_img
        tam = 105
        img = Image.new("RGB", (tam, tam))
        px = img.load()
        for y in range(tam):
            v = 1.0 - (y / float(tam - 1))
            for x in range(tam):
                s = x / float(tam - 1)
                r, g, b = colorsys.hsv_to_rgb(h_norm, s, v)
                px[x, y] = (int(round(r * 255)), int(round(g * 255)), int(round(b * 255)))
        img_grande = img.resize((self.LADO_SV, self.LADO_SV), Image.BILINEAR)
        self._matiz_cache_val = round(h_norm, 4)
        self._matiz_cache_img = img_grande
        return img_grande

    def _rgb_atual(self):
        r, g, b = colorsys.hsv_to_rgb((self.h % 360.0) / 360.0, self.s / 100.0, self.v / 100.0)
        return (int(round(r * 255)), int(round(g * 255)), int(round(b * 255)))

    def _hex_atual(self):
        return _rgb_para_hex(*self._rgb_atual())

    def _atualizar_tudo(self):
        self._sincronizando = True
        try:
            # Desenha campo 2D S/B
            img_sv = self._gerar_campo_sv()
            self._tk_sv = ImageTk.PhotoImage(img_sv)
            self.canvas_sv.delete("all")
            self.canvas_sv.create_image(0, 0, image=self._tk_sv, anchor="nw")
            cx = int(round((self.s / 100.0) * (self.LADO_SV - 1)))
            cy = int(round((1.0 - self.v / 100.0) * (self.LADO_SV - 1)))
            cor_anel = "#000000" if (self.v > 65 and self.s < 50) else "#ffffff"
            self.canvas_sv.create_oval(cx - 6, cy - 6, cx + 6, cy + 6, outline=cor_anel, width=2)

            # Desenha barra Hue
            self.canvas_h.delete("all")
            self.canvas_h.create_image(0, 0, image=self._tk_hue, anchor="nw")
            hy = int(round((1.0 - (self.h % 360.0) / 360.0) * (self.LADO_SV - 1)))
            self.canvas_h.create_rectangle(0, max(0, hy - 2), self.LARG_H, min(self.LADO_SV, hy + 2),
                                           outline="#ffffff", width=2)

            r, g, b = self._rgb_atual()
            hex_cor = _rgb_para_hex(r, g, b)
            self.lbl_nova.configure(bg=hex_cor)
            self.vars_num["H"].set(int(round(self.h)) % 360)
            self.vars_num["S"].set(int(round(self.s)))
            self.vars_num["B"].set(int(round(self.v)))
            self.vars_num["R"].set(r)
            self.vars_num["G"].set(g)
            self.vars_num["B_rgb"].set(b)
            self.var_hex.set(hex_cor.lstrip("#").upper())
            if self.ao_mudar:
                self.ao_mudar(hex_cor)
        finally:
            self._sincronizando = False

    def _evento_sv(self, evento):
        x = max(0, min(self.LADO_SV - 1, evento.x))
        y = max(0, min(self.LADO_SV - 1, evento.y))
        self.s = (x / float(self.LADO_SV - 1)) * 100.0
        self.v = (1.0 - (y / float(self.LADO_SV - 1))) * 100.0
        self._atualizar_tudo()

    def _evento_h(self, evento):
        y = max(0, min(self.LADO_SV - 1, evento.y))
        self.h = (1.0 - (y / float(self.LADO_SV - 1))) * 360.0
        self._atualizar_tudo()

    def _digitou_num(self, chave):
        if self._sincronizando:
            return
        try:
            if chave in ("H", "S", "B"):
                self.h = max(0.0, min(360.0, float(self.vars_num["H"].get())))
                self.s = max(0.0, min(100.0, float(self.vars_num["S"].get())))
                self.v = max(0.0, min(100.0, float(self.vars_num["B"].get())))
            else:
                r = max(0, min(255, int(self.vars_num["R"].get())))
                g = max(0, min(255, int(self.vars_num["G"].get())))
                b = max(0, min(255, int(self.vars_num["B_rgb"].get())))
                h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
                self.h, self.s, self.v = h * 360.0, s * 100.0, v * 100.0
        except (tk.TclError, ValueError):
            return
        self._atualizar_tudo()

    def _digitou_hex(self):
        if self._sincronizando:
            return
        texto = self.var_hex.get().strip().lstrip("#")
        if len(texto) in (3, 6):
            self._definir_hex("#" + texto)

    def _definir_hex(self, hex_cor):
        r, g, b = _hex_para_rgb(hex_cor)
        h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
        self.h, self.s, self.v = h * 360.0, s * 100.0, v * 100.0
        self._atualizar_tudo()

    def _abrir_paleta_sistema(self):
        cor = colorchooser.askcolor(color=self._hex_atual(), parent=self)[1]
        if cor:
            self._definir_hex(cor)

    def iniciar_conta_gotas(self):
        """Oculta temporariamente o seletor, captura a tela inteira e retorna a cor clicada."""
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.withdraw()
        self.update_idletasks()

        def _depois(cor_capturada):
            if not self.winfo_exists():
                return
            self.deiconify()
            self.lift()
            self.grab_set()
            self.focus_force()
            if cor_capturada:
                self._definir_hex(cor_capturada)

        self.after(120, lambda: ContaGotasTela(self.pai, _depois))

    def confirmar(self):
        self.resultado = self._hex_atual()
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()

    def cancelar(self):
        self.resultado = None
        if self.ao_mudar:
            self.ao_mudar(self.cor_inicial)
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()


def escolher_cor_photoshop(pai, cor_inicial="#ffffff", titulo="Seletor de Cores (Photoshop)", ao_mudar=None):
    """Abre o Seletor de Cores estilo Photoshop modal e devolve '#rrggbb' ou None se cancelado."""
    dlg = DialogoCorPhotoshop(pai, cor_inicial=cor_inicial, titulo=titulo, ao_mudar=ao_mudar)
    pai.wait_window(dlg)
    return dlg.resultado
