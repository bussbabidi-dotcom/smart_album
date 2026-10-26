# -*- coding: utf-8 -*-
"""
Programa de diagramação de álbuns.

Janela no estilo dos programas de álbum: lâmina aberta no centro, painel
de fotos e linha do tempo das lâminas embaixo, painel de design à direita.
Fotos e pastas podem ser arrastadas do Explorer, coladas com Ctrl+V ou
passadas ao abrir o programa (arrastar a pasta em cima do iniciar.bat).
"""

import hashlib
import json
import math
import os
import queue
import re
import sys
import threading
import time
import traceback
import tkinter as tk
from collections import OrderedDict
from pathlib import Path
from tkinter import colorchooser, filedialog, messagebox, simpledialog, ttk

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PIL import Image, ImageEnhance, ImageTk

import analise
import atalho_capa
import diagramar
import entrada
import exportar
import modelos_lamina
import photoshop
import projeto as proj
import recorte
import tema
from comum import EXTENSOES, cor_hex, cor_rgb, gravar_json, ler_json, normalizar_caminho, pasta_dados
import fundos
import janela_exportar
import janela_tratamento
from composicao import (
    DIRECOES_DEGRADE, MODOS_FUNDO, acabar_fundo, aplicar_moldura, compor_fundo_completo,
    degrade_padrao, fundo_imagem_padrao, fundo_posicionado, gerar_mascara_degrade, rosto_de, tem_degrade_ativo,
)
from editor_foto import EditorFoto
from observador import Observador
from produtos import area_pagina_mm, area_util_pagina_mm, lamina_mm, lamina_px, listar_produtos

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
except ImportError:
    TkinterDnD = None
    DND_FILES = None

LADO_FOTO_MAXIMO = 220
DESBOTAR_USADA = 0.65
OPACIDADE_USADA = 0.55
PROPORCAO_TIMELINE = 150 / 64.0
MARGEM_LAMINA = 36
ARRASTO_MINIMO = 6
BORDA_ROLAGEM_TIMELINE = 50
ARQUIVO_JANELA = pasta_dados() / "janela.json"
ARQUIVO_FUNDOS = pasta_dados() / "fundos.json"
ARQUIVO_CAPA = pasta_dados() / "capa_programa.json"
ARQUIVO_EXPORTACAO = pasta_dados() / "exportacao.json"
VERSAO_ARRANJO = 3
ARRANJO_PADRAO = {
    "versao": VERSAO_ARRANJO,
    "geometria": "1520x940+40+30",
    "maximizada": True,
    "mostrar_timeline": True,
    "mostrar_fotos": True,
    "mostrar_lateral": True,
    "timeline": "baixo",
    "lateral": "direita",
    "largura_timeline": 430,
    "altura_timeline": 136,
    "altura_fotos": 142,
    "largura_lateral": 330,
}
LADOS_TIMELINE = ("esquerda", "direita", "cima", "baixo")
CHAVES_TIMELINE_V1 = ("timeline", "altura_timeline", "largura_timeline")
MINIMOS = {"timeline": 86, "timeline_vertical": 120, "fotos": 96, "lateral": 280, "centro": 320, "meio": 260}
ALCA = 4
RAIO_ALCA = 7
IMA_PX = 7
LADO_MINIMO_MM = 8
SETA_MM = 1
SETA_GRANDE_MM = 5
ALT = 0x20000
SHIFT = 0x0001
CONTROL = 0x0004
CURSOR_ALCA = {"nw": "size_nw_se", "se": "size_nw_se", "ne": "size_ne_sw", "sw": "size_ne_sw",
               "n": "size_ns", "s": "size_ns", "e": "size_we", "w": "size_we"}


def ler_arranjo():
    """Como a janela estava da última vez (painéis, lados e tamanhos)."""
    arranjo = dict(ARRANJO_PADRAO)
    dados = ler_json(ARQUIVO_JANELA, {})
    if isinstance(dados, dict):
        if dados.get("versao") != VERSAO_ARRANJO:
            dados = {k: v for k, v in dados.items() if k not in CHAVES_TIMELINE_V1 and k != "versao"}
        arranjo.update({k: v for k, v in dados.items() if k in ARRANJO_PADRAO and type(v) is type(ARRANJO_PADRAO[k])})
    arranjo["versao"] = VERSAO_ARRANJO
    if arranjo["timeline"] not in LADOS_TIMELINE:
        arranjo["timeline"] = ARRANJO_PADRAO["timeline"]
    if arranjo["lateral"] not in ("direita", "esquerda"):
        arranjo["lateral"] = "direita"
    return arranjo


def geometria_valida(texto, root):
    """A geometria guardada, se ainda couber na tela (outro monitor pode ter saído)."""
    achado = re.match(r"^(\d+)x(\d+)\+?(-?\d+)\+?(-?\d+)$", str(texto or ""))
    if not achado:
        return None
    w, h, x, y = (int(v) for v in achado.groups())
    largura, altura = root.winfo_screenwidth(), root.winfo_screenheight()
    w, h = min(w, largura), min(h, altura)
    if not (-50 <= x <= largura - 200 and 0 <= y <= altura - 100):
        x, y = 40, 30
    return "%dx%d+%d+%d" % (w, h, x, y)


class Pintor:
    """Uma thread que monta as prévias. Pedido novo com a mesma chave substitui o antigo."""

    def __init__(self):
        self.pedidos = OrderedDict()
        self.cond = threading.Condition()
        threading.Thread(target=self._rodar, daemon=True).start()

    def pedir(self, chave, funcao):
        with self.cond:
            self.pedidos.pop(chave, None)
            self.pedidos[chave] = funcao
            if chave == "principal":
                self.pedidos.move_to_end(chave, last=False)
            self.cond.notify()

    def _rodar(self):
        while True:
            with self.cond:
                while not self.pedidos:
                    self.cond.wait()
                _chave, funcao = self.pedidos.popitem(last=False)
            try:
                funcao()
            except Exception:
                traceback.print_exc()


class Tarefa:
    """O que a tarefa em segundo plano recebe para falar com a janela."""

    def __init__(self, app):
        self.app = app
        self.parada = threading.Event()

    def aviso(self, texto):
        self.app.fila.put(("aviso", str(texto)))

    def progresso(self, n, total):
        self.app.fila.put(("progresso", n, total))

    def parar(self):
        return self.parada.is_set()


class Aplicativo:
    def __init__(self, root, argumentos=()):
        self.root = root
        tema.configurar(root)
        root.title("Álbum")
        root.minsize(1000, 640)
        self.arranjo = ler_arranjo()
        root.geometry(geometria_valida(self.arranjo.get("geometria"), root) or ARRANJO_PADRAO["geometria"])
        if self.arranjo.get("maximizada", True):
            try:
                root.state("zoomed")
            except tk.TclError:
                pass

        self.fila = queue.Queue()
        self.pintor = Pintor()
        self.observador = Observador(lambda c: self.fila.put(("arquivo_mudou", c)))
        self.produtos = listar_produtos()
        self.produto = self.produtos[0] if self.produtos else None
        self.projeto = proj.novo(self.produto["nome"] if self.produto else "")
        self.caminho_projeto = None
        self.modificado = False
        self.info = {}
        self.historico = proj.Historico()
        self.atual = 0
        self.espaco_sel = None
        self.espacos_sel = set()
        self._ancora_espaco = None
        self.fotos_sel = set()
        self.ultima_foto_clicada = None
        self.filtro = tk.StringVar(value="todas")
        self.pasta_filtro = tk.StringVar(value="Todas as pastas")
        self.pastas_recolhidas = set()
        self._itens_painel_fotos = []
        self.igualar_no_grupo = tk.BooleanVar(value=True)
        self.guias = tk.BooleanVar(value=True)
        self.zoom = 1.0
        self._zoom_destino = 1.0
        self.pan = [0, 0]
        self.editor = None
        self.tarefa = None
        self.registro = []
        self.janela_tarefa = None
        self.janela_exportar = None
        self.janela_tratamento = None
        self.processo_capa = None
        self.versao_fotos = 0
        self._versao_render = 0
        self._geo = None
        self._tk_lamina = None
        self._tk_lamina_zoom = None
        self._imagem_lamina = None
        self._cache_laminas = OrderedDict()
        self._anim_layout_id = None
        self._anim_tks = []
        self._tk_miniaturas = OrderedDict()
        self._tl_pil = {}
        self._tl_tk = {}
        self._tl_itens = []
        self._arrasto = None
        self._fantasma = None
        self._agendados = {}
        self._sugestoes = []
        self._modelo_sel = None
        self._carregando = False
        self.editar_layout = tk.BooleanVar(value=False)
        self._insercao_tl = None
        self._rolando_tl = False
        self._fundos_avisados = set()
        self._ultimo_toque = 0.0
        self._num_alvo = None
        self._carregando_fundo = False
        self.fundo_alvo = tk.StringVar(value="lamina")
        self.fundo_fusao = tk.DoubleVar(value=0)
        self._tl_larg, self._tl_alt, self._tl_altura = 150, 64, 92
        self._tl_passo_v = 100
        self._tl_dim = None
        self.molde_sel = None
        self._remendo = None
        self._previa_agendada = None
        self._previa_ultima = 0.0
        self.tempos_arrasto = []
        self.tempo_inicio_arrasto = 0.0
        self.editor_fundo = None
        self._fundos_pasta = None
        self._fundos_lista = []
        self._camada_frente = None
        self._fundo_base_cache = None
        self._tk_previa_fundo = None
        self._previa_fundo_ultima = 0.0
        self._previa_fundo_agendada = None
        self.tempos_previa_fundo = []
        self._celula = (100, 96, 84)

        self._montar()
        self._atalhos()
        self._registrar_drop()
        self.root.protocol("WM_DELETE_WINDOW", self.sair)
        self.root.after(30, self._ler_fila)
        self._carregar_ui_do_projeto()
        self.atualizar_tudo()
        if argumentos:
            self.root.after(300, lambda: self.adicionar_caminhos(list(argumentos)))

    # ==================================================================
    # Montagem da janela
    # ==================================================================

    def _montar(self):
        self.margem_var = tk.DoubleVar()
        self.espacamento_var = tk.DoubleVar()
        self.fotos_lamina_var = tk.IntVar()

        topo = ttk.Frame(self.root, style="Barra.TFrame", padding=(8, 4))
        topo.pack(side="top", fill="x")
        self.barra_topo = topo
        self._montar_topo(topo)

        tk.Frame(self.root, bg=tema.BORDA, height=1).pack(side="top", fill="x")

        barra_opcoes = ttk.Frame(self.root, style="Barra.TFrame", padding=(8, 3))
        barra_opcoes.pack(side="top", fill="x")
        self.barra_opcoes = barra_opcoes
        self._montar_barra_opcoes(barra_opcoes)

        tk.Frame(self.root, bg=tema.BORDA, height=1).pack(side="top", fill="x")

        status = ttk.Frame(self.root, padding=(10, 3))
        status.pack(side="bottom", fill="x")
        tk.Frame(self.root, bg=tema.BORDA, height=1).pack(side="bottom", fill="x")
        self._montar_status(status)

        corpo = ttk.Frame(self.root)
        corpo.pack(side="top", fill="both", expand=True)

        # Barra vertical esquerda de ferramentas estilo Adobe Photoshop CC (igual à demonstração)
        self.barra_ferramentas = tk.Frame(corpo, bg=tema.PAINEL, width=40, padx=4, pady=6)
        self.barra_ferramentas.pack(side="left", fill="y")
        self._montar_ferramentas_esquerda(self.barra_ferramentas)
        tk.Frame(corpo, bg=tema.BORDA, width=1).pack(side="left", fill="y")

        opcoes = dict(bg=tema.BORDA, bd=0, sashwidth=4, sashrelief="flat", showhandle=False, opaqueresize=True)
        self.painel_v = tk.PanedWindow(corpo, orient="vertical", **opcoes)
        self.painel_v.pack(side="left", fill="both", expand=True)
        self.painel_h = tk.PanedWindow(self.painel_v, orient="horizontal", **opcoes)
        self.painel_baixo = tk.PanedWindow(self.painel_v, orient="horizontal", **opcoes)
        self.quadro_timeline = ttk.Frame(self.painel_v)
        self._montar_timeline(self.quadro_timeline)
        self.quadro_fotos = ttk.Frame(self.painel_v)
        self._montar_fotos(self.quadro_fotos)

        centro = ttk.Frame(self.painel_h, style="Fundo.TFrame")
        self.quadro_centro = centro
        self.canvas = tk.Canvas(centro, bg=tema.FUNDO, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.quadro_lateral = ttk.Frame(self.painel_h)
        self._montar_direita(self.quadro_lateral)
        self._aplicar_arranjo()
        for painel in (self.painel_v, self.painel_h, self.painel_baixo):
            painel.bind("<ButtonRelease-1>", lambda e: self.agendar("gravar_arranjo", 400, self.gravar_arranjo), add="+")

        self.canvas.bind("<Configure>", lambda e: self.agendar("lamina", 40, self.desenhar_lamina))
        self.canvas.bind("<ButtonPress-1>", self._lamina_pressionou)
        self.canvas.bind("<B1-Motion>", self._mover_arrasto)
        self.canvas.bind("<ButtonRelease-1>", self._soltar_arrasto)
        self.canvas.bind("<Double-Button-1>", self._lamina_duplo)
        self.canvas.bind("<ButtonPress-3>", self._pan_direito_inicio)
        self.canvas.bind("<B3-Motion>", self._pan_direito_mover)
        self.canvas.bind("<ButtonRelease-3>", self._pan_direito_soltar)
        self.canvas.bind("<MouseWheel>", self._lamina_roda)
        self.canvas.bind("<ButtonPress-2>", self._pan_inicio)
        self.canvas.bind("<B2-Motion>", self._pan_mover)
        self.canvas.bind("<Motion>", self._lamina_passou)

    def _botao(self, pai, texto, comando, dica=None, estilo="Compacto.TButton", **opcoes):
        botao = ttk.Button(pai, text=texto, command=comando, style=estilo, **opcoes)
        if dica:
            tema.dica(botao, dica)
        return botao

    def _montar_topo(self, topo):
        selo = tk.Label(topo, text="Sa", bg="#001e36", fg="#31a8ff",
                        font=tema.FONTE_FORTE, bd=1, relief="solid", padx=5, pady=1)
        selo.pack(side="left", padx=(2, 6))
        tema.dica(selo, "Smart Album CC · Motor Elástico Proporcional (Corte Máx ≤ 22%)")
        ttk.Label(topo, text="Smart Album CC", style="Logo.TLabel").pack(side="left", padx=(0, 10))

        arquivo = ttk.Menubutton(topo, text="Arquivo", style="Compacto.TMenubutton")
        menu = tk.Menu(arquivo, tearoff=0)
        menu.add_command(label="Novo álbum", accelerator="Ctrl+N", command=self.novo_projeto)
        menu.add_command(label="Abrir...", accelerator="Ctrl+O", command=self.abrir_projeto)
        self.menu_recentes = tk.Menu(menu, tearoff=0, postcommand=self._montar_recentes)
        menu.add_cascade(label="Recentes", menu=self.menu_recentes)
        menu.add_separator()
        menu.add_command(label="Salvar", accelerator="Ctrl+S", command=self.salvar_projeto)
        menu.add_command(label="Salvar como...", accelerator="Ctrl+Shift+S", command=lambda: self.salvar_projeto(como=True))
        menu.add_separator()
        menu.add_command(label="Adicionar fotos...", command=self.escolher_fotos)
        menu.add_command(label="Adicionar pasta...", command=self.escolher_pasta)
        menu.add_command(label="Colar fotos do Explorer", accelerator="Ctrl+V", command=self.colar)
        menu.add_command(label="Procurar fotos que mudaram de lugar...", command=self.consertar_caminhos)
        menu.add_command(label="Remover selecionadas do projeto", command=self.remover_fotos_selecionadas)
        menu.add_separator()
        menu.add_command(label="Exportar...", accelerator="Ctrl+E", command=self.abrir_exportar)
        menu.add_command(label="Tratar fotos (Photoshop / Lightroom)...", command=self.abrir_tratamento)
        menu.add_separator()
        menu.add_command(label="Abrir o programa de capas", command=self.abrir_programa_capa)
        menu.add_command(label="Abrir a automação de capas (AUTOMAÇAO-PASTA --ui)", command=self.abrir_automacao_capa)
        menu.add_command(label="Escolher o programa de capas...", command=self.escolher_programa_capa)
        menu.add_separator()
        menu.add_command(label="Informações do produto", command=self.mostrar_info_produto)
        menu.add_command(label='Criar atalho em "Enviar para"', command=self.criar_enviar_para)
        menu.add_separator()
        menu.add_command(label="Sair", command=self.sair)
        arquivo["menu"] = menu
        arquivo.pack(side="left", padx=1)
        self.botao_arquivo = arquivo

        exibir = ttk.Menubutton(topo, text="Exibir", style="Compacto.TMenubutton")
        self.menu_exibir = tk.Menu(exibir, tearoff=0)
        self._montar_menu_exibir(self.menu_exibir)
        exibir["menu"] = self.menu_exibir
        exibir.pack(side="left", padx=1)
        self.botao_exibir = exibir

        ttk.Frame(topo, width=10).pack(side="left")
        self.rotulo_nome = ttk.Label(topo, text="", style="Titulo.TLabel")
        self.rotulo_nome.pack(side="left", padx=(0, 8))

        self.botao_exportar = self._botao(topo, "⤓ Exportar", self.abrir_exportar,
                                          "Exportar lâminas: formato, pasta e nomes dos arquivos (Ctrl+E)",
                                          "Compacto.TButton")
        self.botao_exportar.pack(side="right", padx=(3, 0))
        self.botao_tratar = self._botao(topo, "✦ Ps/Lr", self.abrir_tratamento,
                                        "Tratar fotos no Photoshop (editar/Ação) ou Lightroom (preset)",
                                        "Compacto.TButton")
        self.botao_tratar.pack(side="right", padx=2)
        self.botao_capa = self._botao(topo, "◧ Capa", self.abrir_programa_capa,
                                      "Abrir o programa de capas externo\n"
                                      "(Arquivo > Escolher o programa de capas... para trocar)",
                                      "Compacto.TButton")
        self.botao_capa.pack(side="right", padx=2)

        ttk.Frame(topo, width=6).pack(side="right")
        self._botao(topo, "↷", self.refazer, "Refazer última alteração (Ctrl+Y)", "Icone.TButton", width=3).pack(side="right", padx=1)
        self._botao(topo, "↶", self.desfazer, "Desfazer última alteração (Ctrl+Z)", "Icone.TButton", width=3).pack(side="right", padx=1)
        ttk.Frame(topo, width=6).pack(side="right")
        self._botao(topo, "✦ Diagramar", self.abrir_diagramacao,
                    "Diagramar o álbum inteiro automaticamente (preenchendo a página sem distorcer fotos > 22%)",
                    "PrimarioCompacto.TButton").pack(side="right", padx=2)

    def _montar_barra_opcoes(self, barra):
        """Barra de opções estilo Adobe Photoshop CC (idêntica à demonstração): Álbum, M:, E:, F/L: e ícones rápidos."""
        rot_alb = ttk.Label(barra, text="Álbum:", style="Fraco.TLabel")
        rot_alb.pack(side="left", padx=(2, 4))
        self.produto_var = tk.StringVar(value=self.produto["nome"] if self.produto else "")
        self.combo_produto = ttk.Combobox(barra, textvariable=self.produto_var, state="readonly", width=8,
                                          values=[p["nome"] for p in self.produtos])
        self.combo_produto.pack(side="left", padx=(0, 4))
        self.combo_produto.bind("<<ComboboxSelected>>", self._trocou_produto)
        tema.dica(self.combo_produto, "Produto (formato e dimensões do álbum)")
        self.rotulo_produto = ttk.Label(barra, text="", style="Fraco.TLabel")
        self.rotulo_produto.pack(side="left", padx=(0, 6))
        self.dica_produto = tema.Dica(self.rotulo_produto, "")

        tk.Frame(barra, bg=tema.BORDA, width=1, height=18).pack(side="left", padx=6, pady=2)

        for rot_txt, dica_txt, var, de, ate, passo, sufixo in (
            ("⬚ M:", "Margem externa de segurança da página (mm)", self.margem_var, 0, 60, 1, "mm"),
            ("⇔ E:", "Espaçamento entre as fotos (mm)", self.espacamento_var, 0, 40, 0.5, "mm"),
            ("⊞ F/L:", "Quantidade alvo de fotos por lâmina", self.fotos_lamina_var, 1, 12, 1, ""),
        ):
            r = ttk.Label(barra, text=rot_txt, style="Fraco.TLabel")
            r.pack(side="left", padx=(4, 2))
            tema.dica(r, dica_txt)
            sp = ttk.Spinbox(barra, from_=de, to=ate, increment=passo, textvariable=var, width=4,
                             command=self._mudou_config)
            sp.pack(side="left")
            tema.dica(sp, dica_txt)
            if sufixo:
                ttk.Label(barra, text=sufixo, style="Fraco.TLabel").pack(side="left", padx=(1, 4))

        tk.Frame(barra, bg=tema.BORDA, width=1, height=18).pack(side="left", padx=6, pady=2)

        self._botao(barra, "⟳", self.rediagramar_lamina,
                    "Diagramar Esta Lâmina de Novo\nAlterna entre variações proporcionais sem cortar além de 22%",
                    "Icone.TButton", width=3).pack(side="left", padx=1)
        self._botao(barra, "⊞", self.rediagramar_todas,
                    "Aplicar Geometria em Todas as Lâminas\nRecalcula todas as lâminas do álbum com a margem e espaçamento atuais",
                    "Icone.TButton", width=3).pack(side="left", padx=1)
        guias = ttk.Checkbutton(barra, text="⌗", variable=self.guias, command=self.desenhar_lamina,
                                style="Alternar.Toolbutton")
        guias.pack(side="left", padx=1)
        tema.dica(guias, "Mostrar/Ocultar Guias\nExibe sangria, margem de segurança e linha de dobra central")
        layout = ttk.Checkbutton(barra, text="⬚", variable=self.editar_layout, style="Alternar.Toolbutton",
                                 command=self._mudou_modo_layout)
        layout.pack(side="left", padx=1)
        tema.dica(layout, "Modo Layout (Tecla L ou Duplo Clique)\nMover e redimensionar espaços e moldes. Esc sai.")
        self._botao(barra, "📁+", self.escolher_pasta,
                    "Carregar Pasta de Fotos do PC\nAdiciona todas as imagens de uma pasta ao projeto",
                    "Icone.TButton", width=4).pack(side="left", padx=1)
        self._botao(barra, "🖼+", self.escolher_fotos,
                    "Carregar Fotos do PC\nSeleciona imagens avulsas do computador para o projeto",
                    "Icone.TButton", width=4).pack(side="left", padx=1)

        # Telemetria da lâmina aberta alinhada à direita na barra de opções (igual à demonstração)
        self.rotulo_lamina = ttk.Label(barra, text="", style="Fraco.TLabel", anchor="e")
        self.rotulo_lamina.pack(side="right", padx=(8, 4))

    def _montar_ferramentas_esquerda(self, barra):
        """Dock vertical esquerdo de ferramentas estilo Adobe Photoshop CC com ícones autoexplicativos e dicas no mouse."""
        def sep():
            tk.Frame(barra, bg=tema.BORDA, height=1).pack(fill="x", padx=2, pady=5)

        self._botao(barra, "✦", self.abrir_diagramacao,
                    "Diagramar Álbum Automaticamente (Por Cenário e Pasta)\nMonta as lâminas agrupando fotos de mesmo cenário sem misturar pastas",
                    "Icone.TButton", width=3).pack(pady=1)
        self._botao(barra, "⟳", self.rediagramar_lamina,
                    "Diagramar Esta Lâmina de Novo\nTesta o próximo arranjo elástico (incluindo 60/40 e 70/30 que passam do meio)",
                    "Icone.TButton", width=3).pack(pady=1)
        self._botao(barra, "⊞", self.rediagramar_todas,
                    "Aplicar em Todas as Lâminas\nAplica margem e espaçamento atuais em todo o álbum",
                    "Icone.TButton", width=3).pack(pady=1)
        self._botao(barra, "⇉", self.distribuir_nao_usadas,
                    "Distribuir Fotos Livres\nCria novas lâminas automaticamente com as fotos ainda não usadas",
                    "Icone.TButton", width=3).pack(pady=1)

        sep()

        bt_mover = ttk.Checkbutton(barra, text="⬚", variable=self.editar_layout,
                                   style="Alternar.Toolbutton", command=self._mudou_modo_layout, width=3)
        bt_mover.pack(pady=1)
        tema.dica(bt_mover, "Mover / Transformar Espaços (Tecla L)\nPadrão mantém proporção; segure Shift para Proporção Livre.\nCtrl+clique ou arraste seleciona várias fotos.")

        self._botao(barra, "◐", self.abrir_aba_degrade,
                    "Degradê de Opacidade (Estilo Photoshop)\nAplica máscara de transparência gradual nas fotos selecionadas e/ou nos fundos",
                    "Icone.TButton", width=3).pack(pady=1)
        self._botao(barra, "✂", self.editar_foto_selecionada,
                    "Recortar / Enquadrar Foto (Enter)\nAbre o editor de enquadramento, zoom e cor da foto selecionada",
                    "Icone.TButton", width=3).pack(pady=1)
        self._botao(barra, "↻", self.girar_foto_selecionada,
                    "Girar Foto 90°\nGira a foto do espaço selecionado e reajusta os quadros da página automaticamente",
                    "Icone.TButton", width=3).pack(pady=1)
        self._botao(barra, "＝", lambda: self.alinhar_espaco("igualar"),
                    "Igualar Tamanho das Fotos Selecionadas\nDeixa todas as fotos selecionadas (ou da página) com exatamente o mesmo tamanho",
                    "Icone.TButton", width=3).pack(pady=1)
        self._botao(barra, "⊡", lambda: self.alinhar_espaco("proporcao_foto"),
                    "Usar Proporção Padrão da Foto\nAjusta o quadro exatamente para a proporção nativa da foto (0% de corte)",
                    "Icone.TButton", width=3).pack(pady=1)
        self._botao(barra, "⇕", lambda: self.alinhar_espaco("pagina_util"),
                    "Preencher Página Útil\nExpande a foto selecionada na página respeitando o limite de 22% de corte",
                    "Icone.TButton", width=3).pack(pady=1)
        self._botao(barra, "＋", self.adicionar_espaco,
                    "Novo Espaço na Lâmina\nAdiciona um quadro de foto extra na página",
                    "Icone.TButton", width=3).pack(pady=1)
        self._botao(barra, "❐", self.duplicar_espaco,
                    "Duplicar Espaço Selecionado\nCria uma cópia com as mesmas medidas do espaço atual",
                    "Icone.TButton", width=3).pack(pady=1)
        self._botao(barra, "↑", lambda: self.ordem_espaco(True),
                    "Trazer para a Frente\nColoca o espaço selecionado acima dos demais",
                    "Icone.TButton", width=3).pack(pady=1)
        self._botao(barra, "↓", lambda: self.ordem_espaco(False),
                    "Mandar para Trás\nColoca o espaço selecionado atrás dos demais",
                    "Icone.TButton", width=3).pack(pady=1)
        self._botao(barra, "🗑", self.apagar_espaco_selecionado,
                    "Apagar Espaço Selecionado (Del)\nRemove o quadro e expande as demais fotos para ocupar a página",
                    "Icone.TButton", width=3).pack(pady=1)

        sep()

        self._botao(barra, "✦", self.abrir_tratamento,
                    "Tratar Fotos (Photoshop / Lightroom)\nAbre no Photoshop, roda Ação ou aplica preset do Lightroom",
                    "Icone.TButton", width=3).pack(pady=1)
        self._botao(barra, "◧", self.abrir_programa_capa,
                    "Programa de Capas\nEnvia e abre a capa no programa de capas configurado",
                    "Icone.TButton", width=3).pack(pady=1)
        self._botao(barra, "⤓", self.abrir_exportar,
                    "Exportar Álbum (Ctrl+E)\nExporta as lâminas prontas em alta resolução (JPG/PNG/PDF)",
                    "Icone.TButton", width=3).pack(pady=1)

    def _montar_menu_exibir(self, menu):
        a = self.arranjo
        self.var_mostrar = {chave: tk.BooleanVar(value=a["mostrar_" + chave]) for chave in ("timeline", "fotos", "lateral")}
        self.var_lado_timeline = tk.StringVar(value=a["timeline"])
        self.var_lado_lateral = tk.StringVar(value=a["lateral"])
        for chave, texto in (("timeline", "Linha do tempo (lâminas)"), ("fotos", "Painel de fotos"),
                             ("lateral", "Painel lateral (Layouts, Fundo, Degradê)")):
            menu.add_checkbutton(label=texto, variable=self.var_mostrar[chave], command=self._mudou_exibir)
        menu.add_separator()
        for valor, texto in (("esquerda", "Lâminas à esquerda"), ("direita", "Lâminas à direita"),
                             ("cima", "Lâminas em cima"), ("baixo", "Lâminas embaixo")):
            menu.add_radiobutton(label=texto, value=valor, variable=self.var_lado_timeline, command=self._mudou_exibir)
        menu.add_separator()
        menu.add_radiobutton(label="Painel lateral à direita", value="direita", variable=self.var_lado_lateral,
                             command=self._mudou_exibir)
        menu.add_radiobutton(label="Painel lateral à esquerda", value="esquerda", variable=self.var_lado_lateral,
                             command=self._mudou_exibir)
        menu.add_separator()
        menu.add_command(label="Lâminas maiores", command=lambda: self.mudar_tamanho_painel("timeline", 30))
        menu.add_command(label="Lâminas menores", command=lambda: self.mudar_tamanho_painel("timeline", -30))
        menu.add_command(label="Fotos maiores", command=lambda: self.mudar_tamanho_painel("fotos", 30))
        menu.add_command(label="Fotos menores", command=lambda: self.mudar_tamanho_painel("fotos", -30))
        menu.add_separator()
        menu.add_command(label="Restaurar layout padrão", command=self.restaurar_arranjo)

    def _montar_status(self, status):
        self.rotulo_status = ttk.Label(status, text="Arraste fotos ou uma pasta para a janela.", style="Fraco.TLabel")
        self.rotulo_status.pack(side="left", fill="x", expand=True)
        self._botao(status, "☰", self.mostrar_registro, "Registro de avisos e histórico de eventos", "Icone.TButton", width=3).pack(side="right", padx=(4, 0))
        self.botao_parar = self._botao(status, "■", self.parar_tarefa, "Parar a tarefa em andamento", "Icone.TButton", width=3, state="disabled")
        self.botao_parar.pack(side="right", padx=(4, 0))
        self.barra = ttk.Progressbar(status, length=180, mode="determinate")
        self.barra.pack(side="right")

    def _montar_fotos(self, pai):
        caixa = ttk.Frame(pai, padding=(8, 4, 8, 0))
        caixa.pack(fill="both", expand=True)
        cabeca = ttk.Frame(caixa)
        cabeca.pack(side="top", fill="x")
        self.rotulo_fotos = ttk.Label(cabeca, text="Fotos", style="Titulo.TLabel")
        self.rotulo_fotos.pack(side="left")
        for valor, texto in (("todas", "Todas"), ("nao_usadas", "Não usadas"), ("usadas", "Usadas")):
            ttk.Radiobutton(cabeca, text=texto, value=valor, variable=self.filtro, style="Filtro.TRadiobutton",
                            command=self.atualizar_fotos).pack(side="left", padx=(8 if valor == "todas" else 2, 0))

        ttk.Label(cabeca, text="📂", style="Fraco.TLabel").pack(side="left", padx=(8, 2))
        self.combo_pasta_filtro = ttk.Combobox(cabeca, textvariable=self.pasta_filtro, state="readonly", width=15,
                                               values=["Todas as pastas"])
        self.combo_pasta_filtro.pack(side="left", padx=(0, 4))
        self.combo_pasta_filtro.bind("<<ComboboxSelected>>", lambda e: self.atualizar_fotos())
        tema.dica(self.combo_pasta_filtro, "Filtrar visualização por pasta carregada (as pastas ficam separadas sem se misturar)")

        self.ordem_var = tk.StringVar(value="nome")
        combo = ttk.Combobox(cabeca, textvariable=self.ordem_var, state="readonly", width=7,
                             values=["nome", "data", "cenario"])
        combo.pack(side="left", padx=(4, 0))
        combo.bind("<<ComboboxSelected>>", lambda e: self.ordenar_fotos())
        tema.dica(combo, "Ordenar as fotos dentro de cada pasta por nome, data EXIF ou agrupar por cenário visual")
        self._botao(cabeca, "📁+", self.escolher_pasta, "Adicionar nova pasta de fotos (mantém separada das pastas anteriores)", "Icone.TButton", width=4).pack(side="right", padx=1)
        self._botao(cabeca, "🖼+", self.escolher_fotos, "Adicionar fotos avulsas ao projeto", "Icone.TButton", width=4).pack(side="right", padx=1)
        self._botao(cabeca, "🗑", self.remover_fotos_selecionadas,
                    "Remover fotos selecionadas do projeto (não apaga do disco)", "Icone.TButton", width=3).pack(side="right", padx=(4, 1))
        self._botao(cabeca, "⇉", self.distribuir_nao_usadas,
                    "Distribuir fotos não usadas em lâminas novas automaticamente", "Icone.TButton", width=3).pack(side="right", padx=3)
        barra = ttk.Scrollbar(caixa, orient="horizontal", command=self._rolar_fotos)
        barra.pack(side="bottom", fill="x")
        self.canvas_fotos = tk.Canvas(caixa, height=100, bg=tema.PAINEL, highlightthickness=0)
        self.canvas_fotos.pack(side="top", fill="both", expand=True, pady=(3, 0))
        self.canvas_fotos.configure(xscrollcommand=lambda a, b: (barra.set(a, b), self.agendar("fotos", 15, self._desenhar_fotos_visiveis)))
        self.canvas_fotos.bind("<Configure>", self._mudou_tamanho_fotos)
        self.canvas_fotos.bind("<MouseWheel>", lambda e: self.canvas_fotos.xview_scroll(-int(e.delta / 40), "units"))
        self.canvas_fotos.bind("<ButtonPress-1>", self._fotos_pressionou)
        self.canvas_fotos.bind("<B1-Motion>", self._mover_arrasto)
        self.canvas_fotos.bind("<ButtonRelease-1>", self._soltar_arrasto)
        self.canvas_fotos.bind("<Double-Button-1>", self._fotos_duplo)
        self.canvas_fotos.bind("<Button-3>", self._fotos_menu)
        self.canvas_fotos.configure(xscrollincrement=20)

    def _montar_timeline(self, pai):
        caixa = ttk.Frame(pai, padding=(6, 4, 6, 2))
        caixa.pack(fill="both", expand=True)
        self.caixa_timeline = caixa
        cabeca = ttk.Frame(caixa)
        cabeca.pack(side="top", fill="x")
        self.cabeca_timeline = cabeca
        self.rotulo_timeline = ttk.Label(cabeca, text="Lâminas", style="Titulo.TLabel")
        self.rotulo_timeline.pack(side="left")
        self.botoes_timeline = ttk.Frame(caixa)
        self.botoes_tl_edicao = ttk.Frame(caixa)
        self._botoes_nav = []
        for pai, texto, comando, dica, espaco in (
                (self.botoes_timeline, "◀", lambda: self.passar_lamina(-1), "Lâmina anterior (← ou ↑)", (0, 1)),
                (self.botoes_timeline, "▶", lambda: self.passar_lamina(1), "Próxima lâmina (→ ou ↓)", 1),
                (self.botoes_timeline, "«", lambda: self.mover_lamina_passo(-1), "Mover a lâmina para antes (Ctrl+← ou Ctrl+↑)\n"
                                                                                 "Também dá para arrastar a miniatura.", (6, 1)),
                (self.botoes_timeline, "»", lambda: self.mover_lamina_passo(1), "Mover a lâmina para depois (Ctrl+→ ou Ctrl+↓)", 1),
                (self.botoes_tl_edicao, "+", self.adicionar_lamina, "Nova lâmina depois desta", 1),
                (self.botoes_tl_edicao, "⧉", self.duplicar_lamina, "Duplicar a lâmina", 1),
                (self.botoes_tl_edicao, "−", self.remover_lamina, "Apagar a lâmina", 1)):
            botao = self._botao(pai, texto, comando, dica, "Mini.TButton", width=2)
            botao.pack(side="left", padx=espaco)
            self._botoes_nav.append(botao)
        self.barra_tl_h = ttk.Scrollbar(caixa, orient="horizontal")
        self.barra_tl_v = ttk.Scrollbar(caixa, orient="vertical")
        self.canvas_timeline = tk.Canvas(caixa, height=90, width=180, bg=tema.PAINEL, highlightthickness=0)
        self.barra_tl_h.configure(command=self.canvas_timeline.xview)
        self.barra_tl_v.configure(command=self.canvas_timeline.yview)
        self.canvas_timeline.configure(xscrollcommand=self.barra_tl_h.set, yscrollcommand=self.barra_tl_v.set,
                                       xscrollincrement=20, yscrollincrement=20)
        self._tl_vertical = None
        self.canvas_timeline.bind("<Configure>", self._mudou_tamanho_timeline)
        self.canvas_timeline.bind("<MouseWheel>", self._timeline_roda)
        self.canvas_timeline.bind("<ButtonPress-1>", self._timeline_pressionou)
        self.canvas_timeline.bind("<B1-Motion>", self._mover_arrasto)
        self.canvas_timeline.bind("<ButtonRelease-1>", self._soltar_arrasto)
        self.canvas_timeline.bind("<Button-3>", self._timeline_menu)

    # ---------------- arranjo dos painéis ----------------

    def _timeline_vertical(self):
        return self.arranjo["timeline"] in ("esquerda", "direita")

    def _ler_tamanhos(self):
        """Passa para o arranjo o tamanho atual dos painéis (o usuário pode ter arrastado as divisórias)."""
        em_baixo_junto = (self.arranjo.get("timeline") == "baixo"
                          and self.arranjo.get("mostrar_timeline")
                          and self.arranjo.get("mostrar_fotos"))
        usa_largura_tl = self._tl_vertical or em_baixo_junto
        chave_tl = "largura_timeline" if usa_largura_tl else "altura_timeline"
        for chave, widget, altura in ((chave_tl, self.quadro_timeline, not usa_largura_tl),
                                      ("altura_fotos", self.quadro_fotos, True),
                                      ("largura_lateral", self.quadro_lateral, False)):
            if widget.winfo_ismapped():
                valor = widget.winfo_height() if altura else widget.winfo_width()
                if valor > 20:
                    self.arranjo[chave] = int(valor)

    def _aplicar_arranjo(self):
        a = self.arranjo
        v, h, b = self.painel_v, self.painel_h, self.painel_baixo
        for painel in (v, h, b):
            for nome in painel.panes():
                painel.forget(nome)
        vertical = self._timeline_vertical()
        self._orientar_timeline(vertical)
        lateral = dict(width=a["largura_lateral"], minsize=MINIMOS["lateral"], stretch="never")
        tl_lado = dict(width=a["largura_timeline"], minsize=MINIMOS["timeline_vertical"], stretch="never")
        mostrar_tl, mostrar_lat = a["mostrar_timeline"], a["mostrar_lateral"]
        if mostrar_lat and a["lateral"] == "esquerda":
            h.add(self.quadro_lateral, **lateral)
        if mostrar_tl and a["timeline"] == "esquerda":
            h.add(self.quadro_timeline, **tl_lado)
        h.add(self.quadro_centro, minsize=MINIMOS["centro"], stretch="always")
        if mostrar_tl and a["timeline"] == "direita":
            h.add(self.quadro_timeline, **tl_lado)
        if mostrar_lat and a["lateral"] == "direita":
            h.add(self.quadro_lateral, **lateral)
        tl_faixa = dict(height=a["altura_timeline"], minsize=MINIMOS["timeline"], stretch="never")
        if mostrar_tl and a["timeline"] == "cima":
            v.add(self.quadro_timeline, **tl_faixa)
        v.add(h, minsize=MINIMOS["meio"], stretch="always")
        if mostrar_tl and a["timeline"] == "baixo" and a["mostrar_fotos"]:
            b.add(self.quadro_timeline, width=max(260, a.get("largura_timeline", 420)), minsize=220, stretch="never")
            b.add(self.quadro_fotos, minsize=260, stretch="always")
            v.add(b, height=a["altura_fotos"], minsize=MINIMOS["fotos"], stretch="never")
        else:
            if a["mostrar_fotos"]:
                v.add(self.quadro_fotos, height=a["altura_fotos"], minsize=MINIMOS["fotos"], stretch="never")
            if mostrar_tl and a["timeline"] == "baixo":
                v.add(self.quadro_timeline, **tl_faixa)

    def _orientar_timeline(self, vertical):
        """Lâminas empilhadas (lateral) ou lado a lado (em cima ou embaixo)."""
        if vertical == self._tl_vertical:
            return
        self._tl_vertical = vertical
        for widget in (self.botoes_timeline, self.botoes_tl_edicao, self.barra_tl_h, self.barra_tl_v, self.canvas_timeline):
            widget.pack_forget()
        if vertical:
            self.botoes_tl_edicao.pack(in_=self.cabeca_timeline, side="right")
            self.botoes_timeline.pack(in_=self.caixa_timeline, side="top", fill="x", pady=(3, 0))
            self.barra_tl_v.pack(side="right", fill="y", pady=(3, 0))
        else:
            self.botoes_timeline.pack(in_=self.cabeca_timeline, side="left", padx=(10, 0))
            self.botoes_tl_edicao.pack(in_=self.cabeca_timeline, side="left", padx=(10, 0))
            self.barra_tl_h.pack(side="bottom", fill="x")
        self.canvas_timeline.pack(side="top", fill="both", expand=True, pady=(3, 0))
        textos = ("▲", "▼") if vertical else ("◀", "▶")
        for botao, texto in zip(self._botoes_nav, textos):
            botao.configure(text=texto)
        self.canvas_timeline.xview_moveto(0)
        self.canvas_timeline.yview_moveto(0)
        self._tl_dim = None
        if self._tl_itens is not None:
            self.agendar("timeline", 30, self.desenhar_timeline)

    def _mudou_exibir(self):
        self._ler_tamanhos()
        for chave, var in self.var_mostrar.items():
            self.arranjo["mostrar_" + chave] = bool(var.get())
        self.arranjo["timeline"] = self.var_lado_timeline.get()
        self.arranjo["lateral"] = self.var_lado_lateral.get()
        self._aplicar_arranjo()
        self.agendar("gravar_arranjo", 300, self.gravar_arranjo)

    def mostrar_painel(self, chave, mostrar=True):
        self.var_mostrar[chave].set(bool(mostrar))
        self._mudou_exibir()

    def posicionar_painel(self, chave, lado):
        (self.var_lado_timeline if chave == "timeline" else self.var_lado_lateral).set(lado)
        self._mudou_exibir()

    def mudar_tamanho_painel(self, chave, diferenca):
        self._ler_tamanhos()
        minimo = MINIMOS[chave]
        if chave == "lateral":
            campo = "largura_lateral"
        elif chave == "timeline" and self._tl_vertical:
            campo, minimo = "largura_timeline", MINIMOS["timeline_vertical"]
        else:
            campo = "altura_" + chave
        self.arranjo[campo] = max(minimo, int(self.arranjo[campo]) + diferenca)
        self.var_mostrar[chave].set(True)
        self._mudou_exibir()

    def restaurar_arranjo(self):
        for chave, valor in ARRANJO_PADRAO.items():
            if chave not in ("geometria", "maximizada"):
                self.arranjo[chave] = valor
        for chave, var in self.var_mostrar.items():
            var.set(self.arranjo["mostrar_" + chave])
        self.var_lado_timeline.set(self.arranjo["timeline"])
        self.var_lado_lateral.set(self.arranjo["lateral"])
        self._aplicar_arranjo()
        self.agendar("gravar_arranjo", 300, self.gravar_arranjo)
        self.registrar("Layout da janela restaurado.")

    def gravar_arranjo(self):
        try:
            estado = self.root.state()
        except tk.TclError:
            return
        self._ler_tamanhos()
        self.arranjo["maximizada"] = estado == "zoomed"
        if estado == "normal":
            self.arranjo["geometria"] = self.root.geometry()
        try:
            gravar_json(ARQUIVO_JANELA, self.arranjo)
        except OSError:
            pass

    def _montar_direita(self, direita):
        abas = ttk.Notebook(direita)
        abas.pack(fill="both", expand=True, padx=4, pady=6)
        self.abas = abas
        self.quadros_abas = {}
        for titulo, montar in (("Layouts", self._aba_layouts), ("Fundo", self._aba_fundo),
                               ("Degradê", self._aba_degrade), ("Moldura", self._aba_moldura)):
            quadro = ttk.Frame(abas, padding=8)
            abas.add(quadro, text=titulo)
            self.quadros_abas[titulo] = quadro
            montar(quadro)
        abas.bind("<<NotebookTabChanged>>", self._aba_mudou)

    def abrir_aba_degrade(self):
        if "Degradê" in self.quadros_abas:
            self.abas.select(self.quadros_abas["Degradê"])
            self._atualizar_degrade_ui()

    def _aba_mudou(self, _evento=None):
        selecionada = self.abas.select()
        if selecionada == str(self.quadros_abas["Fundo"]):
            self.atualizar_fundos()
        elif selecionada == str(self.quadros_abas.get("Degradê")):
            self._atualizar_degrade_ui()
        elif selecionada == str(self.quadros_abas["Moldura"]):
            self._atualizar_moldura_ui()

    def _aba_layouts(self, quadro):
        """Sugestões, biblioteca de modelos e edição do layout na mesma aba, uma parte de cada vez."""
        self.secao_layouts = tk.StringVar(value="sugestoes")
        seletor = ttk.Frame(quadro)
        seletor.pack(side="top", fill="x", pady=(0, 6))
        self.secoes_layouts = {}
        for valor, texto, dica in (("sugestoes", "Sugestões", "Layouts prontos para as fotos desta lâmina"),
                                   ("modelos", "Modelos", "Biblioteca de modelos e moldes PNG"),
                                   ("editar", "Editar", "Mover e redimensionar os espaços (duplo clique na lâmina)")):
            botao = ttk.Radiobutton(seletor, text=texto, value=valor, variable=self.secao_layouts,
                                    style="Filtro.TRadiobutton", command=self._mostrar_secao_layouts)
            botao.pack(side="left", fill="x", expand=True, padx=1)
            tema.dica(botao, dica)
            self.secoes_layouts[valor] = ttk.Frame(quadro)
        self._secao_sugestoes(self.secoes_layouts["sugestoes"])
        self._secao_modelos(self.secoes_layouts["modelos"])
        self._montar_edicao_layout(self.secoes_layouts["editar"])
        self._mostrar_secao_layouts()

    def _mostrar_secao_layouts(self, secao=None):
        if secao:
            self.secao_layouts.set(secao)
        for valor, quadro in self.secoes_layouts.items():
            if valor == self.secao_layouts.get():
                quadro.pack(side="top", fill="both", expand=True)
            else:
                quadro.pack_forget()
        if self.secao_layouts.get() == "modelos":
            self.desenhar_modelos()

    def _secao_sugestoes(self, quadro):
        cab = ttk.Frame(quadro)
        cab.pack(side="top", fill="x")
        ttk.Label(cab, text="SUGESTÕES DE LAYOUT", style="Fraco.TLabel").pack(side="left")
        self._botao(cab, "💾", self.salvar_modelo,
                    "Salvar o layout atual desta lâmina como predefinição", "Icone.TButton", width=3).pack(side="right", padx=1)
        self._botao(cab, "⟳", self.rediagramar_lamina,
                    "Diagramar esta lâmina de novo (alterna composição mantendo proporções)", "Icone.TButton", width=3).pack(side="right", padx=1)
        espacos = ttk.LabelFrame(quadro, text="Geometria (mm)", padding=6)
        espacos.pack(side="bottom", fill="x", pady=4)
        self.canvas_sugestoes = tk.Canvas(quadro, bg=tema.PAINEL, highlightthickness=0, height=130)
        self.canvas_sugestoes.pack(side="top", fill="both", expand=True, pady=(4, 6))
        self.canvas_sugestoes.bind("<ButtonPress-1>", self._sugestao_clicada)
        self.canvas_sugestoes.bind("<Configure>", lambda e: self.desenhar_sugestoes())
        self.canvas_sugestoes.bind("<MouseWheel>", lambda e: self.canvas_sugestoes.yview_scroll(-int(e.delta / 120), "units"))
        for texto, dica_txt, var, de, ate, passo in (
            ("⬚ Margem", "Margem externa da página em milímetros", self.margem_var, 0, 60, 1),
            ("⇔ Espaço", "Distância entre as fotos em milímetros", self.espacamento_var, 0, 40, 0.5),
            ("⊞ Fotos/Lâm.", "Quantidade padrão de fotos por lâmina na diagramação", self.fotos_lamina_var, 1, 12, 1)
        ):
            linha = ttk.Frame(espacos)
            linha.pack(fill="x", pady=1)
            rot = ttk.Label(linha, text=texto)
            rot.pack(side="left")
            tema.dica(rot, dica_txt)
            sp = ttk.Spinbox(linha, from_=de, to=ate, increment=passo, textvariable=var, width=6,
                             command=self._mudou_config)
            sp.pack(side="right")
            tema.dica(sp, dica_txt)
        for var in (self.margem_var, self.espacamento_var, self.fotos_lamina_var):
            var.trace_add("write", lambda *a: self.agendar("config", 500, self._mudou_config))
        self._botao(espacos, "⟳ Aplicar em todas as lâminas", self.rediagramar_todas,
                    "Recalcular todas as lâminas com esta margem, espaçamento e motor elástico").pack(fill="x", pady=(6, 0))

    def _secao_modelos(self, quadro):
        self.so_compativeis = tk.BooleanVar(value=True)
        ttk.Checkbutton(quadro, text="Só os que cabem as fotos desta lâmina", variable=self.so_compativeis,
                        command=self.desenhar_modelos).pack(side="top", anchor="w")
        lado = ttk.Frame(quadro)
        lado.pack(side="top", fill="x", pady=(4, 0))
        ttk.Label(lado, text="Modelo de página vai na:").pack(side="left")
        self.pagina_modelo = tk.IntVar(value=0)
        ttk.Radiobutton(lado, text="Esquerda", value=0, variable=self.pagina_modelo).pack(side="left", padx=4)
        ttk.Radiobutton(lado, text="Direita", value=1, variable=self.pagina_modelo).pack(side="left")
        linha = ttk.Frame(quadro)
        linha.pack(side="bottom", fill="x", pady=(2, 0))
        self._botao(linha, "✓ Aplicar", self.aplicar_modelo_selecionado,
                    "Aplicar modelo selecionado na lâmina (ajustando proporções sem distorcer)",
                    "PrimarioCompacto.TButton").pack(side="left", fill="x", expand=True, padx=(0, 2))
        self._botao(linha, "💾", self.salvar_modelo,
                    "Salvar o layout atual desta lâmina como modelo", "Icone.TButton", width=3).pack(side="left", padx=1)
        self._botao(linha, "🖼+", self.importar_png,
                    "Importar molde PNG com janelas transparentes", "Icone.TButton", width=4).pack(side="left", padx=1)
        self._botao(linha, "📁+", self.importar_pasta_png,
                    "Importar todos os moldes PNG de uma pasta", "Icone.TButton", width=4).pack(side="left", padx=1)
        self._botao(linha, "🗑", self.apagar_modelo,
                    "Apagar o modelo selecionado", "Icone.TButton", width=3).pack(side="left", padx=(1, 0))
        self.canvas_modelos = tk.Canvas(quadro, bg=tema.PAINEL, highlightthickness=0, height=120)
        self.canvas_modelos.pack(side="top", fill="both", expand=True, pady=6)
        self.canvas_modelos.bind("<ButtonPress-1>", self._modelo_clicado)
        self.canvas_modelos.bind("<Double-Button-1>", lambda e: self.aplicar_modelo_selecionado())
        self.canvas_modelos.bind("<MouseWheel>", lambda e: self.canvas_modelos.yview_scroll(-int(e.delta / 120), "units"))
        self.canvas_modelos.bind("<Configure>", lambda e: self.desenhar_modelos())

    def _montar_edicao_layout(self, quadro):
        caixa = ttk.LabelFrame(quadro, text="Ferramentas de Espaço (Estilo Photoshop)", padding=6)
        caixa.pack(side="top", fill="x", pady=(0, 4))
        barra_ic = ttk.Frame(caixa)
        barra_ic.pack(fill="x", pady=(0, 4))
        alternar = ttk.Checkbutton(barra_ic, text="⬚ Mover", variable=self.editar_layout,
                                   style="Alternar.Toolbutton", command=self._mudou_modo_layout)
        alternar.pack(side="left", padx=(0, 2))
        tema.dica(alternar, "Ferramenta Mover/Transformar Espaços (Tecla L ou duplo clique na lâmina).\n"
                            "Ctrl+clique seleciona vários · Shift ao arrastar alça libera Proporção Livre · Alt desliga o ímã.")
        chk_igual = ttk.Checkbutton(barra_ic, text="＝", variable=self.igualar_no_grupo,
                                    style="Alternar.Toolbutton")
        chk_igual.pack(side="left", padx=1)
        tema.dica(chk_igual, "Ao redimensionar várias fotos selecionadas juntas, iguala os tamanhos automaticamente")
        self._botao(barra_ic, "✂", self.editar_foto_selecionada,
                    "Recortar / Enquadrar foto dentro do espaço selecionado (Enter)",
                    "Icone.TButton", width=3).pack(side="left", padx=1)
        self._botao(barra_ic, "＋", self.adicionar_espaco,
                    "Adicionar novo espaço de foto na lâmina",
                    "Icone.TButton", width=3).pack(side="left", padx=1)
        self._botao(barra_ic, "❐", self.duplicar_espaco,
                    "Duplicar o espaço selecionado",
                    "Icone.TButton", width=3).pack(side="left", padx=1)
        self._botao(barra_ic, "↑", lambda: self.ordem_espaco(True),
                    "Trazer espaço selecionado para a frente",
                    "Icone.TButton", width=3).pack(side="left", padx=1)
        self._botao(barra_ic, "↓", lambda: self.ordem_espaco(False),
                    "Enviar espaço selecionado para trás",
                    "Icone.TButton", width=3).pack(side="left", padx=1)
        self._botao(barra_ic, "🗑", self.apagar_espaco_selecionado,
                    "Apagar espaço selecionado (reorganiza os demais espaços da página)",
                    "Icone.TButton", width=3).pack(side="left", padx=1)
        posicao = ttk.Menubutton(barra_ic, text="⊞", style="Compacto.TMenubutton", width=3)
        tema.dica(posicao, "Alinhar, distribuir ou igualar tamanho dos espaços selecionados")
        menu = tk.Menu(posicao, tearoff=0)
        for texto, acao in (("＝ Igualar tamanho das fotos selecionadas", "igualar"),
                            ("⇕ Preencher página útil (sem distorcer)", "pagina_util"),
                            ("⊡ Usar proporção padrão da foto", "proporcao_foto"),
                            ("↔ Centralizar na horizontal (na página)", "centro_h"),
                            ("↕ Centralizar na vertical", "centro_v"),
                            ("⬒ Página inteira (até a sangria)", "pagina"),
                            ("▭ Lâmina inteira", "lamina")):
            menu.add_command(label=texto, command=lambda a=acao: self.alinhar_espaco(a))
        posicao["menu"] = menu
        posicao.pack(side="right", padx=(2, 0))
        ttk.Label(caixa, text="Dica: Redimensione pela alça mantendo proporção; segure Shift para Proporção Livre.",
                  style="Fraco.TLabel", wraplength=290).pack(anchor="w", pady=(0, 3))
        numeros = ttk.Frame(caixa)
        numeros.pack(fill="x", pady=(2, 0))
        self.num_vars = {}
        self._num_campos = []
        for coluna, (chave, rotulo) in enumerate((("x", "X"), ("y", "Y"), ("w", "Larg."), ("h", "Alt."))):
            ttk.Label(numeros, text=rotulo, style="Fraco.TLabel").grid(row=0, column=coluna, sticky="w", padx=1)
            var = tk.StringVar()
            campo = ttk.Spinbox(numeros, from_=-50, to=2000, increment=1, textvariable=var, width=6,
                                command=self._aplicar_numeros)
            campo.grid(row=1, column=coluna, sticky="ew", padx=1)
            campo.bind("<Return>", lambda e: self._aplicar_numeros())
            campo.bind("<FocusOut>", lambda e: self._aplicar_numeros())
            numeros.columnconfigure(coluna, weight=1)
            self.num_vars[chave] = var
            self._num_campos.append(campo)
        ttk.Label(numeros, text="mm", style="Fraco.TLabel").grid(row=1, column=4, padx=(2, 0))

    def _aba_fundo(self, quadro):
        topo = ttk.Frame(quadro)
        topo.pack(side="top", fill="x")
        self.rotulo_fundo = ttk.Label(topo, text="", style="Titulo.TLabel")
        self.rotulo_fundo.pack(side="left")
        self._botao(topo, "Cor...", self.escolher_fundo, "Cor de fundo do alvo selecionado (Lâmina, Página Esquerda ou Direita)").pack(side="right")
        self.amostra_fundo = tk.Label(topo, width=3, bg="#ffffff", relief="flat", cursor="hand2")
        self.amostra_fundo.pack(side="right", padx=4)
        self.amostra_fundo.bind("<Button-1>", lambda e: self.escolher_fundo())

        # Alvo do fundo (Lâmina inteira, Página Esquerda ou Página Direita) + Fusão suave no meio
        div_quadro = ttk.LabelFrame(quadro, text="Preenchimento e Mistura de Páginas", padding=6)
        div_quadro.pack(side="top", fill="x", pady=(6, 2))
        linha_alvo = ttk.Frame(div_quadro)
        linha_alvo.pack(fill="x", pady=(0, 4))
        for val, txt, dica_txt in (
            ("lamina", "▭ Lâmina inteira", "Edita o fundo da lâmina inteira"),
            ("esquerda", "◧ Pág. Esq.", "Edita o fundo apenas da página esquerda (permite misturar 2 fundos)"),
            ("direita", "◨ Pág. Dir.", "Edita o fundo apenas da página direita (permite misturar 2 fundos)"),
        ):
            rb = ttk.Radiobutton(linha_alvo, text=txt, value=val, variable=self.fundo_alvo,
                                 style="Filtro.TRadiobutton", command=self._atualizar_fundo_ui)
            rb.pack(side="left", fill="x", expand=True, padx=1)
            tema.dica(rb, dica_txt)

        linha_fusao = ttk.Frame(div_quadro)
        linha_fusao.pack(fill="x", pady=(2, 0))
        rot_fusao = ttk.Label(linha_fusao, text="Fusão no meio", width=12)
        rot_fusao.pack(side="left")
        tema.dica(rot_fusao, "Suaviza a transição na dobra central quando esquerda e direita têm fundos diferentes")
        self.rotulo_fundo_fusao = ttk.Label(linha_fusao, text="0%", style="Fraco.TLabel", width=4, anchor="e")
        self.rotulo_fundo_fusao.pack(side="right")
        self.escala_fundo_fusao = ttk.Scale(linha_fusao, from_=0, to=100, variable=self.fundo_fusao,
                                            command=lambda _v: self._mudou_fusao_fundo(marcar=False))
        self.escala_fundo_fusao.pack(side="right", fill="x", expand=True, padx=6)
        self.escala_fundo_fusao.bind("<ButtonPress-1>", lambda e: self.marcar_historico())
        self.escala_fundo_fusao.bind("<ButtonRelease-1>", lambda e: self._mudou_fusao_fundo(marcar=True))
        self.escala_fundo_fusao.bind("<Double-Button-1>", lambda e: (self.fundo_fusao.set(0), self._mudou_fusao_fundo(marcar=True)))

        imagem = ttk.LabelFrame(quadro, text="Imagem de fundo", padding=6)
        imagem.pack(side="top", fill="x", pady=(4, 4))
        self.rotulo_fundo_imagem = ttk.Label(imagem, text="", style="Fraco.TLabel", wraplength=300, justify="left")
        self.rotulo_fundo_imagem.pack(anchor="w")
        linha = ttk.Frame(imagem)
        linha.pack(fill="x", pady=(4, 2))
        self._botao(linha, "Arquivo...", self.escolher_imagem_fundo,
                    "Escolher um arquivo de imagem para o fundo.\nTambém dá para arrastar uma imagem do Explorer "
                    "com Shift apertado para a lâmina.").pack(side="left", fill="x", expand=True, padx=(0, 2))
        self._botao(linha, "Foto selecionada", self.usar_foto_como_fundo,
                    "Usar a foto selecionada (painel de fotos ou espaço) como fundo").pack(side="left", fill="x", expand=True)
        self.fundo_modo = tk.StringVar(value="preencher")
        self.fundo_opacidade = tk.DoubleVar(value=100)
        self.fundo_desfoque = tk.DoubleVar(value=0)
        self.fundo_pb = tk.BooleanVar(value=False)
        self._controles_fundo = []
        linha = ttk.Frame(imagem)
        linha.pack(fill="x", pady=(4, 2))
        ttk.Label(linha, text="Modo").pack(side="left")
        combo = ttk.Combobox(linha, textvariable=self.fundo_modo, state="readonly", width=10, values=list(MODOS_FUNDO))
        combo.pack(side="left", padx=6)
        combo.bind("<<ComboboxSelected>>", lambda e: self._mudou_fundo_imagem(marcar=True))
        self._controles_fundo.append(combo)
        caixa_pb = ttk.Checkbutton(linha, text="P&B", variable=self.fundo_pb,
                                   command=lambda: self._mudou_fundo_imagem(marcar=True))
        caixa_pb.pack(side="right")
        self._controles_fundo.append(caixa_pb)
        self._rotulos_fundo = {}
        for texto, var, ate in (("Opacidade", self.fundo_opacidade, 100), ("Desfoque", self.fundo_desfoque, 50)):
            linha = ttk.Frame(imagem)
            linha.pack(fill="x", pady=1)
            ttk.Label(linha, text=texto, width=9).pack(side="left")
            rotulo = ttk.Label(linha, text="", style="Fraco.TLabel", width=4, anchor="e")
            rotulo.pack(side="right")
            escala = ttk.Scale(linha, from_=0, to=ate, variable=var, command=lambda v: self._mudou_fundo_imagem(marcar=False))
            escala.pack(side="right", fill="x", expand=True, padx=6)
            escala.bind("<ButtonPress-1>", lambda e: self.marcar_historico())
            escala.bind("<ButtonRelease-1>", lambda e: self.fundo_ajustado(final=True))
            self._controles_fundo.append(escala)
            self._rotulos_fundo[texto] = rotulo
        linha = ttk.Frame(imagem)
        linha.pack(fill="x", pady=(4, 0))
        ajustar = self._botao(linha, "Ajustar cor...", lambda: self.abrir_editor_fundo(),
                              "Brilho, contraste, saturação, matiz e tingir com uma cor\n(também: duplo clique numa imagem da galeria)")
        ajustar.pack(side="left", fill="x", expand=True, padx=(0, 2))
        tirar = self._botao(linha, "Tirar imagem", self.tirar_imagem_fundo, "Remove a imagem do alvo selecionado (Lâmina, Pág. Esq. ou Pág. Dir.)")
        tirar.pack(side="left", fill="x", expand=True)
        self._controles_fundo += [ajustar, tirar]
        self._botao(quadro, "Aplicar este fundo em todas as lâminas", self.fundo_em_todas).pack(side="top", fill="x", pady=(2, 4))

        pasta = ttk.LabelFrame(quadro, text="Pasta de fundos", padding=6)
        pasta.pack(side="top", fill="both", expand=True, pady=(2, 0))
        linha = ttk.Frame(pasta)
        linha.pack(side="top", fill="x")
        self._botao(linha, "Pasta de fundos...", self.escolher_pasta_fundos,
                    "Escolher uma pasta com imagens de fundo (subpastas também entram)").pack(side="left")
        self.botao_recentes_fundos = ttk.Menubutton(linha, text="Recentes", style="Compacto.TMenubutton")
        self.menu_recentes_fundos = tk.Menu(self.botao_recentes_fundos, tearoff=0, postcommand=self._montar_recentes_fundos)
        self.botao_recentes_fundos["menu"] = self.menu_recentes_fundos
        self.botao_recentes_fundos.pack(side="left", padx=4)
        self._botao(linha, "Atualizar", lambda: self.atualizar_fundos(forcar=True),
                    "Ler a pasta de novo (imagens novas ou apagadas)").pack(side="right")
        self.rotulo_pasta_fundos = ttk.Label(pasta, text="", style="Fraco.TLabel", wraplength=300, justify="left")
        self.rotulo_pasta_fundos.pack(side="top", anchor="w", pady=(4, 2))
        ttk.Label(pasta, text="Clique aplica na lâmina · duplo clique ajusta a cor · arraste para uma lâmina",
                  style="Fraco.TLabel", wraplength=300, justify="left").pack(side="bottom", anchor="w", pady=(4, 0))
        self.galeria_fundos = fundos.Galeria(pasta, self._fundo_atual_normalizado, self._galeria_pressionou,
                                             self.abrir_editor_fundo, self._galeria_menu)
        self.galeria_fundos.pack(side="top", fill="both", expand=True)
        self.galeria_fundos.canvas.bind("<B1-Motion>", self._mover_arrasto)
        self.galeria_fundos.canvas.bind("<ButtonRelease-1>", self._soltar_arrasto)

    def _aba_moldura(self, quadro):
        ttk.Label(quadro, text="Moldura da foto", style="Titulo.TLabel").pack(anchor="w")
        ttk.Label(quadro, text="Traçado ao redor das fotos selecionadas.", style="Fraco.TLabel",
                  wraplength=280).pack(anchor="w", pady=(2, 10))
        self.moldura_ativa = tk.BooleanVar(value=False)
        self.moldura_cor = tk.StringVar(value="#ffffff")
        self.moldura_espessura = tk.DoubleVar(value=0.5)
        self.moldura_estilo = tk.StringVar(value="simples")
        self._carregando_moldura = False
        self._moldura_alvo = None

        self.check_moldura = ttk.Checkbutton(quadro, text="Ativar traçado", variable=self.moldura_ativa,
                                             command=lambda: self._alterar_moldura(marcar=True))
        self.check_moldura.pack(anchor="w", pady=(0, 8))
        linha = ttk.Frame(quadro)
        linha.pack(fill="x", pady=3)
        ttk.Label(linha, text="Estilo").pack(side="left")
        self.combo_moldura = ttk.Combobox(linha, textvariable=self.moldura_estilo, state="readonly", width=14,
                                          values=("simples", "dupla", "pontilhada"))
        self.combo_moldura.pack(side="right")
        self.combo_moldura.bind("<<ComboboxSelected>>", self._moldura_estilo_mudou)

        linha = ttk.Frame(quadro)
        linha.pack(fill="x", pady=3)
        ttk.Label(linha, text="Cor").pack(side="left")
        self.amostra_moldura = tk.Label(linha, width=4, bg=self.moldura_cor.get(), relief="solid", bd=1)
        self.amostra_moldura.pack(side="right", padx=(4, 0))
        self.botao_cor_moldura = ttk.Button(linha, text="Escolher cor...", command=self._escolher_cor_moldura)
        self.botao_cor_moldura.pack(side="right")

        linha = ttk.Frame(quadro)
        linha.pack(fill="x", pady=(8, 2))
        self.rotulo_espessura_moldura = ttk.Label(linha, text="Espessura: 0,5 mm")
        self.rotulo_espessura_moldura.pack(side="left")
        self.escala_moldura = ttk.Scale(quadro, from_=0.1, to=3.0, variable=self.moldura_espessura,
                                        command=self._moldura_escala_mudou)
        self.escala_moldura.pack(fill="x", pady=(0, 6))
        self.escala_moldura.bind("<ButtonPress-1>", lambda _e: self._inicio_ajuste_moldura())
        self.escala_moldura.bind("<ButtonRelease-1>", self._fim_ajuste_moldura)

        ttk.Label(quadro, text="A espessura é fixa em milímetros: todas as fotos recebem exatamente o mesmo traçado.",
                  style="Fraco.TLabel", wraplength=280, justify="left").pack(anchor="w", pady=(0, 10))
        presets = ttk.Frame(quadro)
        presets.pack(fill="x", pady=2)
        for texto, valor in (("Fina", 0.25), ("Média", 0.5), ("Grossa", 1.0)):
            ttk.Button(presets, text=texto, command=lambda v=valor: self._preset_moldura(v)).pack(
                side="left", fill="x", expand=True, padx=1)
        ttk.Button(quadro, text="Aplicar às fotos selecionadas",
                   command=self._moldura_em_todas).pack(fill="x", pady=(10, 4))
        ttk.Button(quadro, text="Remover moldura", command=self._remover_moldura).pack(fill="x")
        self._controles_moldura = [self.check_moldura, self.combo_moldura, self.botao_cor_moldura,
                                   self.escala_moldura]

    def _moldura_selecionada(self):
        lamina = self._lamina()
        if lamina is None or not self._espaco_sel_valido():
            return None
        espaco = lamina["espacos"][self.espaco_sel]
        return espaco if espaco.get("foto") else None

    def _espacos_moldura_alvo(self):
        escolhidas = set(self.fotos_sel)
        if escolhidas:
            return [espaco for espaco in proj.todos_espacos(self.projeto)
                    if espaco.get("foto") and normalizar_caminho(espaco["foto"]) in escolhidas]
        lamina = self._lamina()
        selecionados = self._indices_espacos_selecionados()
        if lamina is not None and selecionados:
            return [lamina["espacos"][indice] for indice in selecionados
                    if lamina["espacos"][indice].get("foto")]
        espaco = self._moldura_selecionada()
        return [espaco] if espaco else []

    def _atualizar_depois_da_moldura(self):
        self._marcar_modificado()
        self._desenhar_espacos()
        if self._geo:
            dw, dh = self._geo[:2]
            self.agendar("moldura_render", 80, lambda: self._pedir_render(dw, dh))
        self.agendar("moldura_timeline", 160, self.desenhar_timeline)

    def _atualizar_moldura_ui(self):
        if not hasattr(self, "moldura_ativa"):
            return
        espacos = self._espacos_moldura_alvo()
        espaco = self._moldura_selecionada() or (espacos[0] if espacos else None)
        dados = ((espaco.get("edicao") or {}).get("moldura") or {}) if espaco else {}
        self._carregando_moldura = True
        self._moldura_alvo = (self.atual, self.espaco_sel) if espaco else None
        self.moldura_ativa.set(bool(dados))
        self.moldura_cor.set(dados.get("cor", "#ffffff"))
        self.moldura_espessura.set(float(dados.get("espessura_mm", 0.5)))
        self.moldura_estilo.set(dados.get("estilo", "simples"))
        self.amostra_moldura.configure(bg=self.moldura_cor.get())
        self.rotulo_espessura_moldura.configure(text="Espessura: %.2g mm" % self.moldura_espessura.get())
        estado = "normal" if espacos else "disabled"
        for controle in self._controles_moldura:
            controle.configure(state=estado)
        self._carregando_moldura = False

    def _inicio_ajuste_moldura(self):
        if self._espacos_moldura_alvo():
            self.marcar_historico()
            self._moldura_historico = True

    def _moldura_escala_mudou(self, _valor=None):
        self.rotulo_espessura_moldura.configure(text="Espessura: %.2g mm" % self.moldura_espessura.get())
        if not self._carregando_moldura:
            self.moldura_ativa.set(True)
            self._alterar_moldura(marcar=False)

    def _moldura_estilo_mudou(self, _evento=None):
        self.moldura_ativa.set(True)
        self._alterar_moldura(marcar=True)

    def _fim_ajuste_moldura(self, _evento=None):
        self._moldura_historico = False
        self.agendar("moldura_timeline", 120, self.desenhar_timeline)

    def _moldura_atual_dos_controles(self):
        return {"cor": self.moldura_cor.get(), "espessura_mm": float(self.moldura_espessura.get()),
                "estilo": self.moldura_estilo.get()}

    def _alterar_moldura(self, marcar=True):
        if self._carregando_moldura:
            return
        espacos = self._espacos_moldura_alvo()
        if not espacos:
            return
        if marcar:
            self.marcar_historico()
        for espaco in espacos:
            edicao = espaco.setdefault("edicao", {})
            if self.moldura_ativa.get():
                edicao["moldura"] = self._moldura_atual_dos_controles()
            else:
                edicao.pop("moldura", None)
        self._atualizar_depois_da_moldura()

    def _escolher_cor_moldura(self):
        cor = colorchooser.askcolor(self.moldura_cor.get(), parent=self.root, title="Cor da moldura")[1]
        if cor:
            if not self.moldura_ativa.get():
                self.moldura_ativa.set(True)
            self.moldura_cor.set(cor)
            self.amostra_moldura.configure(bg=cor)
            self._alterar_moldura(marcar=True)

    def _preset_moldura(self, espessura):
        if not self.moldura_ativa.get():
            self.moldura_ativa.set(True)
        self.moldura_espessura.set(espessura)
        self._alterar_moldura(marcar=True)

    def _moldura_em_todas(self):
        espacos = self._espacos_moldura_alvo()
        if not espacos:
            return
        self.moldura_ativa.set(True)
        dados = self._moldura_atual_dos_controles()
        self.marcar_historico()
        for espaco in espacos:
            espaco.setdefault("edicao", {})["moldura"] = dict(dados)
        self._atualizar_depois_da_moldura()

    def _remover_moldura(self):
        if not self._espacos_moldura_alvo():
            return
        self.moldura_ativa.set(False)
        self._alterar_moldura(marcar=True)

    # ---------------- Degradê de Opacidade (Estilo Photoshop) ----------------

    def _aba_degrade(self, quadro):
        ttk.Label(quadro, text="Degradê de Opacidade (Photoshop)", style="Titulo.TLabel").pack(anchor="w")
        self.rotulo_alvo_degrade = ttk.Label(
            quadro,
            text="Aplica máscara de opacidade suave nas fotos e/ou fundos selecionados.",
            style="Fraco.TLabel", wraplength=290
        )
        self.rotulo_alvo_degrade.pack(anchor="w", pady=(2, 6))

        self.degrade_em_fotos = tk.BooleanVar(value=True)
        self.degrade_em_fundos = tk.BooleanVar(value=False)
        self.degrade_fundo_lamina = tk.BooleanVar(value=True)
        self.degrade_fundo_esq = tk.BooleanVar(value=False)
        self.degrade_fundo_dir = tk.BooleanVar(value=False)

        self.degrade_ativo = tk.BooleanVar(value=False)
        self.degrade_opacidade_geral = tk.DoubleVar(value=100.0)
        self.degrade_direcao = tk.StringVar(value="direita")
        self.degrade_inicio = tk.DoubleVar(value=30.0)
        self.degrade_fim = tk.DoubleVar(value=100.0)
        self.degrade_opacidade_fim = tk.DoubleVar(value=0.0)
        self._carregando_degrade = False

        alvos_box = ttk.LabelFrame(quadro, text="Aplicar simultaneamente em", padding=6)
        alvos_box.pack(fill="x", pady=(0, 6))
        ttk.Checkbutton(alvos_box, text="Fotos selecionadas (ou espaço atual)",
                        variable=self.degrade_em_fotos, command=self._atualizar_degrade_ui).pack(anchor="w")
        ttk.Checkbutton(alvos_box, text="Fundos da lâmina atual:",
                        variable=self.degrade_em_fundos, command=self._atualizar_degrade_ui).pack(anchor="w", pady=(3, 1))
        linha_f = ttk.Frame(alvos_box)
        linha_f.pack(fill="x", padx=(14, 0))
        ttk.Checkbutton(linha_f, text="Lâmina", variable=self.degrade_fundo_lamina,
                        command=self._atualizar_degrade_ui).pack(side="left", padx=(0, 6))
        ttk.Checkbutton(linha_f, text="Pág. Esq.", variable=self.degrade_fundo_esq,
                        command=self._atualizar_degrade_ui).pack(side="left", padx=(0, 6))
        ttk.Checkbutton(linha_f, text="Pág. Dir.", variable=self.degrade_fundo_dir,
                        command=self._atualizar_degrade_ui).pack(side="left")

        self.check_degrade = ttk.Checkbutton(
            quadro, text="Ativar máscara de degradê transparente",
            variable=self.degrade_ativo, command=lambda: self._alterar_degrade(marcar=True)
        )
        self.check_degrade.pack(anchor="w", pady=(2, 6))

        dir_box = ttk.LabelFrame(quadro, text="Direção do Degradê", padding=6)
        dir_box.pack(fill="x", pady=(0, 6))
        grade_dir = ttk.Frame(dir_box)
        grade_dir.pack(fill="x")
        direcoes_ui = (
            ("→ Direita", "direita"),
            ("← Esquerda", "esquerda"),
            ("↓ Baixo", "baixo"),
            ("↑ Cima", "cima"),
            ("◎ Radial", "radial"),
            ("⬚ Bordas", "bordas"),
        )
        for idx_d, (txt_d, val_d) in enumerate(direcoes_ui):
            rb = ttk.Radiobutton(
                grade_dir, text=txt_d, value=val_d, variable=self.degrade_direcao,
                style="Filtro.TRadiobutton",
                command=lambda: (self.degrade_ativo.set(True), self._alterar_degrade(marcar=True))
            )
            rb.grid(row=idx_d // 3, column=idx_d % 3, sticky="ew", padx=1, pady=1)
            grade_dir.columnconfigure(idx_d % 3, weight=1)

        controles_box = ttk.LabelFrame(quadro, text="Transição de Opacidade (%)", padding=6)
        controles_box.pack(fill="x", pady=(0, 6))
        self._rotulos_degrade = {}
        sliders_cfg = (
            ("Opacidade base", self.degrade_opacidade_geral, 0, 100, False),
            ("Início do degradê", self.degrade_inicio, 0, 100, True),
            ("Fim do degradê", self.degrade_fim, 0, 100, True),
            ("Opacidade final", self.degrade_opacidade_fim, 0, 100, True),
        )
        for nome_s, var_s, de_s, ate_s, liga_deg in sliders_cfg:
            linha = ttk.Frame(controles_box)
            linha.pack(fill="x", pady=2)
            ttk.Label(linha, text=nome_s, width=15).pack(side="left")
            rot_val = ttk.Label(linha, text="100%", style="Fraco.TLabel", width=5, anchor="e")
            rot_val.pack(side="right")
            self._rotulos_degrade[nome_s] = rot_val

            def _cmd(_v, ligar=liga_deg):
                self._atualizar_rotulos_degrade()
                if not self._carregando_degrade:
                    if ligar:
                        self.degrade_ativo.set(True)
                    self._alterar_degrade(marcar=False)

            esc = ttk.Scale(linha, from_=de_s, to=ate_s, variable=var_s, command=_cmd)
            esc.pack(side="right", fill="x", expand=True, padx=6)
            esc.bind("<ButtonPress-1>", lambda _e: self.marcar_historico())
            esc.bind("<ButtonRelease-1>", lambda _e: self._alterar_degrade(marcar=False, final=True))

        presets = ttk.Frame(quadro)
        presets.pack(fill="x", pady=(2, 4))
        for txt_p, cfg_p in (
            ("Suave 50→100%", (50, 100, 0)),
            ("Forte 0→100%", (0, 100, 0)),
            ("Meio-termo", (25, 95, 25)),
        ):
            ttk.Button(presets, text=txt_p, command=lambda c=cfg_p: self._preset_degrade(*c)).pack(
                side="left", fill="x", expand=True, padx=1
            )

        botoes_acao = ttk.Frame(quadro)
        botoes_acao.pack(fill="x", pady=(4, 0))
        ttk.Button(botoes_acao, text="Aplicar aos selecionados", style="PrimarioCompacto.TButton",
                   command=lambda: (self.degrade_ativo.set(True), self._alterar_degrade(marcar=True, final=True))).pack(
            side="left", fill="x", expand=True, padx=(0, 2)
        )
        ttk.Button(botoes_acao, text="Remover degradê", command=self._remover_degrade).pack(
            side="left", fill="x", expand=True
        )

    def _atualizar_rotulos_degrade(self):
        if not hasattr(self, "_rotulos_degrade"):
            return
        for nome_s, var_s in (
            ("Opacidade base", self.degrade_opacidade_geral),
            ("Início do degradê", self.degrade_inicio),
            ("Fim do degradê", self.degrade_fim),
            ("Opacidade final", self.degrade_opacidade_fim),
        ):
            if nome_s in self._rotulos_degrade:
                self._rotulos_degrade[nome_s].configure(text="%d%%" % int(round(var_s.get())))

    def _alvos_degrade(self):
        """
        Retorna (lista_espacos_foto, lista_dicts_fundo) que receberão o degradê simultaneamente.
        Suporta várias fotos selecionadas (na lâmina ou na tira de fotos) e vários fundos marcados.
        """
        espacos = []
        if getattr(self, "degrade_em_fotos", None) and self.degrade_em_fotos.get():
            espacos = self._espacos_moldura_alvo()
        fundos_alvos = []
        lamina = self._lamina()
        if lamina is not None and getattr(self, "degrade_em_fundos", None) and self.degrade_em_fundos.get():
            chaves = []
            if self.degrade_fundo_lamina.get():
                chaves.append("fundo_imagem")
            if self.degrade_fundo_esq.get():
                chaves.append("fundo_imagem_esquerda")
            if self.degrade_fundo_dir.get():
                chaves.append("fundo_imagem_direita")
            if not chaves:
                _c, ch_ativa = self._chaves_fundo_alvo()
                chaves.append(ch_ativa)
            for ch in chaves:
                dados = lamina.get(ch)
                if isinstance(dados, dict) and dados.get("caminho"):
                    fundos_alvos.append((lamina, ch, dados))
        return espacos, fundos_alvos

    def _degrade_atual_dos_controles(self):
        return {
            "ativo": bool(self.degrade_ativo.get()),
            "direcao": self.degrade_direcao.get() or "direita",
            "inicio": int(round(self.degrade_inicio.get())),
            "fim": int(round(self.degrade_fim.get())),
            "opacidade_fim": int(round(self.degrade_opacidade_fim.get())),
        }

    def _atualizar_degrade_ui(self):
        if not hasattr(self, "degrade_ativo"):
            return
        espacos, fundos_alvos = self._alvos_degrade()
        resumo = []
        if espacos:
            resumo.append("%d foto(s)" % len(espacos))
        if fundos_alvos:
            resumo.append("%d fundo(s)" % len(fundos_alvos))
        self.rotulo_alvo_degrade.configure(
            text=("Alvo atual: " + " + ".join(resumo)) if resumo
            else "Selecione uma ou mais fotos (Ctrl+clique) e/ou marque fundos da lâmina."
        )
        ref_deg = None
        ref_op = 100
        if espacos:
            ed = espacos[0].get("edicao") or {}
            ref_deg = ed.get("degrade")
            ref_op = ed.get("opacidade", 100)
        elif fundos_alvos:
            _l, _ch, dados_f = fundos_alvos[0]
            ref_deg = dados_f.get("degrade")
            ref_op = dados_f.get("opacidade", 100)

        padrao = degrade_padrao()
        if isinstance(ref_deg, dict):
            padrao.update(ref_deg)
        self._carregando_degrade = True
        try:
            self.degrade_ativo.set(bool(padrao.get("ativo")))
            self.degrade_opacidade_geral.set(float(ref_op if ref_op is not None else 100))
            self.degrade_direcao.set(str(padrao.get("direcao") or "direita"))
            self.degrade_inicio.set(float(padrao.get("inicio", 30)))
            self.degrade_fim.set(float(padrao.get("fim", 100)))
            self.degrade_opacidade_fim.set(float(padrao.get("opacidade_fim", 0)))
            self._atualizar_rotulos_degrade()
        finally:
            self._carregando_degrade = False

    def _alterar_degrade(self, marcar=True, final=False):
        if getattr(self, "_carregando_degrade", False):
            return
        espacos, fundos_alvos = self._alvos_degrade()
        if not espacos and not fundos_alvos:
            return
        if marcar:
            self.marcar_historico()
        dados_deg = self._degrade_atual_dos_controles()
        op_geral = int(round(self.degrade_opacidade_geral.get()))
        for esp in espacos:
            ed = esp.setdefault("edicao", {})
            ed["opacidade"] = op_geral
            if dados_deg["ativo"]:
                ed["degrade"] = dict(dados_deg)
            else:
                ed.pop("degrade", None)
        for lamina, ch, dados_f in fundos_alvos:
            novo_f = dict(dados_f)
            novo_f["opacidade"] = op_geral
            if dados_deg["ativo"]:
                novo_f["degrade"] = dict(dados_deg)
            else:
                novo_f.pop("degrade", None)
            lamina[ch] = novo_f
        self._marcar_modificado()
        if self._geo:
            dw, dh = self._geo[:2]
            self.agendar("degrade_render", 25 if not final else 5, lambda: self._pedir_render(dw, dh))
        if final or marcar:
            self.agendar("degrade_timeline", 180, self.desenhar_timeline)

    def _preset_degrade(self, inicio, fim, op_fim):
        self.degrade_ativo.set(True)
        self.degrade_inicio.set(inicio)
        self.degrade_fim.set(fim)
        self.degrade_opacidade_fim.set(op_fim)
        self._atualizar_rotulos_degrade()
        self._alterar_degrade(marcar=True, final=True)

    def _remover_degrade(self):
        self.degrade_ativo.set(False)
        self.degrade_opacidade_geral.set(100)
        self._atualizar_rotulos_degrade()
        self._alterar_degrade(marcar=True, final=True)

    def _atalhos(self):
        r = self.root
        r.bind("<Control-z>", lambda e: self._se_nao_digitando(e, self.desfazer))
        r.bind("<Control-y>", lambda e: self._se_nao_digitando(e, self.refazer))
        r.bind("<Control-s>", lambda e: self.salvar_projeto())
        r.bind("<Control-S>", lambda e: self.salvar_projeto(como=True))
        r.bind("<Control-o>", lambda e: self.abrir_projeto())
        r.bind("<Control-n>", lambda e: self.novo_projeto())
        r.bind("<Control-v>", lambda e: self._se_nao_digitando(e, self.colar))
        r.bind("<Control-e>", lambda e: self.abrir_exportar())
        for tecla, dx, dy in (("Left", -1, 0), ("Right", 1, 0), ("Up", 0, -1), ("Down", 0, 1)):
            r.bind("<%s>" % tecla, lambda e, dx=dx, dy=dy: self._se_nao_digitando(e, lambda: self._seta(dx, dy, SETA_MM)))
            r.bind("<Shift-%s>" % tecla, lambda e, dx=dx, dy=dy: self._se_nao_digitando(e, lambda: self._seta(dx, dy, SETA_GRANDE_MM)))
        r.bind("<Control-Left>", lambda e: self._se_nao_digitando(e, lambda: self.mover_lamina_passo(-1)))
        r.bind("<Control-Right>", lambda e: self._se_nao_digitando(e, lambda: self.mover_lamina_passo(1)))
        r.bind("<Control-Up>", lambda e: self._se_nao_digitando(e, lambda: self.mover_lamina_passo(-1)))
        r.bind("<Control-Down>", lambda e: self._se_nao_digitando(e, lambda: self.mover_lamina_passo(1)))
        r.bind("<Delete>", lambda e: self._se_nao_digitando(e, self._tecla_apagar))
        r.bind("<Control-d>", lambda e: self._se_nao_digitando(e, self.duplicar_espaco))
        r.bind("<Return>", lambda e: self._se_nao_digitando(e, self.editar_foto_selecionada))
        r.bind("<Escape>", lambda e: self._se_nao_digitando(e, lambda: self.alternar_modo_layout(False)))
        for tecla in ("<Key-l>", "<Key-L>"):
            r.bind(tecla, lambda e: self._se_nao_digitando(e, self.alternar_modo_layout))

    def _se_nao_digitando(self, evento, funcao):
        if isinstance(evento.widget, (tk.Entry, ttk.Entry, ttk.Spinbox, ttk.Combobox, ttk.Scale, tk.Text)):
            return None
        funcao()
        return "break"

    def _registrar_drop(self):
        if TkinterDnD is None:
            self.registrar("Arrastar arquivos do Explorer não está disponível (instale tkinterdnd2).")
            return
        for widget, funcao in ((self.root, self._drop_geral), (self.canvas, self._drop_lamina),
                               (self.canvas_fotos, self._drop_geral), (self.canvas_timeline, self._drop_timeline)):
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind("<<Drop>>", funcao)

    # ==================================================================
    # Utilidades de janela
    # ==================================================================

    def agendar(self, chave, ms, funcao):
        anterior = self._agendados.pop(chave, None)
        if anterior:
            self.root.after_cancel(anterior)

        def rodar():
            self._agendados.pop(chave, None)
            funcao()

        self._agendados[chave] = self.root.after(ms, rodar)

    def registrar(self, texto):
        self.registro.append(texto)
        del self.registro[:-800]
        self.rotulo_status.configure(text=texto.splitlines()[0] if texto else "")

    def mostrar_registro(self):
        janela = tk.Toplevel(self.root)
        janela.title("Registro")
        janela.configure(bg=tema.FUNDO)
        janela.geometry("760x420")
        texto = tk.Text(janela, bg=tema.PAINEL_2, fg=tema.TEXTO, insertbackground=tema.TEXTO, relief="flat")
        texto.pack(fill="both", expand=True)
        texto.insert("end", "\n".join(self.registro))
        texto.see("end")

    def marcar_historico(self):
        self.historico.marcar(self.projeto)
        self._marcar_modificado()

    def desfazer(self):
        if self.historico.desfazer(self.projeto):
            self._depois_de_desfazer("Desfeito.")
        else:
            self.registrar("Nada para desfazer.")

    def refazer(self):
        if self.historico.refazer(self.projeto):
            self._depois_de_desfazer("Refeito.")
        else:
            self.registrar("Nada para refazer.")

    def _depois_de_desfazer(self, texto):
        self._definir_produto(self.projeto.get("produto") or self.produto["nome"])
        self._carregar_ui_do_projeto()
        self._marcar_modificado()
        self.atualizar_tudo()
        self.registrar(texto)

    def _marcar_modificado(self):
        self.modificado = True
        self._atualizar_titulo()

    def _atualizar_titulo(self):
        nome = self.projeto.get("nome") or "Novo álbum"
        curto = nome if len(nome) <= 26 else nome[:24] + "…"
        self.rotulo_nome.configure(text=curto + (" *" if self.modificado else ""))
        self.root.title("%s%s - Álbum" % (nome, " *" if self.modificado else ""))

    def _cfg(self):
        return diagramar.configuracao(self.produto, self.projeto.get("config"))

    def _modelos(self):
        return modelos_lamina.listar_modelos()

    # ==================================================================
    # Tarefas em segundo plano
    # ==================================================================

    def executar_quando_livre(self, titulo, funcao, ao_fim=None):
        """Como executar, mas espera a tarefa atual terminar em vez de recusar."""
        if self.tarefa is not None:
            self.root.after(400, lambda: self.executar_quando_livre(titulo, funcao, ao_fim))
            return
        self.executar(titulo, funcao, ao_fim)

    def executar(self, titulo, funcao, ao_fim=None, janela=None):
        """
        funcao(tarefa) roda numa thread; ao_fim(resultado, erro) volta na janela.
        janela (Exportar, Tratamento) recebe os avisos e o andamento enquanto roda.
        """
        if self.tarefa is not None:
            messagebox.showinfo("Aguarde", "Ainda estou terminando: %s" % self.tarefa[0], parent=janela or self.root)
            return False
        tarefa = Tarefa(self)
        self.tarefa = (titulo, tarefa)
        self.janela_tarefa = janela
        self.botao_parar.configure(state="normal")
        self.barra.configure(value=0, maximum=1)
        self.registrar(titulo + "...")

        def rodar():
            resultado, erro = None, None
            try:
                resultado = funcao(tarefa)
            except Exception as e:
                erro = e
                self.fila.put(("aviso", "ERRO: %s" % e))
                traceback.print_exc()
            self.fila.put(("fim", ao_fim, resultado, erro))

        threading.Thread(target=rodar, daemon=True).start()
        return True

    def parar_tarefa(self):
        if self.tarefa:
            self.tarefa[1].parada.set()
            self.registrar("Parando...")

    def _ler_fila(self):
        try:
            while True:
                msg = self.fila.get_nowait()
                tipo = msg[0]
                janela = self.janela_tarefa if self.janela_tarefa is not None and self.janela_tarefa.winfo_exists() else None
                if tipo == "aviso":
                    self.registrar(msg[1])
                    if janela:
                        janela.aviso(msg[1])
                elif tipo == "progresso":
                    self.barra.configure(maximum=max(1, msg[2]), value=msg[1])
                    if janela:
                        janela.progresso(msg[1], msg[2])
                elif tipo == "fim":
                    _t, ao_fim, resultado, erro = msg
                    self.tarefa = None
                    self.janela_tarefa = None
                    self.botao_parar.configure(state="disabled")
                    self.barra.configure(value=0)
                    if ao_fim:
                        try:
                            ao_fim(resultado, erro)
                        except Exception as e:
                            traceback.print_exc()
                            messagebox.showerror("Erro", str(e), parent=self.root)
                    elif erro:
                        messagebox.showerror("Erro", str(erro), parent=self.root)
                elif tipo == "render":
                    self._receber_render(*msg[1:])
                elif tipo == "miniatura_timeline":
                    self._receber_timeline(*msg[1:])
                elif tipo == "foto_trocada":
                    self._foto_trocada(msg[1], msg[2])
                elif tipo == "arquivo_mudou":
                    self._foto_trocada(msg[1], msg[1])
                elif tipo == "analisada":
                    self.info[normalizar_caminho(msg[1])] = msg[2]
                    self._depois_de_trocar_foto(msg[1])
                elif tipo == "chamar":
                    msg[1]()
        except queue.Empty:
            pass
        self.root.after(80, self._ler_fila)

    # ==================================================================
    # Projeto
    # ==================================================================

    def _confirmar_descartar(self):
        if not self.modificado:
            return True
        resposta = messagebox.askyesnocancel("Álbum", "Salvar as mudanças do álbum atual?", parent=self.root)
        if resposta is None:
            return False
        if resposta:
            return self.salvar_projeto()
        return True

    def _definir_produto(self, nome):
        for produto in self.produtos:
            if produto["nome"].casefold() == str(nome).casefold():
                self.produto = produto
                break
        self.projeto["produto"] = self.produto["nome"]
        self.produto_var.set(self.produto["nome"])

    def _carregar_projeto(self, projeto, caminho=None):
        self.projeto = projeto
        self.caminho_projeto = caminho
        self._definir_produto(projeto.get("produto") or self.produto["nome"])
        self.historico.limpar()
        self.atual = 0
        self.espaco_sel = None
        self.fotos_sel.clear()
        self._tl_pil.clear()
        self._tl_tk.clear()
        self.modificado = False
        self._carregar_ui_do_projeto()
        self.atualizar_tudo()

    def _carregar_ui_do_projeto(self):
        cfg = self._cfg()
        self._carregando = True
        try:
            self.margem_var.set(cfg["margem_mm"])
            self.espacamento_var.set(cfg["espacamento_mm"])
            self.fotos_lamina_var.set(cfg["fotos_por_lamina"])
        finally:
            self._carregando = False
        self.atualizar_fundos()

    def novo_projeto(self, silencioso=False):
        if not silencioso and not self._confirmar_descartar():
            return
        self._carregar_projeto(proj.novo(self.produto["nome"]))
        self.info = {}
        self.registrar("Álbum novo. Arraste fotos ou uma pasta para a janela.")

    def abrir_projeto(self, caminho=None):
        if not self._confirmar_descartar():
            return
        if not caminho:
            caminho = filedialog.askopenfilename(parent=self.root, title="Abrir álbum",
                                                 filetypes=[("Álbum", "*" + proj.EXTENSAO), ("Todos", "*.*")])
        if not caminho:
            return
        try:
            projeto = proj.abrir(caminho)
        except Exception as e:
            messagebox.showerror("Abrir", str(e), parent=self.root)
            return
        self.info = {}
        self._carregar_projeto(projeto, caminho)
        entrada.adicionar_recente("projeto", caminho)
        faltam = proj.faltando(projeto)
        if faltam:
            if messagebox.askyesno("Fotos não encontradas",
                                   "%s foto(s) do álbum não estão mais no lugar.\n\nQuer indicar a pasta nova?" % len(faltam),
                                   parent=self.root):
                self.consertar_caminhos()
        self._analisar_projeto()

    def salvar_projeto(self, como=False):
        caminho = self.caminho_projeto
        if como or not caminho:
            pasta = self.projeto.get("pasta_fotos") or str(Path.home())
            caminho = filedialog.asksaveasfilename(
                parent=self.root, title="Salvar álbum", initialdir=pasta,
                initialfile=(self.projeto.get("nome") or "album") + proj.EXTENSAO,
                filetypes=[("Álbum", "*" + proj.EXTENSAO)], defaultextension=proj.EXTENSAO,
            )
            if not caminho:
                return False
        try:
            caminho = proj.salvar(self.projeto, caminho)
        except OSError as e:
            messagebox.showerror("Salvar", str(e), parent=self.root)
            return False
        self.caminho_projeto = str(caminho)
        self.projeto["nome"] = Path(caminho).name[: -len(proj.EXTENSAO)]
        proj.salvar(self.projeto, caminho)
        self.modificado = False
        self._atualizar_titulo()
        entrada.adicionar_recente("projeto", caminho)
        self.registrar("Salvo em %s" % caminho)
        return True

    def _montar_recentes(self):
        self.menu_recentes.delete(0, "end")
        recentes = entrada.listar_recentes()
        if not recentes:
            self.menu_recentes.add_command(label="(nada ainda)", state="disabled")
        for r in recentes:
            rotulo = ("Álbum: " if r["tipo"] == "projeto" else "Pasta: ") + r["caminho"]
            if r["tipo"] == "projeto":
                self.menu_recentes.add_command(label=rotulo, command=lambda c=r["caminho"]: self.abrir_projeto(c))
            else:
                self.menu_recentes.add_command(label=rotulo, command=lambda c=r["caminho"]: self._abrir_pasta_recente(c))

    def _abrir_pasta_recente(self, pasta):
        if self.projeto["fotos"] and not self._confirmar_descartar():
            return
        self.novo_projeto(silencioso=True)
        self.adicionar_caminhos([pasta])

    def _trocou_produto(self, _evento=None):
        nome = self.produto_var.get()
        if nome == self.produto["nome"]:
            return
        self.marcar_historico()
        self._definir_produto(nome)
        self._carregar_ui_do_projeto()
        self._tl_pil.clear()
        self._tl_tk.clear()
        self.atualizar_tudo()
        if any(e.get("foto") for e in proj.todos_espacos(self.projeto)):
            if messagebox.askyesno("Produto", "Diagramar o álbum de novo para o tamanho %s?" % nome, parent=self.root):
                self.diagramar_tudo(False)

    def sair(self):
        if not self._confirmar_descartar():
            return
        self.gravar_arranjo()
        self.observador.parar()
        analise.gravar_cache()
        self.root.destroy()

    # ==================================================================
    # Fotos: entrada
    # ==================================================================

    def adicionar_caminhos(self, caminhos, destino=None):
        """
        Fotos, pastas ou projetos vindos do Explorer, do Ctrl+V ou da linha de comando.
        destino: ("espaco", lamina, espaco) ou ("lamina", lamina) para já colocar as fotos.
        """
        fotos, projetos = entrada.expandir(caminhos)
        if projetos and not fotos:
            self.abrir_projeto(str(projetos[0]))
            return
        if not fotos:
            self.registrar("Nenhuma foto (JPG, PNG ou TIF) encontrada no que foi solto.")
            return
        novo = not self.projeto["fotos"] and not self.projeto["laminas"]
        if novo:
            pasta = entrada.pasta_comum(fotos)
            produto = entrada.adivinhar_produto(pasta, fotos)
            if produto:
                self._definir_produto(produto)
            self.projeto["nome"] = pasta.name if pasta else "Novo álbum"
            self.projeto["pasta_fotos"] = str(pasta) if pasta else ""
            self.projeto["pasta_saida"] = entrada.sugerir_saida(pasta)
            if pasta:
                entrada.adicionar_recente("pasta", pasta)
            self._carregar_ui_do_projeto()
            if produto:
                self.registrar("Produto %s reconhecido pelo nome da pasta." % produto)
        self.marcar_historico()
        novas = proj.adicionar_fotos(self.projeto, fotos)
        self.atualizar_fotos()
        if not novas:
            self.registrar("Essas fotos já estão no projeto.")
            if destino:
                self._colocar_no_destino([str(f) for f in fotos], destino)
            return

        def trabalho(t):
            return analise.analisar_fotos(novas, ao_progresso=lambda n, total, c: t.progresso(n, total), parar=t.parar)

        def fim(resultado, erro):
            if erro:
                messagebox.showerror("Fotos", str(erro), parent=self.root)
                return
            info, erros = resultado
            self.info.update(info)
            for caminho, mensagem in erros.items():
                self.registrar("Não deu para ler %s: %s" % (Path(caminho).name, mensagem))
            self.registrar("%s foto(s) adicionada(s)." % len(info))
            if destino:
                self._colocar_no_destino(novas, destino)
            elif novo:
                self.diagramar_tudo(False)
            self.atualizar_tudo()

        self.executar_quando_livre("Analisando %s foto(s)" % len(novas), trabalho, fim)

    def _analisar_projeto(self):
        caminhos = [f["caminho"] for f in self.projeto["fotos"] if Path(f["caminho"]).is_file()]

        def trabalho(t):
            return analise.analisar_fotos(caminhos, ao_progresso=lambda n, total, c: t.progresso(n, total), parar=t.parar)

        def fim(resultado, erro):
            if resultado:
                self.info.update(resultado[0])
            self.atualizar_tudo()

        self.executar_quando_livre("Lendo as fotos do álbum", trabalho, fim)

    def escolher_fotos(self):
        caminhos = filedialog.askopenfilenames(parent=self.root, title="Adicionar fotos",
                                               filetypes=[("Imagens", "*.jpg *.jpeg *.png *.tif *.tiff"), ("Todos", "*.*")])
        if caminhos:
            self.adicionar_caminhos(list(caminhos))

    def escolher_pasta(self):
        pasta = filedialog.askdirectory(parent=self.root, title="Adicionar pasta de fotos")
        if pasta:
            self.adicionar_caminhos([pasta])

    def colar(self):
        caminhos = entrada.caminhos_da_area_de_transferencia(self.root)
        if caminhos:
            destino = None
            if isinstance(self.atual, int) and self.espaco_sel is not None:
                destino = ("espaco", self.atual, self.espaco_sel)
            self.adicionar_caminhos(caminhos, destino)
        else:
            self.registrar("Nada para colar: copie fotos ou uma pasta no Explorer (Ctrl+C).")

    def _drop_geral(self, evento):
        self.adicionar_caminhos(entrada.caminhos_do_drop(self.root, evento.data))
        return evento.action

    def _drop_lamina(self, evento):
        caminhos = entrada.caminhos_do_drop(self.root, evento.data)
        x = evento.x_root - self.canvas.winfo_rootx()
        y = evento.y_root - self.canvas.winfo_rooty()
        destino = None
        if self._shift_no_drop(evento) and len(caminhos) == 1 and Path(caminhos[0]).suffix.lower() in EXTENSOES \
                and self._dentro_da_lamina(x, y) and self._dono_do_fundo() is not None:
            self.definir_imagem_fundo(caminhos[0])
            return evento.action
        if isinstance(self.atual, int) and self.projeto["laminas"]:
            indice = self._espaco_em(x, y)
            if indice is not None:
                destino = ("espaco", self.atual, indice)
            elif self._dentro_da_lamina(x, y):
                destino = ("lamina", self.atual)
        self.adicionar_caminhos(caminhos, destino)
        return evento.action

    @staticmethod
    def _shift_no_drop(evento):
        if "shift" in str(getattr(evento, "modifiers", "") or "").lower():
            return True
        if not hasattr(evento, "modifiers"):
            return False
        try:
            import win32api
            return win32api.GetKeyState(0x10) < 0
        except Exception:
            return False

    def _drop_timeline(self, evento):
        caminhos = entrada.caminhos_do_drop(self.root, evento.data)
        item = self._timeline_em(self._eixo_tl_root(evento.x_root, evento.y_root))
        destino = ("lamina", item) if isinstance(item, int) else None
        self.adicionar_caminhos(caminhos, destino)
        return evento.action

    def _colocar_no_destino(self, fotos, destino):
        fotos = [f for f in fotos if normalizar_caminho(f) in self.info]
        if not fotos:
            return
        if destino[0] == "espaco":
            _t, li, ei = destino
            self.colocar_foto(fotos[0], li, ei, marcar=False)
            if len(fotos) > 1:
                self.adicionar_na_lamina(fotos[1:], li, marcar=False)
        else:
            self.adicionar_na_lamina(fotos, destino[1], marcar=False)

    def ordenar_fotos(self):
        self.marcar_historico()
        proj.ordenar_fotos(self.projeto, self.info, self.ordem_var.get())
        self.atualizar_fotos()

    def remover_fotos_selecionadas(self):
        fotos = self._fotos_escolhidas()
        if not fotos:
            return
        if not messagebox.askyesno("Remover", "Tirar %s foto(s) do projeto? Os arquivos não são apagados." % len(fotos), parent=self.root):
            return
        self.marcar_historico()
        proj.remover_fotos(self.projeto, fotos)
        self.fotos_sel.clear()
        self.atualizar_tudo()

    def alternar_destaque(self, fotos):
        self.marcar_historico()
        for caminho in fotos:
            registro = proj.registro_foto(self.projeto, caminho)
            if registro:
                registro["destaque"] = not registro.get("destaque")
        self.atualizar_fotos()

    def consertar_caminhos(self):
        pasta = filedialog.askdirectory(parent=self.root, title="Onde estão as fotos agora?")
        if not pasta:
            return
        self.marcar_historico()
        consertadas, faltam = entrada.consertar_caminhos(self.projeto, pasta)
        messagebox.showinfo("Fotos", "%s foto(s) encontradas.%s" % (
            consertadas, ("\n%s ainda não foram achadas." % len(faltam)) if faltam else ""), parent=self.root)
        self._analisar_projeto()

    def criar_enviar_para(self):
        try:
            atalho = entrada.instalar_enviar_para()
            messagebox.showinfo("Enviar para", "Pronto. Clique com o botão direito numa pasta de fotos > "
                                "Enviar para > %s." % atalho.stem, parent=self.root)
        except Exception as e:
            messagebox.showerror("Enviar para", str(e), parent=self.root)

    # ==================================================================
    # Diagramação
    # ==================================================================

    def abrir_diagramacao(self):
        if not proj.fotos_para_diagramar(self.projeto, self.info):
            self.registrar("Adicione fotos antes de diagramar.")
            return
        cfg = self._cfg()
        janela = tk.Toplevel(self.root)
        janela.title("Diagramação automática")
        janela.transient(self.root)
        janela.resizable(False, False)
        janela.grab_set()
        quadro = ttk.Frame(janela, padding=16)
        quadro.pack(fill="both", expand=True)
        ttk.Label(quadro, text="Diagramação automática", style="Titulo.TLabel").pack(anchor="w")
        ttk.Label(quadro, text="Escolha como organizar as fotos nas lâminas.",
                  style="Fraco.TLabel").pack(anchor="w", pady=(2, 12))

        opcoes = ttk.LabelFrame(quadro, text="Organização", padding=10)
        opcoes.pack(fill="x")
        cenario = tk.BooleanVar(value=bool(cfg.get("reconhecer_cenarios", True)))
        rostos = tk.BooleanVar(value=bool(cfg.get("reconhecer_rostos", True)))
        preencher_extras = tk.BooleanVar(value=bool(cfg.get("preencher_espacos_extras", False)))
        repetidas = tk.BooleanVar(value=bool(cfg.get("excluir_repetidas", False)))
        ttk.Checkbutton(opcoes, text="Reconhecer mudanças de cenário e separar as lâminas",
                        variable=cenario).pack(anchor="w", pady=2)
        ttk.Checkbutton(opcoes, text="Reconhecer rostos e priorizar o enquadramento neles",
                        variable=rostos).pack(anchor="w", pady=2)
        ttk.Checkbutton(opcoes, text="Sobrepor uma foto da mesma lâmina sem repetir imagens",
                        variable=preencher_extras).pack(anchor="w", pady=2)
        ttk.Checkbutton(opcoes, text="Excluir fotos repetidas (mantém uma cópia)",
                        variable=repetidas).pack(anchor="w", pady=2)

        numeros = ttk.LabelFrame(quadro, text="Quantidade e espaços", padding=10)
        numeros.pack(fill="x", pady=(10, 0))
        paginas = max(1, int(self.produto["paginas_por_lamina"]))
        vars_num = {
            "laminas_desejadas": tk.IntVar(value=int(cfg.get("laminas_desejadas", self.produto["laminas_min"]))),
            "fotos_por_pagina": tk.IntVar(value=max(1, int(round(cfg["fotos_por_lamina"] / float(paginas))))),
            "max_fotos_pagina": tk.IntVar(value=max(1, min(diagramar.MAX_POR_PAGINA,
                                           int(cfg["max_fotos_lamina"]) // paginas))),
            "margem_mm": tk.DoubleVar(value=float(cfg["margem_mm"])),
            "espacamento_mm": tk.DoubleVar(value=float(cfg["espacamento_mm"])),
        }
        campos = (
            ("Lâminas desejadas", "laminas_desejadas", 1, self.produto["laminas_max"], 1),
            ("Fotos por página (quantidade desejada)", "fotos_por_pagina", 1, diagramar.MAX_POR_PAGINA, 1),
            ("Máximo de fotos por página", "max_fotos_pagina", 1, diagramar.MAX_POR_PAGINA, 1),
            ("Margem (mm)", "margem_mm", 0, 60, 1),
            ("Espaçamento entre fotos (mm)", "espacamento_mm", 0, 40, 0.5),
        )
        for texto, chave, minimo, maximo, passo in campos:
            linha = ttk.Frame(numeros)
            linha.pack(fill="x", pady=2)
            ttk.Label(linha, text=texto).pack(side="left")
            ttk.Spinbox(linha, from_=minimo, to=maximo, increment=passo,
                        textvariable=vars_num[chave], width=8).pack(side="right")
        ttk.Label(numeros, text="A quantidade de fotos por página tem prioridade; o total de lâminas pode variar para respeitar essa meta e o mínimo do produto.",
                  style="Fraco.TLabel", wraplength=340, justify="left").pack(anchor="w", pady=(5, 0))

        botoes = ttk.Frame(quadro)
        botoes.pack(fill="x", pady=(14, 0))

        def iniciar():
            try:
                cfg_novo = {chave: var.get() for chave, var in vars_num.items()}
                cfg_novo["laminas_desejadas"] = int(cfg_novo["laminas_desejadas"])
                cfg_novo["fotos_por_pagina"] = int(cfg_novo["fotos_por_pagina"])
                cfg_novo["max_fotos_pagina"] = int(cfg_novo["max_fotos_pagina"])
                cfg_novo["margem_mm"] = float(cfg_novo["margem_mm"])
                cfg_novo["espacamento_mm"] = float(cfg_novo["espacamento_mm"])
            except (tk.TclError, ValueError):
                messagebox.showwarning("Diagramação", "Confira os valores numéricos.", parent=janela)
                return
            if cfg_novo["max_fotos_pagina"] < cfg_novo["fotos_por_pagina"]:
                messagebox.showwarning("Diagramação", "O máximo por página deve ser igual ou maior que a quantidade desejada.",
                                       parent=janela)
                return
            if any(e.get("foto") for e in proj.todos_espacos(self.projeto)) and not messagebox.askyesno(
                    "Refazer diagramação", "As lâminas existentes serão substituídas. É possível desfazer depois.",
                    parent=janela):
                return
            cfg_novo["reconhecer_cenarios"] = cenario.get()
            cfg_novo["reconhecer_rostos"] = rostos.get()
            cfg_novo["preencher_espacos_extras"] = preencher_extras.get()
            cfg_novo["excluir_repetidas"] = repetidas.get()
            cfg_novo["fotos_por_lamina"] = cfg_novo.pop("fotos_por_pagina") * paginas
            cfg_novo["max_fotos_lamina"] = cfg_novo["max_fotos_pagina"] * paginas
            janela.grab_release()
            janela.destroy()
            self.diagramar_tudo(False, cfg_novo, repetidas.get())

        ttk.Button(botoes, text="Cancelar", command=janela.destroy).pack(side="right")
        ttk.Button(botoes, text="Diagramar", style="Primario.TButton", command=iniciar).pack(side="right", padx=(0, 6))
        janela.bind("<Escape>", lambda _e: janela.destroy())
        janela.bind("<Return>", lambda _e: iniciar())
        janela.update_idletasks()
        x = self.root.winfo_rootx() + max(0, (self.root.winfo_width() - janela.winfo_width()) // 2)
        y = self.root.winfo_rooty() + max(0, (self.root.winfo_height() - janela.winfo_height()) // 2)
        janela.geometry("+%d+%d" % (x, y))
        janela.focus_set()

    def _sem_repetidas(self, fotos, parar, ao_progresso=None):
        """Mantém a primeira imagem de cada grupo com hash visual praticamente igual."""
        hashes = []
        saida, removidas = [], []
        for indice, foto in enumerate(fotos, start=1):
            if parar():
                return None, []
            try:
                valor = analise.hash_perceptual(foto["caminho"])
            except (OSError, ValueError):
                valor = None
            repetida = False
            if valor is not None:
                for anterior, caminho in hashes:
                    if (valor ^ anterior).bit_count() <= 3:
                        repetida = True
                        removidas.append(foto["caminho"])
                        break
                if not repetida:
                    hashes.append((valor, foto["caminho"]))
            if not repetida:
                saida.append(foto)
            if ao_progresso:
                ao_progresso(indice, len(fotos))
        return saida, removidas

    def diagramar_tudo(self, confirmar=True, configuracao=None, excluir_repetidas=False):
        fotos = proj.fotos_para_diagramar(self.projeto, self.info)
        if not fotos:
            self.registrar("Adicione fotos antes de diagramar.")
            return
        if confirmar and any(e.get("foto") for e in proj.todos_espacos(self.projeto)):
            if not messagebox.askyesno("Diagramar", "Refazer o álbum inteiro? Os cortes e trocas feitos na mão "
                                       "serão perdidos (dá para desfazer).", parent=self.root):
                return
        produto = self.produto
        cfg = dict(self._cfg())
        if configuracao:
            cfg.update(configuracao)
        modelos = self._modelos()

        def trabalho(t):
            removidas = []
            fotos_usadas = fotos
            if excluir_repetidas:
                fotos_usadas, removidas = self._sem_repetidas(fotos, t.parar, t.progresso)
                if fotos_usadas is None:
                    return None
            resultado = diagramar.diagramar_album(fotos_usadas, produto, cfg, modelos, ao_progresso=t.progresso)
            return resultado, removidas

        def fim(resultado, erro):
            if erro:
                messagebox.showerror("Diagramar", str(erro), parent=self.root)
                return
            if resultado is None:
                return
            (laminas, sobras), removidas = resultado
            self.marcar_historico()
            if configuracao:
                self.projeto.setdefault("config", {}).update(configuracao)
            self.projeto["laminas"] = laminas
            self.atual = 0
            self.espaco_sel = None
            texto = "Álbum diagramado: %s lâminas." % len(laminas)
            if removidas:
                texto += " %s foto(s) repetida(s) ignorada(s)." % len(removidas)
            self.registrar(texto)
            if sobras:
                messagebox.showinfo("Diagramar", "%s foto(s) não couberam no máximo de %s lâminas do produto."
                                    % (len(sobras), self.produto["laminas_max"]), parent=self.root)
            self.atualizar_tudo()

        self.executar("Diagramando", trabalho, fim)

    def distribuir_nao_usadas(self):
        fotos = proj.fotos_para_diagramar(self.projeto, self.info, somente_nao_usadas=True)
        if not fotos:
            self.registrar("Todas as fotos já estão no álbum.")
            return
        cfg = self._cfg()
        grupos, _sobras = diagramar.dividir(fotos, dict(self.produto, laminas_min=0), cfg)
        self.marcar_historico()
        for grupo in grupos:
            melhor = diagramar.opcoes_lamina(grupo, self.produto, cfg, self._modelos(), maximo=1)[0]
            lamina = diagramar.lamina_vazia(cfg)
            lamina.update({k: melhor[k] for k in ("espacos", "sobreposicoes", "modelo")})
            self.projeto["laminas"].append(lamina)
        self.registrar("%s lâmina(s) novas com as fotos que faltavam." % len(grupos))
        self.atualizar_tudo()

    def _proporcao_efetiva(self, caminho, edicao=None):
        dados = self.info.get(normalizar_caminho(caminho))
        prop = proj.dados_proporcao(dados) if dados else 1.5
        if edicao and int(edicao.get("rot", 0)) % 180 == 90:
            prop = 1.0 / max(0.1, float(prop))
        return max(0.1, float(prop))

    def _mapa_proporcoes(self, lamina=None):
        mapa = {}
        for caminho, dados in self.info.items():
            mapa[caminho] = proj.dados_proporcao(dados)
        if lamina:
            for e in lamina.get("espacos", []):
                if e.get("foto"):
                    mapa[e["foto"]] = self._proporcao_efetiva(e["foto"], e.get("edicao"))
        return mapa

    def _fotos_info(self, caminhos, lamina=None):
        edicoes = {}
        if lamina:
            for e in lamina.get("espacos", []):
                if e.get("foto"):
                    edicoes[normalizar_caminho(e["foto"])] = e.get("edicao") or {}
        saida = []
        for caminho in caminhos:
            chave = normalizar_caminho(caminho)
            dados = self.info.get(chave)
            if dados:
                registro = proj.registro_foto(self.projeto, caminho) or {}
                prop = self._proporcao_efetiva(caminho, edicoes.get(chave))
                saida.append({
                    "caminho": caminho,
                    "proporcao": prop,
                    "data": dados.get("data"),
                    "destaque": bool(registro.get("destaque")),
                    "rosto": dados.get("rosto"),
                    "assinatura_cenario": dados.get("assinatura_cenario"),
                    "pasta": proj.pasta_da_foto(caminho),
                    "nome_pasta": proj.nome_pasta_da_foto(caminho),
                })
        return saida

    def _lamina(self, indice=None):
        indice = self.atual if indice is None else indice
        if isinstance(indice, int) and 0 <= indice < len(self.projeto["laminas"]):
            return self.projeto["laminas"][indice]
        return None

    def _aplicar_opcao(self, lamina, opcao):
        """Troca o layout, mantendo giro e ajustes de cada foto (o corte é refeito)."""
        antigas = {}
        for espaco in lamina.get("espacos", []):
            if espaco.get("foto"):
                ed = dict(espaco.get("edicao") or {})
                ed.pop("corte", None)
                antigas[normalizar_caminho(espaco["foto"])] = ed
        lamina["espacos"] = [dict(e, edicao=dict(antigas.get(normalizar_caminho(e["foto"]), {})) if e.get("foto") else {})
                             for e in opcao["espacos"]]
        lamina["sobreposicoes"] = list(opcao.get("sobreposicoes") or [])
        lamina["modelo"] = opcao.get("modelo")

    def relayout(self, indice, fotos=None, marcar=True):
        lamina = self._lamina(indice)
        if lamina is None:
            return
        if fotos is None:
            fotos = [e["foto"] for e in lamina["espacos"] if e.get("foto")]
        if marcar:
            self.marcar_historico()
        opcoes = diagramar.opcoes_lamina(self._fotos_info(fotos, lamina), self.produto, self._cfg(), self._modelos(), maximo=12)
        if not opcoes:
            return
        atual_geom = tuple((round(e["x"], 3), round(e["y"], 3), round(e["w"], 3), round(e["h"], 3))
                           for e in lamina.get("espacos", []))
        escolhida = opcoes[0]
        if len(opcoes) > 1:
            for idx_op, op in enumerate(opcoes):
                geom = tuple((round(e["x"], 3), round(e["y"], 3), round(e["w"], 3), round(e["h"], 3))
                             for e in op.get("espacos", []))
                if geom == atual_geom:
                    escolhida = opcoes[(idx_op + 1) % len(opcoes)]
                    break
        self._aplicar_opcao(lamina, escolhida)

    def rediagramar_lamina(self):
        lamina = self._lamina()
        if lamina is None:
            return
        antes = [dict(e) for e in lamina.get("espacos", [])]
        self.relayout(self.atual)
        self.espaco_sel = None
        self.atualizar_lamina_atual()
        self._animar_transicao_layout(antes, lamina.get("espacos", []))

    def rediagramar_todas(self):
        if not self.projeto["laminas"]:
            return
        grupos = [[e["foto"] for e in l["espacos"] if e.get("foto")] for l in self.projeto["laminas"]]
        infos = [self._fotos_info(g) for g in grupos]
        produto, cfg, modelos = self.produto, self._cfg(), self._modelos()

        def trabalho(t):
            saida = []
            for n, fotos in enumerate(infos, start=1):
                if t.parar():
                    return None
                saida.append(diagramar.opcoes_lamina(fotos, produto, cfg, modelos, maximo=1)[0])
                t.progresso(n, len(infos))
            return saida

        def fim(resultado, erro):
            if not resultado:
                return
            self.marcar_historico()
            for lamina, opcao in zip(self.projeto["laminas"], resultado):
                self._aplicar_opcao(lamina, opcao)
            self.atualizar_tudo()

        self.executar("Refazendo as lâminas", trabalho, fim)

    def _mudou_config(self):
        if getattr(self, "_carregando", False):
            return
        try:
            cfg = {"margem_mm": float(self.margem_var.get()), "espacamento_mm": float(self.espacamento_var.get()),
                   "fotos_por_lamina": int(self.fotos_lamina_var.get())}
        except (tk.TclError, ValueError):
            return
        atual = self._cfg()
        if any(abs(float(cfg[k]) - float(atual[k])) > 1e-9 for k in cfg):
            self.projeto.setdefault("config", {}).update(cfg)
            self._marcar_modificado()
            self.atualizar_sugestoes()

    def colocar_foto(self, caminho, li, ei, marcar=True):
        lamina = self._lamina(li)
        if lamina is None or not (0 <= ei < len(lamina["espacos"])):
            return
        antes = [dict(e) for e in lamina.get("espacos", [])] if li == self.atual else None
        if marcar:
            self.marcar_historico()
        espaco = lamina["espacos"][ei]
        espaco["foto"] = str(caminho)
        espaco["edicao"] = {}
        if not self.editar_layout.get():
            diagramar.ajustar_espacos_lamina(lamina, self._mapa_proporcoes(lamina), self.produto, self._cfg())
        self.atual, self.espaco_sel = li, ei
        self.atualizar_lamina_atual()
        if antes is not None:
            self._animar_transicao_layout(antes, lamina.get("espacos", []))

    def adicionar_na_lamina(self, fotos, li, marcar=True):
        lamina = self._lamina(li)
        if lamina is None:
            return
        antes = [dict(e) for e in lamina.get("espacos", [])] if li == self.atual else None
        atuais = [e["foto"] for e in lamina["espacos"] if e.get("foto")]
        self.relayout(li, atuais + [str(f) for f in fotos], marcar=marcar)
        self.atual, self.espaco_sel = li, None
        self.atualizar_lamina_atual()
        if antes is not None:
            self._animar_transicao_layout(antes, lamina.get("espacos", []))

    def trocar_espacos(self, li, a, lj, b):
        la, lb = self._lamina(li), self._lamina(lj)
        if la is None or lb is None:
            return
        antes = [dict(e) for e in lb.get("espacos", [])] if li == lj == self.atual else None
        self.marcar_historico()
        ea, eb = la["espacos"][a], lb["espacos"][b]
        ea["foto"], eb["foto"] = eb.get("foto"), ea.get("foto")
        eda, edb = dict(ea.get("edicao") or {}), dict(eb.get("edicao") or {})
        eda.pop("corte", None)
        edb.pop("corte", None)
        ea["edicao"], eb["edicao"] = edb, eda
        if not self.editar_layout.get():
            diagramar.ajustar_espacos_lamina(la, self._mapa_proporcoes(la), self.produto, self._cfg())
            if lb is not la:
                diagramar.ajustar_espacos_lamina(lb, self._mapa_proporcoes(lb), self.produto, self._cfg())
        self.atual, self.espaco_sel = lj, b
        self.atualizar_tudo()
        if antes is not None:
            self._animar_transicao_layout(antes, lb.get("espacos", []))

    def tirar_foto(self, li, ei, marcar=True):
        lamina = self._lamina(li)
        if lamina is None or not (0 <= ei < len(lamina["espacos"])):
            return
        if marcar:
            self.marcar_historico()
        lamina["espacos"][ei]["foto"] = None
        lamina["espacos"][ei]["edicao"] = {}
        self.atualizar_lamina_atual()

    def tirar_foto_selecionada(self):
        if isinstance(self.atual, int) and self.espaco_sel is not None:
            self.tirar_foto(self.atual, self.espaco_sel)

    def apagar_espaco(self, li, ei):
        lamina = self._lamina(li)
        if lamina is None:
            return
        antes = [dict(e) for e in lamina.get("espacos", [])] if li == self.atual else None
        self.marcar_historico()
        del lamina["espacos"][ei]
        lamina["modelo"] = None
        if not self.editar_layout.get() and lamina["espacos"]:
            diagramar.ajustar_espacos_lamina(lamina, self._mapa_proporcoes(lamina), self.produto, self._cfg())
        self.espaco_sel = None
        self.atualizar_lamina_atual()
        if antes is not None:
            self._animar_transicao_layout(antes, lamina.get("espacos", []))

    def adicionar_lamina(self):
        self.marcar_historico()
        posicao = (self.atual + 1) if isinstance(self.atual, int) else 0
        self.projeto["laminas"].insert(posicao, diagramar.lamina_vazia(self._cfg()))
        self.atual, self.espaco_sel = posicao, None
        self.atualizar_tudo()

    def remover_lamina(self, indice=None):
        indice = self.atual if indice is None else indice
        if not isinstance(indice, int) or self._lamina(indice) is None:
            return
        self.marcar_historico()
        del self.projeto["laminas"][indice]
        self.atual = max(0, min(indice, len(self.projeto["laminas"]) - 1))
        self.espaco_sel = None
        self.atualizar_tudo()

    def mover_lamina(self, de, para):
        laminas = self.projeto["laminas"]
        if de == para or not (0 <= de < len(laminas)) or not (0 <= para < len(laminas)):
            return
        self.marcar_historico()
        laminas.insert(para, laminas.pop(de))
        self.atual, self.espaco_sel = para, None
        self.atualizar_tudo()
        self._mostrar_na_timeline(para)
        self.registrar("Lâmina movida para a posição %s de %s." % (para + 1, len(laminas)))

    def mover_lamina_passo(self, passo):
        if isinstance(self.atual, int) and self._lamina() is not None:
            self.mover_lamina(self.atual, self.atual + passo)

    def mover_lamina_para(self, destino, indice=None):
        indice = self.atual if indice is None else indice
        n = len(self.projeto["laminas"])
        if not isinstance(indice, int) or self._lamina(indice) is None:
            return
        if destino == "inicio":
            destino = 0
        elif destino == "fim":
            destino = n - 1
        elif destino == "perguntar":
            destino = simpledialog.askinteger("Mover lâmina", "Nova posição da lâmina %s (1 a %s):" % (indice + 1, n),
                                              parent=self.root, minvalue=1, maxvalue=n, initialvalue=indice + 1)
            if destino is None:
                return
            destino -= 1
        self.mover_lamina(indice, max(0, min(n - 1, int(destino))))

    def duplicar_lamina(self, indice=None):
        indice = self.atual if indice is None else indice
        lamina = self._lamina(indice) if isinstance(indice, int) else None
        if lamina is None:
            return
        self.marcar_historico()
        nova = proj.copia(lamina)
        nova["id"] = diagramar.novo_id()
        self.projeto["laminas"].insert(indice + 1, nova)
        self.atual, self.espaco_sel = indice + 1, None
        self.atualizar_tudo()
        self._mostrar_na_timeline(self.atual)
        self.registrar("Lâmina %s duplicada." % (indice + 1))

    # ==================================================================
    # Modelos
    # ==================================================================

    def _pagina_do_espaco(self, espaco):
        if self.produto["paginas_por_lamina"] < 2:
            return 0
        lw, _lh = lamina_mm(self.produto)
        centro = (espaco["x"] + espaco["w"] / 2) * lw
        for pagina in range(self.produto["paginas_por_lamina"]):
            x, _y, w, _h = area_pagina_mm(self.produto, pagina)
            if x <= centro < x + w:
                return pagina
        return 0

    def aplicar_modelo_selecionado(self):
        lamina = self._lamina()
        modelo = modelos_lamina.carregar_modelo(self._modelo_sel) if self._modelo_sel else None
        if lamina is None or modelo is None:
            self.registrar("Escolha uma lâmina e um modelo.")
            return
        antes = [dict(e) for e in lamina.get("espacos", [])]
        self.marcar_historico()
        if modelo.get("escopo") == "pagina" and self.produto["paginas_por_lamina"] > 1:
            pagina = self.pagina_modelo.get()
            ficam = [e for e in lamina["espacos"] if self._pagina_do_espaco(e) != pagina]
            saem = [e for e in lamina["espacos"] if self._pagina_do_espaco(e) == pagina]
            fotos = self._fotos_info([e["foto"] for e in saem if e.get("foto")], lamina)
            novos, sobre, sobras = diagramar.espacos_com_modelo(modelo, fotos, self.produto, pagina, self._cfg())
            lamina["espacos"] = ficam + novos
            outras = [s for s in lamina.get("sobreposicoes", [])
                      if abs(s["x"] - modelos_lamina.area_do_modelo(self.produto, "pagina", pagina)[0]) > 1e-6]
            lamina["sobreposicoes"] = outras + ([sobre] if sobre else [])
        else:
            fotos = self._fotos_info([e["foto"] for e in lamina["espacos"] if e.get("foto")], lamina)
            novos, sobre, sobras = diagramar.espacos_com_modelo(modelo, fotos, self.produto, None, self._cfg())
            lamina["espacos"] = novos
            lamina["sobreposicoes"] = [sobre] if sobre else []
        lamina["modelo"] = modelo["id"]
        if sobras:
            self.registrar("%s foto(s) não couberam no modelo e voltaram para as não usadas." % len(sobras))
        self.espaco_sel = None
        self.atualizar_lamina_atual()
        self._animar_transicao_layout(antes, lamina.get("espacos", []))

    def salvar_modelo(self):
        lamina = self._lamina()
        if lamina is None or not lamina["espacos"]:
            return
        nome = simpledialog.askstring("Salvar modelo", "Nome do modelo:", parent=self.root)
        if nome:
            modelos_lamina.modelo_da_lamina(lamina, nome)
            self.desenhar_modelos()
            self.registrar("Modelo '%s' salvo." % nome)

    def importar_png(self):
        caminhos = filedialog.askopenfilenames(parent=self.root, title="PNG com os espaços transparentes",
                                               filetypes=[("PNG", "*.png")])
        erros = []
        for caminho in caminhos:
            try:
                modelos_lamina.importar_png(caminho, self.produto)
            except Exception as e:
                erros.append("%s: %s" % (Path(caminho).name, e))
        if erros:
            messagebox.showwarning("Importar", "\n".join(erros), parent=self.root)
        self.desenhar_modelos()

    def importar_pasta_png(self):
        pasta = filedialog.askdirectory(parent=self.root, title="Pasta com os moldes PNG")
        if not pasta:
            return
        modelos, erros = modelos_lamina.importar_pasta_png(pasta, self.produto)
        self.registrar("%s modelo(s) importados." % len(modelos))
        if erros:
            messagebox.showwarning("Importar", "\n".join(erros[:20]), parent=self.root)
        self.desenhar_modelos()

    def apagar_modelo(self):
        if self._modelo_sel and messagebox.askyesno("Modelos", "Apagar o modelo selecionado?", parent=self.root):
            modelos_lamina.apagar_modelo(self._modelo_sel)
            self._modelo_sel = None
            self.desenhar_modelos()

    # ==================================================================
    # Seleção e desenho
    # ==================================================================

    def _itens_timeline(self):
        return list(range(len(self.projeto["laminas"])))

    def selecionar_lamina(self, item):
        self._cancelar_animacao_layout()
        self.atual = item
        self._definir_selecao_espacos(set())
        self._ancora_espaco = None
        self.molde_sel = None
        self._remendo = None
        self.pan = [0, 0]
        self.desenhar_lamina()
        self.desenhar_timeline()
        self.atualizar_sugestoes()
        self._atualizar_fundo_ui()
        self._atualizar_degrade_ui()
        if self.editor:
            alvo = self._alvo_padrao()
            if alvo:
                self.editor.mostrar(alvo)

    def passar_lamina(self, passo):
        itens = self._itens_timeline()
        if not itens:
            return
        posicao = itens.index(self.atual) if self.atual in itens else 0
        self.selecionar_lamina(itens[max(0, min(len(itens) - 1, posicao + passo))])
        self._rolar_timeline_para(self.atual)

    def selecionar_alvo(self, alvo):
        _t, li, ei = alvo
        if li != self.atual:
            self.atual = li
            self.desenhar_timeline()
            self.atualizar_sugestoes()
        self._definir_selecao_espacos({ei}, ei)
        self._ancora_espaco = ei
        self.desenhar_lamina()
        if self.editor:
            self.editor.mostrar(alvo)

    def atualizar_tudo(self):
        itens = self._itens_timeline()
        if self.atual not in itens:
            self.atual = itens[0] if itens else 0
        lamina = self._lamina()
        if lamina is None or self.espaco_sel is None or self.espaco_sel >= len(lamina["espacos"]):
            self._definir_selecao_espacos(set())
        else:
            self.espacos_sel = {i for i in self.espacos_sel if 0 <= i < len(lamina["espacos"])}
        self._atualizar_titulo()
        self._atualizar_produto_ui()
        self.atualizar_fotos()
        self.desenhar_timeline()
        self.desenhar_lamina()
        self.atualizar_sugestoes()
        self.desenhar_modelos()
        self._atualizar_fundo_ui()
        self._atualizar_degrade_ui()
        if self.editor and self.editor.alvo:
            self.editor.mostrar(self.editor.alvo if self._alvo_valido(self.editor.alvo) else self._alvo_padrao())

    def atualizar_lamina_atual(self, timeline=True):
        self._marcar_modificado()
        self.desenhar_lamina()
        self.atualizar_fotos()
        if timeline:
            self.desenhar_timeline()
        self.atualizar_sugestoes()
        if self.editor:
            alvo = self._alvo_padrao()
            if alvo:
                self.editor.mostrar(alvo)

    def _alvo_padrao(self):
        lamina = self._lamina()
        if lamina is None or not lamina["espacos"]:
            return None
        ei = self.espaco_sel if self.espaco_sel is not None else 0
        return ("lamina", self.atual, min(ei, len(lamina["espacos"]) - 1))

    def _alvo_valido(self, alvo):
        if alvo[0] != "lamina":
            return False
        lamina = self._lamina(alvo[1])
        return lamina is not None and 0 <= alvo[2] < len(lamina["espacos"])

    def _atualizar_produto_ui(self):
        p = self.produto
        lw, lh = lamina_mm(p)
        W, H = lamina_px(p)
        self.rotulo_produto.configure(text="%.0f×%.0f mm · %s dpi" % (lw, lh, p["dpi"]))
        self.dica_produto.texto = (
            "%s\nPágina %s x %s mm, %s por lâmina\nSangria %s mm, segurança %s mm, dobra %s mm\n"
            "Lâmina: %s x %s px\nLâminas: de %s a %s" % (
                p.get("descricao") or p["nome"], p["pagina_mm"][0], p["pagina_mm"][1], p["paginas_por_lamina"],
                p["sangria_mm"], p["seguranca_mm"], p["dobra_mm"], W, H, p["laminas_min"], p["laminas_max"]))

    # ---------------- lâmina central (sem piscar e com animação) ----------------

    def _tamanho_item(self, item):
        return lamina_mm(self.produto)

    def _geometria(self):
        cw = max(100, self.canvas.winfo_width())
        ch = max(100, self.canvas.winfo_height())
        w_mm, h_mm = self._tamanho_item(self.atual)
        aspecto = w_mm / float(h_mm)
        dw = min(cw - 2 * MARGEM_LAMINA, (ch - 2 * MARGEM_LAMINA) * aspecto) * self.zoom
        dw = max(50, int(dw))
        dh = max(20, int(dw / aspecto))
        ox = (cw - dw) // 2 + self.pan[0]
        oy = (ch - dh) // 2 + self.pan[1]
        return dw, dh, ox, oy

    def _copia_para_render(self):
        n = len(self.projeto["laminas"])
        mini = {"capa": None, "laminas": [None] * n}
        if isinstance(self.atual, int) and 0 <= self.atual < n:
            mini["laminas"][self.atual] = json.loads(json.dumps(self.projeto["laminas"][self.atual]))
        return mini

    def _assinatura_lamina_render(self, item, dw, dh):
        if not (isinstance(item, int) and 0 <= item < len(self.projeto["laminas"])):
            return None
        return (self._assinatura(item), int(dw), int(dh), bool(self.guias.get()))

    def _guardar_cache_lamina(self, item, dw, dh, img, tk_img=None):
        if not (isinstance(item, int) and 0 <= item < len(self.projeto["laminas"])):
            return None
        id_lam = self.projeto["laminas"][item].get("id") or str(item)
        ass = self._assinatura_lamina_render(item, dw, dh)
        if tk_img is None:
            tk_img = ImageTk.PhotoImage(img)
        self._cache_laminas[id_lam] = (ass, (dw, dh), img.copy(), tk_img)
        self._cache_laminas.move_to_end(id_lam)
        while len(self._cache_laminas) > 24:
            self._cache_laminas.popitem(last=False)
        return tk_img

    def desenhar_lamina(self, pedir_render=True):
        itens = self._itens_timeline()
        if not self.projeto["fotos"] and not self.projeto["laminas"]:
            self.canvas.delete("all")
            self._desenhar_vazio()
            return
        if not itens:
            self.canvas.delete("all")
            self.canvas.create_text(self.canvas.winfo_width() // 2, self.canvas.winfo_height() // 2,
                                    text="Clique em 'Diagramar automático' ou em '+ Lâmina'.", fill=tema.TEXTO_FRACO,
                                    font=tema.FONTE_TITULO)
            self.rotulo_lamina.configure(text="")
            return
        dw, dh, ox, oy = self._geometria()
        self._geo = (dw, dh, ox, oy)
        lamina = self._lamina()
        id_lam = (lamina or {}).get("id") or str(self.atual)
        ass_atual = self._assinatura_lamina_render(self.atual, dw, dh)
        entrada_cache = self._cache_laminas.get(id_lam)

        tk_para_exibir = None
        precisa_worker = pedir_render

        if entrada_cache is not None:
            ass_cache, tam_cache, img_cache, tk_cache = entrada_cache
            self._cache_laminas.move_to_end(id_lam)
            if tam_cache == (dw, dh):
                tk_para_exibir = tk_cache
                self._tk_lamina = (tk_cache, (self.atual, dw, dh))
                self._imagem_lamina = (self.atual, img_cache)
                if ass_cache == ass_atual:
                    precisa_worker = False
            else:
                redim = img_cache.resize((dw, dh), Image.BILINEAR)
                self._tk_lamina_zoom = ImageTk.PhotoImage(redim)
                tk_para_exibir = self._tk_lamina_zoom
                self._imagem_lamina = (self.atual, redim)
        elif self._tk_lamina is not None and self._tk_lamina[1] == (self.atual, dw, dh):
            tk_para_exibir = self._tk_lamina[0]
        elif self._imagem_lamina is not None and self._imagem_lamina[0] == self.atual:
            imagem = self._imagem_lamina[1].resize((dw, dh), Image.BILINEAR)
            self._tk_lamina_zoom = ImageTk.PhotoImage(imagem)
            tk_para_exibir = self._tk_lamina_zoom
        else:
            # Renderização rápida imediata na troca de lâmina para NUNCA piscar branco
            try:
                mini = self._copia_para_render()
                img_imediata = exportar.renderizar_item(
                    mini, self.produto, self.info, self.atual,
                    largura=dw, guias=self.guias.get(), rapido=True
                )
                tk_para_exibir = self._guardar_cache_lamina(self.atual, dw, dh, img_imediata)
                self._tk_lamina = (tk_para_exibir, (self.atual, dw, dh))
                self._imagem_lamina = (self.atual, img_imediata)
                precisa_worker = False
            except Exception:
                tk_para_exibir = None

        self.canvas.delete("all")
        self.canvas.create_rectangle(ox + 6, oy + 8, ox + dw + 6, oy + dh + 8, fill="#0d0d0f", outline="")
        if tk_para_exibir is not None:
            self.canvas.create_image(ox, oy, image=tk_para_exibir, anchor="nw", tags="img")
        else:
            fundo = (lamina or {}).get("fundo", "#ffffff")
            self.canvas.create_rectangle(ox, oy, ox + dw, oy + dh, fill=cor_hex(fundo), outline="", tags="img")
        self._desenhar_remendo()
        self._desenhar_espacos()
        if precisa_worker:
            self._pedir_render(dw, dh)
        self._atualizar_rotulo_lamina()

    def _cancelar_animacao_layout(self):
        if self._anim_layout_id is not None:
            try:
                self.root.after_cancel(self._anim_layout_id)
            except tk.TclError:
                pass
            self._anim_layout_id = None
        self._anim_tks = []
        self.canvas.delete("anim_layout")

    def _animar_transicao_layout(self, antes_espacos, depois_espacos, duracao=0.18):
        """
        Deslize suave (~180ms, cubic ease-out) das fotos da posição antiga até a nova
        quando o layout da lâmina muda.
        """
        self._cancelar_animacao_layout()
        if self._geo is None or not antes_espacos or not depois_espacos:
            return
        lamina = self._lamina()
        if lamina is None:
            return
        dw, dh, ox, oy = self._geo
        if dw < 40 or dh < 20:
            return

        # Emparelha cada foto do novo layout com sua posição anterior
        filas_antes = {}
        for e in antes_espacos:
            if e.get("foto"):
                ch = normalizar_caminho(e["foto"])
                filas_antes.setdefault(ch, []).append(e)

        pares = []
        moveu_algo = False
        for e_dep in depois_espacos:
            foto = e_dep.get("foto")
            if not foto:
                continue
            ch = normalizar_caminho(foto)
            lista_ant = filas_antes.get(ch)
            e_ant = lista_ant.pop(0) if lista_ant else e_dep
            if any(abs(float(e_ant[k]) - float(e_dep[k])) > 0.003 for k in ("x", "y", "w", "h")):
                moveu_algo = True
            pares.append((e_ant, e_dep))

        if not moveu_algo or not pares:
            return

        # Prepara o fundo limpo da lâmina e a imagem base de cada foto uma única vez
        try:
            fundo_rgba = compor_fundo_completo(
                (dw, dh), lamina.get("fundo", "#ffffff"),
                lamina.get("fundo_imagem"), rapido=True, opcoes_pagina=lamina
            )
            fundo_rgb = fundo_rgba.convert("RGB")
            if self.guias.get():
                exportar._guias_lamina(fundo_rgb, self.produto)
            tk_fundo_limpo = ImageTk.PhotoImage(fundo_rgb)
        except Exception:
            return

        lado_max = max(dw, dh)
        px_mm = dw / float(lamina_mm(self.produto)[0])
        cor_fundo_rgb = cor_rgb(lamina.get("fundo", "#ffffff"))
        itens_anim = []
        for e_ant, e_dep in pares:
            foto = e_dep["foto"]
            ed = e_dep.get("edicao") or {}
            try:
                fonte = analise.imagem_para(foto, lado_max)
                img_t = recorte.aplicar_ajustes(recorte.transformar(fonte, ed), ed)
                rosto_t = recorte.rosto_transformado(rosto_de(self.info, foto), ed)
                itens_anim.append({
                    "ant": (float(e_ant["x"]), float(e_ant["y"]), float(e_ant["w"]), float(e_ant["h"])),
                    "dep": (float(e_dep["x"]), float(e_dep["y"]), float(e_dep["w"]), float(e_dep["h"])),
                    "img": img_t,
                    "edicao": ed,
                    "rosto_t": rosto_t,
                    "moldura": ed.get("moldura"),
                })
            except Exception:
                continue

        if not itens_anim:
            return

        inicio = time.perf_counter()

        def passo_animacao():
            self._anim_layout_id = None
            if self._geo != (dw, dh, ox, oy):
                self._cancelar_animacao_layout()
                return
            decorrido = time.perf_counter() - inicio
            t = min(1.0, max(0.0, decorrido / duracao))
            if t >= 1.0:
                self._cancelar_animacao_layout()
                self.desenhar_lamina(pedir_render=True)
                return
            # Ease-out cúbico suave
            suave = 1.0 - ((1.0 - t) ** 3)
            c = self.canvas
            c.delete("anim_layout")
            novos_tks = [tk_fundo_limpo]
            c.create_image(ox, oy, image=tk_fundo_limpo, anchor="nw", tags="anim_layout")
            for info_f in itens_anim:
                ax, ay, aw, ah = info_f["ant"]
                bx, by, bw, bh = info_f["dep"]
                ix = ax + (bx - ax) * suave
                iy = ay + (by - ay) * suave
                iw = aw + (bw - aw) * suave
                ih = ah + (bh - ah) * suave
                x0 = int(round(ox + ix * dw))
                y0 = int(round(oy + iy * dh))
                pw = max(2, int(round(iw * dw)))
                ph = max(2, int(round(ih * dh)))
                img_f = info_f["img"]
                W, H = img_f.size
                aspecto = pw / float(ph)
                dobra_x = None
                if ix < 0.5 < ix + iw and iw > 0.05:
                    dobra_x = (0.5 - ix) / float(iw)
                corte = recorte.corte_do_espaco(W, H, aspecto, info_f["edicao"], info_f["rosto_t"],
                                                rosto_ja_transformado=True, dobra_x=dobra_x)
                rec = recorte.recortar(img_f, recorte.caixa_corte(W, H, aspecto, corte), (pw, ph), cor_fundo_rgb, rapido=True)
                if info_f["moldura"]:
                    aplicar_moldura(rec, info_f["moldura"], px_mm)
                tk_f = ImageTk.PhotoImage(rec)
                novos_tks.append(tk_f)
                c.create_image(x0, y0, image=tk_f, anchor="nw", tags="anim_layout")
            self._anim_tks = novos_tks
            c.tag_raise("anim_layout")
            c.tag_raise("espaco")
            self._anim_layout_id = self.root.after(14, passo_animacao)

        passo_animacao()

    def _atualizar_rotulo_lamina(self):
        lamina = self._lamina()
        n_fotos = sum(1 for e in lamina["espacos"] if e.get("foto")) if lamina else 0
        vazios = sum(1 for e in lamina["espacos"] if not e.get("foto")) if lamina else 0
        texto = "Lâmina %s de %s · %s foto(s)%s" % (self.atual + 1, len(self.projeto["laminas"]), n_fotos,
                                                     (" · %s espaço(s) vazio(s)" % vazios) if vazios else "")
        if lamina is not None:
            texto += ("   ·   Editando layout (Photoshop): alça redimensiona na proporção · Shift = Proporção Livre · Ctrl+clique = várias fotos"
                      if self.editar_layout.get() else "   ·   Duplo clique ou tecla L para editar o layout")
        self.rotulo_lamina.configure(text=texto)

    def _desenhar_vazio(self):
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        x0, y0, x1, y1 = cw * 0.2, ch * 0.25, cw * 0.8, ch * 0.75
        self.canvas.create_rectangle(x0, y0, x1, y1, outline=tema.BORDA, dash=(6, 6), width=2)
        self.canvas.create_text(cw / 2, ch / 2 - 18, text="Arraste fotos ou várias pastas para cá",
                                fill=tema.TEXTO, font=("Segoe UI Semibold", 16))
        self.canvas.create_text(cw / 2, ch / 2 + 16, fill=tema.TEXTO_FRACO,
                                text="As pastas ficam separadas na tira de fotos e os cenários são agrupados automaticamente")
        self.rotulo_lamina.configure(text="")

    def _desenhar_espacos(self, ocultar=None):
        c = self.canvas
        c.delete("espaco")
        if self._geo is None:
            return
        lamina = self._lamina()
        if lamina is None:
            return
        dw, dh, ox, oy = self._geo
        editando = self.editar_layout.get()
        alvo = self._alvo_sel()
        selecionados = self._indices_espacos_selecionados()
        if editando:
            for i, s in enumerate(lamina.get("sobreposicoes") or []):
                if ("molde", i) == ocultar:
                    continue
                x0, y0 = ox + s["x"] * dw, oy + s["y"] * dh
                x1, y1 = x0 + s["w"] * dw, y0 + s["h"] * dh
                if ("molde", i) == alvo:
                    c.create_rectangle(x0, y0, x1, y1, outline=tema.ALERTA, width=2, tags="espaco")
                else:
                    c.create_rectangle(x0 + 1, y0 + 1, x1 - 1, y1 - 1, outline=tema.ALERTA, dash=(2, 6), tags="espaco")
        for i, e in enumerate(lamina["espacos"]):
            if ("espaco", i) == ocultar:
                continue
            x0, y0 = ox + e["x"] * dw, oy + e["y"] * dh
            x1, y1 = x0 + e["w"] * dw, y0 + e["h"] * dh
            if not e.get("foto"):
                c.create_rectangle(x0, y0, x1, y1, fill=tema.PAINEL_3, outline=tema.BORDA, dash=(4, 3), tags="espaco")
                c.create_text((x0 + x1) / 2, (y0 + y1) / 2, text="+", fill=tema.TEXTO_FRACO,
                              font=("Segoe UI", 18), tags="espaco")
            if editando and i not in selecionados:
                c.create_rectangle(x0, y0, x1, y1, outline="#ffffff", width=1, tags="espaco")
                c.create_rectangle(x0, y0, x1, y1, outline=tema.DESTAQUE_ESCURO, dash=(4, 4), width=1, tags="espaco")
            if i in selecionados or (not editando and i == self.espaco_sel):
                c.create_rectangle(x0, y0, x1, y1, outline=tema.DESTAQUE,
                                   width=3 if i == self.espaco_sel else 2, tags="espaco")
        if editando and len(selecionados) > 1:
            grupo = self._caixa_selecao_espacos(selecionados)
            x0, y0 = ox + grupo["x"] * dw, oy + grupo["y"] * dh
            x1, y1 = x0 + grupo["w"] * dw, y0 + grupo["h"] * dh
            c.create_rectangle(x0, y0, x1, y1, outline=tema.DESTAQUE, width=1, dash=(2, 2), tags="espaco")
            for hx, hy in self._alcas(grupo).values():
                c.create_rectangle(hx - ALCA, hy - ALCA, hx + ALCA, hy + ALCA, fill="#ffffff",
                                   outline=tema.DESTAQUE, width=1, tags="espaco")
        elif editando and alvo and alvo != ocultar:
            cor = tema.DESTAQUE if alvo[0] == "espaco" else tema.ALERTA
            for hx, hy in self._alcas(self._item_layout(alvo, lamina)).values():
                c.create_rectangle(hx - ALCA, hy - ALCA, hx + ALCA, hy + ALCA, fill="#ffffff",
                                   outline=cor, width=1, tags="espaco")
        self._atualizar_numeros()
        self._atualizar_moldura_ui()
        self._atualizar_degrade_ui()

    def _desenhar_remendo(self):
        """Depois de soltar, a prévia do arrasto com o fundo limpo fica até a imagem final chegar."""
        r = self._remendo
        if not r:
            return
        if r["item"] != self.atual or r["geo"] != self._geo:
            self._remendo = None
            return
        _dw, _dh, ox, oy = self._geo
        if r.get("base_limpa_tk") is not None:
            self.canvas.create_image(ox, oy, image=r["base_limpa_tk"], anchor="nw", tags="remendo")
        if r.get("tk") is not None:
            self.canvas.create_image(r["pos"][0], r["pos"][1], image=r["tk"], anchor="nw", tags="remendo")

    def _pedir_render(self, dw, dh):
        self._avisar_fundo_faltando()
        self._versao_render += 1
        versao = self._versao_render
        mini = self._copia_para_render()
        item = self.atual
        produto = self.produto
        guias = self.guias.get()
        info = self.info

        def trabalho():
            if versao != self._versao_render:
                return
            img = exportar.renderizar_item(mini, produto, info, item, largura=dw, guias=guias, rapido=True)
            self.fila.put(("render", versao, item, dw, dh, img))

        self.pintor.pedir("principal", trabalho)

    def _receber_render(self, versao, item, dw, dh, img):
        if versao != self._versao_render:
            return
        tk_img = self._guardar_cache_lamina(item, dw, dh, img)
        if tk_img is None:
            tk_img = ImageTk.PhotoImage(img)
        self._tk_lamina = (tk_img, (item, dw, dh))
        self._imagem_lamina = (item, img.copy())
        if self._geo is None or (item, dw, dh) != (self.atual, self._geo[0], self._geo[1]):
            return
        if self._anim_layout_id is not None:
            return
        _dw, _dh, ox, oy = self._geo
        self._remendo = None
        self.canvas.delete("img")
        self.canvas.delete("remendo")
        self.canvas.create_image(ox, oy, image=tk_img, anchor="nw", tags="img")
        self.canvas.tag_raise("espaco")
        self.canvas.tag_raise("arrasto")

    def mudar_zoom(self, fator, centro=None):
        if centro is None:
            centro = (self.canvas.winfo_width() / 2, self.canvas.winfo_height() / 2)
        destino = 1.0 if fator is None else max(0.3, min(3.0, self._zoom_destino * fator))
        if abs(destino - self.zoom) < 0.001:
            return
        if self._geo is None:
            self._geo = self._geometria()
        dw, dh, ox, oy = self._geo
        fracao_x = (centro[0] - ox) / float(dw)
        fracao_y = (centro[1] - oy) / float(dh)
        inicio = self.zoom
        self._zoom_destino = destino
        comeco = time.perf_counter()
        duracao = 0.12

        def animar():
            t = min(1.0, (time.perf_counter() - comeco) / duracao)
            suave = t * t * (3 - 2 * t)
            self.zoom = inicio + (destino - inicio) * suave
            dw_n, dh_n, ox_n, oy_n = self._geometria()
            self.pan[0] += centro[0] - (ox_n + fracao_x * dw_n)
            self.pan[1] += centro[1] - (oy_n + fracao_y * dh_n)
            self.desenhar_lamina(pedir_render=False)
            if t < 1.0:
                self.agendar("zoom_animacao", 16, animar)
            else:
                self.zoom = destino
                self._geo = self._geometria()
                dw_final, dh_final = self._geo[:2]
                self.agendar("zoom_render", 60, lambda: self._pedir_render(dw_final, dh_final))

        self.agendar("zoom_animacao", 1, animar)

    def _lamina_roda(self, evento):
        if evento.delta:
            self.mudar_zoom(1.12 ** (evento.delta / 120.0), (evento.x, evento.y))
        return "break"

    def _pan_inicio(self, evento):
        self._pan_ref = (evento.x, evento.y, list(self.pan))

    def _pan_mover(self, evento):
        x0, y0, pan = self._pan_ref
        self.pan = [pan[0] + evento.x - x0, pan[1] + evento.y - y0]
        self.agendar("pan", 15, self.desenhar_lamina)

    def _pan_direito_inicio(self, evento):
        self._pan_ref_direito = (evento.x, evento.y, list(self.pan), False)
        self.canvas.configure(cursor="fleur")

    def _pan_direito_mover(self, evento):
        if not hasattr(self, "_pan_ref_direito"):
            return
        x0, y0, pan, moveu = self._pan_ref_direito
        if not moveu and abs(evento.x - x0) + abs(evento.y - y0) < 3:
            return
        self._pan_ref_direito = (x0, y0, pan, True)
        self.pan = [pan[0] + evento.x - x0, pan[1] + evento.y - y0]
        self.desenhar_lamina(pedir_render=False)

    def _pan_direito_soltar(self, evento):
        estado = getattr(self, "_pan_ref_direito", None)
        self.canvas.configure(cursor="")
        if estado is None:
            return
        del self._pan_ref_direito
        if estado[3]:
            if self._geo:
                dw, dh = self._geo[:2]
                self.agendar("pan_render", 60, lambda: self._pedir_render(dw, dh))
        else:
            self._lamina_menu(evento)

    def _espaco_em(self, x, y):
        lamina = self._lamina()
        if lamina is None or self._geo is None:
            return None
        dw, dh, ox, oy = self._geo
        u, v = (x - ox) / float(dw), (y - oy) / float(dh)
        for i in range(len(lamina["espacos"]) - 1, -1, -1):
            e = lamina["espacos"][i]
            if e["x"] <= u <= e["x"] + e["w"] and e["y"] <= v <= e["y"] + e["h"]:
                return i
        return None

    def _dentro_da_lamina(self, x, y):
        if self._geo is None:
            return False
        dw, dh, ox, oy = self._geo
        return ox <= x <= ox + dw and oy <= y <= oy + dh

    def _lamina_passou(self, evento):
        if self._editando_lamina():
            alca = self._alca_em(evento.x, evento.y)
            if alca:
                self.canvas.configure(cursor=CURSOR_ALCA[alca])
                return
            sobre = self._espaco_em(evento.x, evento.y) is not None or self._molde_em(evento.x, evento.y) is not None
            self.canvas.configure(cursor="fleur" if sobre else "")
            return
        indice = self._espaco_em(evento.x, evento.y)
        self.canvas.configure(cursor="hand2" if indice is not None else "")

    def _lamina_pressionou(self, evento):
        if self._foco() in getattr(self, "_num_campos", []):
            self._aplicar_numeros()
        self.canvas.focus_set()
        if self._editando_lamina():
            self._layout_pressionou(evento)
            return
        indice = self._espaco_em(evento.x, evento.y)
        estado = getattr(evento, "state", 0)
        com_ctrl = bool(estado & CONTROL)
        com_shift = bool(estado & SHIFT)
        selecionados = self._indices_espacos_selecionados()
        if indice is not None and (com_ctrl or com_shift):
            if com_ctrl:
                if indice in selecionados:
                    selecionados.remove(indice)
                else:
                    selecionados.add(indice)
                    self._ancora_espaco = indice
            elif com_shift:
                ancora = self._ancora_espaco if self._ancora_espaco is not None else (self.espaco_sel if self.espaco_sel is not None else indice)
                selecionados.update(range(min(ancora, indice), max(ancora, indice) + 1))
            self._definir_selecao_espacos(selecionados, indice if indice in selecionados else None)
            if len(selecionados) > 1:
                self.alternar_modo_layout(True)
            else:
                self._desenhar_espacos()
            return
        self._definir_selecao_espacos({indice} if indice is not None else set(), indice)
        self._ancora_espaco = indice
        self._desenhar_espacos()
        if indice is not None:
            lamina = self._lamina()
            if lamina["espacos"][indice].get("foto"):
                self._arrasto = {"tipo": "espaco", "lamina": self.atual, "espaco": indice,
                                 "x": evento.x_root, "y": evento.y_root, "ativo": False}
            if self.editor:
                self.editor.mostrar(("lamina", self.atual, indice))

    def _lamina_duplo(self, evento):
        """Duplo clique liga a edição do layout; já editando, abre a foto do espaço para o corte."""
        indice = self._espaco_em(evento.x, evento.y)
        if self._editando_lamina():
            if indice is not None:
                self._arrasto = None
                self.abrir_editor(("lamina", self.atual, indice))
            return
        if self._lamina() is None or (indice is None and not self._dentro_da_lamina(evento.x, evento.y)):
            return
        self._arrasto = None
        self._definir_selecao_espacos({indice} if indice is not None else set(), indice)
        self._ancora_espaco, self.molde_sel = indice, None
        self.alternar_modo_layout(True)

    def editar_foto_selecionada(self):
        if isinstance(self.atual, int) and self._espaco_sel_valido():
            self.abrir_editor(("lamina", self.atual, self.espaco_sel))
        else:
            self.registrar("Selecione um espaço da lâmina para editar a foto (corte).")

    def _lamina_menu(self, evento):
        menu = tk.Menu(self.root, tearoff=0)
        indice = self._espaco_em(evento.x, evento.y)
        molde = self._molde_em(evento.x, evento.y) if indice is None and self.editar_layout.get() else None
        if molde is not None:
            self._selecionar_alvo_layout(("molde", molde))
            menu.add_command(label="Apagar o molde PNG", command=self.apagar_molde_selecionado)
            menu.add_separator()
        if indice is not None:
            if indice not in self._indices_espacos_selecionados():
                self._definir_selecao_espacos({indice}, indice)
            else:
                self.espaco_sel = indice
            self.molde_sel = None
            self._desenhar_espacos()
            foto = self._lamina()["espacos"][indice].get("foto")
            menu.add_command(label="Editar foto (corte / Lightroom)...", accelerator="Enter",
                             command=lambda: self.abrir_editor(("lamina", self.atual, indice)))
            menu.add_command(label="Degradê de opacidade (Photoshop)...", command=self.abrir_aba_degrade)
            if len(self._indices_espacos_selecionados()) > 1:
                menu.add_command(label="Igualar tamanho das %d fotos selecionadas" % len(self._indices_espacos_selecionados()),
                                 command=lambda: self.alinhar_espaco("igualar"))
            if foto:
                menu.add_command(label="Editar no Photoshop", command=lambda: self.editar_no_photoshop([foto]))
                menu.add_command(label="Tratar a foto (Ação / Lightroom)...", command=lambda: self.abrir_tratamento([foto]))
                menu.add_separator()
                menu.add_command(label="Tirar a foto do espaço", command=lambda: self.tirar_foto(self.atual, indice))
            menu.add_command(label="Apagar o espaço", command=lambda: self.apagar_espaco(self.atual, indice))
            menu.add_command(label="Duplicar o espaço", command=self.duplicar_espaco)
            menu.add_command(label="Trazer para a frente", command=lambda: self.ordem_espaco(True))
            menu.add_command(label="Mandar para trás", command=lambda: self.ordem_espaco(False))
            menu.add_separator()
        menu.add_command(label="Sair da edição de layout" if self.editar_layout.get() else "Editar layout (duplo clique)",
                         accelerator="Esc" if self.editar_layout.get() else "L", command=self.alternar_modo_layout)
        menu.add_command(label="Novo espaço vazio", command=self.adicionar_espaco)
        menu.add_command(label="Diagramar esta lâmina de novo", command=self.rediagramar_lamina)
        menu.add_separator()
        self._itens_menu_fundo(menu)
        menu.tk_popup(evento.x_root, evento.y_root)

    def _itens_menu_fundo(self, menu):
        menu.add_command(label="Cor de fundo...", command=self.escolher_fundo)
        menu.add_command(label="Imagem de fundo...", command=self.escolher_imagem_fundo)
        menu.add_command(label="Usar foto selecionada como fundo", command=self.usar_foto_como_fundo)
        if (self._dono_do_fundo() or {}).get("fundo_imagem"):
            menu.add_command(label="Tirar imagem de fundo", command=self.tirar_imagem_fundo)

    # ---------------- edição do layout ----------------

    def _editando_lamina(self):
        return self.editar_layout.get() and isinstance(self.atual, int) and self._lamina() is not None

    def _indices_espacos_selecionados(self):
        lamina = self._lamina()
        if lamina is None or self.espaco_sel is None:
            return set()
        validos = {i for i in self.espacos_sel if 0 <= i < len(lamina["espacos"])}
        if self.espaco_sel is not None and 0 <= self.espaco_sel < len(lamina["espacos"]):
            validos.add(self.espaco_sel)
        return validos

    def _definir_selecao_espacos(self, indices, principal=None):
        lamina = self._lamina()
        validos = set(indices)
        if lamina is not None:
            validos = {i for i in validos if isinstance(i, int) and 0 <= i < len(lamina["espacos"])}
        else:
            validos.clear()
        self.espacos_sel = validos
        self.espaco_sel = principal if principal in validos else (min(validos) if validos else None)

    def _caixa_selecao_espacos(self, indices=None):
        lamina = self._lamina()
        indices = self._indices_espacos_selecionados() if indices is None else indices
        if lamina is None or not indices:
            return None
        caixas = [self._espaco_mm(lamina["espacos"][i]) for i in indices]
        x0 = min(c[0] for c in caixas)
        y0 = min(c[1] for c in caixas)
        x1 = max(c[0] + c[2] for c in caixas)
        y1 = max(c[1] + c[3] for c in caixas)
        lw, lh = self._mm()
        return {"x": x0 / lw, "y": y0 / lh, "w": (x1 - x0) / lw, "h": (y1 - y0) / lh}

    def _espaco_sel_valido(self):
        lamina = self._lamina()
        return lamina is not None and self.espaco_sel is not None and 0 <= self.espaco_sel < len(lamina["espacos"])

    def _molde_sel_valido(self):
        lamina = self._lamina()
        return (lamina is not None and self.espaco_sel is None and self.molde_sel is not None
                and self.molde_sel[0] == self.atual and 0 <= self.molde_sel[1] < len(lamina.get("sobreposicoes") or []))

    def _alvo_sel(self):
        """O que está selecionado na edição: ("espaco", i), ("molde", i) ou None."""
        if self._espaco_sel_valido():
            return ("espaco", self.espaco_sel)
        if self._molde_sel_valido():
            return ("molde", self.molde_sel[1])
        return None

    def _item_layout(self, alvo, lamina=None):
        lamina = lamina if lamina is not None else self._lamina()
        if lamina is None or alvo is None:
            return None
        lista = lamina["espacos"] if alvo[0] == "espaco" else (lamina.get("sobreposicoes") or [])
        return lista[alvo[1]] if 0 <= alvo[1] < len(lista) else None

    def _selecionar_alvo_layout(self, alvo):
        self._definir_selecao_espacos({alvo[1]} if alvo and alvo[0] == "espaco" else set(),
                                      alvo[1] if alvo and alvo[0] == "espaco" else None)
        self.molde_sel = (self.atual, alvo[1]) if alvo and alvo[0] == "molde" else None
        self._desenhar_espacos()

    def _molde_em(self, x, y):
        lamina = self._lamina()
        if lamina is None or self._geo is None:
            return None
        dw, dh, ox, oy = self._geo
        u, v = (x - ox) / float(dw), (y - oy) / float(dh)
        sobreposicoes = lamina.get("sobreposicoes") or []
        for i in range(len(sobreposicoes) - 1, -1, -1):
            s = sobreposicoes[i]
            if s["x"] <= u <= s["x"] + s["w"] and s["y"] <= v <= s["y"] + s["h"]:
                return i
        return None

    def alternar_modo_layout(self, ligar=None):
        ligar = not self.editar_layout.get() if ligar is None else ligar
        if ligar == self.editar_layout.get():
            return
        self.editar_layout.set(ligar)
        self._mudou_modo_layout()

    def _mudou_modo_layout(self):
        if self.editar_layout.get():
            self._mostrar_secao_layouts("editar")
            self.registrar("Editar layout (Photoshop): arraste alças mantendo proporção · segure Shift para Proporção Livre · "
                           "Ctrl+clique seleciona várias fotos (com tamanho igualado) · Alt desliga o ímã · Esc sai.")
        else:
            self.canvas.delete("guia_ima")
            self.canvas.delete("arrasto")
            self.molde_sel = None
            self.registrar("Edição de layout desligada. Duplo clique na lâmina para editar de novo.")
        self.canvas.configure(cursor="")
        self._desenhar_espacos()
        self._atualizar_rotulo_lamina()

    def _mm(self):
        return lamina_mm(self.produto)

    def _espaco_mm(self, e):
        lw, lh = self._mm()
        return e["x"] * lw, e["y"] * lh, e["w"] * lw, e["h"] * lh

    def _definir_espaco_mm(self, e, x, y, w, h):
        lw, lh = self._mm()
        e["x"], e["y"], e["w"], e["h"] = x / lw, y / lh, w / lw, h / lh

    def _alcas(self, e):
        dw, dh, ox, oy = self._geo
        x0, y0 = ox + e["x"] * dw, oy + e["y"] * dh
        x1, y1 = x0 + e["w"] * dw, y0 + e["h"] * dh
        xm, ym = (x0 + x1) / 2, (y0 + y1) / 2
        return {"nw": (x0, y0), "n": (xm, y0), "ne": (x1, y0), "e": (x1, ym),
                "se": (x1, y1), "s": (xm, y1), "sw": (x0, y1), "w": (x0, ym)}

    def _alca_em(self, x, y):
        alvo = self._alvo_sel()
        if self._geo is None or alvo is None:
            return None
        selecionados = self._indices_espacos_selecionados()
        item = self._caixa_selecao_espacos(selecionados) if len(selecionados) > 1 else self._item_layout(alvo)
        for nome, (hx, hy) in self._alcas(item).items():
            if abs(x - hx) <= RAIO_ALCA and abs(y - hy) <= RAIO_ALCA:
                return nome
        return None

    def _alvos_ima(self, lamina, ignorar):
        """Linhas (mm) onde as bordas e o centro do espaço grudam: (verticais, horizontais)."""
        p = self.produto
        lw, lh = self._mm()
        b, seg = p["sangria_mm"], p["seguranca_mm"]
        pw = p["pagina_mm"][0]
        margem = self._cfg()["margem_mm"]
        esp = self._cfg()["espacamento_mm"]
        xs = {0.0, lw, b, lw - b, lw / 2.0}
        ys = {0.0, lh, b, lh - b, b + seg, lh - b - seg, b + margem, lh - b - margem, lh / 2}
        for pagina in range(p["paginas_por_lamina"]):
            px, py, pw_pagina, ph_pagina = area_pagina_mm(p, pagina)
            xs |= {px, px + pw_pagina, px + pw_pagina / 2}
            ys |= {py, py + ph_pagina, py + ph_pagina / 2}
            x0 = b + pw * pagina
            xs |= {x0, x0 + pw, x0 + pw / 2, x0 + seg, x0 + pw - seg}
            ux, uy, uw, uh = area_util_pagina_mm(p, pagina, margem)
            xs |= {ux, ux + uw / 2, ux + uw}
            ys |= {uy, uy + uh / 2, uy + uh}
        for j, outro in enumerate(lamina["espacos"]):
            if ("espaco", j) == ignorar:
                continue
            ex, ey, ew, eh = self._espaco_mm(outro)
            xs |= {ex, ex + ew / 2, ex + ew, ex - esp, ex + ew + esp}
            ys |= {ey, ey + eh / 2, ey + eh, ey - esp, ey + eh + esp}
        return sorted(xs), sorted(ys)

    @staticmethod
    def _imantar(valores, alvos, tolerancia, anterior=None):
        """
        (deslocamento, linha) que leva o valor mais perto até uma linha, ou (0, None).
        A linha que já estava grudada vale até um pouco mais longe, para não pular entre duas.
        """
        if anterior is not None and valores:
            d = min((anterior - v for v in valores), key=abs)
            if abs(d) <= tolerancia * 1.6:
                return d, anterior
        melhor = None
        for v in valores:
            for a in alvos:
                d = a - v
                if abs(d) <= tolerancia and (melhor is None or abs(d) < abs(melhor[0])):
                    melhor = (d, a)
        return melhor or (0.0, None)

    def _layout_pressionou(self, evento):
        lamina = self._lamina()
        x, y = evento.x, evento.y
        estado = getattr(evento, "state", 0)
        com_shift = bool(estado & SHIFT)
        com_ctrl = bool(estado & CONTROL)
        selecionados = self._indices_espacos_selecionados()
        alca = self._alca_em(x, y)
        if alca:
            alvo = ("grupo", tuple(sorted(selecionados))) if len(selecionados) > 1 else self._alvo_sel()
        else:
            indice = self._espaco_em(x, y)
            if indice is not None:
                if com_ctrl:
                    if indice in selecionados:
                        selecionados.remove(indice)
                    else:
                        selecionados.add(indice)
                        self._ancora_espaco = indice
                    if indice not in selecionados:
                        principal = self.espaco_sel if self.espaco_sel in selecionados else None
                        self._definir_selecao_espacos(selecionados, principal)
                        self.molde_sel = None
                        self._desenhar_espacos()
                        return
                elif com_shift:
                    lamina = self._lamina()
                    ancora = self._ancora_espaco
                    if ancora is None or not 0 <= ancora < len(lamina["espacos"]):
                        ancora = self.espaco_sel if self.espaco_sel is not None else indice
                    selecionados.update(range(min(ancora, indice), max(ancora, indice) + 1))
                elif indice not in selecionados:
                    selecionados = {indice}
                    self._ancora_espaco = indice
                self._definir_selecao_espacos(selecionados, indice)
                self.molde_sel = None
                alvo = ("grupo", tuple(sorted(selecionados))) if len(selecionados) > 1 else ("espaco", indice)
            else:
                molde = self._molde_em(x, y)
                self._definir_selecao_espacos(set())
                self._ancora_espaco = None
                alvo = ("molde", molde) if molde is not None else None
        if alvo is None and not self._dentro_da_lamina(x, y):
            self.alternar_modo_layout(False)
            return
        if alvo and alvo[0] == "grupo":
            self.molde_sel = None
            self._desenhar_espacos()
        else:
            self._selecionar_alvo_layout(alvo)
        if alvo is None:
            return
        if self.editor and alvo[0] == "espaco":
            self.editor.mostrar(("lamina", self.atual, alvo[1]))
        arrasto = {"tipo": "layout", "alca": alca, "lamina": self.atual, "alvo": alvo,
                   "x": x, "y": y, "x_root": evento.x_root, "y_root": evento.y_root,
                   "ativo": False, "ima": {}}
        if alvo[0] == "grupo":
            arrasto["grupo"] = {i: self._espaco_mm(lamina["espacos"][i]) for i in alvo[1]}
            xs = [e[0] for e in arrasto["grupo"].values()]
            ys = [e[1] for e in arrasto["grupo"].values()]
            x1 = [e[0] + e[2] for e in arrasto["grupo"].values()]
            y1 = [e[1] + e[3] for e in arrasto["grupo"].values()]
            arrasto["caixa_grupo"] = (min(xs), min(ys), max(x1), max(y1))
        else:
            arrasto["inicio"] = self._espaco_mm(self._item_layout(alvo, lamina))
            if alvo[0] == "espaco":
                foto = self._item_layout(alvo, lamina).get("foto")
                dados = self.info.get(normalizar_caminho(foto)) if foto else None
                if dados:
                    arrasto["proporcao_foto"] = proj.dados_proporcao(dados)
        self._arrasto = arrasto

    def _layout_mover(self, evento):
        """
        Movimentação e redimensionamento de um único espaço ou molde no estilo Photoshop CC:
        - Por padrão, arrastar as alças de canto mantém a proporção original do espaço sem travar nas bordas.
        - Segurar Shift libera a Proporção Livre (estica livremente em X e Y, igual ao Photoshop moderno).
        - Alt desliga o ímã (alinhamento inteligente).
        """
        a = self._arrasto
        if a and a.get("grupo") is not None:
            self._layout_mover_grupo(evento, a)
            return
        inicio_t = time.perf_counter()
        lamina = self._lamina(a["lamina"])
        item = self._item_layout(a["alvo"], lamina)
        if lamina is None or item is None or a["lamina"] != self.atual or self._geo is None:
            return
        if not a["ativo"]:
            if abs(evento.x - a["x"]) + abs(evento.y - a["y"]) < 3:
                return
            a["ativo"] = True
            self.marcar_historico()
            lamina["modelo"] = None
            a["alvos"] = self._alvos_ima(lamina, a["alvo"])
            self._iniciar_previa(a, lamina, item)
            self.tempo_inicio_arrasto = time.perf_counter() - inicio_t
            inicio_t = time.perf_counter()
        lw, lh = self._mm()
        dw, dh = self._geo[0], self._geo[1]
        k = lw / float(dw)
        dx, dy = (evento.x - a["x"]) * k, (evento.y - a["y"]) * (lh / float(dh))
        estado = getattr(evento, "state", 0)
        ima = not (estado & ALT)
        proporcao_livre = bool(estado & SHIFT)
        tolerancia = IMA_PX * k
        xs, ys = a["alvos"] if ima else ([], [])
        memoria = a["ima"] if ima else {}
        x0, y0, w0, h0 = a["inicio"]
        guias_x, guias_y = [], []
        alca = a["alca"]

        def imantar(chave, valores, alvos):
            d, linha = self._imantar(valores, alvos, tolerancia, memoria.get(chave))
            memoria[chave] = linha
            return d, linha

        if not alca:
            x = min(max(x0 + dx, 0.0), lw - w0)
            y = min(max(y0 + dy, 0.0), lh - h0)
            if ima:
                d, linha = imantar("x", [x, x + w0 / 2, x + w0], xs)
                if linha is not None and 0 <= x + d <= lw - w0:
                    x += d
                    guias_x.append(linha)
                d, linha = imantar("y", [y, y + h0 / 2, y + h0], ys)
                if linha is not None and 0 <= y + d <= lh - h0:
                    y += d
                    guias_y.append(linha)
            w, h = w0, h0
        else:
            esq, topo, dir_, base = x0, y0, x0 + w0, y0 + h0
            if "w" in alca:
                esq = x0 + dx
            if "e" in alca:
                dir_ = x0 + w0 + dx
            if "n" in alca:
                topo = y0 + dy
            if "s" in alca:
                base = y0 + h0 + dy
            if ima:
                for lado in ("w", "e"):
                    if lado in alca:
                        d, linha = imantar(lado, [esq if lado == "w" else dir_], xs)
                        if linha is not None:
                            guias_x.append(linha)
                            if lado == "w":
                                esq += d
                            else:
                                dir_ += d
                for lado in ("n", "s"):
                    if lado in alca:
                        d, linha = imantar(lado, [topo if lado == "n" else base], ys)
                        if linha is not None:
                            guias_y.append(linha)
                            if lado == "n":
                                topo += d
                            else:
                                base += d

            # Limita às bordas da lâmina de forma suave (sem travar!)
            esq = max(0.0, min(esq, lw - LADO_MINIMO_MM))
            dir_ = min(lw, max(dir_, LADO_MINIMO_MM))
            topo = max(0.0, min(topo, lh - LADO_MINIMO_MM))
            base = min(lh, max(base, LADO_MINIMO_MM))
            if "w" in alca:
                esq = min(esq, dir_ - LADO_MINIMO_MM)
            else:
                dir_ = max(dir_, esq + LADO_MINIMO_MM)
            if "n" in alca:
                topo = min(topo, base - LADO_MINIMO_MM)
            else:
                base = max(base, topo + LADO_MINIMO_MM)

            # Estilo Photoshop: alças de canto mantêm a proporção por padrão; Shift libera Proporção Livre
            manter_proporcao = (not proporcao_livre) and (len(alca) == 2) and w0 > 0 and h0 > 0
            if manter_proporcao:
                razao = w0 / float(h0)
                w, h = dir_ - esq, base - topo
                # Âncora oposta à alça puxada
                ax = dir_ if "w" in alca else esq
                ay = base if "n" in alca else topo
                max_w = ax if "w" in alca else (lw - ax)
                max_h = ay if "n" in alca else (lh - ay)

                if abs(w - w0) / max(1.0, w0) >= abs(h - h0) / max(1.0, h0):
                    w_desejado = w
                    h_desejado = w_desejado / razao
                else:
                    h_desejado = h
                    w_desejado = h_desejado * razao

                # Restringe suavemente ao espaço máximo disponível na lâmina (nunca trava!)
                if w_desejado > max_w:
                    w_desejado = max_w
                    h_desejado = w_desejado / razao
                if h_desejado > max_h:
                    h_desejado = max_h
                    w_desejado = h_desejado * razao

                w_desejado = max(LADO_MINIMO_MM, w_desejado)
                h_desejado = max(LADO_MINIMO_MM, h_desejado)

                if "w" in alca:
                    esq = max(0.0, ax - w_desejado)
                else:
                    dir_ = min(lw, ax + w_desejado)
                if "n" in alca:
                    topo = max(0.0, ay - h_desejado)
                else:
                    base = min(lh, ay + h_desejado)

            x, y, w, h = esq, topo, max(LADO_MINIMO_MM, dir_ - esq), max(LADO_MINIMO_MM, base - topo)
        self._definir_espaco_mm(item, x, y, w, h)
        a["guias"] = (guias_x, guias_y)
        self._pedir_previa()
        self.tempos_arrasto.append(time.perf_counter() - inicio_t)
        del self.tempos_arrasto[:-2000]

    def _layout_mover_grupo(self, evento, arrasto):
        """
        Movimentação e redimensionamento de várias fotos selecionadas ao mesmo tempo (Estilo Photoshop):
        - Por padrão mantém a proporção do grupo; segurando Shift permite Proporção Livre (sx e sy independentes).
        - Quando 'Igualar no grupo' (self.igualar_no_grupo) está ativo, todas as fotos selecionadas
          recebem exatamente o mesmo tamanho ao redimensionar, evitando tamanhos desiguais!
        """
        lamina = self._lamina(arrasto["lamina"])
        if lamina is None or arrasto["lamina"] != self.atual or self._geo is None:
            return
        if not arrasto["ativo"]:
            if abs(evento.x - arrasto["x"]) + abs(evento.y - arrasto["y"]) < 3:
                return
            arrasto["ativo"] = True
            self.marcar_historico()
            lamina["modelo"] = None
        lw, lh = self._mm()
        dw, dh = self._geo[0], self._geo[1]
        dx = (evento.x - arrasto["x"]) * lw / float(dw)
        dy = (evento.y - arrasto["y"]) * lh / float(dh)
        estado = getattr(evento, "state", 0)
        proporcao_livre = bool(estado & SHIFT)
        x0, y0, x1, y1 = arrasto["caixa_grupo"]
        gw = max(1.0, x1 - x0)
        gh = max(1.0, y1 - y0)
        alca = arrasto["alca"]
        geometrias = arrasto["grupo"]
        novas = {}
        if not alca:
            mx = min(max(dx, -x0), lw - x1)
            my = min(max(dy, -y0), lh - y1)
            for i, (x, y, w, h) in geometrias.items():
                novas[i] = (x + mx, y + my, w, h)
        else:
            sx = sy = 1.0
            if "w" in alca:
                sx = (gw - dx) / gw
            elif "e" in alca:
                sx = (gw + dx) / gw
            if "n" in alca:
                sy = (gh - dy) / gh
            elif "s" in alca:
                sy = (gh + dy) / gh

            if not proporcao_livre:
                if len(alca) == 2:
                    escala_u = sx if abs(sx - 1.0) >= abs(sy - 1.0) else sy
                    sx = sy = escala_u
                elif alca in ("e", "w"):
                    sy = sx
                else:
                    sx = sy
            else:
                if alca in ("e", "w"):
                    sy = 1.0
                elif alca in ("n", "s"):
                    sx = 1.0

            # Tamanho médio base caso igualar_no_grupo esteja ativo
            igualar = bool(getattr(self, "igualar_no_grupo", None) and self.igualar_no_grupo.get())
            w_medio = sum(w for _x, _y, w, _h in geometrias.values()) / float(len(geometrias))
            h_medio = sum(h for _x, _y, _w, h in geometrias.values()) / float(len(geometrias))

            min_sx = max((LADO_MINIMO_MM / (w_medio if igualar else max(1.0, w))) for _x, _y, w, _h in geometrias.values())
            min_sy = max((LADO_MINIMO_MM / (h_medio if igualar else max(1.0, h))) for _x, _y, _w, h in geometrias.values())
            sx = max(min_sx, min(3.5, sx))
            sy = max(min_sy, min(3.5, sy))
            if not proporcao_livre:
                s_uni = max(sx, sy)
                sx = sy = s_uni

            ancora_x = x1 if "w" in alca else (x0 if "e" in alca else (x0 + x1) / 2.0)
            ancora_y = y1 if "n" in alca else (y0 if "s" in alca else (y0 + y1) / 2.0)

            for i, (x, y, w, h) in geometrias.items():
                cx_i = x + w / 2.0
                cy_i = y + h / 2.0
                ncx = ancora_x + (cx_i - ancora_x) * sx
                ncy = ancora_y + (cy_i - ancora_y) * sy
                base_w = w_medio if igualar else w
                base_h = h_medio if igualar else h
                nw = min(lw, max(LADO_MINIMO_MM, base_w * sx))
                nh = min(lh, max(LADO_MINIMO_MM, base_h * sy))
                nx = ncx - nw / 2.0
                ny = ncy - nh / 2.0
                novas[i] = (nx, ny, nw, nh)

            min_x = min(v[0] for v in novas.values())
            min_y = min(v[1] for v in novas.values())
            max_x = max(v[0] + v[2] for v in novas.values())
            max_y = max(v[1] + v[3] for v in novas.values())
            mover_x = -min_x if min_x < 0 else (lw - max_x if max_x > lw else 0)
            mover_y = -min_y if min_y < 0 else (lh - max_y if max_y > lh else 0)
            novas = {
                i: (
                    min(max(0.0, x + mover_x), lw - w),
                    min(max(0.0, y + mover_y), lh - h),
                    w,
                    h,
                )
                for i, (x, y, w, h) in novas.items()
            }
        for i, caixa in novas.items():
            self._definir_espaco_mm(lamina["espacos"][i], *caixa)
        self._marcar_modificado()
        self._desenhar_espacos()
        if self._geo:
            dw, dh = self._geo[0], self._geo[1]
            self.agendar("render_layout", 35, lambda: self._pedir_render(dw, dh))

    def _iniciar_previa(self, a, lamina, item):
        """
        No começo do arrasto/redimensionamento: gera a imagem base limpa da lâmina SEM o espaço
        que está sendo movido/redimensionado (eliminando qualquer rastro branco ou fantasma)
        e prepara a foto já transformada para 60fps.
        """
        c = self.canvas
        dw, dh, ox, oy = self._geo
        lado = max(dw, dh)
        c.delete("arrasto")
        a["base_limpa_tk"] = None
        a["previa"] = None
        a["tk"] = None

        # Renderiza a lâmina sem o item ativo para mostrar o fundo verdadeiro por baixo
        try:
            lamina_sem = dict(lamina)
            idx_alvo = a["alvo"][1]
            if a["alvo"][0] == "espaco":
                lamina_sem["espacos"] = [e for idx, e in enumerate(lamina.get("espacos", [])) if idx != idx_alvo]
            else:
                lamina_sem["sobreposicoes"] = [s for idx, s in enumerate(lamina.get("sobreposicoes") or []) if idx != idx_alvo]
            img_limpa = exportar.renderizar_lamina(
                lamina_sem, self.produto, self.info, largura=dw, guias=self.guias.get(), rapido=True
            )
            a["base_limpa_tk"] = ImageTk.PhotoImage(img_limpa)
            c.delete("img")
            c.create_image(ox, oy, image=a["base_limpa_tk"], anchor="nw", tags="img")
        except Exception:
            pass

        if a["alvo"][0] == "espaco":
            foto = item.get("foto")
            if foto:
                try:
                    ed = item.get("edicao") or {}
                    img = recorte.transformar(analise.imagem_para(foto, lado), ed)
                    if max(img.size) > lado:
                        escala = lado / float(max(img.size))
                        img = img.resize((max(1, round(img.width * escala)), max(1, round(img.height * escala))), Image.BILINEAR)
                    a["previa"] = {
                        "img": recorte.aplicar_ajustes(img, ed),
                        "edicao": ed,
                        "rosto_t": recorte.rosto_transformado(rosto_de(self.info, foto), ed),
                        "moldura": ed.get("moldura"),
                        "px_mm": dw / float(lamina_mm(self.produto)[0]),
                        "fundo": cor_rgb(lamina.get("fundo")),
                    }
                except (OSError, ValueError):
                    pass
        else:
            try:
                with Image.open(item["png"]) as png:
                    png = png.convert("RGBA")
                png.thumbnail((lado, lado), Image.BILINEAR)
                a["previa"] = {"molde": png}
            except (OSError, KeyError):
                pass
        self._desenhar_espacos(ocultar=a["alvo"])

    def _pedir_previa(self):
        """Desenha a prévia agora, ou no máximo 60 vezes por segundo."""
        espera = 0.016 - (time.perf_counter() - self._previa_ultima)
        if espera <= 0:
            self._cancelar_previa()
            self._desenhar_previa()
        elif self._previa_agendada is None:
            self._previa_agendada = self.root.after(max(1, int(espera * 1000)), self._desenhar_previa)

    def _cancelar_previa(self):
        if self._previa_agendada is not None:
            self.root.after_cancel(self._previa_agendada)
            self._previa_agendada = None

    def _desenhar_previa(self):
        self._previa_agendada = None
        self._previa_ultima = time.perf_counter()
        a = self._arrasto
        if not a or a.get("tipo") != "layout" or not a["ativo"] or self._geo is None:
            return
        item = self._item_layout(a["alvo"], self._lamina(a["lamina"]))
        if item is None:
            return
        c = self.canvas
        c.delete("arrasto_item")
        dw, dh, ox, oy = self._geo
        x0, y0 = int(round(ox + item["x"] * dw)), int(round(oy + item["y"] * dh))
        x1, y1 = int(round(ox + (item["x"] + item["w"]) * dw)), int(round(oy + (item["y"] + item["h"]) * dh))
        pw, ph = max(1, x1 - x0), max(1, y1 - y0)
        etiqueta = ("arrasto", "arrasto_item")
        previa = a.get("previa")
        if a["alvo"][0] == "espaco":
            cor = tema.DESTAQUE
            if previa:
                img = previa["img"]
                W, H = img.size
                aspecto = pw / float(ph)
                dobra_x = None
                if item["x"] < 0.5 < item["x"] + item["w"] and item["w"] > 0.05:
                    dobra_x = (0.5 - item["x"]) / float(item["w"])
                corte = recorte.corte_do_espaco(W, H, aspecto, previa["edicao"], previa["rosto_t"],
                                                rosto_ja_transformado=True, dobra_x=dobra_x)
                pronta = recorte.recortar(img, recorte.caixa_corte(W, H, aspecto, corte), (pw, ph), previa["fundo"], rapido=True)
                if previa.get("moldura"):
                    aplicar_moldura(pronta, previa["moldura"], previa.get("px_mm"))
                a["tk"] = ImageTk.PhotoImage(pronta)
                c.create_image(x0, y0, image=a["tk"], anchor="nw", tags=etiqueta)
            else:
                c.create_rectangle(x0, y0, x1, y1, fill=tema.PAINEL_3, outline=tema.BORDA, dash=(4, 3), tags=etiqueta)
        else:
            cor = tema.ALERTA
            if previa:
                a["tk"] = ImageTk.PhotoImage(previa["molde"].resize((pw, ph), Image.BILINEAR))
                c.create_image(x0, y0, image=a["tk"], anchor="nw", tags=etiqueta)
        a["pos"] = (x0, y0)
        c.create_rectangle(x0, y0, x1, y1, outline=cor, width=2, tags=etiqueta)
        for hx, hy in self._alcas(item).values():
            c.create_rectangle(hx - ALCA, hy - ALCA, hx + ALCA, hy + ALCA, fill="#ffffff", outline=cor, tags=etiqueta)
        self._desenhar_guias_ima(*a.get("guias", ([], [])))
        self._atualizar_numeros()

    def _desenhar_guias_ima(self, guias_x, guias_y):
        self.canvas.delete("guia_ima")
        if self._geo is None:
            return
        dw, dh, ox, oy = self._geo
        lw, lh = self._mm()
        for gx in guias_x:
            x = ox + gx / lw * dw
            self.canvas.create_line(x, oy - 12, x, oy + dh + 12, fill="#ff3fb4", width=1, tags=("espaco", "guia_ima"))
        for gy in guias_y:
            y = oy + gy / lh * dh
            self.canvas.create_line(ox - 12, y, ox + dw + 12, y, fill="#ff3fb4", width=1, tags=("espaco", "guia_ima"))

    def _layout_soltar(self, a):
        self._cancelar_previa()
        self.canvas.delete("guia_ima")
        if not a["ativo"]:
            self.canvas.delete("arrasto")
            return
        if a.get("grupo") is not None:
            self.canvas.delete("arrasto")
            self._atualizar_layout_sem_piscar()
            return
        self._desenhar_previa_final(a)
        self._remendo = {"item": self.atual, "geo": self._geo, "base_limpa_tk": a.get("base_limpa_tk"),
                         "tk": a.get("tk"), "pos": a.get("pos", (0, 0))}
        self.canvas.delete("arrasto")
        self._desenhar_remendo()
        self._atualizar_layout_sem_piscar()

    def _atualizar_layout_sem_piscar(self):
        self._marcar_modificado()
        self._desenhar_espacos()
        self.atualizar_fotos()
        self.desenhar_timeline()
        self.atualizar_sugestoes()
        if self._geo:
            dw, dh = self._geo[:2]
            self._pedir_render(dw, dh)

    def _desenhar_previa_final(self, a):
        self._arrasto = a
        try:
            self._desenhar_previa()
        finally:
            self._arrasto = None

    def _cancelar_agendado(self, chave):
        anterior = self._agendados.pop(chave, None)
        if anterior:
            self.root.after_cancel(anterior)

    def _mudar_espacos(self, funcao, texto=None):
        """Uma mudança nos espaços da lâmina atual, com desfazer, modelo esquecido e animação suave."""
        lamina = self._lamina()
        if lamina is None:
            return False
        antes = [dict(e) for e in lamina.get("espacos", [])]
        self.marcar_historico()
        funcao(lamina)
        lamina["modelo"] = None
        self.atualizar_lamina_atual()
        self._animar_transicao_layout(antes, lamina.get("espacos", []))
        if texto:
            self.registrar(texto)
        return True

    def _seta(self, dx, dy, passo_mm):
        alvo = self._alvo_sel() if self._editando_lamina() else None
        if alvo is None:
            if dy == 0 or self._tl_vertical:
                self.passar_lamina(dx or dy)
            return
        lamina = self._lamina()
        agora = time.monotonic()
        if agora - self._ultimo_toque > 1.0:
            self.marcar_historico()
        self._ultimo_toque = agora
        lw, lh = self._mm()
        e = self._item_layout(alvo, lamina)
        x, y, w, h = self._espaco_mm(e)
        x = min(max(x + dx * passo_mm, 0.0), lw - w)
        y = min(max(y + dy * passo_mm, 0.0), lh - h)
        self._definir_espaco_mm(e, x, y, w, h)
        lamina["modelo"] = None
        self._marcar_modificado()
        self._desenhar_espacos()
        if self._geo:
            dw, dh = self._geo[0], self._geo[1]
            self.agendar("render_layout", 120, lambda: self._pedir_render(dw, dh))
        self.agendar("timeline_layout", 700, self.desenhar_timeline)

    def _tecla_apagar(self):
        alvo = self._alvo_sel() if self._editando_lamina() else None
        if alvo and alvo[0] == "molde":
            self.apagar_molde_selecionado()
        elif alvo:
            self.apagar_espaco_selecionado()
        else:
            self.tirar_foto_selecionada()

    def apagar_molde_selecionado(self):
        if not self._molde_sel_valido():
            return
        i = self.molde_sel[1]
        self.molde_sel = None
        self._mudar_espacos(lambda l: l["sobreposicoes"].pop(i), "Molde PNG tirado da lâmina.")

    def _pagina_livre(self, lamina):
        n = self.produto["paginas_por_lamina"]
        contagem = [0] * n
        for e in lamina["espacos"]:
            contagem[min(n - 1, self._pagina_do_espaco(e))] += 1
        return contagem.index(min(contagem))

    def adicionar_espaco(self):
        lamina = self._lamina()
        if lamina is None:
            self.registrar("Escolha uma lâmina para pôr um espaço novo.")
            return
        pagina = self._pagina_do_espaco(lamina["espacos"][self.espaco_sel]) if self._espaco_sel_valido() \
            else self._pagina_livre(lamina)
        ux, uy, uw, uh = area_util_pagina_mm(self.produto, pagina, self._cfg()["margem_mm"])
        w, h = uw * 0.5, uh * 0.5
        x, y = ux + (uw - w) / 2, uy + (uh - h) / 2
        lw, lh = self._mm()
        ocupados = {(round(e["x"] * lw, 1), round(e["y"] * lh, 1)) for e in lamina["espacos"]}
        while (round(x, 1), round(y, 1)) in ocupados and x + w + 5 <= lw and y + h + 5 <= lh:
            x, y = x + 5, y + 5
        novo = {"foto": None, "edicao": {}}
        self._definir_espaco_mm(novo, x, y, w, h)

        def mudar(l):
            l["espacos"].append(novo)
            self.espaco_sel = len(l["espacos"]) - 1

        self.alternar_modo_layout(True)
        self._mudar_espacos(mudar, "Espaço novo na página %s. Arraste uma foto para ele." % (pagina + 1))

    def duplicar_espaco(self):
        if not (isinstance(self.atual, int) and self._espaco_sel_valido()):
            self.registrar("Selecione um espaço para duplicar.")
            return
        lw, lh = self._mm()
        original = self._lamina()["espacos"][self.espaco_sel]
        x, y, w, h = self._espaco_mm(original)
        x = x + 5 if x + w + 5 <= lw else max(0.0, x - 5)
        y = y + 5 if y + h + 5 <= lh else max(0.0, y - 5)
        novo = {"foto": None, "edicao": {}}
        self._definir_espaco_mm(novo, x, y, w, h)

        def mudar(l):
            l["espacos"].append(novo)
            self.espaco_sel = len(l["espacos"]) - 1

        self.alternar_modo_layout(True)
        self._mudar_espacos(mudar, "Espaço duplicado (vazio).")

    def apagar_espaco_selecionado(self):
        if isinstance(self.atual, int) and self._espaco_sel_valido():
            self.apagar_espaco(self.atual, self.espaco_sel)

    def ordem_espaco(self, frente):
        if not (isinstance(self.atual, int) and self._espaco_sel_valido()):
            return
        i = self.espaco_sel

        def mudar(l):
            espaco = l["espacos"].pop(i)
            if frente:
                l["espacos"].append(espaco)
                self.espaco_sel = len(l["espacos"]) - 1
            else:
                l["espacos"].insert(0, espaco)
                self.espaco_sel = 0

        self._mudar_espacos(mudar)

    def girar_foto_selecionada(self, graus=90):
        if not (isinstance(self.atual, int) and self._espaco_sel_valido()):
            self.registrar("Selecione um espaço com foto para girar.")
            return
        i = self.espaco_sel
        lamina = self._lamina()
        if not lamina or not lamina["espacos"][i].get("foto"):
            self.registrar("O espaço selecionado está vazio.")
            return

        def mudar(l):
            esp = l["espacos"][i]
            ed = dict(esp.get("edicao") or {})
            ed["rot"] = (int(ed.get("rot", 0)) + graus) % 360
            ed.pop("corte", None)
            esp["edicao"] = ed
            diagramar.ajustar_espacos_lamina(l, self._mapa_proporcoes(l), self.produto, self._cfg())

        self._mudar_espacos(mudar, "Foto girada em %s° com reajuste proporcional da página." % graus)

    def alinhar_espaco(self, acao):
        alvo = self._alvo_sel() if isinstance(self.atual, int) else None
        if alvo is None or (alvo[0] == "molde" and acao == "igualar"):
            self.registrar("Selecione um ou mais espaços na lâmina.")
            return
        lw, lh = self._mm()
        i = alvo[1] if alvo[0] == "espaco" else None
        lamina = self._lamina()
        e = self._item_layout(alvo, lamina)
        pagina = self._pagina_do_espaco(e)
        ux, uy, uw, uh = area_util_pagina_mm(self.produto, pagina, self._cfg()["margem_mm"])
        selecionados = self._indices_espacos_selecionados()

        def mudar(l):
            x, y, w, h = self._espaco_mm(e)
            if acao == "centro_h":
                x = ux + (uw - w) / 2
            elif acao == "centro_v":
                y = uy + (uh - h) / 2
            elif acao == "proporcao_foto":
                if e.get("foto"):
                    prop = self._proporcao_efetiva(e["foto"], e.get("edicao"))
                    x, y, w, h = diagramar.caixa_proporcao_padrao(prop, (x, y, w, h))
            elif acao == "pagina_util":
                x, y, w, h = ux, uy, uw, uh
                if e.get("foto"):
                    prop = self._proporcao_efetiva(e["foto"], e.get("edicao"))
                    if diagramar._corte_quadro(prop, (x, y, w, h)) > diagramar.CORTE_MAXIMO:
                        x, y, w, h = diagramar.caixa_proporcao_padrao(prop, (ux, uy, uw, uh))
            elif acao == "pagina":
                x, y, w, h = area_pagina_mm(self.produto, pagina)
                if e.get("foto"):
                    prop = self._proporcao_efetiva(e["foto"], e.get("edicao"))
                    if diagramar._corte_quadro(prop, (x, y, w, h)) > diagramar.CORTE_MAXIMO:
                        x, y, w, h = diagramar.caixa_proporcao_padrao(prop, (x, y, w, h))
            elif acao == "lamina":
                x, y, w, h = 0.0, 0.0, lw, lh
                if e.get("foto"):
                    prop = self._proporcao_efetiva(e["foto"], e.get("edicao"))
                    if diagramar._corte_quadro(prop, (x, y, w, h)) > diagramar.CORTE_MAXIMO:
                        x, y, w, h = diagramar.caixa_proporcao_padrao(prop, (0.0, 0.0, lw, lh))
            elif acao == "igualar":
                alvos_ig = selecionados if len(selecionados) > 1 else set(range(len(l["espacos"])))
                caixas_ig = [self._espaco_mm(l["espacos"][j]) for j in alvos_ig]
                w_ref = sum(c[2] for c in caixas_ig) / float(max(1, len(caixas_ig))) if len(selecionados) > 1 else w
                h_ref = sum(c[3] for c in caixas_ig) / float(max(1, len(caixas_ig))) if len(selecionados) > 1 else h
                w, h = w_ref, h_ref
                for j in alvos_ig:
                    outro = l["espacos"][j]
                    ox_, oy_, ow, oh = self._espaco_mm(outro)
                    nx = min(max(ox_ + ow / 2 - w_ref / 2, 0.0), lw - w_ref)
                    ny = min(max(oy_ + oh / 2 - h_ref / 2, 0.0), lh - h_ref)
                    self._definir_espaco_mm(outro, nx, ny, w_ref, h_ref)
                return
            x, y = min(max(x, 0.0), lw - w), min(max(y, 0.0), lh - h)
            self._definir_espaco_mm(e, x, y, w, h)

        self._mudar_espacos(mudar, "Tamanho das fotos igualado." if acao == "igualar" else None)

    def _atualizar_numeros(self):
        if not hasattr(self, "num_vars"):
            return
        sel = self._alvo_sel()
        alvo = (self.atual, sel) if sel else None
        if alvo == self._num_alvo and self._foco() in self._num_campos:
            return
        self._num_alvo = alvo
        estado = "normal" if alvo else "disabled"
        valores = self._espaco_mm(self._item_layout(sel)) if alvo else ("",) * 4
        for (chave, var), valor, campo in zip(self.num_vars.items(), valores, self._num_campos):
            var.set(("%.1f" % valor) if alvo else "")
            campo.configure(state=estado)

    def _foco(self):
        try:
            return self.root.focus_get()
        except (KeyError, tk.TclError):
            return None

    def _aplicar_numeros(self):
        sel = self._alvo_sel()
        if sel is None or not isinstance(self.atual, int) or self._num_alvo != (self.atual, sel):
            return
        try:
            x, y, w, h = (float(self.num_vars[k].get().replace(",", ".")) for k in ("x", "y", "w", "h"))
        except ValueError:
            self._num_alvo = None
            self._atualizar_numeros()
            return
        lw, lh = self._mm()
        w = min(max(w, LADO_MINIMO_MM), lw)
        h = min(max(h, LADO_MINIMO_MM), lh)
        x, y = min(max(x, 0.0), lw - w), min(max(y, 0.0), lh - h)
        e = self._item_layout(sel)
        if all(abs(a - b) < 0.05 for a, b in zip((x, y, w, h), self._espaco_mm(e))):
            return
        self._num_alvo = None
        self._mudar_espacos(lambda l: self._definir_espaco_mm(e, x, y, w, h))

    # ---------------- arrastar entre painéis ----------------

    def _mover_arrasto(self, evento):
        a = self._arrasto
        if not a:
            return
        if a["tipo"] == "layout":
            self._layout_mover(evento)
            return
        if not a["ativo"]:
            if abs(evento.x_root - a["x"]) + abs(evento.y_root - a["y"]) < ARRASTO_MINIMO:
                return
            a["ativo"] = True
            self._fantasma = tk.Toplevel(self.root)
            self._fantasma.overrideredirect(True)
            self._fantasma.attributes("-topmost", True)
            self._fantasma.attributes("-alpha", 0.85)
            texto = {"foto": "%s foto(s)" % len(a.get("fotos", [])), "espaco": "mover foto",
                     "lamina": "mover lâmina", "fundo": "fundo: %s" % Path(a.get("caminho", "")).name}[a["tipo"]]
            tk.Label(self._fantasma, text=texto, bg=tema.DESTAQUE, fg="#ffffff", padx=8, pady=3).pack()
            if a["tipo"] == "lamina" and not self._rolando_tl:
                self._rolando_tl = True
                self.root.after(60, self._rolar_timeline_no_arrasto)
        self._fantasma.geometry("+%d+%d" % (evento.x_root + 16, evento.y_root + 12))
        if a["tipo"] == "lamina":
            a["ponteiro"] = (evento.x_root, evento.y_root)
            self._marcar_insercao(evento.x_root, evento.y_root)

    def _sobre(self, widget, x_root, y_root):
        if not widget.winfo_ismapped():
            return False
        x, y = widget.winfo_rootx(), widget.winfo_rooty()
        return x <= x_root < x + widget.winfo_width() and y <= y_root < y + widget.winfo_height()

    def _eixo_tl(self, x, y):
        """Coordenada ao longo da linha do tempo (y empilhada, x lado a lado) para um ponto do widget."""
        c = self.canvas_timeline
        return c.canvasy(y) if self._tl_vertical else c.canvasx(x)

    def _eixo_tl_root(self, x_root, y_root):
        c = self.canvas_timeline
        return self._eixo_tl(x_root - c.winfo_rootx(), y_root - c.winfo_rooty())

    def _insercao_em(self, pos):
        """Posição na linha do tempo (0 = antes da primeira miniatura) para soltar uma lâmina em pos."""
        passo = self._tl_passo()
        centro = passo / 2.0 if self._tl_vertical else 6 + self._tl_larg / 2.0
        k = int((pos - centro + passo) // passo)
        return max(0, min(len(self._tl_itens), k))

    def _destino_insercao(self, de, k):
        """Índice final da lâmina de para entrar na posição k da linha do tempo."""
        i = k
        return i - 1 if i > de else i

    def _marcar_insercao(self, x_root, y_root):
        c = self.canvas_timeline
        c.delete("insercao")
        if not self._sobre(c, x_root, y_root):
            self._insercao_tl = None
            return
        self._insercao_tl = self._insercao_em(self._eixo_tl_root(x_root, y_root))
        self._desenhar_insercao()

    def _desenhar_insercao(self):
        c = self.canvas_timeline
        c.delete("insercao")
        if self._insercao_tl is None:
            return
        cor = tema.DESTAQUE
        if self._tl_vertical:
            y = self._insercao_tl * self._tl_passo()
            largura = c.winfo_width()
            c.create_line(0, y, largura, y, fill=cor, width=4, tags="insercao")
            c.create_polygon(0, y - 7, 0, y + 7, 8, y, fill=cor, outline="", tags="insercao")
            c.create_polygon(largura, y - 7, largura, y + 7, largura - 8, y, fill=cor, outline="", tags="insercao")
        else:
            x = self._insercao_tl * self._tl_passo() - 1
            altura = self._tl_altura
            c.create_line(x, 0, x, altura, fill=cor, width=4, tags="insercao")
            c.create_polygon(x - 7, 0, x + 7, 0, x, 8, fill=cor, outline="", tags="insercao")
            c.create_polygon(x - 7, altura, x + 7, altura, x, altura - 8, fill=cor, outline="", tags="insercao")

    def _rolar_timeline_no_arrasto(self):
        a = self._arrasto
        if not a or a["tipo"] != "lamina" or not a["ativo"]:
            self._rolando_tl = False
            return
        c = self.canvas_timeline
        x_root, y_root = a.get("ponteiro", (a["x"], a["y"]))
        if self._tl_vertical:
            cruzado, local, tamanho = x_root - c.winfo_rootx(), y_root - c.winfo_rooty(), c.winfo_height()
            perto = -40 <= cruzado <= c.winfo_width() + 40
            rolar = c.yview_scroll
        else:
            cruzado, local, tamanho = y_root - c.winfo_rooty(), x_root - c.winfo_rootx(), c.winfo_width()
            perto = -40 <= cruzado <= c.winfo_height() + 40
            rolar = c.xview_scroll
        if perto:
            if local < BORDA_ROLAGEM_TIMELINE:
                rolar(-1, "units")
            elif local > tamanho - BORDA_ROLAGEM_TIMELINE:
                rolar(1, "units")
            self._marcar_insercao(x_root, y_root)
        self.root.after(60, self._rolar_timeline_no_arrasto)

    def _soltar_arrasto(self, evento):
        a = self._arrasto
        self._arrasto = None
        if a and a["tipo"] == "layout":
            self._layout_soltar(a)
            return
        if self._fantasma is not None:
            self._fantasma.destroy()
            self._fantasma = None
        self._insercao_tl = None
        self.canvas_timeline.delete("insercao")
        if a and a["tipo"] == "fundo":
            self._soltar_fundo(a, evento)
            return
        if not a or not a["ativo"]:
            return
        if a["tipo"] == "lamina":
            c = self.canvas_timeline
            if isinstance(a["item"], int) and self._sobre(c, evento.x_root, evento.y_root):
                k = self._insercao_em(self._eixo_tl_root(evento.x_root, evento.y_root))
                self.mover_lamina(a["item"], self._destino_insercao(a["item"], k))
            return
        alvo = self.root.winfo_containing(evento.x_root, evento.y_root)
        if alvo is self.canvas:
            x = evento.x_root - self.canvas.winfo_rootx()
            y = evento.y_root - self.canvas.winfo_rooty()
            indice = self._espaco_em(x, y)
            if a["tipo"] == "foto" and isinstance(self.atual, int):
                if indice is not None:
                    self.colocar_foto(a["fotos"][0], self.atual, indice)
                    if len(a["fotos"]) > 1:
                        self.adicionar_na_lamina(a["fotos"][1:], self.atual, marcar=False)
                elif self._dentro_da_lamina(x, y):
                    self.adicionar_na_lamina(a["fotos"], self.atual)
            elif a["tipo"] == "espaco" and indice is not None and (a["lamina"], a["espaco"]) != (self.atual, indice):
                self.trocar_espacos(a["lamina"], a["espaco"], self.atual, indice)
        elif alvo is self.canvas_timeline:
            item = self._timeline_em(self._eixo_tl_root(evento.x_root, evento.y_root))
            if a["tipo"] == "foto" and isinstance(item, int):
                self.adicionar_na_lamina(a["fotos"], item)
            elif a["tipo"] == "espaco" and isinstance(item, int) and item != a["lamina"]:
                foto = self._lamina(a["lamina"])["espacos"][a["espaco"]]["foto"]
                self.marcar_historico()
                self.tirar_foto(a["lamina"], a["espaco"], marcar=False)
                self.relayout(a["lamina"], marcar=False)
                self.adicionar_na_lamina([foto], item, marcar=False)
        elif alvo is self.canvas_fotos and a["tipo"] == "espaco":
            self.tirar_foto(a["lamina"], a["espaco"])

    # ---------------- painel de fotos (com divisões por pastas) ----------------

    def _atualizar_combo_pastas(self):
        if not hasattr(self, "combo_pasta"):
            return
        pastas = proj.listar_pastas_projeto(self.projeto)
        self._mapa_pastas_ui = {"Todas as pastas": "todas"}
        opcoes = ["Todas as pastas"]
        nomes_vistos = {}
        for ch_pasta, nome_pasta, qtd in pastas:
            cont = nomes_vistos.get(nome_pasta, 0) + 1
            nomes_vistos[nome_pasta] = cont
            rotulo = "%s (%d)" % (nome_pasta, qtd) if cont == 1 else "%s #%d (%d)" % (nome_pasta, cont, qtd)
            self._mapa_pastas_ui[rotulo] = ch_pasta
            opcoes.append(rotulo)
        self.combo_pasta.configure(values=opcoes)
        atual = self.pasta_filtro.get()
        if atual != "todas" and atual not in [p[0] for p in pastas]:
            self.pasta_filtro.set("todas")
            self.combo_pasta.set("Todas as pastas")
        elif atual == "todas":
            self.combo_pasta.set("Todas as pastas")

    def _mudou_filtro_pasta(self, _evento=None):
        escolha = self.combo_pasta.get()
        mapa = getattr(self, "_mapa_pastas_ui", {})
        self.pasta_filtro.set(mapa.get(escolha, "todas"))
        self.atualizar_fotos()

    def _fotos_filtradas(self):
        contagem = proj.usos(self.projeto)
        filtro = self.filtro.get()
        pasta_alvo = self.pasta_filtro.get() if hasattr(self, "pasta_filtro") else "todas"
        lista = []
        for foto in self.projeto["fotos"]:
            if pasta_alvo != "todas" and proj.pasta_da_foto(foto) != pasta_alvo:
                continue
            usos = contagem.get(normalizar_caminho(foto["caminho"]), 0)
            if filtro == "usadas" and not usos:
                continue
            if filtro == "nao_usadas" and usos:
                continue
            lista.append((foto, usos))
        return lista

    def _construir_itens_painel_fotos(self):
        """
        Monta a sequência visual da tira de fotos separando cada pasta com um divisor clicável
        (para nunca misturar pastas carregadas).
        """
        cw, _ch, _lado = self._celula
        larg_div = 54
        pastas_projeto = proj.listar_pastas_projeto(self.projeto)
        mostrar_divisores = len(pastas_projeto) >= 1 and self.pasta_filtro.get() == "todas"

        grupos = OrderedDict()
        for idx_f, (foto, usos) in enumerate(self._lista_fotos):
            ch_p = proj.pasta_da_foto(foto)
            nome_p = proj.nome_pasta_da_foto(foto)
            grupos.setdefault(ch_p, (nome_p, []))[1].append((idx_f, foto, usos))

        itens = []
        x_cursor = 0
        for ch_p, (nome_p, fotos_p) in grupos.items():
            recolhida = ch_p in self.pastas_recolhidas
            if mostrar_divisores:
                itens.append({
                    "tipo": "pasta",
                    "pasta": ch_p,
                    "nome": nome_p,
                    "qtd": len(fotos_p),
                    "recolhida": recolhida,
                    "x0": x_cursor,
                    "x1": x_cursor + larg_div,
                })
                x_cursor += larg_div
            if not recolhida:
                for idx_f, foto, usos in fotos_p:
                    itens.append({
                        "tipo": "foto",
                        "indice": idx_f,
                        "foto": foto,
                        "usos": usos,
                        "pasta": ch_p,
                        "nome_pasta": nome_p,
                        "x0": x_cursor,
                        "x1": x_cursor + cw,
                    })
                    x_cursor += cw
        self._itens_painel_fotos = itens
        self._largura_total_fotos = max(1, x_cursor)

    def atualizar_fotos(self):
        self._atualizar_combo_pastas()
        self._lista_fotos = self._fotos_filtradas()
        total = len(self.projeto["fotos"])
        usadas = sum(1 for _f, u in self._fotos_filtradas_todas() if u)
        selecionadas = len(self._fotos_escolhidas())
        n_pastas = len(proj.listar_pastas_projeto(self.projeto))
        texto = "Fotos  %s/%s no álbum" % (usadas, total)
        if n_pastas > 1:
            texto += " · %d pastas" % n_pastas
        if selecionadas:
            texto += " · %s sel." % selecionadas
        self.rotulo_fotos.configure(text=texto)
        self._celula = self._medidas_fotos()
        self._construir_itens_painel_fotos()
        self._area_fotos()
        self._desenhar_fotos_visiveis()

    def _medidas_fotos(self):
        """(largura da célula, altura, lado da miniatura): as miniaturas crescem com o painel."""
        c = self.canvas_fotos
        altura = c.winfo_height() if c.winfo_ismapped() and c.winfo_height() > 10 else int(c.cget("height"))
        lado = max(40, min(LADO_FOTO_MAXIMO, altura - 12))
        return lado + 16, altura, lado

    def _area_fotos(self):
        largura = getattr(self, "_largura_total_fotos", max(1, len(getattr(self, "_lista_fotos", [])) * self._celula[0]))
        area = "0 0 %d %d" % (largura, self._celula[1])
        if self.canvas_fotos.cget("scrollregion") != area:
            self.canvas_fotos.configure(scrollregion=area)

    def _mudou_tamanho_fotos(self, evento):
        if evento.height != self._celula[1]:
            self._celula = self._medidas_fotos()
            self._construir_itens_painel_fotos()
            self._area_fotos()
        self.agendar("fotos", 30, self._desenhar_fotos_visiveis)

    def _fotos_filtradas_todas(self):
        contagem = proj.usos(self.projeto)
        return [(f, contagem.get(normalizar_caminho(f["caminho"]), 0)) for f in self.projeto["fotos"]]

    def _rolar_fotos(self, *args):
        self.canvas_fotos.xview(*args)

    def _miniatura_pil(self, caminho, lado, usada=False):
        """Miniatura do painel de fotos; a já usada no álbum fica meio cinza e meio transparente."""
        img = analise.miniatura(caminho).copy()
        img.thumbnail((lado, lado - 10))
        if usada:
            img = ImageEnhance.Color(img).enhance(1.0 - DESBOTAR_USADA)
            img = Image.blend(Image.new("RGB", img.size, cor_rgb(tema.PAINEL)), img, OPACIDADE_USADA)
        return img

    def _miniatura_tk(self, caminho, usada=False):
        chave = normalizar_caminho(caminho)
        lado = self._celula[2]
        try:
            chave = (chave, os.stat(caminho).st_mtime_ns, lado, bool(usada))
        except OSError:
            return None
        guardada = self._tk_miniaturas.get(chave)
        if guardada is not None:
            self._tk_miniaturas.move_to_end(chave)
            return guardada
        if normalizar_caminho(caminho) not in self.info:
            return None
        try:
            img = self._miniatura_pil(caminho, lado, usada)
        except OSError:
            return None
        foto = ImageTk.PhotoImage(img)
        self._tk_miniaturas[chave] = foto
        while len(self._tk_miniaturas) > 900:
            self._tk_miniaturas.popitem(last=False)
        return foto

    def _desenhar_fotos_visiveis(self):
        c = self.canvas_fotos
        c.delete("all")
        itens = getattr(self, "_itens_painel_fotos", [])
        cw, ch, lado = self._celula
        if not itens:
            c.create_text(12, ch // 2, anchor="w", fill=tema.TEXTO_FRACO,
                          text="Sem fotos aqui. Arraste fotos ou várias pastas do Explorer (elas não se misturam).")
            return
        x0 = c.canvasx(0)
        x1 = x0 + c.winfo_width()
        letras = max(10, (cw - 6) // 6)
        for item in itens:
            if item["x1"] < x0 - cw or item["x0"] > x1 + cw:
                continue
            if item["tipo"] == "pasta":
                ix0, ix1 = item["x0"], item["x1"]
                cx = (ix0 + ix1) // 2
                recolhida = item["recolhida"]
                c.create_rectangle(ix0 + 3, 4, ix1 - 3, ch - 4,
                                   fill=tema.PAINEL_2 if not recolhida else tema.DESTAQUE_ESCURO,
                                   outline=tema.BORDA, width=1)
                seta = "▶" if recolhida else "▼"
                c.create_text(cx, 16, text="📁 %s" % seta, fill=tema.ALERTA, font=tema.FONTE_FORTE)
                nome_p = item["nome"]
                curto_p = nome_p if len(nome_p) <= 12 else nome_p[:10] + "…"
                c.create_text(cx, ch // 2, text=curto_p, fill=tema.TEXTO, font=tema.FONTE_PEQUENA, width=ix1 - ix0 - 8)
                c.create_text(cx, ch - 14, text="%d foto(s)" % item["qtd"], fill=tema.TEXTO_FRACO, font=tema.FONTE_PEQUENA)
                continue

            foto, usos = item["foto"], item["usos"]
            cx = (item["x0"] + item["x1"]) // 2
            cy = ch // 2 - 4
            selecionada = normalizar_caminho(foto["caminho"]) in self.fotos_sel
            if selecionada:
                c.create_rectangle(cx - cw // 2 + 3, 2, cx + cw // 2 - 3, ch - 2, fill=tema.DESTAQUE_ESCURO, outline="")
            imagem = self._miniatura_tk(foto["caminho"], usada=bool(usos))
            if imagem is not None:
                c.create_image(cx, cy, image=imagem)
                if selecionada:
                    meia_w, meia_h = imagem.width() // 2, imagem.height() // 2
                    c.create_rectangle(cx - meia_w - 2, cy - meia_h - 2, cx + meia_w + 1, cy + meia_h + 1,
                                       outline=tema.DESTAQUE, width=2)
            else:
                c.create_rectangle(cx - lado * 0.45, cy - lado * 0.31, cx + lado * 0.45, cy + lado * 0.31,
                                   fill=tema.PAINEL_3, outline="")
                c.create_text(cx, cy, text="...", fill=tema.TEXTO_FRACO)
            direita = cx + lado // 2
            if usos:
                c.create_oval(direita - 16, 4, direita + 2, 22, fill=tema.VERDE, outline="")
                c.create_text(direita - 7, 13, text=str(usos), fill="#0b2a1c", font=tema.FONTE_FORTE)
            if foto.get("destaque"):
                c.create_text(cx - lado // 2 + 6, 12, text="★", fill=tema.ALERTA, font=("Segoe UI", 12))
            nome = Path(foto["caminho"]).name
            c.create_text(cx, ch - 9, text=nome if len(nome) <= letras else nome[:letras - 2] + "…",
                          fill=tema.TEXTO if selecionada else tema.TEXTO_FRACO, font=tema.FONTE_PEQUENA)

    def _item_painel_fotos_em(self, evento):
        x = self.canvas_fotos.canvasx(evento.x)
        for item in getattr(self, "_itens_painel_fotos", []):
            if item["x0"] <= x < item["x1"]:
                return item
        return None

    def _foto_em(self, evento):
        item = self._item_painel_fotos_em(evento)
        if item and item.get("tipo") == "foto":
            return item["indice"]
        return None

    def _fotos_pressionou(self, evento):
        item = self._item_painel_fotos_em(evento)
        if item is not None and item.get("tipo") == "pasta":
            ch_p = item["pasta"]
            if ch_p in self.pastas_recolhidas:
                self.pastas_recolhidas.discard(ch_p)
            else:
                self.pastas_recolhidas.add(ch_p)
            self.atualizar_fotos()
            return
        indice = item["indice"] if (item and item.get("tipo") == "foto") else None
        if indice is None:
            self.fotos_sel.clear()
            self.ultima_foto_clicada = None
            self.atualizar_fotos()
            self._atualizar_degrade_ui()
            return
        caminho = self._lista_fotos[indice][0]["caminho"]
        chave = normalizar_caminho(caminho)
        if evento.state & 0x0004:
            self.fotos_sel.symmetric_difference_update({chave})
        elif evento.state & 0x0001 and self.ultima_foto_clicada is not None:
            a, b = sorted((self.ultima_foto_clicada, indice))
            for f, _u in self._lista_fotos[a:b + 1]:
                self.fotos_sel.add(normalizar_caminho(f["caminho"]))
        elif chave not in self.fotos_sel:
            self.fotos_sel = {chave}
        self.ultima_foto_clicada = indice
        self.atualizar_fotos()
        self._atualizar_degrade_ui()
        fotos = [f["caminho"] for f, _u in self._lista_fotos if normalizar_caminho(f["caminho"]) in self.fotos_sel]
        if fotos:
            self._arrasto = {"tipo": "foto", "fotos": fotos, "x": evento.x_root, "y": evento.y_root, "ativo": False}

    def _fotos_duplo(self, evento):
        indice = self._foto_em(evento)
        if indice is not None and isinstance(self.atual, int) and self._lamina() is not None:
            self.adicionar_na_lamina([self._lista_fotos[indice][0]["caminho"]], self.atual)

    def _selecionar_pasta_inteira(self, ch_pasta):
        for f, _u in self._lista_fotos:
            if proj.pasta_da_foto(f) == ch_pasta:
                self.fotos_sel.add(normalizar_caminho(f["caminho"]))
        self.atualizar_fotos()
        self._atualizar_degrade_ui()

    def _fotos_menu(self, evento):
        item = self._item_painel_fotos_em(evento)
        if item is not None and item.get("tipo") == "pasta":
            ch_pasta = item["pasta"]
            nome_pasta = item["nome"]
            menu = tk.Menu(self.root, tearoff=0)
            menu.add_command(label="Selecionar todas da pasta '%s'" % nome_pasta,
                             command=lambda: self._selecionar_pasta_inteira(ch_pasta))
            menu.add_command(label="Filtrar somente a pasta '%s'" % nome_pasta,
                             command=lambda: (self.pasta_filtro.set(ch_pasta), self.atualizar_fotos()))
            menu.add_command(label="Mostrar todas as pastas",
                             command=lambda: (self.pasta_filtro.set("todas"), self.atualizar_fotos()))
            menu.tk_popup(evento.x_root, evento.y_root)
            return
        indice = item["indice"] if (item and item.get("tipo") == "foto") else None
        if indice is None:
            return
        caminho = self._lista_fotos[indice][0]["caminho"]
        if normalizar_caminho(caminho) not in self.fotos_sel:
            self.fotos_sel = {normalizar_caminho(caminho)}
            self.ultima_foto_clicada = indice
            self.atualizar_fotos()
        fotos = self._fotos_escolhidas()
        quantidade = len(fotos)
        pasta_atual = proj.pasta_da_foto(caminho)
        nome_pasta = proj.nome_pasta_da_foto(caminho)
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="Colocar %s foto(s) na lâmina atual" % quantidade,
                         command=lambda: self.adicionar_na_lamina(fotos, self.atual)
                         if isinstance(self.atual, int) else None)
        menu.add_command(label="Degradê de opacidade nas %s foto(s)..." % quantidade,
                         command=self.abrir_aba_degrade)
        menu.add_command(label="Alternar destaque (%s foto(s))" % quantidade,
                         command=lambda: self.alternar_destaque(fotos))
        menu.add_separator()
        menu.add_command(label="Selecionar todas da pasta '%s'" % nome_pasta,
                         command=lambda: self._selecionar_pasta_inteira(pasta_atual))
        menu.add_command(label="Editar %s foto(s) no Photoshop" % quantidade,
                         command=lambda: self.editar_no_photoshop(fotos))
        menu.add_command(label="Tratar %s foto(s) (Ação / Lightroom)..." % quantidade,
                         command=lambda: self.abrir_tratamento(fotos))
        menu.add_separator()
        selecao = tk.Menu(menu, tearoff=0)
        selecao.add_command(label="Selecionar todas as fotos visíveis", command=self.selecionar_fotos_visiveis)
        selecao.add_command(label="Inverter seleção visível", command=self.inverter_selecao_fotos_visiveis)
        selecao.add_command(label="Limpar seleção", command=self.limpar_selecao_fotos)
        menu.add_cascade(label="Seleção", menu=selecao)
        menu.add_command(label="Abrir pasta da foto", command=lambda: os.startfile(str(Path(fotos[0]).parent)))
        menu.add_command(label="Remover %s foto(s) do projeto" % quantidade, command=self.remover_fotos_selecionadas)
        menu.tk_popup(evento.x_root, evento.y_root)

    def selecionar_fotos_visiveis(self):
        self.fotos_sel.update(normalizar_caminho(foto["caminho"]) for foto, _usos in self._lista_fotos)
        self.atualizar_fotos()

    def inverter_selecao_fotos_visiveis(self):
        visiveis = {normalizar_caminho(foto["caminho"]) for foto, _usos in self._lista_fotos}
        self.fotos_sel.symmetric_difference_update(visiveis)
        self.atualizar_fotos()

    def limpar_selecao_fotos(self):
        self.fotos_sel.clear()
        self.ultima_foto_clicada = None
        self.atualizar_fotos()

    def _fotos_escolhidas(self):
        fotos = [f["caminho"] for f in self.projeto["fotos"] if normalizar_caminho(f["caminho"]) in self.fotos_sel]
        if fotos:
            return fotos
        lamina = self._lamina()
        if lamina is not None and self.espaco_sel is not None and self.espaco_sel < len(lamina["espacos"]):
            foto = lamina["espacos"][self.espaco_sel].get("foto")
            if foto:
                return [foto]
        return []

    def _fotos_da_lamina(self):
        lamina = self._lamina()
        return [e["foto"] for e in lamina["espacos"] if e.get("foto")] if lamina else []

    def _fotos_do_album(self):
        vistos, saida = set(), []
        for espaco in proj.todos_espacos(self.projeto):
            if espaco.get("foto") and normalizar_caminho(espaco["foto"]) not in vistos:
                vistos.add(normalizar_caminho(espaco["foto"]))
                saida.append(espaco["foto"])
        return saida

    # ---------------- linha do tempo ----------------

    def _assinatura(self, item):
        dados = self.projeto["laminas"][item]
        texto = json.dumps(dados, sort_keys=True) + self.produto["nome"] + str(self.versao_fotos)
        return hashlib.md5(texto.encode("utf-8")).hexdigest()

    def _chave_timeline(self, item):
        return self.projeto["laminas"][item]["id"]

    def desenhar_timeline(self):
        c = self.canvas_timeline
        c.delete("all")
        itens = self._itens_timeline()
        self._tl_itens = itens
        self.rotulo_timeline.configure(text="Lâminas  %s" % len(self.projeto["laminas"]))
        vertical = bool(self._tl_vertical)
        larg, alt, altura = self._medidas_timeline()
        self._tl_larg, self._tl_alt, self._tl_altura = larg, alt, altura
        self._tl_passo_v = alt + 28
        self._tl_dim = c.winfo_width() if vertical else altura
        passo = self._tl_passo()
        if vertical:
            largura = max(larg + 16, c.winfo_width())
            c.configure(scrollregion=(0, 0, largura, max(1, len(itens) * passo)))
        else:
            c.configure(scrollregion=(0, 0, max(1, len(itens) * passo), altura))
        for posicao, item in enumerate(itens):
            if vertical:
                x, y = 8, posicao * passo + 6
            else:
                x, y = posicao * passo + 6, 6
            w_mm, h_mm = self._tamanho_item(item)
            h = min(int(larg * h_mm / w_mm), alt)
            w = int(h * w_mm / h_mm) if h == alt else larg
            xi = x + (larg - w) // 2
            yi = y + (alt - h) // 2
            selecionado = item == self.atual
            if selecionado:
                if vertical:
                    c.create_rectangle(3, y - 4, largura - 3, y + passo - 7, fill=tema.DESTAQUE_ESCURO, outline="")
                else:
                    c.create_rectangle(x - 4, 2, x + larg + 4, altura - 2, fill=tema.DESTAQUE_ESCURO, outline="")
            chave = self._chave_timeline(item)
            assinatura = "%s:%d" % (self._assinatura(item), w)
            pronta = self._tl_pil.get(chave)
            if pronta and pronta[0] == assinatura:
                if chave not in self._tl_tk or self._tl_tk[chave][0] != assinatura:
                    self._tl_tk[chave] = (assinatura, ImageTk.PhotoImage(pronta[1]))
                c.create_image(xi, yi, image=self._tl_tk[chave][1], anchor="nw")
            else:
                c.create_rectangle(xi, yi, xi + w, yi + h, fill="#ffffff", outline="")
                antiga = self._tl_tk.get(chave)
                if antiga and antiga[1].width() == w:
                    c.create_image(xi, yi, image=antiga[1], anchor="nw")
                self._pedir_timeline(item, chave, assinatura, w)
            if self.produto["paginas_por_lamina"] > 1:
                c.create_line(xi + w // 2, yi, xi + w // 2, yi + h, fill="#999999")
            rotulo = str(item + 1)
            c.create_text(x + larg // 2, y + alt + 10, text=rotulo, fill=tema.TEXTO if selecionado else tema.TEXTO_FRACO,
                          font=tema.FONTE_PEQUENA)
        self._desenhar_insercao()

    def _medidas_timeline(self):
        """(largura e altura da miniatura, altura do canvas): as miniaturas crescem com o painel."""
        c = self.canvas_timeline
        mapeado = c.winfo_ismapped() and c.winfo_height() > 10
        altura = c.winfo_height() if mapeado else int(c.cget("height"))
        if self._tl_vertical:
            largura = c.winfo_width() if mapeado else int(c.cget("width"))
            larg = max(60, largura - 16)
            lw, lh = lamina_mm(self.produto)
            return larg, max(20, int(round(larg * lh / lw))), altura
        alt = max(30, altura - 26)
        return int(round(alt * PROPORCAO_TIMELINE)), alt, altura

    def _tl_passo(self):
        return self._tl_passo_v if self._tl_vertical else self._tl_larg + 14

    def _mudou_tamanho_timeline(self, evento):
        if (evento.width if self._tl_vertical else evento.height) != self._tl_dim:
            self.agendar("timeline", 80, self.desenhar_timeline)

    def _timeline_roda(self, evento):
        passos = -int(evento.delta / 40)
        if self._tl_vertical:
            self.canvas_timeline.yview_scroll(passos, "units")
        else:
            self.canvas_timeline.xview_scroll(passos, "units")

    def _pedir_timeline(self, item, chave, assinatura, largura):
        mini = {"capa": None, "laminas": {item: json.loads(json.dumps(self.projeto["laminas"][item]))}}
        produto, info = self.produto, self.info

        def trabalho():
            img = exportar.renderizar_item(mini, produto, info, item, largura=largura, rapido=True)
            self.fila.put(("miniatura_timeline", chave, assinatura, img))

        self.pintor.pedir("tl:" + chave, trabalho)

    def _receber_timeline(self, chave, assinatura, img):
        self._tl_pil[chave] = (assinatura, img)
        self.agendar("timeline", 120, self.desenhar_timeline)

    def _timeline_em(self, x):
        passo = self._tl_passo()
        posicao = int(x // passo)
        if 0 <= posicao < len(self._tl_itens):
            return self._tl_itens[posicao]
        return None

    def _rolar_timeline_para(self, item):
        if item not in self._tl_itens or not self._tl_itens:
            return
        posicao = self._tl_itens.index(item)
        fracao = max(0.0, (posicao - 2) / float(len(self._tl_itens)))
        if self._tl_vertical:
            self.canvas_timeline.yview_moveto(fracao)
        else:
            self.canvas_timeline.xview_moveto(fracao)

    def _timeline_pressionou(self, evento):
        item = self._timeline_em(self._eixo_tl(evento.x, evento.y))
        if item is None:
            return
        self.selecionar_lamina(item)
        if isinstance(item, int):
            self._arrasto = {"tipo": "lamina", "item": item, "x": evento.x_root, "y": evento.y_root, "ativo": False}

    def _timeline_menu(self, evento):
        item = self._timeline_em(self._eixo_tl(evento.x, evento.y))
        if not isinstance(item, int):
            return
        self.selecionar_lamina(item)
        n = len(self.projeto["laminas"])
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="Nova lâmina depois desta", command=self.adicionar_lamina)
        menu.add_command(label="Duplicar lâmina", command=lambda: self.duplicar_lamina(item))
        menu.add_command(label="Diagramar de novo", command=self.rediagramar_lamina)
        menu.add_separator()

        def estado(ok):
            return "normal" if ok else "disabled"

        menu.add_command(label="Mover para antes (Ctrl+←)", state=estado(item > 0),
                         command=lambda: self.mover_lamina(item, item - 1))
        menu.add_command(label="Mover para depois (Ctrl+→)", state=estado(item < n - 1),
                         command=lambda: self.mover_lamina(item, item + 1))
        menu.add_command(label="Mover para o início", state=estado(item > 0),
                         command=lambda: self.mover_lamina_para("inicio", item))
        menu.add_command(label="Mover para o fim", state=estado(item < n - 1),
                         command=lambda: self.mover_lamina_para("fim", item))
        menu.add_command(label="Mover para a posição...", state=estado(n > 1),
                         command=lambda: self.mover_lamina_para("perguntar", item))
        menu.add_separator()
        menu.add_command(label="Apagar lâmina", command=lambda: self.remover_lamina(item))
        menu.tk_popup(evento.x_root, evento.y_root)

    def _mostrar_na_timeline(self, item):
        """Rola a linha do tempo só o necessário para a miniatura aparecer inteira."""
        if item not in self._tl_itens:
            return
        c = self.canvas_timeline
        passo = self._tl_passo()
        total = max(1, len(self._tl_itens) * passo)
        p0 = self._tl_itens.index(item) * passo
        p1 = p0 + passo
        if self._tl_vertical:
            visivel0, tamanho, mover = c.canvasy(0), c.winfo_height(), c.yview_moveto
        else:
            visivel0, tamanho, mover = c.canvasx(0), c.winfo_width(), c.xview_moveto
        if p0 < visivel0:
            mover(p0 / float(total))
        elif p1 > visivel0 + tamanho:
            mover(max(0.0, p1 - tamanho) / float(total))

    # ---------------- sugestões e modelos ----------------

    def atualizar_sugestoes(self):
        self.agendar("sugestoes", 150, self._calcular_sugestoes)

    def _calcular_sugestoes(self):
        lamina = self._lamina()
        self._sugestoes = []
        if lamina is not None:
            fotos = self._fotos_info([e["foto"] for e in lamina["espacos"] if e.get("foto")], lamina)
            if fotos:
                self._sugestoes = diagramar.opcoes_lamina(fotos, self.produto, self._cfg(), self._modelos(), maximo=12)
        self.desenhar_sugestoes()
        self.desenhar_modelos()
        self._atualizar_fundo_ui()

    def _desenhar_esquema(self, c, x, y, w, espacos, selecionado=False, paginas=2, tag=""):
        lw, lh = lamina_mm(self.produto)
        h = w * lh / lw
        c.create_rectangle(x, y, x + w, y + h, fill="#f4f4f4", outline=tema.DESTAQUE if selecionado else tema.BORDA,
                           width=2 if selecionado else 1, tags=tag)
        if paginas > 1:
            c.create_line(x + w / 2, y, x + w / 2, y + h, fill="#bbbbbb", tags=tag)
        for e in espacos:
            atravessa = e["x"] < 0.49 and (e["x"] + e["w"]) > 0.51
            preenchimento = "#7b93b8" if atravessa else "#9aa3ad"
            c.create_rectangle(x + e["x"] * w, y + e["y"] * h, x + (e["x"] + e["w"]) * w, y + (e["y"] + e["h"]) * h,
                               fill=preenchimento, outline="#f4f4f4", tags=tag)
        return h

    def desenhar_sugestoes(self):
        c = self.canvas_sugestoes
        c.delete("all")
        if not self._sugestoes:
            c.create_text(10, 20, anchor="w", fill=tema.TEXTO_FRACO,
                          text="Selecione uma lâmina com fotos." if isinstance(self.atual, int) else "A capa não tem sugestões.")
            return
        largura = max(100, (c.winfo_width() - 18) // 2)
        lamina = self._lamina()
        atual = tuple((round(e["x"], 3), round(e["y"], 3)) for e in lamina["espacos"]) if lamina else ()
        self._areas_sugestoes = []
        y = 4
        rotulos = {
            "panoramico_meio": "Panorâmica atravessa a dobra",
            "transpasse_60_40": "Transpassa o meio (60/40)",
            "transpasse_70_30": "Transpassa o meio (70/30)",
            "sem_corte": "Proporção original, sem corte",
        }
        for i, opcao in enumerate(self._sugestoes):
            coluna = i % 2
            x = 4 + coluna * (largura + 10)
            igual = tuple((round(e["x"], 3), round(e["y"], 3)) for e in opcao["espacos"]) == atual
            h = self._desenhar_esquema(c, x, y, largura, opcao["espacos"], igual, self.produto["paginas_por_lamina"])
            rotulo = rotulos.get(opcao.get("tipo_layout"))
            if rotulo:
                c.create_text(x + 3, y + h + 3, anchor="nw", text=rotulo, fill=tema.TEXTO_FRACO,
                              font=tema.FONTE_PEQUENA)
            cartao_h = h + 20
            self._areas_sugestoes.append((x, y, x + largura, y + cartao_h, i))
            if coluna == 1:
                y += h + 30
        c.configure(scrollregion=(0, 0, c.winfo_width(), y + 80))

    def _sugestao_clicada(self, evento):
        y = self.canvas_sugestoes.canvasy(evento.y)
        for x0, y0, x1, y1, i in getattr(self, "_areas_sugestoes", []):
            if x0 <= evento.x <= x1 and y0 <= y <= y1:
                lamina = self._lamina()
                if lamina is not None:
                    antes = [dict(e) for e in lamina.get("espacos", [])]
                    self.marcar_historico()
                    self._aplicar_opcao(lamina, self._sugestoes[i])
                    self.espaco_sel = None
                    self._marcar_modificado()
                    self.desenhar_lamina()
                    self.desenhar_timeline()
                    self.desenhar_sugestoes()
                    self._animar_transicao_layout(antes, lamina.get("espacos", []))
                    if self.editor:
                        alvo = self._alvo_padrao()
                        if alvo:
                            self.editor.mostrar(alvo)
                return

    def desenhar_modelos(self):
        c = self.canvas_modelos
        c.delete("all")
        modelos = self._modelos()
        lamina = self._lamina()
        n = sum(1 for e in lamina["espacos"] if e.get("foto")) if lamina else 0
        if self.so_compativeis.get() and n:
            modelos = [m for m in modelos if len(m["espacos"]) == n
                       or (m.get("escopo") == "pagina" and len(m["espacos"]) <= n)]
        self._areas_modelos = []
        largura = max(100, (c.winfo_width() - 18) // 2)
        y = 4
        for i, modelo in enumerate(modelos):
            coluna = i % 2
            x = 4 + coluna * (largura + 10)
            if modelo.get("escopo") == "pagina" and self.produto["paginas_por_lamina"] > 1:
                espacos, _s = modelos_lamina.espacos_do_modelo(modelo, self.produto, 0)
                espacos = [dict(e, x=e["x"] * 2, w=e["w"] * 2) for e in espacos]
                lw, lh = lamina_mm(self.produto)
                pw = largura / 2.0
                ph = pw * lh / (lw / 2.0)
                selecionado = modelo["id"] == self._modelo_sel
                c.create_rectangle(x + pw / 2, y, x + pw * 1.5, y + ph, fill="#f4f4f4",
                                   outline=tema.DESTAQUE if selecionado else tema.BORDA, width=2 if selecionado else 1)
                for e in espacos:
                    c.create_rectangle(x + pw / 2 + e["x"] * pw, y + e["y"] * ph, x + pw / 2 + (e["x"] + e["w"]) * pw,
                                       y + (e["y"] + e["h"]) * ph, fill="#9aa3ad", outline="#f4f4f4")
                h = ph
            else:
                espacos, _s = modelos_lamina.espacos_do_modelo(modelo, self.produto)
                h = self._desenhar_esquema(c, x, y, largura, espacos, modelo["id"] == self._modelo_sel,
                                           self.produto["paginas_por_lamina"])
            nome = modelo.get("nome", modelo["id"])
            c.create_text(x + largura / 2, y + h + 9, text=nome if len(nome) < 26 else nome[:24] + "…",
                          fill=tema.TEXTO_FRACO, font=tema.FONTE_PEQUENA)
            self._areas_modelos.append((x, y, x + largura, y + h + 16, modelo["id"]))
            if coluna == 1 or i == len(modelos) - 1:
                y += h + 28
        if not modelos:
            c.create_text(10, 20, anchor="w", fill=tema.TEXTO_FRACO, text="Nenhum modelo com essa quantidade de fotos.")
        c.configure(scrollregion=(0, 0, c.winfo_width(), y + 10))

    def _modelo_clicado(self, evento):
        y = self.canvas_modelos.canvasy(evento.y)
        for x0, y0, x1, y1, id_modelo in getattr(self, "_areas_modelos", []):
            if x0 <= evento.x <= x1 and y0 <= y <= y1:
                self._modelo_sel = id_modelo
                self.desenhar_modelos()
                return

    # ---------------- fundo (lâmina inteira, por página e mistura no meio) ----------------

    def _dono_do_fundo(self):
        """O dicionário que guarda o fundo da lâmina aberta."""
        return self._lamina()

    def _chaves_fundo_alvo(self, alvo=None):
        alvo = (self.fundo_alvo.get() if hasattr(self, "fundo_alvo") else "lamina") if alvo is None else alvo
        if alvo == "esquerda":
            return "fundo_esquerda", "fundo_imagem_esquerda"
        if alvo == "direita":
            return "fundo_direita", "fundo_imagem_direita"
        return "fundo", "fundo_imagem"

    def _dados_fundo_imagem_ativo(self, dono=None):
        dono = self._dono_do_fundo() if dono is None else dono
        if dono is None:
            return None
        _ch_cor, ch_img = self._chaves_fundo_alvo()
        return dono.get(ch_img)

    def _cor_fundo_ativa(self, dono=None):
        dono = self._dono_do_fundo() if dono is None else dono
        if dono is None:
            return "#ffffff"
        ch_cor, _ch_img = self._chaves_fundo_alvo()
        return dono.get(ch_cor) or dono.get("fundo", "#ffffff")

    def escolher_fundo(self):
        dono = self._dono_do_fundo()
        if dono is None:
            return
        cor_atual = cor_hex(self._cor_fundo_ativa(dono))
        cor = colorchooser.askcolor(color=cor_atual, parent=self.root)[1]
        if cor:
            self.marcar_historico()
            ch_cor, _ch_img = self._chaves_fundo_alvo()
            dono[ch_cor] = cor
            if ch_cor == "fundo":
                self.projeto.setdefault("config", {})["fundo"] = cor
            self._atualizar_fundo_ui()
            self.atualizar_lamina_atual()

    def _mudou_fusao_fundo(self, marcar=False):
        if self._carregando_fundo:
            return
        dono = self._dono_do_fundo()
        valor = int(round(self.fundo_fusao.get()))
        if hasattr(self, "rotulo_fundo_fusao"):
            self.rotulo_fundo_fusao.configure(text="%d%%" % valor)
        if dono is None:
            return
        if int(dono.get("fundo_fusao") or 0) == valor and not marcar:
            return
        dono["fundo_fusao"] = valor
        self.fundo_ajustado(final=marcar)

    def fundo_em_todas(self):
        dono = self._dono_do_fundo()
        if dono is None or not self.projeto["laminas"]:
            return
        self.marcar_historico()
        chaves_copiar = ("fundo", "fundo_esquerda", "fundo_direita", "fundo_fusao",
                         "fundo_imagem", "fundo_imagem_esquerda", "fundo_imagem_direita")
        for outra in self.projeto["laminas"]:
            for ch in chaves_copiar:
                if dono.get(ch) is not None:
                    outra[ch] = proj.copia(dono[ch])
                else:
                    outra.pop(ch, None)
        self.atualizar_tudo()
        self.registrar("Fundo aplicado em todas as %s lâminas." % len(self.projeto["laminas"]))

    def escolher_imagem_fundo(self):
        if self._dono_do_fundo() is None:
            self.registrar("Escolha uma lâmina ou a capa.")
            return
        caminho = filedialog.askopenfilename(parent=self.root, title="Imagem de fundo",
                                             initialdir=self.projeto.get("pasta_fotos") or None,
                                             filetypes=[("Imagens", "*.jpg *.jpeg *.png *.tif *.tiff"), ("Todos", "*.*")])
        if caminho:
            self.definir_imagem_fundo(caminho)

    def usar_foto_como_fundo(self):
        fotos = self._fotos_escolhidas()
        if not fotos:
            self.registrar("Selecione uma foto no painel de fotos ou um espaço com foto.")
            return
        self.definir_imagem_fundo(fotos[0])

    def definir_imagem_fundo(self, caminho):
        self.aplicar_fundo_em(caminho, self.atual)

    def aplicar_fundo_em(self, caminho, item=None, alvo=None):
        """
        Põe a imagem como fundo do alvo selecionado (lâmina inteira, página esquerda ou página direita),
        mantendo modo, opacidade, desfoque, P&B e os ajustes de cor que já tinha.
        """
        item = self.atual if item is None else item
        dono = self._lamina(item)
        if dono is None:
            self.registrar("Escolha uma lâmina.")
            return False
        _ch_cor, ch_img = self._chaves_fundo_alvo(alvo)
        anterior = (dono.get(ch_img) or {}).get("caminho")
        if anterior and normalizar_caminho(anterior) == normalizar_caminho(caminho):
            return False
        self.marcar_historico()
        dados = fundo_imagem_padrao()
        dados.update(proj.copia(dono.get(ch_img) or dono.get("fundo_imagem") or {}))
        dados["caminho"] = str(Path(caminho))
        dono[ch_img] = dados
        # Se aplicou em uma página (esquerda/direita) e ainda não tinha fusão configurada, mantém suave se o outro lado existir
        self._fundos_avisados.discard(normalizar_caminho(caminho))
        self._atualizar_fundo_ui()
        self.atualizar_lamina_atual()
        nome_alvo = {"fundo_imagem": "lâmina %s" % (item + 1),
                     "fundo_imagem_esquerda": "lâmina %s (pág. esquerda)" % (item + 1),
                     "fundo_imagem_direita": "lâmina %s (pág. direita)" % (item + 1)}[ch_img]
        self.registrar("Fundo da %s: %s" % (nome_alvo, Path(caminho).name))
        return True

    def aplicar_fundo_em_todas(self, caminho):
        if not self.projeto["laminas"]:
            return
        self.marcar_historico()
        _ch_cor, ch_img = self._chaves_fundo_alvo()
        for lamina in self.projeto["laminas"]:
            dados = fundo_imagem_padrao()
            dados.update(proj.copia(lamina.get(ch_img) or lamina.get("fundo_imagem") or {}))
            dados["caminho"] = str(Path(caminho))
            lamina[ch_img] = dados
        self.atualizar_tudo()
        self.registrar("Fundo %s em todas as %s lâminas." % (Path(caminho).name, len(self.projeto["laminas"])))

    def fundo_imagem_em_todas(self):
        """A imagem e todos os ajustes do fundo aberto copiados para as lâminas."""
        dono = self._dono_do_fundo()
        _ch_cor, ch_img = self._chaves_fundo_alvo()
        dados = (dono or {}).get(ch_img)
        if not dados or not self.projeto["laminas"]:
            return
        self.marcar_historico()
        for lamina in self.projeto["laminas"]:
            lamina[ch_img] = proj.copia(dados)
            if dono.get("fundo_fusao") is not None:
                lamina["fundo_fusao"] = dono.get("fundo_fusao")
        self.atualizar_tudo()
        self.registrar("Fundo e ajustes copiados para as %s lâminas." % len(self.projeto["laminas"]))

    # ---------------- pasta de fundos e galeria ----------------

    def _pasta_fundos(self):
        return self.projeto.get("pasta_fundos") or fundos.ler_config(ARQUIVO_FUNDOS).get("pasta") or ""

    def escolher_pasta_fundos(self):
        pasta = filedialog.askdirectory(parent=self.root, title="Pasta com imagens de fundo",
                                        initialdir=self._pasta_fundos() or None)
        if pasta:
            self.usar_pasta_fundos(pasta)

    def usar_pasta_fundos(self, pasta):
        fundos.lembrar_pasta(ARQUIVO_FUNDOS, pasta)
        if self.projeto.get("pasta_fundos") != str(pasta):
            self.projeto["pasta_fundos"] = str(pasta)
            self._marcar_modificado()
        self.atualizar_fundos(forcar=True)

    def esquecer_pasta_fundos_do_album(self):
        if self.projeto.pop("pasta_fundos", None):
            self._marcar_modificado()
        self.atualizar_fundos(forcar=True)

    def _montar_recentes_fundos(self):
        menu = self.menu_recentes_fundos
        menu.delete(0, "end")
        recentes = [p for p in fundos.ler_config(ARQUIVO_FUNDOS)["recentes"] if Path(p).is_dir()]
        if not recentes:
            menu.add_command(label="(nenhuma ainda)", state="disabled")
        for pasta in recentes:
            menu.add_command(label=pasta, command=lambda p=pasta: self.usar_pasta_fundos(p))
        if self.projeto.get("pasta_fundos"):
            menu.add_separator()
            menu.add_command(label="Usar a pasta padrão (esquecer a deste álbum)", command=self.esquecer_pasta_fundos_do_album)

    def atualizar_fundos(self, forcar=False):
        """Lê a pasta de fundos (a do álbum ou a padrão) e atualiza a galeria se algo mudou."""
        if not hasattr(self, "galeria_fundos"):
            return
        pasta = self._pasta_fundos()
        lista = fundos.listar_fundos(pasta)
        if forcar or pasta != self._fundos_pasta or lista != self._fundos_lista:
            self._fundos_pasta, self._fundos_lista = pasta, lista
            self.galeria_fundos.definir(lista)
        if not pasta:
            texto = "Nenhuma pasta escolhida."
        elif not Path(pasta).is_dir():
            texto = "Pasta não encontrada: %s" % pasta
        else:
            texto = "%s · %s imagem(ns)%s" % (Path(pasta).name, len(lista),
                                             " · deste álbum" if self.projeto.get("pasta_fundos") else "")
        self.rotulo_pasta_fundos.configure(text=texto)

    def _fundo_atual_normalizado(self):
        dados = self._dados_fundo_imagem_ativo() or {}
        return normalizar_caminho(dados["caminho"]) if dados.get("caminho") else None

    def _galeria_pressionou(self, caminho, evento):
        self._arrasto = {"tipo": "fundo", "caminho": caminho, "x": evento.x_root, "y": evento.y_root, "ativo": False}

    def _soltar_fundo(self, a, evento):
        if not a["ativo"]:
            self.aplicar_fundo_em(a["caminho"])
            return
        alvo = self.root.winfo_containing(evento.x_root, evento.y_root)
        if alvo is self.canvas:
            cx = evento.x_root - self.canvas.winfo_rootx()
            cy = evento.y_root - self.canvas.winfo_rooty()
            if self._dentro_da_lamina(cx, cy):
                self.aplicar_fundo_em(a["caminho"])
        elif alvo is self.canvas_timeline:
            item = self._timeline_em(self._eixo_tl_root(evento.x_root, evento.y_root))
            if item is not None:
                self.aplicar_fundo_em(a["caminho"], item)

    def _galeria_menu(self, caminho, evento):
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="Aplicar no alvo atual (%s)" % self.fundo_alvo.get(), command=lambda: self.aplicar_fundo_em(caminho))
        menu.add_command(label="Aplicar na Lâmina inteira", command=lambda: (self.fundo_alvo.set("lamina"), self.aplicar_fundo_em(caminho, alvo="lamina")))
        menu.add_command(label="Aplicar na Página Esquerda", command=lambda: (self.fundo_alvo.set("esquerda"), self.aplicar_fundo_em(caminho, alvo="esquerda")))
        menu.add_command(label="Aplicar na Página Direita", command=lambda: (self.fundo_alvo.set("direita"), self.aplicar_fundo_em(caminho, alvo="direita")))
        menu.add_separator()
        menu.add_command(label="Aplicar em todas as lâminas", command=lambda: self.aplicar_fundo_em_todas(caminho))
        menu.add_command(label="Ajustar cor...", command=lambda: self.abrir_editor_fundo(caminho))
        menu.add_separator()
        menu.add_command(label="Abrir pasta", command=lambda: os.startfile(str(Path(caminho).parent)))
        menu.tk_popup(evento.x_root, evento.y_root)

    def abrir_editor_fundo(self, caminho=None):
        """Janela de ajuste de cor do fundo; os ajustes não mexem no arquivo da imagem."""
        if self._dono_do_fundo() is None:
            self.registrar("Escolha uma lâmina ou a capa.")
            return
        if caminho:
            self.aplicar_fundo_em(caminho)
        if self.editor_fundo is None:
            self.editor_fundo = fundos.EditorFundo(self)
        else:
            self.editor_fundo.mostrar()
            self.editor_fundo.lift()
        self._pedir_camada_frente()

    def fundo_ajustado(self, final=False):
        """O fundo mudou por um controle: prévia rápida agora e o desenho completo quando parar."""
        self._marcar_modificado()
        if final:
            self._cancelar_agendado("lamina_fundo")
            self._cancelar_previa_fundo()
            self._atualizar_fundo_ui()
            self.desenhar_lamina()
        else:
            self._pedir_previa_fundo()
            self.agendar("lamina_fundo", 450, self.desenhar_lamina)
        self.agendar("timeline_fundo", 800, self.desenhar_timeline)

    def _pedir_previa_fundo(self):
        espera = 0.03 - (time.perf_counter() - self._previa_fundo_ultima)
        if espera <= 0:
            self._cancelar_previa_fundo()
            self._previa_fundo_rapida()
        elif self._previa_fundo_agendada is None:
            self._previa_fundo_agendada = self.root.after(max(1, int(espera * 1000)), self._previa_fundo_rapida)

    def _cancelar_previa_fundo(self):
        if self._previa_fundo_agendada is not None:
            self.root.after_cancel(self._previa_fundo_agendada)
            self._previa_fundo_agendada = None

    def _chave_frente(self):
        if self._geo is None:
            return None
        lamina = self._lamina()
        if lamina is None:
            return None
        dados = {"e": lamina["espacos"], "s": lamina.get("sobreposicoes")}
        return (self.atual, self._geo[0], self._geo[1], json.dumps(dados, sort_keys=True), self.versao_fotos)

    def _pedir_camada_frente(self):
        """Fotos, moldes e textos sem o fundo, para a prévia rápida só refazer o fundo."""
        chave = self._chave_frente()
        if chave is None or (self._camada_frente and self._camada_frente[0] == chave):
            return
        mini, item, produto, info, largura = self._copia_para_render(), self.atual, self.produto, self.info, chave[1]

        def trabalho():
            img = exportar.renderizar_item(mini, produto, info, item, largura=largura, rapido=True, sem_fundo=True)
            self.fila.put(("chamar", lambda: setattr(self, "_camada_frente", (chave, img))))

        self.pintor.pedir("frente", trabalho)

    def _previa_fundo_rapida(self):
        self._previa_fundo_agendada = None
        self._previa_fundo_ultima = time.perf_counter()
        dono = self._dono_do_fundo()
        chave = self._chave_frente()
        if dono is None or chave is None:
            return
        if not self._camada_frente or self._camada_frente[0] != chave:
            self._pedir_camada_frente()
            return
        inicio = time.perf_counter()
        frente = self._camada_frente[1]
        base = compor_fundo_completo(
            frente.size, dono.get("fundo", "#ffffff"),
            dono.get("fundo_imagem"), rapido=True, opcoes_pagina=dono
        )
        base.alpha_composite(frente)
        self._tk_previa_fundo = ImageTk.PhotoImage(base.convert("RGB"))
        _dw, _dh, ox, oy = self._geo
        c = self.canvas
        c.delete("img")
        c.delete("remendo")
        c.create_image(ox, oy, image=self._tk_previa_fundo, anchor="nw", tags="img")
        c.tag_raise("espaco")
        c.tag_raise("arrasto")
        self.tempos_previa_fundo.append(time.perf_counter() - inicio)
        del self.tempos_previa_fundo[:-500]

    def tirar_imagem_fundo(self):
        dono = self._dono_do_fundo()
        if dono is None:
            return
        ch_cor, ch_img = self._chaves_fundo_alvo()
        tem_algo = bool(dono.get(ch_img) or (ch_cor != "fundo" and dono.get(ch_cor)))
        if not tem_algo:
            return
        self.marcar_historico()
        dono.pop(ch_img, None)
        if ch_cor != "fundo":
            dono.pop(ch_cor, None)
        self._atualizar_fundo_ui()
        self.atualizar_lamina_atual()

    def _mudou_fundo_imagem(self, marcar):
        if self._carregando_fundo:
            return
        dono = self._dono_do_fundo()
        _ch_cor, ch_img = self._chaves_fundo_alvo()
        dados = (dono or {}).get(ch_img)
        self._rotulos_fundo["Opacidade"].configure(text="%d%%" % round(self.fundo_opacidade.get()))
        self._rotulos_fundo["Desfoque"].configure(text="%d" % round(self.fundo_desfoque.get()))
        if not dados:
            return
        novo = dict(dados, modo=self.fundo_modo.get(), opacidade=int(round(self.fundo_opacidade.get())),
                    desfoque=int(round(self.fundo_desfoque.get())), pb=bool(self.fundo_pb.get()))
        if novo == dados:
            return
        if marcar:
            self.marcar_historico()
        dono[ch_img] = novo
        self.fundo_ajustado(final=marcar)

    def _atualizar_fundo_ui(self):
        if not hasattr(self, "rotulo_fundo"):
            return
        dono = self._dono_do_fundo()
        alvo = self.fundo_alvo.get() if hasattr(self, "fundo_alvo") else "lamina"
        sufixo_alvo = {"lamina": "", "esquerda": " · Pág. Esq.", "direita": " · Pág. Dir."}.get(alvo, "")
        if dono is None:
            self.rotulo_fundo.configure(text="Fundo")
        else:
            self.rotulo_fundo.configure(text="Fundo da lâmina %s%s" % (self.atual + 1, sufixo_alvo))
        self.amostra_fundo.configure(bg=cor_hex(self._cor_fundo_ativa(dono)))
        dados = self._dados_fundo_imagem_ativo(dono)
        fusao_val = int(round(float((dono or {}).get("fundo_fusao") or 0)))
        self._carregando_fundo = True
        try:
            if hasattr(self, "fundo_fusao"):
                self.fundo_fusao.set(fusao_val)
            if hasattr(self, "rotulo_fundo_fusao"):
                self.rotulo_fundo_fusao.configure(text="%d%%" % fusao_val)
            if dados and dados.get("caminho"):
                base = fundo_imagem_padrao()
                base.update(dados)
                faltando = not Path(base["caminho"]).is_file()
                self.rotulo_fundo_imagem.configure(text=Path(base["caminho"]).name + ("  (arquivo não encontrado)" if faltando else ""))
                self.fundo_modo.set(base["modo"])
                self.fundo_opacidade.set(base["opacidade"])
                self.fundo_desfoque.set(base["desfoque"])
                self.fundo_pb.set(bool(base["pb"]))
            else:
                if alvo in ("esquerda", "direita") and not (dono or {}).get("fundo_" + alvo):
                    self.rotulo_fundo_imagem.configure(text="Usando o fundo da lâmina inteira (escolha cor ou imagem para separar esta página).")
                else:
                    self.rotulo_fundo_imagem.configure(text="Sem imagem: só a cor.")
            for controle in self._controles_fundo:
                controle.state(["!disabled"] if (dados and dados.get("caminho")) else ["disabled"])
        finally:
            self._carregando_fundo = False
        self._rotulos_fundo["Opacidade"].configure(text="%d%%" % round(self.fundo_opacidade.get()))
        self._rotulos_fundo["Desfoque"].configure(text="%d" % round(self.fundo_desfoque.get()))
        self.galeria_fundos._agendar()
        if self.editor_fundo is not None:
            self.editor_fundo.mostrar()

    def _avisar_fundo_faltando(self):
        dono = self._dono_do_fundo()
        if not dono:
            return
        for ch_img in ("fundo_imagem", "fundo_imagem_esquerda", "fundo_imagem_direita"):
            dados = dono.get(ch_img)
            if not dados or not dados.get("caminho"):
                continue
            chave = normalizar_caminho(dados["caminho"])
            if chave not in self._fundos_avisados and not Path(dados["caminho"]).is_file():
                self._fundos_avisados.add(chave)
                self.registrar("Imagem de fundo não encontrada (a lâmina sai só com a cor): %s" % dados["caminho"])

    # ==================================================================
    # Tela de edição
    # ==================================================================

    def abrir_editor(self, alvo):
        if self.editor is None:
            self.editor = EditorFoto(self)
        if alvo[0] == "lamina":
            self.atual, self.espaco_sel = alvo[1], alvo[2]
            self.desenhar_lamina()
        self.editor.mostrar(alvo)

    def alvo_espaco(self, alvo):
        """(espaço, proporção do espaço, descrição) para a tela de edição."""
        if alvo[0] != "lamina":
            return None, 1.0, ""
        _t, li, ei = alvo
        lamina = self._lamina(li)
        if lamina is None or not (0 <= ei < len(lamina["espacos"])):
            return None, 1.0, ""
        espaco = lamina["espacos"][ei]
        W, H = lamina_px(self.produto)
        return espaco, (espaco["w"] * W) / (espaco["h"] * H), "Lâmina %s · espaço %s de %s" % (
            li + 1, ei + 1, len(lamina["espacos"]))

    def fundo_do_alvo(self, alvo):
        lamina = self._lamina(alvo[1]) if alvo and alvo[0] == "lamina" else None
        return (lamina or {}).get("fundo", "#ffffff")

    def alvo_vizinho(self, alvo, passo):
        lista = [("lamina", li, ei) for li, l in enumerate(self.projeto["laminas"])
                 for ei, e in enumerate(l["espacos"]) if e.get("foto")]
        if not lista:
            return None
        if alvo in lista:
            return lista[(lista.index(alvo) + passo) % len(lista)]
        return lista[0]

    def edicao_mudou(self, alvo, rapido=False):
        self._marcar_modificado()
        self.agendar("lamina_edicao", 60 if rapido else 10, self.desenhar_lamina)
        self.agendar("timeline_edicao", 700, self.desenhar_timeline)

    # ==================================================================
    # Photoshop e Lightroom
    # ==================================================================

    def editar_no_photoshop(self, caminhos):
        caminhos = [c for c in caminhos if c]
        if not caminhos:
            self.registrar("Selecione uma ou mais fotos.")
            return
        for caminho in caminhos:
            self.observador.vigiar(caminho)

        def trabalho(t):
            photoshop.abrir_para_editar(caminhos)

        def fim(_r, erro):
            if erro:
                messagebox.showerror("Photoshop", str(erro), parent=self.root)
            else:
                self.registrar("Aberto no Photoshop. Edite e salve com Ctrl+S: o álbum atualiza sozinho.")

        self.executar("Abrindo no Photoshop", trabalho, fim)

    def _foto_trocada(self, original, final):
        """A foto foi tratada (ou salva no Photoshop): atualiza caminho, miniaturas e análise."""
        if normalizar_caminho(original) != normalizar_caminho(final):
            proj.trocar_caminho(self.projeto, original, final)
            self._marcar_modificado()
        analise.esquecer_miniaturas(original)
        analise.esquecer_miniaturas(final)

        def reanalisar():
            try:
                dados = analise.analisar_foto(final)
                analise.gravar_cache()
                self.fila.put(("analisada", final, dados))
            except Exception as e:
                self.fila.put(("aviso", "Não deu para ler %s: %s" % (Path(final).name, e)))

        threading.Thread(target=reanalisar, daemon=True).start()

    def _depois_de_trocar_foto(self, caminho):
        self.versao_fotos += 1
        self._tk_lamina = None
        self.atualizar_fotos()
        self.desenhar_lamina()
        self.desenhar_timeline()
        if self.editor and self.editor.espaco and self.editor.espaco.get("foto") and \
                normalizar_caminho(self.editor.espaco["foto"]) == normalizar_caminho(caminho):
            self.editor.recarregar()

    # ==================================================================
    # Janelas de tratamento e exportação, programa de capas
    # ==================================================================

    def abrir_tratamento(self, fotos=None):
        """Janela de tratamento; com fotos, já vem com elas escolhidas."""
        if self.janela_tratamento is not None and self.janela_tratamento.winfo_exists():
            self.janela_tratamento.fechar()
        self.janela_tratamento = janela_tratamento.JanelaTratamento(self, fotos)
        return self.janela_tratamento

    def abrir_programa_capa(self, programa=None):
        """A capa é feita no programa de capas: abre ele e o trabalho continua lá."""
        try:
            processo, comando = atalho_capa.abrir(ARQUIVO_CAPA, programa)
        except (OSError, FileNotFoundError) as e:
            if messagebox.askyesno("Capa", "%s\n\nQuer indicar onde está o programa de capas?" % e, parent=self.root):
                self.escolher_programa_capa(abrir=True)
            return None
        self.processo_capa = processo
        self.registrar("Programa de capas aberto: %s" % " ".join('"%s"' % c if " " in c else c for c in comando))
        return processo

    def abrir_automacao_capa(self):
        programa = atalho_capa.programa_configurado(ARQUIVO_CAPA)
        pasta = programa.parent if programa else None
        if not pasta or not (pasta / atalho_capa.PROGRAMA_AUTOMACAO).is_file():
            self.registrar("Não achei %s." % atalho_capa.PROGRAMA_AUTOMACAO)
            return None
        return self.abrir_programa_capa(pasta / atalho_capa.PROGRAMA_AUTOMACAO)

    def escolher_programa_capa(self, abrir=False):
        atual = atalho_capa.programa_configurado(ARQUIVO_CAPA)
        caminho = filedialog.askopenfilename(
            parent=self.root, title="Programa de capas", initialdir=str(atual.parent) if atual else None,
            filetypes=[("Programa Python ou exe", "*.pyw *.py *.exe"), ("Todos", "*.*")])
        if not caminho:
            return
        atalho_capa.gravar_config(ARQUIVO_CAPA, programa=caminho)
        self.registrar("Programa de capas: %s" % caminho)
        if abrir:
            self.abrir_programa_capa()

    def mostrar_info_produto(self):
        messagebox.showinfo("Produto", self.dica_produto.texto, parent=self.root)

    def arquivo_exportacao(self):
        return ARQUIVO_EXPORTACAO

    def opcoes_exportacao(self):
        op = exportar.opcoes_padrao()
        op.update({k: v for k, v in (self.projeto.get("exportacao") or {}).items() if k in op})
        if not op["pasta"]:
            op["pasta"] = self.projeto.get("pasta_saida") or entrada.sugerir_saida(self.projeto.get("pasta_fotos"))
        return op

    def guardar_opcoes_exportacao(self, op):
        dados = {k: op[k] for k in exportar.opcoes_padrao() if k in op}
        if dados != self.projeto.get("exportacao") or op.get("pasta") != self.projeto.get("pasta_saida"):
            self.projeto["exportacao"] = dados
            if op.get("pasta"):
                self.projeto["pasta_saida"] = op["pasta"]
            self._marcar_modificado()

    def abrir_exportar(self):
        if self.janela_exportar is not None and self.janela_exportar.winfo_exists():
            self.janela_exportar.lift()
            return self.janela_exportar
        self.janela_exportar = janela_exportar.JanelaExportar(self)
        return self.janela_exportar

    def executar_exportacao(self, op, janela=None):
        """Exporta com as opções da janela. Devolve False se não começou."""
        if self.tarefa is not None:
            messagebox.showinfo("Aguarde", "Ainda estou terminando: %s" % self.tarefa[0], parent=janela or self.root)
            return False
        try:
            indices = janela_exportar.indices_escolhidos(op, self.projeto, self.atual)
            itens = exportar.itens_exportacao(self.projeto, indices, op["padrao"], self.produto)
        except ValueError as e:
            messagebox.showwarning("Exportar", str(e), parent=janela or self.root)
            return False
        if not op.get("pasta"):
            messagebox.showwarning("Exportar", "Escolha a pasta de saída.", parent=janela or self.root)
            return False
        pasta = janela_exportar.pasta_final(op, self.projeto)
        extensoes = {"jpg": [".jpg"], "psd": [".psd"], "ambos": [".jpg", ".psd"]}[op["formato"]]
        existentes = [nome + ext for nome, _i in itens for ext in extensoes if (pasta / (nome + ext)).exists()]
        modo = op["existentes"]
        if modo == "perguntar" and existentes:
            resposta = messagebox.askyesnocancel(
                "Exportar", "%s arquivo(s) já existem em\n%s\n\nSim: substituir   ·   Não: pular os que existem"
                % (len(existentes), pasta), parent=janela or self.root)
            if resposta is None:
                return False
            modo = "substituir" if resposta else "pular"
        elif modo == "perguntar":
            modo = "substituir"
        self.guardar_opcoes_exportacao(op)
        janela_exportar.lembrar_pasta(self.arquivo_exportacao(), op["pasta"])
        opcoes = dict(op, indices=indices, existentes=modo)
        copia = json.loads(json.dumps(self.projeto))
        produto, info, formato = self.produto, dict(self.info), op["formato"]

        def trabalho(t):
            gravados = []
            if formato in ("jpg", "ambos"):
                gravados += exportar.exportar_jpg(copia, produto, info, pasta, t.aviso, t.parar, t.progresso, opcoes)
            if formato in ("psd", "ambos") and not t.parar():
                import exportar_psd
                gravados += exportar_psd.exportar_psd(copia, produto, info, pasta, t.aviso, t.parar, t.progresso, opcoes)
            return gravados

        def fim(gravados, erro):
            if janela is not None and janela.winfo_exists():
                janela.terminou(gravados, erro)
            elif erro:
                messagebox.showerror("Exportar", str(erro), parent=self.root)
            elif gravados and messagebox.askyesno("Exportar", "%s arquivo(s) gravados em\n%s\n\nAbrir a pasta?" % (
                    len(gravados), pasta), parent=self.root):
                os.startfile(str(pasta))

        return self.executar("Exportando", trabalho, fim, janela=janela)

    def exportar(self, tipo=None):
        """Sem tipo abre a janela Exportar; com tipo exporta direto com as opções guardadas no álbum."""
        if tipo is None:
            return self.abrir_exportar()
        if not self.projeto["laminas"]:
            self.registrar("Não há lâminas para exportar.")
            return False
        op = dict(self.opcoes_exportacao(), formato=tipo)
        avisos = janela_exportar.conferir(self.projeto, janela_exportar.indices_escolhidos(op, self.projeto, self.atual))
        if avisos and not messagebox.askyesno("Exportar", "\n".join(avisos) + "\n\nExportar mesmo assim?", parent=self.root):
            return False
        return self.executar_exportacao(op)


def main():
    try:
        argumentos = [a for a in sys.argv[1:] if a]
        root = TkinterDnD.Tk() if TkinterDnD is not None else tk.Tk()
        Aplicativo(root, argumentos)
        root.mainloop()
    except Exception:
        try:
            messagebox.showerror("Erro ao iniciar Smart Album", traceback.format_exc())
        except Exception:
            pass
        raise


if __name__ == "__main__":
    main()
