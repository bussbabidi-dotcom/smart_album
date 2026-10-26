# -*- coding: utf-8 -*-
"""Tema escuro inspirado no Adobe Photoshop CC (grafite #181818 / #252526 com ícones e dicas rápidas)."""

import tkinter as tk
from tkinter import ttk

FUNDO = "#181818"
PAINEL = "#252526"
PAINEL_2 = "#323233"
PAINEL_3 = "#3e3e42"
BORDA = "#3c3c3c"
TEXTO = "#e6e6e6"
TEXTO_FRACO = "#969696"
DESTAQUE = "#0d99ff"
DESTAQUE_ESCURO = "#007acc"
VERDE = "#4cc38a"
ALERTA = "#e0a040"
ERRO = "#e05555"

FONTE = ("Segoe UI", 9)
FONTE_FORTE = ("Segoe UI Semibold", 9)
FONTE_TITULO = ("Segoe UI Semibold", 10)
FONTE_PEQUENA = ("Segoe UI", 8)
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
    estilo.configure("Barra.TFrame", background=PAINEL)

    estilo.configure("TLabel", background=PAINEL, foreground=TEXTO)
    estilo.configure("Fraco.TLabel", foreground=TEXTO_FRACO)
    estilo.configure("Fundo.TLabel", background=FUNDO, foreground=TEXTO_FRACO)
    estilo.configure("Titulo.TLabel", font=FONTE_TITULO)
    estilo.configure("Logo.TLabel", font=("Segoe UI Black", 12), foreground=DESTAQUE)

    estilo.configure("TButton", background=PAINEL_2, foreground=TEXTO, borderwidth=0, padding=(8, 4),
                     relief="flat")
    estilo.map("TButton", background=[("pressed", DESTAQUE_ESCURO), ("active", PAINEL_3), ("disabled", PAINEL)],
               foreground=[("pressed", "#ffffff"), ("disabled", TEXTO_FRACO)])

    estilo.configure("Primario.TButton", background=DESTAQUE, foreground="#ffffff", font=FONTE_FORTE,
                     padding=(8, 4))
    estilo.map("Primario.TButton", background=[("pressed", DESTAQUE_ESCURO), ("active", "#33adff"),
                                               ("disabled", PAINEL_3)])

    estilo.configure("Icone.TButton", padding=(6, 4), font=FONTE_ICONE, anchor="center", width=0)
    estilo.configure("Compacto.TButton", padding=(7, 3), width=0)
    estilo.configure("Mini.TButton", padding=(6, 3), anchor="center", font=FONTE_ICONE, width=0)
    estilo.configure("PrimarioCompacto.TButton", background=DESTAQUE, foreground="#ffffff", font=FONTE_FORTE,
                     padding=(8, 3), width=0)
    estilo.map("PrimarioCompacto.TButton", background=[("pressed", DESTAQUE_ESCURO), ("active", "#33adff"),
                                                       ("disabled", PAINEL_3)])

    estilo.configure("Compacto.TMenubutton", background=PAINEL_2, foreground=TEXTO, borderwidth=0, padding=(7, 3),
                     relief="flat", arrowcolor=TEXTO_FRACO, width=0)
    estilo.map("Compacto.TMenubutton", background=[("active", PAINEL_3)])
    estilo.configure("Compacto.TCheckbutton", padding=(4, 2))
    estilo.configure("PrimarioCompacto.TMenubutton", background=DESTAQUE, foreground="#ffffff", font=FONTE_FORTE,
                     arrowcolor="#ffffff", borderwidth=0, relief="flat", padding=(8, 3), width=0)
    estilo.map("PrimarioCompacto.TMenubutton", background=[("active", "#33adff")])

    estilo.configure("Alternar.Toolbutton", background=PAINEL_2, foreground=TEXTO, borderwidth=0, padding=(7, 3),
                     relief="flat", anchor="center", width=0)
    estilo.map("Alternar.Toolbutton", background=[("selected", DESTAQUE), ("active", PAINEL_3)],
               foreground=[("selected", "#ffffff")])

    estilo.configure("TMenubutton", background=PAINEL_2, foreground=TEXTO, borderwidth=0, padding=(8, 4),
                     relief="flat", arrowcolor=TEXTO_FRACO)
    estilo.map("TMenubutton", background=[("active", PAINEL_3)])
    estilo.configure("Primario.TMenubutton", background=DESTAQUE, foreground="#ffffff", font=FONTE_FORTE,
                     arrowcolor="#ffffff")
    estilo.map("Primario.TMenubutton", background=[("active", "#33adff")])

    estilo.configure("TCheckbutton", background=PAINEL, foreground=TEXTO, indicatorbackground=PAINEL_2,
                     indicatorforeground=DESTAQUE)
    estilo.map("TCheckbutton", background=[("active", PAINEL)], indicatorbackground=[("selected", PAINEL_2)])
    estilo.configure("TRadiobutton", background=PAINEL, foreground=TEXTO, indicatorbackground=PAINEL_2,
                     indicatorforeground=DESTAQUE)
    estilo.map("TRadiobutton", background=[("active", PAINEL)])
    estilo.configure("Filtro.TRadiobutton", background=PAINEL_2, foreground=TEXTO_FRACO, padding=(7, 3),
                     indicatorsize=0, indicatormargin=0)
    estilo.map("Filtro.TRadiobutton", background=[("selected", DESTAQUE_ESCURO), ("active", PAINEL_3)],
               foreground=[("selected", "#ffffff")])

    estilo.configure("TEntry", fieldbackground=FUNDO, foreground=TEXTO, borderwidth=1)
    estilo.configure("TSpinbox", fieldbackground=FUNDO, foreground=TEXTO, arrowcolor=TEXTO_FRACO, background=PAINEL_2)
    estilo.configure("TCombobox", fieldbackground=FUNDO, foreground=TEXTO, arrowcolor=TEXTO_FRACO, background=PAINEL_2)
    estilo.map("TCombobox", fieldbackground=[("readonly", FUNDO)], foreground=[("readonly", TEXTO)],
               selectbackground=[("readonly", FUNDO)], selectforeground=[("readonly", TEXTO)])

    estilo.configure("TNotebook", background=PAINEL, borderwidth=0, tabmargins=(2, 2, 2, 0))
    estilo.configure("TNotebook.Tab", background=FUNDO, foreground=TEXTO_FRACO, padding=(10, 4), borderwidth=0)
    estilo.map("TNotebook.Tab", background=[("selected", PAINEL)], foreground=[("selected", TEXTO)])

    estilo.configure("TLabelframe", background=PAINEL, bordercolor=BORDA)
    estilo.configure("TLabelframe.Label", background=PAINEL, foreground=TEXTO_FRACO, font=FONTE_PEQUENA)

    estilo.configure("Horizontal.TProgressbar", background=DESTAQUE, troughcolor=FUNDO, borderwidth=0,
                     lightcolor=DESTAQUE, darkcolor=DESTAQUE)
    estilo.configure("Horizontal.TScale", background=PAINEL, troughcolor=FUNDO)
    estilo.configure("TScrollbar", background=PAINEL_2, troughcolor=FUNDO, arrowcolor=TEXTO_FRACO, borderwidth=0)
    estilo.map("TScrollbar", background=[("active", PAINEL_3)])
    estilo.configure("TPanedwindow", background=FUNDO)
    estilo.configure("Sash", sashthickness=3)
    return estilo


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
