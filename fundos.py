# -*- coding: utf-8 -*-
"""
Pasta de fundos: a galeria de imagens de fundo da aba Fundo e a janela de
ajuste de cor do fundo (brilho, contraste, saturação, matiz e tingir com conta-gotas de tela).

Os ajustes nunca mexem no arquivo da imagem: ficam exclusivamente no fundo
daquela lâmina/página (fundo_imagem["ajustes"]) e não passam para outros fundos
ao trocar a imagem, a menos que o usuário clique em Aplicar nas Selecionadas / Todas.
"""

import threading
import tkinter as tk
from collections import OrderedDict
from pathlib import Path
from tkinter import ttk

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
        self.canvas = tk.Canvas(self.quadro, bg=tema.FUNDO, highlightthickness=1, highlightbackground=tema.BORDA,
                                height=150, yscrollincrement=20)
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
                    c.create_rectangle(x + 6, y + 4, x + cw - 6, y + 4 + th, fill=tema.PAINEL_2, outline=tema.BORDA)
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
    """
    Painel de Ajuste de Fundo e Colorização estilo Adobe Photoshop CC:
    - Luz e Cor (Brilho, Contraste, Saturação, Matiz, Opacidade e Desfoque)
    - Tingir com Cor (Colorize): Seletor Photoshop HSB/RGB/Hex + Conta-gotas de Tela Cheia
    - Edições isoladas somente neste fundo, com opção de aplicar nas lâminas selecionadas ou em todas
    """

    CAMPOS = (
        ("brilho", "Brilho", -100, 100),
        ("contraste", "Contraste", -100, 100),
        ("saturacao", "Saturação", -100, 100),
        ("matiz", "Matiz (Hue)", -180, 180),
    )

    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.title("Propriedades do Fundo · Photoshop CC")
        self.configure(bg=tema.PAINEL)
        self.transient(app.root)
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self.fechar)
        self.bind("<Escape>", lambda e: self.fechar())
        self.bind("<Control-z>", lambda e: self.app.desfazer())
        self.bind("<Control-y>", lambda e: self.app.refazer())
        self._carregando = False

        _faixa, self.rotulo, self.rotulo_arquivo = tema.criar_cabecalho_janela(
            self,
            "Ajustes de Cor e Tingimento do Fundo",
            "Edições exclusivas deste fundo (não passam para outros ao trocar a imagem)",
            icone="◐",
        )

        quadro = ttk.Frame(self, padding=12)
        quadro.pack(fill="both", expand=True)

        self.vars = {}
        self.rotulos = {}
        self._controles = []

        # Seção 1: Ajustes de Luz, Cor e Presença
        sec_luz = ttk.LabelFrame(quadro, text="Matiz / Saturação / Luz e Desfoque", padding=8)
        sec_luz.pack(fill="x", pady=(0, 8))
        campos = self.CAMPOS + (("opacidade", "Opacidade (%)", 0, 100), ("desfoque", "Desfoque (px)", 0, 50))
        for chave, texto, de, ate in campos:
            self._escala(sec_luz, chave, texto, de, ate)

        # Seção 2: Tingir com Cor (Colorize) + Conta-gotas de Tela + Seletor Photoshop
        tingir = ttk.LabelFrame(quadro, text="Tingir com uma Cor (Colorize · Photoshop)", padding=8)
        tingir.pack(fill="x", pady=(0, 8))

        linha_cor = ttk.Frame(tingir)
        linha_cor.pack(fill="x", pady=(0, 6))

        borda_amostra = tk.Frame(linha_cor, bg=tema.BORDA_CLARA, padx=1, pady=1)
        borda_amostra.pack(side="left", padx=(0, 8))
        self.amostra = tk.Label(borda_amostra, width=5, height=2, bg="#c08a4a", relief="flat", cursor="hand2")
        self.amostra.pack()
        self.amostra.bind("<Button-1>", lambda e: self.escolher_cor())
        tema.dica(self.amostra, "Clique para abrir o Seletor de Cores estilo Photoshop (HSB/RGB/Hex)")

        col_info_cor = ttk.Frame(linha_cor)
        col_info_cor.pack(side="left", fill="x", expand=True)

        linha_hex = ttk.Frame(col_info_cor)
        linha_hex.pack(fill="x", pady=(0, 3))
        ttk.Label(linha_hex, text="Cor ativa:", style="Fraco.TLabel").pack(side="left")
        self.rotulo_hex = ttk.Label(linha_hex, text="#C08A4A", font=tema.FONTE_MONO)
        self.rotulo_hex.pack(side="left", padx=(6, 0))

        linha_bts_cor = ttk.Frame(col_info_cor)
        linha_bts_cor.pack(fill="x")
        botao_cor = ttk.Button(
            linha_bts_cor,
            text="🎨 Seletor de Cor...",
            style="Compacto.TButton",
            command=self.escolher_cor,
        )
        botao_cor.pack(side="left", fill="x", expand=True, padx=(0, 4))
        tema.dica(botao_cor, "Abre o seletor de cores completo estilo Adobe Photoshop (HSB / RGB / Hex)")

        botao_gotas = ttk.Button(
            linha_bts_cor,
            text="🎯 Capturar da Tela",
            style="PrimarioCompacto.TButton",
            command=self.capturar_cor_da_tela,
        )
        botao_gotas.pack(side="left", fill="x", expand=True)
        tema.dica(botao_gotas, "Ativa o conta-gotas com lupa para capturar qualquer pixel visível na tela (fotos do álbum, barra ou outro app)")
        self._controles.extend([botao_cor, botao_gotas])

        # Amostras rápidas para tingimento instantâneo
        linha_chips = ttk.Frame(tingir)
        linha_chips.pack(fill="x", pady=(2, 6))
        ttk.Label(linha_chips, text="Tons:", style="Fraco.TLabel").pack(side="left", padx=(0, 6))
        for hex_chip in ("#c08a4a", "#d4a373", "#8d5b4c", "#457b9d", "#1d3557", "#2a9d8f", "#6b705c", "#3a3a3c"):
            chip = tk.Label(linha_chips, bg=hex_chip, width=2, height=1, relief="solid", bd=1, cursor="hand2")
            chip.pack(side="left", padx=2)
            chip.bind("<Button-1>", lambda _e, c=hex_chip: self.definir_tinta(c))

        self._escala(tingir, "tingir_forca", "Intensidade", 0, 100)

        # Rodapé de ações (Zerar, Aplicar nas selecionadas, Aplicar em todas, OK)
        botoes = ttk.Frame(quadro)
        botoes.pack(fill="x", pady=(6, 0))

        bt_zerar = ttk.Button(botoes, text="↺ Zerar", style="Compacto.TButton", command=self.zerar)
        bt_zerar.pack(side="left", padx=(0, 4))
        self._controles.append(bt_zerar)

        self.botao_selecionadas = ttk.Button(
            botoes,
            text="Aplicar nas selecionadas",
            style="Compacto.TButton",
            command=self.aplicar_selecionadas,
        )
        self.botao_selecionadas.pack(side="left", padx=(0, 4))
        tema.dica(
            self.botao_selecionadas,
            "Copia este fundo e todas as suas configurações para as lâminas selecionadas na linha do tempo (Ctrl+Clique ou Shift+Clique)",
        )
        self._controles.append(self.botao_selecionadas)

        bt_todas = ttk.Button(botoes, text="Aplicar em todas", style="Compacto.TButton", command=self.aplicar_todas)
        bt_todas.pack(side="left", padx=(0, 4))
        self._controles.append(bt_todas)

        ttk.Button(botoes, text="OK", style="PrimarioCompacto.TButton", command=self.fechar).pack(side="right")

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
        rot = ttk.Label(linha, text=texto, width=14)
        rot.pack(side="left")
        tema.dica(rot, "Duplo clique na barra para restaurar o valor padrão")
        var = tk.DoubleVar(value=0)
        rotulo = ttk.Label(linha, text="0", style="Fraco.TLabel", width=5, anchor="e", font=tema.FONTE_MONO)
        rotulo.pack(side="right")
        escala = ttk.Scale(linha, from_=de, to=ate, variable=var, length=220, command=lambda v, c=chave: self._mudou(c))
        escala.pack(side="right", fill="x", expand=True, padx=6)
        escala.bind("<ButtonPress-1>", lambda e: self.app.marcar_historico())
        escala.bind("<ButtonRelease-1>", lambda e: self.app.fundo_ajustado(final=True))
        escala.bind("<Double-Button-1>", lambda e, c=chave: self._zerar_um(c))
        self.vars[chave] = var
        self.rotulos[chave] = rotulo
        self._controles.append(escala)

    def _dados(self):
        dono = self.app._dono_do_fundo()
        if hasattr(self.app, "_dados_fundo_imagem_ativo"):
            return dono, self.app._dados_fundo_imagem_ativo(dono)
        return dono, (dono or {}).get("fundo_imagem")

    def mostrar(self):
        dono, dados = self._dados()
        alvo = getattr(self.app, "fundo_alvo", None)
        nome_alvo = {"lamina": "Lâmina inteira", "esquerda": "Página Esquerda", "direita": "Página Direita"}.get(
            alvo.get() if alvo is not None else "lamina", "Lâmina inteira"
        )
        if self.app.atual == "capa":
            self.rotulo.configure(text="Fundo da Capa")
        elif dono is not None:
            self.rotulo.configure(text="Fundo da Lâmina %s · %s" % (self.app.atual + 1, nome_alvo))
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
            cor_t = cor_hex(ajustes["tingir_cor"])
            self.amostra.configure(bg=cor_t)
            self.rotulo_hex.configure(text=cor_t.upper())
            if dados and dados.get("caminho"):
                self.rotulo_arquivo.configure(
                    text="Arquivo: %s  (edições salvas somente neste fundo)" % Path(dados["caminho"]).name
                )
            else:
                self.rotulo_arquivo.configure(
                    text="Fundo sem imagem: Tingir com Cor altera a cor do fundo diretamente."
                )
            qtd_sel = len(getattr(self.app, "laminas_sel", set()) or ([self.app.atual] if isinstance(self.app.atual, int) else []))
            self.botao_selecionadas.configure(
                text="Aplicar nas selecionadas (%d)" % max(1, qtd_sel)
            )
            for controle in self._controles:
                controle.state(["!disabled"] if dono is not None else ["disabled"])
        finally:
            self._carregando = False

    def _aplicar_cor_sem_imagem(self, dono, cor_tinta, forca):
        if dono is None:
            return
        ch_cor, _ch_img = self.app._chaves_fundo_alvo() if hasattr(self.app, "_chaves_fundo_alvo") else ("fundo", "fundo_imagem")
        f = max(0.0, min(100.0, float(forca if forca is not None else 100.0))) / 100.0
        cr, cg, cb = cor_rgb(cor_tinta)
        # Mistura suave do branco/base com a cor escolhida conforme a intensidade
        r = int(round(255 * (1.0 - f) + cr * f))
        g = int(round(255 * (1.0 - f) + cg * f))
        b = int(round(255 * (1.0 - f) + cb * f))
        dono[ch_cor] = "#%02x%02x%02x" % (max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b)))
        dono["_tingir_sem_img"] = {"tingir_cor": cor_hex(cor_tinta), "tingir_forca": int(round(f * 100))}

    def _mudou(self, chave):
        valor = int(round(self.vars[chave].get()))
        self.rotulos[chave].configure(text="%d" % valor)
        if self._carregando:
            return
        dono, dados = self._dados()
        if dono is None:
            return
        if not dados or not dados.get("caminho"):
            if chave == "tingir_forca":
                info_s = dono.get("_tingir_sem_img") or {}
                cor_atual = info_s.get("tingir_cor") or self.amostra.cget("bg") or AJUSTES_FUNDO_PADRAO["tingir_cor"]
                self._aplicar_cor_sem_imagem(dono, cor_atual, valor)
                self.app.fundo_ajustado()
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
        if dono is None:
            return
        self.app.marcar_historico()
        if dados:
            dados["ajustes"] = {}
            dados["opacidade"] = 100
            dados["desfoque"] = 0
        else:
            ch_cor, _ = self.app._chaves_fundo_alvo() if hasattr(self.app, "_chaves_fundo_alvo") else ("fundo", "fundo_imagem")
            dono[ch_cor] = "#ffffff"
            dono.pop("_tingir_sem_img", None)
        self.mostrar()
        self.app.fundo_ajustado(final=True)

    def escolher_cor(self):
        dono, dados = self._dados()
        if dono is None:
            return
        ajustes = (dados.get("ajustes") if dados else dono.get("_tingir_sem_img")) or {}
        cor_ini = cor_hex(ajustes.get("tingir_cor", AJUSTES_FUNDO_PADRAO["tingir_cor"]))
        forca_ini = ajustes.get("tingir_forca", 0)
        self.app.marcar_historico()

        def _previa_cor(nova_cor):
            nova_hex = cor_hex(nova_cor)
            if dados and dados.get("caminho"):
                aj = dados.setdefault("ajustes", {})
                aj["tingir_cor"] = nova_hex
                if not aj.get("tingir_forca"):
                    aj["tingir_forca"] = 65
                f_atual = aj["tingir_forca"]
            else:
                info_s = dono.get("_tingir_sem_img") or {}
                f_atual = info_s.get("tingir_forca") or 80
                self._aplicar_cor_sem_imagem(dono, nova_hex, f_atual)
            self.amostra.configure(bg=nova_hex)
            self.rotulo_hex.configure(text=nova_hex.upper())
            if "tingir_forca" in self.vars:
                self._carregando = True
                try:
                    self.vars["tingir_forca"].set(f_atual)
                    self.rotulos["tingir_forca"].configure(text="%d" % int(round(float(f_atual))))
                finally:
                    self._carregando = False
            self.app.fundo_ajustado()

        cor = tema.escolher_cor_photoshop(
            self,
            cor_inicial=cor_ini,
            titulo="Tingir Fundo · Seletor Photoshop",
            ao_mudar=_previa_cor,
        )
        if not cor:
            # Restaura caso cancelado
            if dados and dados.get("caminho"):
                aj = dados.setdefault("ajustes", {})
                aj["tingir_cor"] = cor_ini
                aj["tingir_forca"] = forca_ini
            elif forca_ini:
                self._aplicar_cor_sem_imagem(dono, cor_ini, forca_ini)
            self.mostrar()
            self.app.fundo_ajustado(final=True)
            return
        self.definir_tinta(cor, marcar=False)

    def capturar_cor_da_tela(self):
        """Ativa o conta-gotas em tela cheia para capturar qualquer pixel da tela."""
        dono, _dados = self._dados()
        if dono is None:
            return
        self.withdraw()
        self.update_idletasks()

        def _ao_capturar(cor_hex_capturada):
            if not self.winfo_exists():
                return
            self.deiconify()
            self.lift()
            self.focus_force()
            if cor_hex_capturada:
                self.definir_tinta(cor_hex_capturada)

        self.after(120, lambda: tema.ContaGotasTela(self.app.root, _ao_capturar))

    def definir_tinta(self, cor, forca=None, marcar=True):
        dono, dados = self._dados()
        if dono is None:
            return
        if marcar:
            self.app.marcar_historico()
        cor_limpa = cor_hex(cor)
        if dados and dados.get("caminho"):
            ajustes = dados.setdefault("ajustes", {})
            ajustes["tingir_cor"] = cor_limpa
            if forca is not None:
                ajustes["tingir_forca"] = int(forca)
            elif not ajustes.get("tingir_forca"):
                ajustes["tingir_forca"] = 65
        else:
            info_s = dono.get("_tingir_sem_img") or {}
            f_val = int(forca) if forca is not None else (info_s.get("tingir_forca") or 80)
            self._aplicar_cor_sem_imagem(dono, cor_limpa, f_val)
        self.mostrar()
        self.app.fundo_ajustado(final=True)

    def aplicar_selecionadas(self):
        if hasattr(self.app, "fundo_em_selecionadas"):
            self.app.fundo_em_selecionadas()

    def aplicar_todas(self):
        self.app.fundo_imagem_em_todas()

    def fechar(self):
        self.app.editor_fundo = None
        self.destroy()
