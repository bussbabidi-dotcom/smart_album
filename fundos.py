# -*- coding: utf-8 -*-
"""
Pasta de fundos: a galeria de imagens de fundo da aba Fundo e a janela de
ajuste de cor do fundo (brilho, contraste, saturação, matiz e tingir).

Os ajustes nunca mexem no arquivo da imagem: ficam em fundo_imagem["ajustes"]
e são aplicados no desenho (composicao.ajustar_cores).
"""

import threading
import tkinter as tk
from collections import OrderedDict
from pathlib import Path
from tkinter import colorchooser, ttk

from PIL import ImageTk

import analise
import tema
from comum import cor_hex, gravar_json, ler_json, normalizar_caminho
from composicao import AJUSTES_FUNDO_PADRAO

LIMITE_RECENTES = 8
LARGURA_MINIMA_CELULA = 96


def ler_config(arquivo):
    dados = ler_json(arquivo, {})
    if not isinstance(dados, dict):
        dados = {}
    dados.setdefault("pasta", "")
    dados["recentes"] = [p for p in dados.get("recentes") or [] if isinstance(p, str)]
    return dados


def lembrar_pasta(arquivo, pasta):
    """Guarda a pasta escolhida como padrão e no topo das recentes."""
    dados = ler_config(arquivo)
    pasta = str(pasta)
    dados["pasta"] = pasta
    dados["recentes"] = [pasta] + [p for p in dados["recentes"] if normalizar_caminho(p) != normalizar_caminho(pasta)]
    dados["recentes"] = dados["recentes"][:LIMITE_RECENTES]
    try:
        gravar_json(arquivo, dados)
    except OSError:
        pass
    return dados


def listar_fundos(pasta):
    """Imagens da pasta e das subpastas, em ordem natural."""
    if not pasta or not Path(pasta).is_dir():
        return []
    return [str(f) for f in analise.listar_imagens(pasta)]


class Galeria:
    """
    Grade de miniaturas com rolagem. Só desenha as linhas visíveis; as miniaturas
    são feitas numa thread (cache de miniaturas do programa) e guardadas prontas.
    """

    def __init__(self, pai, atual, ao_pressionar, ao_duplo, ao_menu):
        self.atual = atual
        self.ao_pressionar = ao_pressionar
        self.ao_duplo = ao_duplo
        self.ao_menu = ao_menu
        self.caminhos = []
        self.quadro = ttk.Frame(pai)
        barra = ttk.Scrollbar(self.quadro, orient="vertical")
        barra.pack(side="right", fill="y")
        self.canvas = tk.Canvas(self.quadro, bg=tema.PAINEL_2, highlightthickness=0, height=150, yscrollincrement=20)
        self.canvas.pack(side="left", fill="both", expand=True)
        barra.configure(command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=lambda a, b: (barra.set(a, b), self._agendar()))
        self.canvas.bind("<Configure>", lambda e: self._agendar())
        self.canvas.bind("<MouseWheel>", lambda e: self.canvas.yview_scroll(-int(e.delta / 40), "units"))
        self.canvas.bind("<ButtonPress-1>", self._pressionou)
        self.canvas.bind("<Double-Button-1>", self._duplo)
        self.canvas.bind("<Button-3>", self._menu)
        self._tk = OrderedDict()
        self._pendentes = OrderedDict()
        self._prontas = []
        self._trava = threading.Condition()
        self._agendado = None
        self._medidas = (110, 96, 74)
        threading.Thread(target=self._trabalhar, daemon=True).start()
        self.canvas.after(80, self._receber)

    def pack(self, **opcoes):
        self.quadro.pack(**opcoes)

    def definir(self, caminhos):
        self.caminhos = list(caminhos)
        with self._trava:
            self._pendentes.clear()
        self.canvas.yview_moveto(0)
        self.desenhar()

    def medidas(self):
        """(colunas, largura da célula, altura da célula, altura da miniatura)."""
        largura = max(LARGURA_MINIMA_CELULA * 2, self.canvas.winfo_width())
        colunas = max(2, min(4, largura // LARGURA_MINIMA_CELULA))
        cw = largura // colunas
        th = int(cw * 0.66)
        return colunas, cw, th + 22, th

    def _agendar(self):
        if self._agendado is None:
            self._agendado = self.canvas.after(15, self.desenhar)

    def desenhar(self):
        self._agendado = None
        c = self.canvas
        c.delete("all")
        colunas, cw, ch, th = self.medidas()
        linhas = (len(self.caminhos) + colunas - 1) // colunas
        c.configure(scrollregion=(0, 0, colunas * cw, max(1, linhas * ch)))
        if not self.caminhos:
            c.create_text(10, 16, anchor="nw", fill=tema.TEXTO_FRACO, width=max(120, c.winfo_width() - 20),
                          text="Escolha uma pasta com imagens de fundo (botão 'Pasta de fundos...').")
            return
        y0 = c.canvasy(0)
        primeira = max(0, int(y0 // ch))
        ultima = min(linhas, int((y0 + c.winfo_height()) // ch) + 1)
        atual = self.atual()
        letras = max(8, (cw - 8) // 6)
        for linha in range(primeira, ultima):
            for coluna in range(colunas):
                indice = linha * colunas + coluna
                if indice >= len(self.caminhos):
                    break
                caminho = self.caminhos[indice]
                x, y = coluna * cw, linha * ch
                if atual and normalizar_caminho(caminho) == atual:
                    c.create_rectangle(x + 1, y + 1, x + cw - 1, y + ch - 1, fill=tema.DESTAQUE_ESCURO,
                                       outline=tema.DESTAQUE, width=2, tags="atual")
                imagem = self._tk.get((caminho, cw))
                cx, cy = x + cw // 2, y + 4 + th // 2
                if imagem is not None:
                    self._tk.move_to_end((caminho, cw))
                    c.create_image(cx, cy, image=imagem, tags="miniatura")
                else:
                    c.create_rectangle(x + 6, y + 4, x + cw - 6, y + 4 + th, fill=tema.PAINEL_3, outline="")
                    c.create_text(cx, cy, text="...", fill=tema.TEXTO_FRACO)
                    self._pedir(caminho, cw - 10, th)
                nome = Path(caminho).name
                c.create_text(cx, y + th + 13, text=nome if len(nome) <= letras else nome[:letras - 1] + "…",
                              fill=tema.TEXTO if atual and normalizar_caminho(caminho) == atual else tema.TEXTO_FRACO,
                              font=tema.FONTE_PEQUENA)

    def indice_em(self, x, y):
        colunas, cw, ch, _th = self.medidas()
        coluna, linha = int(x // cw), int(self.canvas.canvasy(y) // ch)
        indice = linha * colunas + coluna
        if 0 <= coluna < colunas and 0 <= indice < len(self.caminhos):
            return indice
        return None

    def caminho_em(self, x, y):
        indice = self.indice_em(x, y)
        return self.caminhos[indice] if indice is not None else None

    def _pressionou(self, evento):
        caminho = self.caminho_em(evento.x, evento.y)
        if caminho:
            self.ao_pressionar(caminho, evento)

    def _duplo(self, evento):
        caminho = self.caminho_em(evento.x, evento.y)
        if caminho:
            self.ao_duplo(caminho)

    def _menu(self, evento):
        caminho = self.caminho_em(evento.x, evento.y)
        if caminho:
            self.ao_menu(caminho, evento)

    def _pedir(self, caminho, largura, altura):
        with self._trava:
            self._pendentes.pop(caminho, None)
            self._pendentes[caminho] = (largura, altura)
            self._trava.notify()

    def _trabalhar(self):
        while True:
            with self._trava:
                while not self._pendentes:
                    self._trava.wait()
                caminho, (largura, altura) = self._pendentes.popitem(last=True)
            try:
                img = analise.miniatura(caminho).copy()
                img.thumbnail((largura, altura))
            except Exception:
                continue
            with self._trava:
                self._prontas.append((caminho, largura + 10, img))

    def _receber(self):
        with self._trava:
            prontas, self._prontas = self._prontas, []
        for caminho, cw, img in prontas:
            self._tk[(caminho, cw)] = ImageTk.PhotoImage(img)
            while len(self._tk) > 600:
                self._tk.popitem(last=False)
        if prontas:
            self._agendar()
        try:
            self.canvas.after(80, self._receber)
        except tk.TclError:
            pass


class EditorFundo(tk.Toplevel):
    """Ajuste de cor do fundo da lâmina aberta (ou da capa), com prévia na hora."""

    CAMPOS = (("brilho", "Brilho", -100, 100), ("contraste", "Contraste", -100, 100),
              ("saturacao", "Saturação", -100, 100), ("matiz", "Matiz (cor)", -180, 180))

    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.title("Ajustar fundo")
        self.configure(bg=tema.PAINEL)
        self.transient(app.root)
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self.fechar)
        self.bind("<Escape>", lambda e: self.fechar())
        self.bind("<Control-z>", lambda e: self.app.desfazer())
        self.bind("<Control-y>", lambda e: self.app.refazer())
        self._carregando = False
        quadro = ttk.Frame(self, padding=12)
        quadro.pack(fill="both", expand=True)
        self.rotulo = ttk.Label(quadro, text="", style="Titulo.TLabel")
        self.rotulo.pack(anchor="w")
        self.rotulo_arquivo = ttk.Label(quadro, text="", style="Fraco.TLabel")
        self.rotulo_arquivo.pack(anchor="w", pady=(0, 8))
        self.vars = {}
        self.rotulos = {}
        self._controles = []
        campos = self.CAMPOS + (("opacidade", "Opacidade", 0, 100), ("desfoque", "Desfoque", 0, 50))
        for chave, texto, de, ate in campos:
            self._escala(quadro, chave, texto, de, ate)
        tingir = ttk.LabelFrame(quadro, text="Tingir com cor", padding=6)
        tingir.pack(fill="x", pady=(8, 0))
        linha = ttk.Frame(tingir)
        linha.pack(fill="x")
        self.amostra = tk.Label(linha, width=4, bg="#c08a4a", relief="flat", cursor="hand2")
        self.amostra.pack(side="left", padx=(0, 6))
        self.amostra.bind("<Button-1>", lambda e: self.escolher_cor())
        botao_cor = ttk.Button(linha, text="Escolher cor...", style="Compacto.TButton", command=self.escolher_cor)
        botao_cor.pack(side="left")
        self._controles.append(botao_cor)
        self._escala(tingir, "tingir_forca", "Intensidade", 0, 100)
        botoes = ttk.Frame(quadro)
        botoes.pack(fill="x", pady=(12, 0))
        for texto, comando, estilo in (("Zerar", self.zerar, "Compacto.TButton"),
                                       ("Aplicar em todas as lâminas", self.aplicar_todas, "Compacto.TButton"),
                                       ("OK", self.fechar, "PrimarioCompacto.TButton")):
            botao = ttk.Button(botoes, text=texto, style=estilo, command=comando)
            botao.pack(side="left" if texto != "OK" else "right", padx=(0, 4))
            if texto != "OK":
                self._controles.append(botao)
        ttk.Label(quadro, text="Os ajustes não mudam o arquivo da imagem: valem só no álbum.",
                  style="Fraco.TLabel").pack(anchor="w", pady=(8, 0))
        self.mostrar()
        self.update_idletasks()
        lateral = getattr(app, "quadro_lateral", None)
        if lateral is not None and lateral.winfo_ismapped() and lateral.winfo_rootx() > app.canvas.winfo_rootx():
            x = lateral.winfo_rootx() + lateral.winfo_width() - self.winfo_reqwidth()
            y = lateral.winfo_rooty() + 30
        else:
            x, y = app.canvas.winfo_rootx() + 20, app.canvas.winfo_rooty() + 20
        self.geometry("+%d+%d" % (max(0, x), max(0, y)))

    def _escala(self, pai, chave, texto, de, ate):
        linha = ttk.Frame(pai)
        linha.pack(fill="x", pady=2)
        ttk.Label(linha, text=texto, width=12).pack(side="left")
        var = tk.DoubleVar(value=0)
        rotulo = ttk.Label(linha, text="0", style="Fraco.TLabel", width=5, anchor="e")
        rotulo.pack(side="right")
        escala = ttk.Scale(linha, from_=de, to=ate, variable=var, length=220, command=lambda v, c=chave: self._mudou(c))
        escala.pack(side="right", padx=6)
        escala.bind("<ButtonPress-1>", lambda e: self.app.marcar_historico())
        escala.bind("<ButtonRelease-1>", lambda e: self.app.fundo_ajustado(final=True))
        escala.bind("<Double-Button-1>", lambda e, c=chave: self._zerar_um(c))
        self.vars[chave] = var
        self.rotulos[chave] = rotulo
        self._controles.append(escala)

    def _dados(self):
        dono = self.app._dono_do_fundo()
        return dono, (dono or {}).get("fundo_imagem")

    def mostrar(self):
        dono, dados = self._dados()
        if self.app.atual == "capa":
            self.rotulo.configure(text="Fundo da capa")
        elif dono is not None:
            self.rotulo.configure(text="Fundo da lâmina %s" % (self.app.atual + 1))
        self._carregando = True
        try:
            ajustes = dict(AJUSTES_FUNDO_PADRAO)
            ajustes.update((dados or {}).get("ajustes") or {})
            for chave, var in self.vars.items():
                if chave in ("opacidade", "desfoque"):
                    valor = (dados or {}).get(chave, 100 if chave == "opacidade" else 0)
                else:
                    valor = ajustes.get(chave, 0)
                var.set(valor)
                self.rotulos[chave].configure(text="%d" % round(float(valor)))
            self.amostra.configure(bg=cor_hex(ajustes["tingir_cor"]))
            if dados:
                self.rotulo_arquivo.configure(text=Path(dados["caminho"]).name)
            else:
                self.rotulo_arquivo.configure(text="Sem imagem de fundo: clique numa imagem da galeria primeiro.")
            for controle in self._controles:
                controle.state(["!disabled"] if dados else ["disabled"])
        finally:
            self._carregando = False

    def _mudou(self, chave):
        valor = int(round(self.vars[chave].get()))
        self.rotulos[chave].configure(text="%d" % valor)
        if self._carregando:
            return
        dono, dados = self._dados()
        if not dados:
            return
        if chave in ("opacidade", "desfoque"):
            if dados.get(chave) == valor:
                return
            dados[chave] = valor
        else:
            ajustes = dados.setdefault("ajustes", {})
            if ajustes.get(chave, AJUSTES_FUNDO_PADRAO[chave]) == valor:
                return
            ajustes[chave] = valor
        self.app.fundo_ajustado()

    def _zerar_um(self, chave):
        dono, dados = self._dados()
        if not dados:
            return
        self.app.marcar_historico()
        if chave in ("opacidade", "desfoque"):
            dados[chave] = 100 if chave == "opacidade" else 0
        else:
            dados.setdefault("ajustes", {})[chave] = AJUSTES_FUNDO_PADRAO[chave]
        self.mostrar()
        self.app.fundo_ajustado(final=True)

    def zerar(self):
        dono, dados = self._dados()
        if not dados:
            return
        self.app.marcar_historico()
        dados["ajustes"] = {}
        self.mostrar()
        self.app.fundo_ajustado(final=True)

    def escolher_cor(self):
        dono, dados = self._dados()
        if not dados:
            return
        ajustes = dados.get("ajustes") or {}
        cor = colorchooser.askcolor(color=cor_hex(ajustes.get("tingir_cor", AJUSTES_FUNDO_PADRAO["tingir_cor"])),
                                    parent=self)[1]
        if not cor:
            return
        self.definir_tinta(cor)

    def definir_tinta(self, cor, forca=None):
        dono, dados = self._dados()
        if not dados:
            return
        self.app.marcar_historico()
        ajustes = dados.setdefault("ajustes", {})
        ajustes["tingir_cor"] = cor_hex(cor)
        if forca is not None:
            ajustes["tingir_forca"] = int(forca)
        elif not ajustes.get("tingir_forca"):
            ajustes["tingir_forca"] = 50
        self.mostrar()
        self.app.fundo_ajustado(final=True)

    def aplicar_todas(self):
        self.app.fundo_imagem_em_todas()

    def fechar(self):
        self.app.editor_fundo = None
        self.destroy()
