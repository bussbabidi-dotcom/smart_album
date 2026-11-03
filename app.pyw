# -*- coding: utf-8 -*-
"""
Programa de diagramação de álbuns.

Janela no estilo dos programas de álbum: lâmina aberta no centro, painel
de fotos e linha do tempo das lâminas embaixo, painel de design à direita.
Fotos e pastas podem ser arrastadas do Explorer, coladas com Ctrl+V ou
passadas ao abrir o programa (arrastar a pasta em cima do iniciar.bat).
"""

import atexit
import bisect
import copy
import hashlib
import json
import math
import os
import queue
import re
import shutil
import sys
import tempfile
import threading
import time
import traceback
import urllib.parse
import urllib.request
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
from comum import (
    EXTENSOES, apagar_pasta, cor_hex, cor_rgb, gravar_json, ler_json,
    limpar_temporarios_disco, normalizar_caminho, pasta_dados,
)
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
ARQUIVO_ATALHOS = pasta_dados() / "atalhos.json"

DEFINICOES_ATALHOS = (
    ("novo_projeto", "Novo projeto", "Ctrl+N"),
    ("abrir_projeto", "Abrir projeto", "Ctrl+O"),
    ("salvar_projeto", "Salvar projeto", "Ctrl+S"),
    ("escolher_pasta", "Carregar pasta", "Ctrl+P"),
    ("importar_link_drive", "Colar link do Drive", "Ctrl+G"),
    ("escolher_fotos", "Carregar fotos", "Ctrl+I"),
    ("abrir_exportar", "Exportar álbum", "Ctrl+E"),
    ("desfazer", "Desfazer", "Ctrl+Z"),
    ("refazer", "Refazer", "Ctrl+Y"),
    ("abrir_diagramacao", "Diagramar álbum", "F5"),
    ("rediagramar_lamina", "Outro layout na lâmina", "R"),
    ("rediagramar_todas", "Aplicar margens em todas", "Ctrl+R"),
    ("distribuir_nao_usadas", "Distribuir fotos livres", "Ctrl+L"),
    ("lamina_anterior", "Lâmina anterior", "Prior"),
    ("proxima_lamina", "Próxima lâmina", "Next"),
    ("adicionar_lamina", "Nova lâmina", "Insert"),
    ("duplicar_lamina", "Duplicar lâmina", "Ctrl+J"),
    ("remover_lamina", "Apagar lâmina", "Shift+Delete"),
    ("alternar_modo_layout", "Modo mover/transformar", "L"),
    ("editar_foto_selecionada", "Editor de cor", "Return"),
    ("girar_foto", "Girar foto 90°", "G"),
    ("preencher_pagina", "Preencher página 100%", "F"),
    ("proporcao_original", "Proporção original", "O"),
    ("igualar_tamanhos", "Igualar tamanhos", "I"),
    ("adicionar_espaco", "Novo espaço", "N"),
    ("duplicar_espaco", "Duplicar espaço", "Ctrl+D"),
    ("ordem_frente", "Trazer para frente", "U"),
    ("ordem_tras", "Mandar para trás", "J"),
    ("apagar_foto_espaco", "Tirar foto / espaço", "Delete"),
    ("alternar_guias", "Mostrar/ocultar guias", "H"),
    ("abrir_aba_degrade", "Aba Degradê", "M"),
    ("abrir_tratamento", "Photoshop / Lightroom", "T"),
    ("abrir_capa", "Programa de capas", "K"),
)
VERSAO_ARRANJO = 4
ARRANJO_PADRAO = {
    "versao": VERSAO_ARRANJO,
    "geometria": "1280x760+30+20",
    "maximizada": True,
    "mostrar_timeline": True,
    "mostrar_fotos": True,
    "mostrar_lateral": True,
    "timeline": "baixo",
    "lateral": "direita",
    "largura_timeline": 360,
    "altura_timeline": 114,
    "altura_fotos": 118,
    "largura_lateral": 310,
}
LADOS_TIMELINE = ("esquerda", "direita", "cima", "baixo")
CHAVES_TIMELINE_V1 = ("timeline", "altura_timeline", "largura_timeline", "altura_fotos", "largura_lateral")
MINIMOS = {"timeline": 76, "timeline_vertical": 110, "fotos": 84, "lateral": 265, "centro": 280, "meio": 220}
ALCA = 4
RAIO_ALCA = 7
IMA_PX = 5
IMA_SANGRIA_PX = 2.0
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
    """A geometria guardada, se ainda couber na área útil real da tela (ex: 1440x900, 1366x768)."""
    achado = re.match(r"^(\d+)x(\d+)\+?(-?\d+)\+?(-?\d+)$", str(texto or ""))
    if not achado:
        return None
    w, h, x, y = (int(v) for v in achado.groups())
    wx, wy, largura, altura = tema.area_util_tela(root)
    w = max(900, min(w, largura - 16))
    h = max(540, min(h, altura - 36))
    if not (wx - 20 <= x <= wx + largura - 200 and wy <= y <= wy + altura - 100):
        x, y = wx + 20, wy + 16
    return "%dx%d+%d+%d" % (w, h, x, y)


class Pintor:
    """
    Motor de renderização em segundo plano com duas filas independentes:
    - Fila prioritária dedicada para a lâmina principal ("principal" e "frente"), garantindo resposta imediata nas opções.
    - Pool de threads separado para miniaturas da linha do tempo ("tl:*"), sem nunca bloquear o canvas central.
    """

    def __init__(self):
        self.pedidos_principal = OrderedDict()
        self.pedidos_tl = OrderedDict()
        self.cond_principal = threading.Condition()
        self.cond_tl = threading.Condition()
        threading.Thread(target=self._rodar_principal, daemon=True).start()
        for _ in range(2):
            threading.Thread(target=self._rodar_tl, daemon=True).start()

    def pedir(self, chave, funcao):
        eh_principal = chave in ("principal", "frente")
        if eh_principal:
            with self.cond_principal:
                self.pedidos_principal.pop(chave, None)
                self.pedidos_principal[chave] = funcao
                if chave == "principal":
                    self.pedidos_principal.move_to_end(chave, last=False)
                self.cond_principal.notify()
        else:
            with self.cond_tl:
                self.pedidos_tl.pop(chave, None)
                self.pedidos_tl[chave] = funcao
                while len(self.pedidos_tl) > 36:
                    self.pedidos_tl.popitem(last=False)
                self.cond_tl.notify()

    def _rodar_principal(self):
        while True:
            with self.cond_principal:
                while not self.pedidos_principal:
                    self.cond_principal.wait()
                _chave, funcao = self.pedidos_principal.popitem(last=False)
            try:
                funcao()
            except Exception:
                traceback.print_exc()

    def _rodar_tl(self):
        while True:
            with self.cond_tl:
                while not self.pedidos_tl:
                    self.cond_tl.wait()
                _chave, funcao = self.pedidos_tl.popitem(last=True)
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
        root.minsize(900, 540)
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
        self.laminas_sel = {0}
        self.ultima_lamina_clicada = 0
        self.espaco_sel = None
        self.espacos_sel = set()
        self._ancora_espaco = None
        self._seq_aba_album = 1
        self.abas_albuns = [
            {
                "id": "aba_1",
                "projeto": self.projeto,
                "produto": self.produto,
                "caminho_projeto": None,
                "modificado": False,
                "info": self.info,
                "historico": self.historico,
                "atual": 0,
                "laminas_sel": {0},
            }
        ]
        self.aba_album_ativa = 0
        self.fotos_sel = set()
        self.ultima_foto_clicada = None
        self.filtro = tk.StringVar(value="todas")
        self.pasta_filtro = tk.StringVar(value="todas")
        self.pastas_recolhidas = set()
        self._itens_painel_fotos = []
        self._xs_painel_fotos = []
        self._miniaturas_pendentes = set()
        self.igualar_no_grupo = tk.BooleanVar(value=False)
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
        self._tl_pil = OrderedDict()
        self._tl_tk = OrderedDict()
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
        self._pastas_temp_drive = []
        atexit.register(self._limpar_pastas_temp_drive)
        threading.Thread(
            target=lambda: limpar_temporarios_disco(pastas_protegidas=self._pastas_temp_drive, limpar_autosave=False),
            daemon=True,
        ).start()
        self.atalhos_mapa = self._carregar_atalhos()
        self._gravando_atalho_acao = None
        self._botoes_atalhos_ui = {}
        self._teclas_vinculadas = []

        self._montar()
        self._atalhos()
        self._registrar_drop()
        self.root.protocol("WM_DELETE_WINDOW", self.sair)
        self.root.after(30, self._ler_fila)
        self._carregar_ui_do_projeto()
        self.agendar("limpeza_temp_drive", 30000, self._executar_limpeza_programada_temp_drive)
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
        self.auto_salvar_var = tk.IntVar(value=5)
        self.proteger_rostos_var = tk.BooleanVar(value=True)
        self.separar_pastas_var = tk.BooleanVar(value=True)

        topo = ttk.Frame(self.root, style="Barra.TFrame", padding=(8, 4))
        topo.pack(side="top", fill="x")
        self.barra_topo = topo
        self._montar_topo(topo)

        tk.Frame(self.root, bg=tema.BORDA, height=1).pack(side="top", fill="x")

        barra_abas_albuns = tk.Frame(self.root, bg=tema.PAINEL_2, padx=8, pady=3)
        barra_abas_albuns.pack(side="top", fill="x")
        self.barra_abas_albuns = barra_abas_albuns
        self._atualizar_barra_abas_albuns()

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
        ttk.Label(topo, text="Smart Album", style="Logo.TLabel").pack(side="left", padx=(0, 8))

        arquivo = ttk.Menubutton(topo, text="Arquivo", style="Compacto.TMenubutton")
        menu = tk.Menu(arquivo, tearoff=0)
        menu.add_command(label="Nova aba de álbum", accelerator="Ctrl+T", command=self.nova_aba_album)
        menu.add_command(label="Novo álbum (nesta aba)", accelerator="Ctrl+N", command=self.novo_projeto)
        menu.add_command(label="Abrir...", accelerator="Ctrl+O", command=self.abrir_projeto)
        menu.add_command(label="Abrir em nova aba...", command=lambda: self.abrir_projeto(em_nova_aba=True))
        self.menu_recentes = tk.Menu(menu, tearoff=0, postcommand=self._montar_recentes)
        menu.add_cascade(label="Recentes", menu=self.menu_recentes)
        menu.add_separator()
        menu.add_command(label="Salvar", accelerator="Ctrl+S", command=self.salvar_projeto)
        menu.add_command(label="Salvar como...", accelerator="Ctrl+Shift+S", command=lambda: self.salvar_projeto(como=True))
        menu.add_separator()
        menu.add_command(label="Colar fotos do Explorer", accelerator="Ctrl+V", command=self.colar)
        menu.add_command(label="Relocalizar fotos movidas...", command=self.consertar_caminhos)
        menu.add_command(label="Escolher programa de capas...", command=self.escolher_programa_capa)
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

        tk.Frame(topo, bg=tema.BORDA, width=1, height=18).pack(side="left", padx=4, pady=2)
        self._botao(topo, "📁 Pasta", self.escolher_pasta,
                    "Carregar pasta de fotos do computador", "Compacto.TButton").pack(side="left", padx=1)
        self._botao(topo, "☁ Drive", self.importar_link_drive,
                    "Colar link do Google Drive e baixar fotos em pasta temporária (apagada ao fechar)",
                    "Compacto.TButton").pack(side="left", padx=1)
        self._botao(topo, "🖼 Fotos", self.escolher_fotos,
                    "Carregar fotos avulsas do computador", "Compacto.TButton").pack(side="left", padx=1)

        tk.Frame(topo, bg=tema.BORDA, width=1, height=18).pack(side="left", padx=4, pady=2)
        self._botao(topo, "💾 Salvar", self.salvar_projeto_como,
                    "Salvar o arquivo JSON do álbum para continuar o projeto mais tarde",
                    "Compacto.TButton").pack(side="left", padx=1)
        self._botao(topo, "📂 Abrir", self.abrir_projeto,
                    "Abrir um arquivo JSON de álbum salvo anteriormente",
                    "Compacto.TButton").pack(side="left", padx=1)
        self._botao(topo, "🔗 Relinkar", self.consertar_caminhos,
                    "Relinkar a pasta de fotos caso o projeto ou as fotos tenham mudado de lugar",
                    "Compacto.TButton").pack(side="left", padx=1)

        ttk.Frame(topo, width=4).pack(side="left")
        self.rotulo_nome = ttk.Label(topo, text="", style="Titulo.TLabel")
        self.rotulo_nome.pack(side="left", padx=(0, 4))

        self.botao_exportar = self._botao(topo, "⤓ Exportar", self.abrir_exportar,
                                          "Exportar álbum", "Compacto.TButton")
        self.botao_exportar.pack(side="right", padx=(2, 0))
        self.botao_tratar = self._botao(topo, "✦ Ps/Lr", self.abrir_tratamento,
                                        "Photoshop / Lightroom", "Compacto.TButton")
        self.botao_tratar.pack(side="right", padx=1)
        self.botao_capa = self._botao(topo, "◧ Capa", self.abrir_programa_capa,
                                      "Abrir programa de capas", "Compacto.TButton")
        self.botao_capa.pack(side="right", padx=1)

        ttk.Frame(topo, width=4).pack(side="right")
        self._botao(topo, "? Guia", self.mostrar_guia_rapido,
                    "Guia rápido passo a passo e dicas de uso para novos usuários", "Compacto.TButton").pack(side="right", padx=1)
        self._botao(topo, "↷", self.refazer, "Refazer", "Icone.TButton", width=3).pack(side="right", padx=1)
        self._botao(topo, "↶", self.desfazer, "Desfazer", "Icone.TButton", width=3).pack(side="right", padx=1)
        ttk.Frame(topo, width=4).pack(side="right")
        self._botao(topo, "✦ Diagramar", self.abrir_diagramacao,
                    "Diagramação automática", "PrimarioCompacto.TButton").pack(side="right", padx=1)
        self._botao(topo, "🗂 Ordem/Grupos", self.abrir_ordem_grupos,
                    "Definir qual foto ou grupo numerado vem primeiro sem alterar os arquivos no PC (nunca mistura grupos diferentes na mesma lâmina)",
                    "Compacto.TButton").pack(side="right", padx=1)

    def _montar_barra_opcoes(self, barra):
        """Barra de opções enxuta: apenas Produto, Margem, Espaçamento, Fotos/Lâmina, Aplicar em Todas e Guias."""
        rot_alb = ttk.Label(barra, text="Álbum:", style="Fraco.TLabel")
        rot_alb.pack(side="left", padx=(2, 4))
        self.produto_var = tk.StringVar(value=self.produto["nome"] if self.produto else "")
        self.combo_produto = ttk.Combobox(barra, textvariable=self.produto_var, state="readonly", width=8,
                                          values=[p["nome"] for p in self.produtos])
        self.combo_produto.pack(side="left", padx=(0, 4))
        self.combo_produto.bind("<<ComboboxSelected>>", self._trocou_produto)
        self.rotulo_produto = ttk.Label(barra, text="", style="Fraco.TLabel")
        self.rotulo_produto.pack(side="left", padx=(0, 6))
        self.dica_produto = tema.Dica(self.rotulo_produto, "")

        tk.Frame(barra, bg=tema.BORDA, width=1, height=18).pack(side="left", padx=6, pady=2)

        for rot_txt, dica_txt, var, de, ate, passo, sufixo in (
            ("M:", "Margem externa (mm)", self.margem_var, 0, 60, 1, "mm"),
            ("E:", "Espaçamento (mm)", self.espacamento_var, 0, 40, 0.5, "mm"),
            ("F/L:", "Fotos por lâmina", self.fotos_lamina_var, 1, 12, 1, ""),
            ("Auto-salvar:", "Intervalo de auto-salvamento do arquivo .album.json local em minutos (0 = desativado)", self.auto_salvar_var, 0, 60, 1, "min"),
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

        self._botao(barra, "⊞", self.rediagramar_todas,
                    "Aplicar margem e espaçamento em todas as lâminas",
                    "Icone.TButton", width=3).pack(side="left", padx=1)
        bt_rostos = ttk.Checkbutton(
            barra,
            text="👤 Rostos",
            variable=self.proteger_rostos_var,
            command=self._alternou_protecao_rostos,
            style="Alternar.Toolbutton",
        )
        bt_rostos.pack(side="left", padx=2)
        tema.dica(
            bt_rostos,
            "Identificar quantos rostos existem em cada foto e ajustar o quadro/enquadramento para não cortar nenhuma pessoa",
        )
        bt_pastas = ttk.Checkbutton(
            barra,
            text="📁 Separar Pastas",
            variable=self.separar_pastas_var,
            command=self._alternou_separar_pastas,
            style="Alternar.Toolbutton",
        )
        bt_pastas.pack(side="left", padx=2)
        tema.dica(
            bt_pastas,
            "Separar por pastas carregadas: fotos de pastas diferentes nunca se misturam na mesma lâmina",
        )
        guias = ttk.Checkbutton(barra, text="⌗", variable=self.guias, command=self.desenhar_lamina,
                                style="Alternar.Toolbutton")
        guias.pack(side="left", padx=1)
        tema.dica(guias, "Mostrar/ocultar guias ciano (Safe Zone e Sangria)")

        self.rotulo_lamina = ttk.Label(barra, text="", style="Fraco.TLabel", anchor="e")
        self.rotulo_lamina.pack(side="right", padx=(8, 4))

    def _montar_ferramentas_esquerda(self, barra):
        """Barra vertical esquerda com apenas 1 botão por ferramenta de edição de foto/espaço."""
        bt_mover = ttk.Checkbutton(barra, text="⬚", variable=self.editar_layout,
                                   style="Alternar.Toolbutton", command=self._mudou_modo_layout, width=3)
        bt_mover.pack(pady=1)
        tema.dica(bt_mover, "Mover / redimensionar espaços")

        self._botao(barra, "✂", self.editar_foto_selecionada,
                    "Editor de cor e luz", "Icone.TButton", width=3).pack(pady=1)
        self._botao(barra, "↻", self.girar_foto_selecionada,
                    "Girar foto 90°", "Icone.TButton", width=3).pack(pady=1)
        self._botao(barra, "＝", lambda: self.alinhar_espaco("igualar"),
                    "Igualar tamanho das fotos selecionadas", "Icone.TButton", width=3).pack(pady=1)
        self._botao(barra, "⊡", lambda: self.alinhar_espaco("proporcao_foto"),
                    "Proporção original da foto (0% corte)", "Icone.TButton", width=3).pack(pady=1)
        self._botao(barra, "⇕", lambda: self.alinhar_espaco("pagina_util"),
                    "Preencher página 100% até a sangria", "Icone.TButton", width=3).pack(pady=1)
        self._botao(barra, "＋", self.adicionar_espaco,
                    "Novo espaço na página", "Icone.TButton", width=3).pack(pady=1)
        self._botao(barra, "❐", self.duplicar_espaco,
                    "Duplicar espaço selecionado", "Icone.TButton", width=3).pack(pady=1)
        self._botao(barra, "↑", lambda: self.ordem_espaco(True),
                    "Trazer para a frente", "Icone.TButton", width=3).pack(pady=1)
        self._botao(barra, "↓", lambda: self.ordem_espaco(False),
                    "Mandar para trás", "Icone.TButton", width=3).pack(pady=1)
        self._botao(barra, "🗑", self._tecla_apagar,
                    "Tirar foto / apagar espaço selecionado", "Icone.TButton", width=3).pack(pady=1)

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
        self.combo_pasta_filtro = ttk.Combobox(cabeca, state="readonly", width=15,
                                               values=["Todas as pastas"])
        self.combo_pasta = self.combo_pasta_filtro
        self.combo_pasta_filtro.set("Todas as pastas")
        self.combo_pasta_filtro.pack(side="left", padx=(0, 4))
        self.combo_pasta_filtro.bind("<<ComboboxSelected>>", self._mudou_filtro_pasta)
        tema.dica(self.combo_pasta_filtro, "Filtrar visualização por pasta carregada (as pastas ficam separadas sem se misturar)")

        self.ordem_var = tk.StringVar(value="grupos")
        combo = ttk.Combobox(cabeca, textvariable=self.ordem_var, state="readonly", width=7,
                             values=["grupos", "nome", "data"])
        combo.pack(side="left", padx=(4, 0))
        combo.bind("<<ComboboxSelected>>", lambda e: self.ordenar_fotos())
        tema.dica(combo, "Ordenar fotos por grupos numerados/ordem manual, nome ou data (sem renomear arquivos)")
        self._botao(cabeca, "🗂 Ordem / Grupos", self.abrir_ordem_grupos,
                    "Organizar fotos em grupos numerados (Grupo 1, Grupo 2...) e escolher qual foto vem primeiro sem alterar arquivos",
                    "Compacto.TButton").pack(side="left", padx=(6, 0))
        self._botao(cabeca, "📁 Separar Lâminas", self.separar_laminas_por_pasta,
                    "Separar pastas carregadas em lâminas exclusivas (nunca mistura fotos de pastas diferentes na mesma lâmina)",
                    "Compacto.TButton").pack(side="left", padx=(4, 0))
        self._botao(cabeca, "🗑", self.remover_fotos_selecionadas,
                    "Remover fotos selecionadas do projeto", "Icone.TButton", width=3).pack(side="right", padx=(4, 1))
        self._botao(cabeca, "⇉", self.distribuir_nao_usadas,
                    "Distribuir fotos livres em novas lâminas", "Icone.TButton", width=3).pack(side="right", padx=3)
        barra = ttk.Scrollbar(caixa, orient="horizontal", command=self._rolar_fotos)
        barra.pack(side="bottom", fill="x")
        self.canvas_fotos = tk.Canvas(caixa, height=82, bg=tema.PAINEL, highlightthickness=0)
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
                (self.botoes_tl_edicao, "☑", self.alternar_selecao_todas_laminas, "Selecionar todas as lâminas / limpar seleção em grupo", (4, 1)),
                (self.botoes_tl_edicao, "+", self.adicionar_lamina, "Nova lâmina depois desta", 1),
                (self.botoes_tl_edicao, "⧉", self.duplicar_lamina, "Duplicar a(s) lâmina(s) selecionada(s)", 1),
                (self.botoes_tl_edicao, "−", self.remover_lamina, "Apagar todas as lâminas selecionadas de uma vez (Del ou Shift+Del)", 1)):
            botao = self._botao(pai, texto, comando, dica, "Mini.TButton", width=2)
            botao.pack(side="left", padx=espaco)
            self._botoes_nav.append(botao)
        self.barra_tl_h = ttk.Scrollbar(caixa, orient="horizontal")
        self.barra_tl_v = ttk.Scrollbar(caixa, orient="vertical")
        self.canvas_timeline = tk.Canvas(caixa, height=90, width=180, bg=tema.PAINEL, highlightthickness=0)
        self.barra_tl_h.configure(command=self.canvas_timeline.xview)
        self.barra_tl_v.configure(command=self.canvas_timeline.yview)
        self.canvas_timeline.configure(
            xscrollcommand=lambda a, b: (self.barra_tl_h.set(a, b), self.agendar("timeline", 14, self.desenhar_timeline)),
            yscrollcommand=lambda a, b: (self.barra_tl_v.set(a, b), self.agendar("timeline", 14, self.desenhar_timeline)),
            xscrollincrement=20,
            yscrollincrement=20,
        )
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
        _wx, _wy, sw, sh = tema.area_util_tela(self.root)
        # Limita proporcionalmente a altura/largura dos painéis periféricos em telas compactas (ex: 1440x900, 1366x768)
        larg_lat = min(int(a["largura_lateral"]), max(MINIMOS["lateral"], int(sw * 0.25)))
        larg_tl = min(int(a.get("largura_timeline", 360)), max(220, int(sw * 0.30)))
        alt_tl = min(int(a["altura_timeline"]), max(MINIMOS["timeline"], int(sh * 0.16)))
        alt_fotos = min(int(a["altura_fotos"]), max(MINIMOS["fotos"], int(sh * 0.17)))

        lateral = dict(width=larg_lat, minsize=MINIMOS["lateral"], stretch="never")
        tl_lado = dict(width=larg_tl, minsize=MINIMOS["timeline_vertical"], stretch="never")
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
        tl_faixa = dict(height=alt_tl, minsize=MINIMOS["timeline"], stretch="never")
        if mostrar_tl and a["timeline"] == "cima":
            v.add(self.quadro_timeline, **tl_faixa)
        v.add(h, minsize=MINIMOS["meio"], stretch="always")
        if mostrar_tl and a["timeline"] == "baixo" and a["mostrar_fotos"]:
            b.add(self.quadro_timeline, width=max(220, larg_tl), minsize=200, stretch="never")
            b.add(self.quadro_fotos, minsize=240, stretch="always")
            v.add(b, height=alt_fotos, minsize=MINIMOS["fotos"], stretch="never")
        else:
            if a["mostrar_fotos"]:
                v.add(self.quadro_fotos, height=alt_fotos, minsize=MINIMOS["fotos"], stretch="never")
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
        abas.pack(fill="both", expand=True, padx=4, pady=4)
        self.abas = abas
        self.quadros_abas = {}
        abas_rolaveis = {"Fundo", "Degradê", "Moldura"}
        for titulo, montar in (("Layouts", self._aba_layouts), ("Fundo", self._aba_fundo),
                               ("Degradê", self._aba_degrade), ("Moldura", self._aba_moldura),
                               ("⌨ Atalhos", self._aba_atalhos)):
            if titulo in abas_rolaveis:
                quadro = ttk.Frame(abas, padding=0)
                abas.add(quadro, text=titulo)
                self.quadros_abas[titulo] = quadro
                interno, _cont, _cv = tema.criar_corpo_rolavel(quadro, padding=6)
                montar(interno)
            else:
                quadro = ttk.Frame(abas, padding=6)
                abas.add(quadro, text=titulo)
                self.quadros_abas[titulo] = quadro
                montar(quadro)
        abas.bind("<<NotebookTabChanged>>", self._aba_mudou)

    def abrir_aba_degrade(self):
        if "Degradê" in self.quadros_abas:
            self.abas.select(self.quadros_abas["Degradê"])
            self._atualizar_degrade_ui()

    def abrir_aba_atalhos(self):
        if "⌨ Atalhos" in self.quadros_abas:
            self.abas.select(self.quadros_abas["⌨ Atalhos"])

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
        for valor, texto, dica in (("sugestoes", "Sugestões", "Layouts para a lâmina inteira"),
                                   ("modelos", "Páginas", "Diagramação por página (Esquerda ou Direita); se uma foto passar do meio, diagrama o restante da página sem mexer nela"),
                                   ("editar", "Editar", "Medidas e enquadramento da foto")):
            botao = ttk.Radiobutton(seletor, text=texto, value=valor, variable=self.secao_layouts,
                                    style="Filtro.TRadiobutton", command=self._mostrar_secao_layouts)
            botao.pack(side="left", fill="x", expand=True, padx=1)
            tema.dica(botao, dica)
            self.secoes_layouts[valor] = ttk.Frame(quadro)
        self._secao_sugestoes(self.secoes_layouts["sugestoes"])
        self._secao_modelos(self.secoes_layouts["modelos"])
        interno_ed, _cont_ed, _cv_ed = tema.criar_corpo_rolavel(self.secoes_layouts["editar"], padding=2)
        self._montar_edicao_layout(interno_ed)
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
        elif self.secao_layouts.get() == "editar":
            self._atualizar_enquadramento_inline()

    def _secao_sugestoes(self, quadro):
        cab = ttk.Frame(quadro)
        cab.pack(side="top", fill="x")
        ttk.Label(cab, text="Sugestões", style="Fraco.TLabel").pack(side="left")
        self._botao(cab, "💾", self.salvar_modelo,
                    "Salvar layout atual como modelo", "Icone.TButton", width=3).pack(side="right", padx=1)
        self._botao(cab, "⟳", self.rediagramar_lamina,
                    "Alternar layout desta lâmina", "Icone.TButton", width=3).pack(side="right", padx=1)
        self.canvas_sugestoes = tk.Canvas(quadro, bg=tema.PAINEL, highlightthickness=0, height=130)
        self.canvas_sugestoes.pack(side="top", fill="both", expand=True, pady=(4, 2))
        self.canvas_sugestoes.bind("<ButtonPress-1>", self._sugestao_clicada)
        self.canvas_sugestoes.bind("<Configure>", lambda e: self.desenhar_sugestoes())
        self.canvas_sugestoes.bind("<MouseWheel>", lambda e: self.canvas_sugestoes.yview_scroll(-int(e.delta / 120), "units"))
        for var in (self.margem_var, self.espacamento_var, self.fotos_lamina_var, self.auto_salvar_var):
            var.trace_add("write", lambda *a: self.agendar("config", 160, self._mudou_config))

    def _secao_modelos(self, quadro):
        self.so_compativeis = tk.BooleanVar(value=True)
        self.pagina_modelo = tk.IntVar(value=0)
        self._opcoes_pagina_modelos = []

        topo_pag = ttk.LabelFrame(quadro, text="Páginas (Diagramação por Lado)", padding=6)
        topo_pag.pack(side="top", fill="x", pady=(0, 4))

        linha_lado = ttk.Frame(topo_pag)
        linha_lado.pack(fill="x", pady=(0, 4))
        for val_p, txt_p, dica_p in (
            (0, "◧ Página Esquerda", "Gera e aplica layouts na Página Esquerda (preservando fotos que passam do meio)"),
            (1, "◨ Página Direita", "Gera e aplica layouts na Página Direita (preservando fotos que passam do meio)"),
        ):
            rb_p = ttk.Radiobutton(
                linha_lado,
                text=txt_p,
                value=val_p,
                variable=self.pagina_modelo,
                style="Filtro.TRadiobutton",
                command=self.desenhar_modelos,
            )
            rb_p.pack(side="left", fill="x", expand=True, padx=1)
            tema.dica(rb_p, dica_p)

        linha_qtd = ttk.Frame(topo_pag)
        linha_qtd.pack(fill="x")
        self.rotulo_info_pagina_modelo = ttk.Label(linha_qtd, text="Página Esquerda", style="Fraco.TLabel")
        self.rotulo_info_pagina_modelo.pack(side="left")
        self._botao(
            linha_qtd,
            "−1 Espaço",
            lambda: self._mudar_qtd_fotos_pagina(-1),
            "Remover 1 espaço desta página (sem mexer na outra página nem na foto que passou do meio)",
            "Icone.TButton",
            width=9,
        ).pack(side="right", padx=(2, 0))
        self._botao(
            linha_qtd,
            "+1 Espaço",
            lambda: self._mudar_qtd_fotos_pagina(1),
            "Adicionar 1 espaço no restante desta página (sem mexer na outra página nem na foto que passou do meio)",
            "Icone.TButton",
            width=9,
        ).pack(side="right", padx=1)

        ttk.Checkbutton(
            topo_pag,
            text="Se uma foto passar do meio, diagramar com o restante da página sem mexer nela",
            variable=self.so_compativeis,
            command=self.desenhar_modelos,
        ).pack(side="top", anchor="w", pady=(4, 0))

        linha = ttk.Frame(quadro)
        linha.pack(side="bottom", fill="x", pady=(2, 0))
        self._botao(linha, "✓ Aplicar na Página", self.aplicar_modelo_selecionado,
                    "Aplicar o layout selecionado apenas na página escolhida (Esquerda ou Direita)",
                    "PrimarioCompacto.TButton").pack(side="left", fill="x", expand=True, padx=(0, 2))
        self._botao(linha, "💾", self.salvar_modelo,
                    "Salvar o layout atual desta lâmina como modelo", "Icone.TButton", width=3).pack(side="left", padx=1)
        self._botao(linha, "🖼+", self.importar_png,
                    "Importar molde PNG com janelas transparentes", "Icone.TButton", width=4).pack(side="left", padx=1)
        self._botao(linha, "📁+", self.importar_pasta_png,
                    "Importar todos os moldes PNG de uma pasta", "Icone.TButton", width=4).pack(side="left", padx=1)
        self._botao(linha, "🗑", self.apagar_modelo,
                    "Apagar o modelo salvo selecionado", "Icone.TButton", width=3).pack(side="left", padx=(1, 0))
        self.canvas_modelos = tk.Canvas(quadro, bg=tema.PAINEL, highlightthickness=0, height=140)
        self.canvas_modelos.pack(side="top", fill="both", expand=True, pady=4)
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
        tema.dica(chk_igual, "Opcional: quando ligado, iguala os tamanhos ao redimensionar várias fotos.\n"
                             "Desligado (padrão): cada foto redimensiona na sua própria proporção individual.")
        self._botao(barra_ic, "✂", self.editar_foto_selecionada,
                    "Abrir editor ampliado da foto (Enter)",
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
        for texto, acao in (("⇕ Preencher página inteira (100% até a sangria)", "pagina_util"),
                            ("⬚ Encaixar dentro da Safe Zone", "pagina_safe"),
                            ("▭ Preencher lâmina inteira (100% até a sangria)", "lamina"),
                            ("＝ Igualar tamanho das fotos selecionadas", "igualar"),
                            ("⊡ Usar proporção padrão da foto (0% corte)", "proporcao_foto"),
                            ("↔ Centralizar na horizontal (na página)", "centro_h"),
                            ("↕ Centralizar na vertical", "centro_v")):
            menu.add_command(label=texto, command=lambda a=acao: self.alinhar_espaco(a))
        posicao["menu"] = menu
        posicao.pack(side="right", padx=(2, 0))
        ttk.Label(caixa, text="Dica: Com 1 ou várias fotos, puxe a alça mantendo a proporção individual (Shift = Livre).",
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

        # Painel de Enquadramento Direto da Foto Selecionada (Sem precisar de Duplo Clique)
        enq = ttk.LabelFrame(quadro, text="Enquadramento da Foto (Sem Duplo Clique)", padding=6)
        enq.pack(side="top", fill="both", expand=True, pady=(2, 0))
        barra_enq = ttk.Frame(enq)
        barra_enq.pack(fill="x", pady=(0, 4))
        self.rotulo_enq_status = ttk.Label(barra_enq, text="Clique em uma foto na lâmina", style="Fraco.TLabel")
        self.rotulo_enq_status.pack(side="left")
        self._botao(barra_enq, "⊡ 1.0x", self._resetar_enquadramento_inline,
                    "Centralizar foto e restaurar zoom 1.0x", "Icone.TButton", width=6).pack(side="right", padx=(2, 0))
        self._botao(barra_enq, "↻", lambda: self.girar_foto_selecionada(90),
                    "Girar foto 90° no sentido horário", "Icone.TButton", width=3).pack(side="right", padx=1)
        self._botao(barra_enq, "↺", lambda: self.girar_foto_selecionada(-90),
                    "Girar foto 90° no sentido anti-horário", "Icone.TButton", width=3).pack(side="right", padx=1)

        self.canvas_enquadramento = tk.Canvas(
            enq, bg="#18181b", highlightthickness=1, highlightbackground=tema.BORDA, height=104, cursor="fleur"
        )
        self.canvas_enquadramento.pack(fill="both", expand=True, pady=(0, 4))
        self.canvas_enquadramento.bind("<ButtonPress-1>", self._enquadramento_inicio)
        self.canvas_enquadramento.bind("<B1-Motion>", self._enquadramento_arrastar)
        self.canvas_enquadramento.bind("<ButtonRelease-1>", self._enquadramento_soltar)
        self.canvas_enquadramento.bind("<MouseWheel>", self._enquadramento_roda)
        self.canvas_enquadramento.bind("<Double-Button-1>", lambda e: self._resetar_enquadramento_inline())
        self.canvas_enquadramento.bind("<Configure>", lambda e: self._atualizar_enquadramento_inline())

        self._carregando_enq = False
        self.enq_zoom_var = tk.DoubleVar(value=1.0)
        self.enq_cx_var = tk.DoubleVar(value=50.0)
        self.enq_cy_var = tk.DoubleVar(value=50.0)
        self.rotulos_enq = {}
        self.escalas_enq = {}

        for chave, titulo, de, ate in (
            ("zoom", "Zoom", 1.0, 3.0),
            ("cx", "Posição H", 0.0, 100.0),
            ("cy", "Posição V", 0.0, 100.0),
        ):
            var_e = getattr(self, "enq_%s_var" % chave)
            linha_e = ttk.Frame(enq)
            linha_e.pack(fill="x", pady=1)
            ttk.Label(linha_e, text=titulo, width=9).pack(side="left")
            rot_val = ttk.Label(linha_e, text="", style="Fraco.TLabel", width=5, anchor="e")
            rot_val.pack(side="right")
            self.rotulos_enq[chave] = rot_val
            esc = ttk.Scale(
                linha_e,
                from_=de,
                to=ate,
                variable=var_e,
                command=lambda _v, ch=chave: self._mudou_enquadramento_slider(ch, final=False),
            )
            esc.pack(side="right", fill="x", expand=True, padx=4)
            esc.bind("<ButtonPress-1>", lambda e: self.marcar_historico())
            esc.bind("<ButtonRelease-1>", lambda e, ch=chave: self._mudou_enquadramento_slider(ch, final=True))
            self.escalas_enq[chave] = esc

    def _aba_fundo(self, quadro):
        topo = ttk.Frame(quadro)
        topo.pack(side="top", fill="x")
        self.rotulo_fundo = ttk.Label(topo, text="", style="Titulo.TLabel")
        self.rotulo_fundo.pack(side="left")
        self._botao(topo, "🎯", self.capturar_cor_fundo_tela, "Capturar cor de fundo de qualquer pixel da tela (conta-gotas)", "Compacto.TButton").pack(side="right", padx=(2, 0))
        self._botao(topo, "Cor...", self.escolher_fundo, "Cor de fundo do alvo selecionado (Seletor Photoshop HSB/RGB/Hex)", "Compacto.TButton").pack(side="right")
        self.amostra_fundo = tk.Label(topo, width=3, bg="#ffffff", relief="solid", bd=1, cursor="hand2")
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
        self.fundo_escala = tk.DoubleVar(value=100)
        self.fundo_pb = tk.BooleanVar(value=False)
        self.fundo_espelhar_h = tk.BooleanVar(value=False)
        self.fundo_espelhar_v = tk.BooleanVar(value=False)
        self.fundo_giro = tk.IntVar(value=0)
        self._controles_fundo = []

        # Escolha rápida entre Cortar (Preencher sem distorcer) e Distorcer (Esticar), além de Ajustar/Repetir
        linha_encaixe = ttk.Frame(imagem)
        linha_encaixe.pack(fill="x", pady=(4, 2))
        rb_cortar = ttk.Radiobutton(
            linha_encaixe,
            text="✂ Cortar (Sem Distorcer)",
            value="preencher",
            variable=self.fundo_modo,
            style="Filtro.TRadiobutton",
            command=lambda: self._mudou_fundo_imagem(marcar=True),
        )
        rb_cortar.pack(side="left", fill="x", expand=True, padx=(0, 2))
        tema.dica(rb_cortar, "Preenche a área cortando o excesso sem distorcer a proporção da imagem")
        rb_distorcer = ttk.Radiobutton(
            linha_encaixe,
            text="↔ Distorcer (Esticar)",
            value="esticar",
            variable=self.fundo_modo,
            style="Filtro.TRadiobutton",
            command=lambda: self._mudou_fundo_imagem(marcar=True),
        )
        rb_distorcer.pack(side="left", fill="x", expand=True)
        tema.dica(rb_distorcer, "Estica/distorce a imagem para caber exatamente na área do fundo")
        self._controles_fundo.extend([rb_cortar, rb_distorcer])

        # Botões de Espelhar H, Espelhar V e Girar 90°
        linha_tf = ttk.Frame(imagem)
        linha_tf.pack(fill="x", pady=(2, 2))
        bt_esp_h = self._botao(
            linha_tf,
            "⇄ Espelhar H",
            lambda: self._alternar_espelho_fundo("espelhar_h"),
            "Espelhar imagem de fundo horizontalmente",
            "Compacto.TButton",
        )
        bt_esp_h.pack(side="left", fill="x", expand=True, padx=(0, 2))
        bt_esp_v = self._botao(
            linha_tf,
            "⇅ Espelhar V",
            lambda: self._alternar_espelho_fundo("espelhar_v"),
            "Espelhar imagem de fundo verticalmente",
            "Compacto.TButton",
        )
        bt_esp_v.pack(side="left", fill="x", expand=True, padx=(0, 2))
        bt_girar_fundo = self._botao(
            linha_tf,
            "↻ Girar 90°",
            self._girar_fundo_90,
            "Girar a imagem de fundo em 90 graus no sentido horário",
            "Compacto.TButton",
        )
        bt_girar_fundo.pack(side="left", fill="x", expand=True)
        self._controles_fundo.extend([bt_esp_h, bt_esp_v, bt_girar_fundo])

        linha = ttk.Frame(imagem)
        linha.pack(fill="x", pady=(2, 2))
        ttk.Label(linha, text="Modo").pack(side="left")
        combo = ttk.Combobox(linha, textvariable=self.fundo_modo, state="readonly", width=10, values=list(MODOS_FUNDO))
        combo.pack(side="left", padx=6)
        combo.bind("<<ComboboxSelected>>", lambda e: self._mudou_fundo_imagem(marcar=True))
        self._controles_fundo.append(combo)
        self.rotulo_info_tf_fundo = ttk.Label(linha, text="0°", style="Fraco.TLabel", font=tema.FONTE_MONO)
        self.rotulo_info_tf_fundo.pack(side="left", padx=(2, 4))
        caixa_pb = ttk.Checkbutton(linha, text="P&B", variable=self.fundo_pb,
                                   command=lambda: self._mudou_fundo_imagem(marcar=True))
        caixa_pb.pack(side="right")
        self._controles_fundo.append(caixa_pb)
        self._rotulos_fundo = {}
        for texto, var, de, ate in (
            ("Zoom", self.fundo_escala, 100, 300),
            ("Opacidade", self.fundo_opacidade, 0, 100),
            ("Desfoque", self.fundo_desfoque, 0, 50),
        ):
            linha = ttk.Frame(imagem)
            linha.pack(fill="x", pady=1)
            ttk.Label(linha, text=texto, width=9).pack(side="left")
            rotulo = ttk.Label(linha, text="", style="Fraco.TLabel", width=5, anchor="e")
            rotulo.pack(side="right")
            escala = ttk.Scale(linha, from_=de, to=ate, variable=var, command=lambda v: self._mudou_fundo_imagem(marcar=False))
            escala.pack(side="right", fill="x", expand=True, padx=6)
            escala.bind("<ButtonPress-1>", lambda e: self.marcar_historico())
            escala.bind("<ButtonRelease-1>", lambda e: self.fundo_ajustado(final=True))
            if texto == "Zoom":
                escala.bind("<Double-Button-1>", lambda e: (self.fundo_escala.set(100), self._mudou_fundo_imagem(marcar=True)))
            self._controles_fundo.append(escala)
            self._rotulos_fundo[texto] = rotulo
        linha = ttk.Frame(imagem)
        linha.pack(fill="x", pady=(4, 0))
        ajustar = self._botao(linha, "🎨 Ajustar cor / Tingir...", lambda: self.abrir_editor_fundo(),
                              "Brilho, contraste, saturação, matiz e tingir com conta-gotas da tela\n(também: duplo clique numa imagem da galeria)")
        ajustar.pack(side="left", fill="x", expand=True, padx=(0, 2))
        tirar = self._botao(linha, "Tirar imagem", self.tirar_imagem_fundo, "Remove a imagem do alvo selecionado (Lâmina, Pág. Esq. ou Pág. Dir.)")
        tirar.pack(side="left", fill="x", expand=True)
        self._controles_fundo += [ajustar, tirar]

        linha_aplicar = ttk.Frame(quadro)
        linha_aplicar.pack(side="top", fill="x", pady=(3, 4))
        self.botao_fundo_selecionadas = self._botao(
            linha_aplicar,
            "Aplicar nas selecionadas (1)",
            self.fundo_em_selecionadas,
            "Aplica este fundo e todas as suas configurações nas lâminas selecionadas na linha do tempo (use Ctrl+Clique ou Shift+Clique nas miniaturas das lâminas)",
            "PrimarioCompacto.TButton",
        )
        self.botao_fundo_selecionadas.pack(side="left", fill="x", expand=True, padx=(0, 2))
        self._botao(
            linha_aplicar,
            "Aplicar em todas",
            self.fundo_em_todas,
            "Aplica este fundo e todas as suas configurações em todas as lâminas do álbum",
            "Compacto.TButton",
        ).pack(side="left", fill="x", expand=True, padx=(2, 0))

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
            self.agendar("moldura_render", 16, lambda: self._pedir_render(dw, dh))
        self.agendar("moldura_timeline", 140, self.desenhar_timeline)

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
        cor = tema.escolher_cor_photoshop(self.root, cor_inicial=self.moldura_cor.get(), titulo="Cor da Moldura · Photoshop")
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
            self.agendar("degrade_render", 14 if not final else 4, lambda: self._pedir_render(dw, dh))
        if final or marcar:
            self.agendar("degrade_timeline", 140, self.desenhar_timeline)

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

    # ---------------- Aba de Atalhos Personalizáveis ----------------

    def _carregar_atalhos(self):
        padrao = {ch: tec for ch, _rot, tec in DEFINICOES_ATALHOS}
        salvos = ler_json(ARQUIVO_ATALHOS, {})
        if isinstance(salvos, dict):
            for ch, val in salvos.items():
                if ch in padrao and isinstance(val, str) and val.strip():
                    padrao[ch] = val.strip()
        return padrao

    def _salvar_atalhos(self):
        try:
            gravar_json(ARQUIVO_ATALHOS, self.atalhos_mapa)
        except OSError:
            pass

    def _aba_atalhos(self, quadro):
        topo = ttk.Frame(quadro)
        topo.pack(side="top", fill="x", pady=(0, 6))
        ttk.Label(topo, text="Atalhos (clique p/ alterar)", style="Titulo.TLabel").pack(side="left")
        self._botao(topo, "Restaurar", self._restaurar_atalhos_padrao,
                    "Voltar todos os atalhos para o padrão", "Compacto.TButton").pack(side="right")

        container = ttk.Frame(quadro)
        container.pack(side="top", fill="both", expand=True)
        canvas_at = tk.Canvas(container, bg=tema.PAINEL, highlightthickness=0)
        barra_at = ttk.Scrollbar(container, orient="vertical", command=canvas_at.yview)
        canvas_at.configure(yscrollcommand=barra_at.set)
        barra_at.pack(side="right", fill="y")
        canvas_at.pack(side="left", fill="both", expand=True)

        interno = ttk.Frame(canvas_at)
        id_win = canvas_at.create_window((0, 0), window=interno, anchor="nw")
        interno.bind("<Configure>", lambda _e: canvas_at.configure(scrollregion=canvas_at.bbox("all")))
        canvas_at.bind("<Configure>", lambda e: canvas_at.itemconfigure(id_win, width=e.width))
        canvas_at.bind("<MouseWheel>", lambda e: canvas_at.yview_scroll(-int(e.delta / 120), "units"))

        self._botoes_atalhos_ui = {}
        for ch_acao, rotulo_acao, _tec_pad in DEFINICOES_ATALHOS:
            linha = ttk.Frame(interno)
            linha.pack(fill="x", pady=2, padx=2)
            ttk.Label(linha, text=rotulo_acao).pack(side="left")
            bt = tk.Button(
                linha,
                text=self.atalhos_mapa.get(ch_acao, ""),
                width=13,
                bg=tema.PAINEL_2,
                fg=tema.TEXTO,
                activebackground=tema.DESTAQUE,
                activeforeground="#ffffff",
                relief="solid",
                bd=1,
                cursor="hand2",
                command=lambda c=ch_acao: self._iniciar_gravacao_atalho(c),
            )
            bt.pack(side="right")
            self._botoes_atalhos_ui[ch_acao] = bt

    def _iniciar_gravacao_atalho(self, ch_acao):
        self._gravando_atalho_acao = ch_acao
        for k, bt in self._botoes_atalhos_ui.items():
            if k == ch_acao:
                bt.configure(text="Aperte a tecla...", bg="#00365c", fg="#00e5ff")
            else:
                bt.configure(text=self.atalhos_mapa.get(k, ""), bg=tema.PAINEL_2, fg=tema.TEXTO)
        self.root.focus_set()

    def _restaurar_atalhos_padrao(self):
        self._gravando_atalho_acao = None
        self.atalhos_mapa = {ch: tec for ch, _rot, tec in DEFINICOES_ATALHOS}
        self._salvar_atalhos()
        for ch, bt in self._botoes_atalhos_ui.items():
            bt.configure(text=self.atalhos_mapa.get(ch, ""), bg=tema.PAINEL_2, fg=tema.TEXTO)
        self._atalhos()
        self.registrar("Atalhos restaurados para o padrão.")

    def _executar_acao_atalho(self, ch_acao):
        acoes = {
            "novo_projeto": self.novo_projeto,
            "abrir_projeto": self.abrir_projeto,
            "salvar_projeto": self.salvar_projeto,
            "escolher_pasta": self.escolher_pasta,
            "importar_link_drive": self.importar_link_drive,
            "escolher_fotos": self.escolher_fotos,
            "abrir_exportar": self.abrir_exportar,
            "desfazer": self.desfazer,
            "refazer": self.refazer,
            "abrir_diagramacao": self.abrir_diagramacao,
            "rediagramar_lamina": self.rediagramar_lamina,
            "rediagramar_todas": self.rediagramar_todas,
            "distribuir_nao_usadas": self.distribuir_nao_usadas,
            "lamina_anterior": lambda: self.passar_lamina(-1),
            "proxima_lamina": lambda: self.passar_lamina(1),
            "adicionar_lamina": self.adicionar_lamina,
            "duplicar_lamina": self.duplicar_lamina,
            "remover_lamina": self.remover_lamina,
            "alternar_modo_layout": self.alternar_modo_layout,
            "editar_foto_selecionada": self.editar_foto_selecionada,
            "girar_foto": self.girar_foto_selecionada,
            "preencher_pagina": lambda: self.alinhar_espaco("pagina_util"),
            "proporcao_original": lambda: self.alinhar_espaco("proporcao_foto"),
            "igualar_tamanhos": lambda: self.alinhar_espaco("igualar"),
            "adicionar_espaco": self.adicionar_espaco,
            "duplicar_espaco": self.duplicar_espaco,
            "ordem_frente": lambda: self.ordem_espaco(True),
            "ordem_tras": lambda: self.ordem_espaco(False),
            "apagar_foto_espaco": self._tecla_apagar,
            "alternar_guias": lambda: (self.guias.set(not self.guias.get()), self.desenhar_lamina()),
            "abrir_aba_degrade": self.abrir_aba_degrade,
            "abrir_tratamento": self.abrir_tratamento,
            "abrir_capa": self.abrir_programa_capa,
        }
        fn = acoes.get(ch_acao)
        if fn:
            fn()

    @staticmethod
    def _converter_atalho_tk(texto_atalho):
        if not texto_atalho:
            return []
        partes = [p.strip() for p in str(texto_atalho).split("+") if p.strip()]
        if not partes:
            return []
        mods = []
        tecla = partes[-1]
        for m in partes[:-1]:
            ml = m.lower()
            if ml in ("ctrl", "control"):
                mods.append("Control")
            elif ml == "shift":
                mods.append("Shift")
            elif ml == "alt":
                mods.append("Alt")
        prefixo = "-".join(mods) + "-" if mods else ""
        if len(tecla) == 1 and tecla.isalpha():
            return ["<%s%s>" % (prefixo, tecla.lower()), "<%s%s>" % (prefixo, tecla.upper())]
        return ["<%s%s>" % (prefixo, tecla)]

    def _capturar_tecla_atalho(self, evento):
        if not self._gravando_atalho_acao:
            return None
        ks = evento.keysym
        if ks in ("Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R", "Win_L", "Win_R"):
            return "break"
        ch_acao = self._gravando_atalho_acao
        self._gravando_atalho_acao = None
        if ks == "Escape":
            bt = self._botoes_atalhos_ui.get(ch_acao)
            if bt:
                bt.configure(text=self.atalhos_mapa.get(ch_acao, ""), bg=tema.PAINEL_2, fg=tema.TEXTO)
            return "break"
        estado = getattr(evento, "state", 0)
        partes = []
        if estado & CONTROL:
            partes.append("Ctrl")
        if estado & SHIFT and len(ks) > 1:
            partes.append("Shift")
        if estado & ALT:
            partes.append("Alt")
        nome_tecla = ks.upper() if len(ks) == 1 else ks
        novo_atalho = "+".join(partes + [nome_tecla])
        self.atalhos_mapa[ch_acao] = novo_atalho
        self._salvar_atalhos()
        for k, bt in self._botoes_atalhos_ui.items():
            bt.configure(text=self.atalhos_mapa.get(k, ""), bg=tema.PAINEL_2, fg=tema.TEXTO)
        self._atalhos()
        self.registrar("Atalho atualizado: %s" % novo_atalho)
        return "break"

    def _atalhos(self):
        r = self.root
        for seq in getattr(self, "_teclas_vinculadas", []):
            try:
                r.unbind(seq)
            except tk.TclError:
                pass
        self._teclas_vinculadas = []

        r.bind("<KeyPress>", self._capturar_tecla_atalho, add="+")
        r.bind("<Control-S>", lambda e: self.salvar_projeto(como=True))
        r.bind("<Control-v>", lambda e: self._se_nao_digitando(e, self.colar))
        for tecla, dx, dy in (("Left", -1, 0), ("Right", 1, 0), ("Up", 0, -1), ("Down", 0, 1)):
            r.bind("<%s>" % tecla, lambda e, dx=dx, dy=dy: self._se_nao_digitando(e, lambda: self._seta(dx, dy, SETA_MM)))
            r.bind("<Shift-%s>" % tecla, lambda e, dx=dx, dy=dy: self._se_nao_digitando(e, lambda: self._seta(dx, dy, SETA_GRANDE_MM)))
        r.bind("<Control-Left>", lambda e: self._se_nao_digitando(e, lambda: self.mover_lamina_passo(-1)))
        r.bind("<Control-Right>", lambda e: self._se_nao_digitando(e, lambda: self.mover_lamina_passo(1)))
        r.bind("<Control-Up>", lambda e: self._se_nao_digitando(e, lambda: self.mover_lamina_passo(-1)))
        r.bind("<Control-Down>", lambda e: self._se_nao_digitando(e, lambda: self.mover_lamina_passo(1)))
        r.bind("<Escape>", lambda e: self._se_nao_digitando(e, lambda: self.alternar_modo_layout(False)))
        r.bind("<BackSpace>", lambda e: self._se_nao_digitando(e, self._tecla_apagar))

        for ch_acao, _rot, _pad in DEFINICOES_ATALHOS:
            txt_at = self.atalhos_mapa.get(ch_acao, "")
            for seq in self._converter_atalho_tk(txt_at):
                try:
                    r.bind(seq, lambda e, c=ch_acao: self._se_nao_digitando(e, lambda: self._executar_acao_atalho(c)))
                    self._teclas_vinculadas.append(seq)
                except tk.TclError:
                    pass

    def _se_nao_digitando(self, evento, funcao):
        if self._gravando_atalho_acao:
            return None
        if isinstance(evento.widget, (tk.Entry, ttk.Entry, ttk.Spinbox, ttk.Combobox, ttk.Scale, tk.Text)):
            return None
        funcao()
        return "break"

    def _registrar_drop(self):
        entrada.desbloquear_uipi_drop_windows(self.root)
        registrado_tkdnd = False
        if TkinterDnD is not None and DND_FILES is not None:
            alvos_drop = [
                (self.root, self._drop_geral),
                (self.canvas, self._drop_lamina),
                (self.canvas_fotos, self._drop_geral),
                (self.canvas_timeline, self._drop_timeline),
            ]
            for extra in (
                getattr(self, "quadro_lateral", None),
                getattr(self, "quadro_fotos", None),
                getattr(self, "quadro_timeline", None),
                getattr(self, "barra_topo", None),
                getattr(self, "barra_opcoes", None),
                getattr(self, "barra_ferramentas", None),
            ):
                if extra is not None:
                    alvos_drop.append((extra, self._drop_geral))
            for widget, funcao in alvos_drop:
                try:
                    widget.drop_target_register(DND_FILES)
                    widget.dnd_bind("<<Drop>>", funcao)
                    registrado_tkdnd = True
                except Exception:
                    pass
        if not registrado_tkdnd:
            if entrada.registrar_drop_nativo_windows(self.root, self._receber_drop_nativo_windows):
                self.registrar("Arrastar pastas e fotos do Windows Explorer ativado (modo nativo Win32).")
            else:
                self.registrar("Arraste pastas/fotos ou use o botão '📁 Pasta' (Ctrl+P) / Ctrl+V.")


    # ==================================================================
    # Utilidades de janela
    # ==================================================================

    def agendar(self, chave, ms, funcao):
        anterior = self._agendados.pop(chave, None)
        if anterior:
            try:
                self.root.after_cancel(anterior)
            except tk.TclError:
                pass

        def rodar():
            self._agendados.pop(chave, None)
            try:
                funcao()
            except tk.TclError:
                pass
            except Exception:
                traceback.print_exc()

        try:
            self._agendados[chave] = self.root.after(max(1, int(ms)), rodar)
        except tk.TclError:
            pass

    def cancelar_agendado(self, chave):
        anterior = self._agendados.pop(chave, None)
        if anterior:
            try:
                self.root.after_cancel(anterior)
            except Exception:
                pass

    def registrar(self, texto):
        self.registro.append(texto)
        del self.registro[:-800]
        self.rotulo_status.configure(text=texto.splitlines()[0] if texto else "")

    def mostrar_registro(self):
        janela = tk.Toplevel(self.root)
        janela.title("Registro de Atividades")
        janela.configure(bg=tema.PAINEL)
        janela.transient(self.root)
        tema.criar_cabecalho_janela(
            janela,
            "Registro de Atividades do Álbum",
            "Histórico detalhado de ações e operações da sessão atual",
            icone="📋",
        )
        rodape = tk.Frame(janela, bg=tema.CABECALHO, highlightthickness=1, highlightbackground=tema.BORDA, padx=12, pady=8)
        rodape.pack(side="bottom", fill="x")
        ttk.Button(rodape, text="Fechar", style="Compacto.TButton", command=janela.destroy).pack(side="right", padx=(0, 10))

        corpo = ttk.Frame(janela, padding=10)
        corpo.pack(fill="both", expand=True)
        barra = ttk.Scrollbar(corpo, orient="vertical")
        barra.pack(side="right", fill="y")
        texto = tk.Text(
            corpo,
            bg=tema.FUNDO,
            fg=tema.TEXTO,
            insertbackground=tema.TEXTO,
            relief="solid",
            bd=1,
            font=tema.FONTE_MONO,
            wrap="word",
            yscrollcommand=barra.set,
        )
        texto.pack(side="left", fill="both", expand=True)
        barra.configure(command=texto.yview)
        texto.insert("end", "\n".join(self.registro))
        texto.see("end")
        janela.bind("<Escape>", lambda _e: janela.destroy())
        tema.configurar_janela_secundaria(
            janela,
            pai=self,
            chave="janela_registro",
            largura=760,
            altura=460,
            min_largura=460,
            min_altura=280,
        )

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
        lim_nome = 18 if self.root.winfo_screenwidth() <= 1500 else 26
        curto = nome if len(nome) <= lim_nome else nome[:lim_nome - 2] + "…"
        self.rotulo_nome.configure(text=curto + (" *" if self.modificado else ""))
        self.root.title("%s%s - Álbum" % (nome, " *" if self.modificado else ""))
        self._salvar_estado_aba_atual()
        self._atualizar_barra_abas_albuns()

    # ==================================================================
    # Abas de Álbuns (trabalhar em vários álbuns durante exportação)
    # ==================================================================

    def _salvar_estado_aba_atual(self):
        if not hasattr(self, "abas_albuns") or not self.abas_albuns:
            return
        idx = max(0, min(len(self.abas_albuns) - 1, int(getattr(self, "aba_album_ativa", 0))))
        aba = self.abas_albuns[idx]
        aba["projeto"] = self.projeto
        aba["produto"] = self.produto
        aba["caminho_projeto"] = self.caminho_projeto
        aba["modificado"] = self.modificado
        aba["info"] = self.info
        aba["historico"] = self.historico
        aba["atual"] = self.atual
        aba["laminas_sel"] = set(self.laminas_sel or {0})

    def _carregar_estado_aba(self, idx):
        if not hasattr(self, "abas_albuns") or not (0 <= idx < len(self.abas_albuns)):
            return
        self.aba_album_ativa = idx
        aba = self.abas_albuns[idx]
        self.projeto = aba["projeto"]
        self.produto = aba["produto"] or (self.produtos[0] if self.produtos else None)
        self.caminho_projeto = aba.get("caminho_projeto")
        self.modificado = bool(aba.get("modificado", False))
        self.info = aba.get("info") if isinstance(aba.get("info"), dict) else {}
        self.historico = aba.get("historico") or proj.Historico()
        total_l = len(self.projeto.get("laminas", []))
        self.atual = max(0, min(max(0, total_l - 1), int(aba.get("atual") or 0)))
        self.laminas_sel = set(aba.get("laminas_sel") or {self.atual})
        self.ultima_lamina_clicada = self.atual
        self.espaco_sel = None
        self.espacos_sel.clear()
        self.fotos_sel.clear()
        self._tl_pil.clear()
        self._tl_tk.clear()
        self._cache_laminas.clear()
        self._tk_lamina = None
        self._tk_lamina_zoom = None
        self._imagem_lamina = None
        if self.produto:
            self.produto_var.set(self.produto["nome"])
        self._carregar_ui_do_projeto()
        self.atualizar_tudo()

    def alternar_aba_album(self, idx):
        if not hasattr(self, "abas_albuns") or not (0 <= idx < len(self.abas_albuns)):
            return
        if idx == self.aba_album_ativa:
            return
        self._salvar_estado_aba_atual()
        self._carregar_estado_aba(idx)
        nome_alb = self.projeto.get("nome") or "Novo álbum"
        self.registrar("Alternou para a aba '%s' (%s)." % (nome_alb, (self.produto or {}).get("nome", "")))

    def nova_aba_album(self, nome_inicial=None, projeto_inicial=None, caminho=None):
        self._salvar_estado_aba_atual()
        self._seq_aba_album = getattr(self, "_seq_aba_album", 1) + 1
        prod = self.produto or (self.produtos[0] if self.produtos else None)
        novo_proj = projeto_inicial if isinstance(projeto_inicial, dict) else proj.novo(prod["nome"] if prod else "")
        if not projeto_inicial:
            novo_proj["nome"] = nome_inicial or ("Álbum %02d" % len(self.abas_albuns) if len(self.abas_albuns) < 9 else "Álbum %d" % (len(self.abas_albuns) + 1))
            if nome_inicial is None:
                novo_proj["nome"] = "Álbum %02d" % (len(self.abas_albuns) + 1)
        nova = {
            "id": "aba_%d" % self._seq_aba_album,
            "projeto": novo_proj,
            "produto": prod,
            "caminho_projeto": caminho,
            "modificado": False,
            "info": {},
            "historico": proj.Historico(),
            "atual": 0,
            "laminas_sel": {0},
        }
        self.abas_albuns.append(nova)
        self._carregar_estado_aba(len(self.abas_albuns) - 1)
        self.registrar("Nova aba criada: '%s'. Você pode editar enquanto outras exportações rodam em background." % novo_proj.get("nome", "Álbum"))
        return len(self.abas_albuns) - 1

    def renomear_aba_album(self, idx=None):
        if idx is None:
            idx = self.aba_album_ativa
        if not (0 <= idx < len(self.abas_albuns)):
            return
        aba = self.abas_albuns[idx]
        atual_nome = aba["projeto"].get("nome") or "Novo álbum"
        novo_nome = simpledialog.askstring(
            "Nome do Álbum",
            "Nome do álbum (usado para separar a subpasta na exportação PSD/JPG):",
            initialvalue=atual_nome,
            parent=self.root,
        )
        if novo_nome and novo_nome.strip():
            aba["projeto"]["nome"] = novo_nome.strip()
            if idx == self.aba_album_ativa:
                self.projeto["nome"] = novo_nome.strip()
                self._marcar_modificado()
            else:
                aba["modificado"] = True
                self._atualizar_barra_abas_albuns()

    def fechar_aba_album(self, idx):
        if not hasattr(self, "abas_albuns") or len(self.abas_albuns) <= 1:
            self.novo_projeto()
            return
        if not (0 <= idx < len(self.abas_albuns)):
            return
        self._salvar_estado_aba_atual()
        aba = self.abas_albuns[idx]
        if aba.get("modificado"):
            nome_a = aba["projeto"].get("nome") or "Novo álbum"
            resp = messagebox.askyesnocancel("Fechar Aba", "Salvar as mudanças de '%s' antes de fechar a aba?" % nome_a, parent=self.root)
            if resp is None:
                return
            if resp:
                self.alternar_aba_album(idx)
                if not self.salvar_projeto():
                    return
        self.abas_albuns.pop(idx)
        novo_idx = max(0, min(len(self.abas_albuns) - 1, self.aba_album_ativa if idx > self.aba_album_ativa else self.aba_album_ativa - 1))
        self._carregar_estado_aba(novo_idx)

    def _atualizar_barra_abas_albuns(self):
        barra = getattr(self, "barra_abas_albuns", None)
        if barra is None or not barra.winfo_exists():
            return
        for w in barra.winfo_children():
            w.destroy()

        tk.Label(
            barra, text="ÁLBUNS ABERTOS:", bg=tema.PAINEL_2, fg=tema.TEXTO_FRACO, font=tema.FONTE_PEQUENA
        ).pack(side="left", padx=(0, 6))

        ids_exportando = set()
        job_at = getattr(self, "_job_exportacao_atual", None)
        if isinstance(job_at, dict) and job_at.get("id_aba"):
            ids_exportando.add(job_at["id_aba"])
        for j in getattr(self, "fila_jobs_exportacao", []):
            if isinstance(j, dict) and j.get("id_aba"):
                ids_exportando.add(j["id_aba"])

        for i, aba in enumerate(getattr(self, "abas_albuns", [])):
            ativa = (i == getattr(self, "aba_album_ativa", 0))
            proj_a = aba["projeto"] if not ativa else self.projeto
            prod_a = aba["produto"] if not ativa else self.produto
            mod_a = aba.get("modificado", False) if not ativa else self.modificado
            nome_a = (proj_a.get("nome") or ("Álbum %02d" % (i + 1))).strip()
            curto = nome_a if len(nome_a) <= 20 else nome_a[:18] + "…"
            tam_txt = exportar.tamanho_produto_texto(prod_a)
            qtd_l = len(proj_a.get("laminas", []))
            em_exp = aba.get("id") in ids_exportando
            selo_exp = " ⚡Exportando" if em_exp else ""
            rotulo = "%s%s · %s (%d)%s" % (curto, " *" if mod_a else "", tam_txt, qtd_l, selo_exp)

            quadro_aba = tk.Frame(
                barra,
                bg=tema.DESTAQUE_ESCURO if ativa else tema.PAINEL,
                highlightthickness=1,
                highlightbackground=tema.DESTAQUE if ativa else ("#f59e0b" if em_exp else tema.BORDA),
                 padx=4,
                pady=1,
            )
            quadro_aba.pack(side="left", padx=2)

            bt_aba = tk.Button(
                quadro_aba,
                text=rotulo,
                bg=tema.DESTAQUE_ESCURO if ativa else tema.PAINEL,
                fg="#ffffff" if ativa else ("#fbbf24" if em_exp else tema.TEXTO),
                activebackground=tema.DESTAQUE,
                activeforeground="#ffffff",
                bd=0,
                relief="flat",
                cursor="hand2",
                font=tema.FONTE_FORTE if ativa else tema.FONTE_NORMAL,
                command=lambda idx=i: self.alternar_aba_album(idx),
            )
            bt_aba.pack(side="left", padx=(2, 2))
            bt_aba.bind("<Double-Button-1>", lambda _e, idx=i: self.renomear_aba_album(idx))
            tema.dica(
                bt_aba,
                "Clique para alternar para '%s' · Duplo clique para renomear o álbum (subpasta: '%s')"
                % (nome_a, exportar.nome_pasta_album(prod_a, proj_a)),
            )

            if len(self.abas_albuns) > 1:
                bt_fechar = tk.Button(
                    quadro_aba,
                    text="×",
                    bg=tema.DESTAQUE_ESCURO if ativa else tema.PAINEL,
                    fg=tema.TEXTO_FRACO,
                    activebackground=tema.ERRO,
                    activeforeground="#ffffff",
                    bd=0,
                    relief="flat",
                    cursor="hand2",
                    padx=2,
                    command=lambda idx=i: self.fechar_aba_album(idx),
                )
                bt_fechar.pack(side="left")
                tema.dica(bt_fechar, "Fechar esta aba de álbum")

        self._botao(
            barra,
            "＋ Novo Álbum em Aba",
            self.nova_aba_album,
            "Criar uma nova aba de álbum para trabalhar em outro álbum enquanto a exportação roda em segundo plano",
            "Compacto.TButton",
        ).pack(side="left", padx=(6, 2))
        self._botao(
            barra,
            "✎ Renomear Álbum",
            lambda: self.renomear_aba_album(self.aba_album_ativa),
            "Definir o nome deste álbum/projeto (cria subpasta exclusiva na exportação PSD/JPG)",
            "Compacto.TButton",
        ).pack(side="left", padx=2)

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
        inicio_tick = time.perf_counter()
        ultimo_progresso = None
        ultimo_render = None
        try:
            while (time.perf_counter() - inicio_tick) < 0.012:
                msg = self.fila.get_nowait()
                tipo = msg[0]
                if tipo == "progresso":
                    ultimo_progresso = msg
                    continue
                if tipo == "render":
                    ultimo_render = msg
                    continue
                janela = self.janela_tarefa if self.janela_tarefa is not None and self.janela_tarefa.winfo_exists() else None
                if tipo == "aviso":
                    self.registrar(msg[1])
                    if janela:
                        janela.aviso(msg[1])
                elif tipo == "fim":
                    if ultimo_progresso is not None:
                        self.barra.configure(maximum=max(1, ultimo_progresso[2]), value=ultimo_progresso[1])
                        ultimo_progresso = None
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
                elif tipo == "miniatura_timeline":
                    self._receber_timeline(*msg[1:])
                elif tipo == "miniatura_foto":
                    self._receber_miniatura_foto(*msg[1:])
                elif tipo == "foto_trocada":
                    self._foto_trocada(msg[1], msg[2])
                elif tipo == "arquivo_mudou":
                    self._foto_trocada(msg[1], msg[1])
                elif tipo == "analisada":
                    self.info[normalizar_caminho(msg[1])] = msg[2]
                    self._depois_de_trocar_foto(msg[1])
                elif tipo == "chamar":
                    try:
                        msg[1]()
                    except Exception:
                        traceback.print_exc()
        except queue.Empty:
            pass
        except tk.TclError:
            pass
        try:
            if ultimo_progresso is not None:
                janela = self.janela_tarefa if self.janela_tarefa is not None and self.janela_tarefa.winfo_exists() else None
                self.barra.configure(maximum=max(1, ultimo_progresso[2]), value=ultimo_progresso[1])
                if janela:
                    janela.progresso(ultimo_progresso[1], ultimo_progresso[2])
            if ultimo_render is not None:
                self._receber_render(*ultimo_render[1:])
        except tk.TclError:
            pass
        try:
            self.root.after(18, self._ler_fila)
        except tk.TclError:
            pass

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
        self.laminas_sel = {0}
        self.ultima_lamina_clicada = 0
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
            self.auto_salvar_var.set(int(cfg.get("auto_salvar_minutos", 5)))
            self.proteger_rostos_var.set(bool(cfg.get("reconhecer_rostos", True) and cfg.get("nao_cortar_pessoas", True)))
            self.separar_pastas_var.set(bool(cfg.get("separar_pastas", True)))
        finally:
            self._carregando = False
        self._reagendar_auto_salvar()
        self.atualizar_fundos()

    def _reagendar_auto_salvar(self):
        self.cancelar_agendado("auto_salvar_json")
        try:
            minutos = max(0, int(self._cfg().get("auto_salvar_minutos", 5)))
        except (TypeError, ValueError):
            minutos = 5
        if minutos > 0:
            self.agendar("auto_salvar_json", minutos * 60 * 1000, self._executar_auto_salvar)

    def _eh_pasta_temporaria_drive(self, caminho):
        if not caminho:
            return False
        ch_norm = normalizar_caminho(caminho)
        if "smartalbum_drive_" in ch_norm.lower():
            return True
        for p in getattr(self, "_pastas_temp_drive", []):
            if not p:
                continue
            p_norm = normalizar_caminho(p)
            if ch_norm == p_norm or ch_norm.startswith(p_norm + os.sep):
                return True
        return False

    def _executar_auto_salvar(self):
        try:
            if self.projeto.get("fotos") or self.projeto.get("laminas"):
                caminho = self.caminho_projeto
                if not caminho:
                    pasta_fotos = self.projeto.get("pasta_fotos") or ""
                    # Nunca grava auto-save dentro de pasta temporária do Drive nem polui o disco com vários arquivos
                    if pasta_fotos and Path(pasta_fotos).is_dir() and not self._eh_pasta_temporaria_drive(pasta_fotos):
                        nome_limpo = re.sub(r'[<>:"/\\|?*]', "_", (self.projeto.get("nome") or "album").strip()) or "album"
                        caminho = str(Path(pasta_fotos) / (nome_limpo + proj.EXTENSAO))
                    else:
                        # Se o projeto é apenas temporário (ex.: pasta temporária do Drive) e não foi salvo pelo usuário, não grava lixo em disco
                        return
                caminho_salvo = proj.salvar(self.projeto, caminho)
                if not self.caminho_projeto:
                    self.caminho_projeto = str(caminho_salvo)
                self.modificado = False
                self._atualizar_titulo()
                self.registrar("Auto-salvamento (.album.json): salvo em %s" % Path(caminho_salvo).name)
        except Exception as e:
            self.registrar("Aviso no auto-salvamento: %s" % e)
        finally:
            self._reagendar_auto_salvar()

    def novo_projeto(self, silencioso=False):
        if not silencioso and not self._confirmar_descartar():
            return
        self._limpar_pastas_temp_drive(limpar_autosave=True)
        self._carregar_projeto(proj.novo(self.produto["nome"]))
        self.info = {}
        self.registrar("Álbum novo. Arraste fotos ou uma pasta para a janela.")

    def abrir_projeto(self, caminho=None, em_nova_aba=False):
        if not em_nova_aba and not self._confirmar_descartar():
            return
        if not caminho:
            caminho = filedialog.askopenfilename(parent=self.root, title="Abrir álbum",
                                                 filetypes=[("Álbum", "*" + proj.EXTENSAO), ("Todos", "*.*")])
        if not caminho:
            return
        if not em_nova_aba:
            self._limpar_pastas_temp_drive(limpar_autosave=True)
        try:
            projeto = proj.abrir(caminho)
        except Exception as e:
            messagebox.showerror("Abrir", str(e), parent=self.root)
            return
        if em_nova_aba:
            self.nova_aba_album(projeto_inicial=projeto, caminho=caminho)
        else:
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

    def salvar_projeto_como(self):
        return self.salvar_projeto(como=True)

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

    def _executar_limpeza_programada_temp_drive(self, limite_mb=250):
        """
        Limpeza programada periódica: monitora o uso de memória/disco das pastas temporárias
        do Google Drive na sessão atual e remove fotos/pastas temporárias não utilizadas
        nas lâminas caso o limite (padrão 250 MB) seja atingido.
        """
        try:
            pastas = [p for p in list(getattr(self, "_pastas_temp_drive", [])) if p and Path(p).exists()]
            if pastas:
                usadas = {
                    normalizar_caminho(e["foto"])
                    for e in proj.todos_espacos(self.projeto)
                    if e.get("foto")
                }
                arquivos_temp = []
                bytes_total = 0
                for p in pastas:
                    for arq in Path(p).rglob("*"):
                        if arq.is_file():
                            try:
                                tam = arq.stat().st_size
                                bytes_total += tam
                                arquivos_temp.append((arq, tam, normalizar_caminho(arq) in usadas))
                            except OSError:
                                pass

                limite_bytes = max(32, int(limite_mb)) * 1024 * 1024
                if bytes_total > limite_bytes and self.tarefa is None:
                    meta_bytes = int(limite_bytes * 0.75)
                    removidas = []
                    for arq, tam, em_uso in arquivos_temp:
                        if bytes_total <= meta_bytes:
                            break
                        if not em_uso:
                            try:
                                ch = str(arq)
                                if hasattr(self, "observador") and self.observador:
                                    self.observador.esquecer(ch)
                                analise.esquecer_miniaturas(ch)
                                arq.unlink(missing_ok=True)
                                removidas.append(ch)
                                bytes_total -= tam
                            except OSError:
                                pass
                    if removidas:
                        proj.remover_fotos(self.projeto, removidas)
                        for r in removidas:
                            self.info.pop(normalizar_caminho(r), None)
                        self.atualizar_fotos()
                        self.registrar(
                            "Limpeza programada de memória: %d foto(s) temporária(s) não usada(s) do Drive removida(s)."
                            % len(removidas)
                        )
        except Exception:
            pass
        finally:
            self.agendar("limpeza_temp_drive", 30000, self._executar_limpeza_programada_temp_drive)

    def _limpar_pastas_temp_drive(self, limpar_autosave=True):
        """
        Remove imediatamente do disco todas as pastas temporárias do Google Drive e
        quaisquer arquivos temporários/caches órfãos, liberando descritores abertos no Windows.
        """
        self.cancelar_agendado("limpeza_temp_drive")
        pastas = list(getattr(self, "_pastas_temp_drive", []))
        for p in pastas:
            if not p:
                continue
            try:
                p_norm = normalizar_caminho(p)
                if hasattr(self, "observador") and self.observador:
                    for vig in list(self.observador.vigiados()):
                        v_norm = normalizar_caminho(vig)
                        if v_norm == p_norm or v_norm.startswith(p_norm + os.sep):
                            self.observador.esquecer(vig)
                analise.esquecer_pasta(p)
            except Exception:
                pass
            try:
                apagar_pasta(p, tentativas=6, pausa=0.2)
            except Exception:
                pass
        if hasattr(self, "_pastas_temp_drive"):
            self._pastas_temp_drive.clear()
        try:
            limpar_temporarios_disco(pastas_protegidas=(), limpar_autosave=limpar_autosave)
        except Exception:
            pass

    def sair(self):
        if not self._confirmar_descartar():
            return
        self.gravar_arranjo()
        self.observador.parar()
        try:
            self._tk_lamina = None
            self._tk_lamina_zoom = None
            self._imagem_lamina = None
            self._cache_laminas.clear()
            self._tk_miniaturas.clear()
            self._tl_pil.clear()
            self._tl_tk.clear()
        except Exception:
            pass
        self._limpar_pastas_temp_drive(limpar_autosave=True)
        analise.gravar_cache()
        self.root.destroy()

    # ==================================================================
    # Fotos: entrada
    # ==================================================================

    def adicionar_caminhos(self, caminhos, destino=None, perguntar_diagramar=False):
        """
        Fotos, pastas ou projetos vindos do Explorer, do Ctrl+V, do Link do Drive ou da linha de comando.
        destino: ("espaco", lamina, espaco) ou ("lamina", lamina) para já colocar as fotos.
        """
        caminhos = [c for c in (caminhos or []) if c]
        tem_pasta_arrastada = any(Path(c).is_dir() for c in caminhos)
        if tem_pasta_arrastada:
            # Quando uma pasta inteira é arrastada do PC, nunca espreme todas as fotos dentro de um único quadro
            destino = None
        fotos, projetos = entrada.expandir(caminhos)
        if projetos and not fotos:
            self.abrir_projeto(str(projetos[0]))
            return
        if not fotos:
            self.registrar("Nenhuma foto (JPG, PNG, TIF ou WEBP) encontrada na pasta selecionada.")
            return
        novo = not self.projeto["fotos"] and not self.projeto["laminas"]
        sem_laminas = not self.projeto.get("laminas")
        pasta_origem = entrada.pasta_comum(fotos)
        if novo:
            produto = entrada.adivinhar_produto(pasta_origem, fotos)
            if produto:
                self._definir_produto(produto)
            self.projeto["nome"] = pasta_origem.name if pasta_origem else "Novo álbum"
            self.projeto["pasta_fotos"] = str(pasta_origem) if pasta_origem else ""
            self.projeto["pasta_saida"] = str(pasta_origem) if pasta_origem else ""
            if pasta_origem and str(pasta_origem) not in getattr(self, "_pastas_temp_drive", []):
                entrada.adicionar_recente("pasta", pasta_origem)
            self._carregar_ui_do_projeto()
        elif pasta_origem and str(pasta_origem) not in getattr(self, "_pastas_temp_drive", []):
            entrada.adicionar_recente("pasta", pasta_origem)
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
            elif perguntar_diagramar:
                if messagebox.askyesno(
                    "Google Drive",
                    "%d foto(s) baixada(s) na pasta temporária.\nDeseja diagramar essas fotos agora?" % len(info),
                    parent=self.root,
                ):
                    if novo or sem_laminas:
                        self.diagramar_tudo(False)
                    else:
                        self.diagramar_tudo(False, escopo="pasta", pasta_alvo=str(pasta_origem) if pasta_origem else None)
            elif novo or sem_laminas:
                self.diagramar_tudo(False)
            elif self.projeto.get("laminas") and (self._cfg().get("separar_pastas", True) or tem_pasta_arrastada):
                pastas_novas = []
                nomes_novas = []
                for cam_n in novas:
                    ch_n = normalizar_caminho(proj.pasta_da_foto(cam_n))
                    if ch_n not in pastas_novas:
                        pastas_novas.append(ch_n)
                        nomes_novas.append(proj.nome_pasta_da_foto(cam_n))
                resumo_pastas = ", ".join(nomes_novas[:3]) + ("..." if len(nomes_novas) > 3 else "")
                if tem_pasta_arrastada:
                    if len(pastas_novas) == 1:
                        self.diagramar_tudo(False, escopo="pasta", pasta_alvo=pastas_novas[0])
                    else:
                        self.distribuir_nao_usadas()
                elif messagebox.askyesno(
                    "Separar por Pastas Carregadas",
                    "%d foto(s) carregada(s) na pasta '%s'.\nDeseja diagramar esta pasta em novas lâminas separadas ao final do álbum (sem misturar com as pastas anteriores)?"
                    % (len(novas), resumo_pastas),
                    parent=self.root,
                ):
                    if len(pastas_novas) == 1:
                        self.diagramar_tudo(False, escopo="pasta", pasta_alvo=pastas_novas[0])
                    else:
                        self.distribuir_nao_usadas()
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

    _HEADERS_DRIVE = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        "Origin": "https://drive.google.com",
        "Referer": "https://drive.google.com/",
    }
    _CHAVES_API_DRIVE_PADRAO = (
        "AIzaSyAWGrfCCr7albM3lmCc937gx4uIphbpeKQ",
        "AIzaSyC1qbk75NzWBvSaDh6KnsjjA9pIrP4lYIE",
    )
    _chave_api_drive_ativa = None

    @staticmethod
    def _extrair_id_drive(url):
        url = str(url or "").strip()
        parsed = urllib.parse.urlparse(url)
        qs = urllib.parse.parse_qs(parsed.query)
        resourcekey = qs.get("resourcekey", [None])[0]
        m_pasta = re.search(r"/folders/([a-zA-Z0-9_-]{10,})", url)
        if m_pasta:
            return "pasta", m_pasta.group(1), resourcekey
        m_arq = re.search(r"/file/d/([a-zA-Z0-9_-]{10,})", url)
        if m_arq:
            return "arquivo", m_arq.group(1), resourcekey
        if "id" in qs and qs["id"]:
            return "pasta", qs["id"][0], resourcekey
        if re.fullmatch(r"[a-zA-Z0-9_-]{18,}", url):
            return "pasta", url, None
        return None, None, None

    @staticmethod
    def _decodificar_html_drive(raw_html):
        s = re.sub(r"\\x([0-9a-fA-F]{2})", lambda m: chr(int(m.group(1), 16)), raw_html)
        s = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), s)
        return s.replace("\\/", "/")

    @classmethod
    def _obter_chaves_api_drive(cls, folder_or_file_id=None, html_ja_lido=None):
        chaves = []
        if cls._chave_api_drive_ativa:
            chaves.append(cls._chave_api_drive_ativa)
        for k in cls._CHAVES_API_DRIVE_PADRAO:
            if k not in chaves:
                chaves.append(k)
        if html_ja_lido:
            for k in re.findall(r"AIza[0-9A-Za-z_-]{35}", html_ja_lido):
                if k not in chaves:
                    chaves.append(k)
        elif folder_or_file_id:
            try:
                url = "https://drive.google.com/drive/folders/%s?usp=sharing" % folder_or_file_id
                req = urllib.request.Request(url, headers=cls._HEADERS_DRIVE)
                with urllib.request.urlopen(req, timeout=15) as resp:
                    raw = resp.read().decode("utf-8", errors="ignore")
                for k in re.findall(r"AIza[0-9A-Za-z_-]{35}", raw):
                    if k not in chaves:
                        chaves.append(k)
            except Exception:
                pass
        return chaves

    @classmethod
    def _requisitar_api_drive_v3(cls, caminho_api, params, resourcekey=None, chaves_candidatas=None):
        chaves = chaves_candidatas or cls._obter_chaves_api_drive()
        headers = dict(cls._HEADERS_DRIVE)
        if resourcekey:
            headers["X-Goog-Drive-Resource-Keys"] = resourcekey

        for chave in chaves:
            q_params = dict(params)
            q_params["key"] = chave
            url = "https://www.googleapis.com/drive/v3/%s?%s" % (
                caminho_api.lstrip("/"),
                urllib.parse.urlencode(q_params),
            )
            for tentativa in range(3):
                try:
                    req = urllib.request.Request(url, headers=headers)
                    with urllib.request.urlopen(req, timeout=25) as resp:
                        dados = json.loads(resp.read().decode("utf-8", errors="ignore"))
                    cls._chave_api_drive_ativa = chave
                    return dados
                except urllib.error.HTTPError as e:
                    if e.code in (429, 500, 502, 503, 504) and tentativa < 2:
                        time.sleep(0.6 * (tentativa + 1))
                        continue
                    break
                except Exception:
                    if tentativa < 2:
                        time.sleep(0.5 * (tentativa + 1))
                        continue
                    break
        return None

    @classmethod
    def _resolver_atalho_drive(cls, file_id, resourcekey=None):
        meta = cls._requisitar_api_drive_v3(
            "files/%s" % file_id,
            {
                "fields": "id,name,mimeType,shortcutDetails(targetId,targetMimeType,targetResourceKey)",
                "supportsAllDrives": "true",
            },
            resourcekey=resourcekey,
        )
        if isinstance(meta, dict) and meta.get("id"):
            atalho = meta.get("shortcutDetails") or {}
            if atalho.get("targetId"):
                return atalho["targetId"], meta.get("name"), atalho.get("targetMimeType")
            return meta["id"], meta.get("name"), meta.get("mimeType")

        try:
            url = "https://drive.google.com/file/d/%s/view" % file_id
            req = urllib.request.Request(url, headers=cls._HEADERS_DRIVE)
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read().decode("utf-8", errors="ignore")
            s = cls._decodificar_html_drive(raw)
            m = re.search(
                r'\["([a-zA-Z0-9_-]{15,})",(?:\["[^"]*"\]|null),"([^"]+)","(image/[^"]+)"',
                s,
            )
            if m:
                return m.group(1), m.group(2), m.group(3)
        except Exception:
            pass
        return file_id, None, None

    @classmethod
    def _eh_nome_ou_mime_imagem(cls, nome, mime):
        mime_l = str(mime or "").lower()
        ext_l = Path(str(nome or "")).suffix.lower()
        if mime_l in (
            "application/vnd.google-apps.folder",
            "application/vnd.google-apps.shortcut",
            "application/vnd.google-apps.document",
            "application/vnd.google-apps.spreadsheet",
            "application/vnd.google-apps.presentation",
            "application/vnd.google-apps.form",
            "application/pdf",
            "video/mp4",
            "video/quicktime",
        ):
            return False
        if mime_l.startswith("video/") or mime_l.startswith("audio/"):
            return False
        if ext_l in EXTENSOES:
            return True
        if mime_l.startswith("image/"):
            return True
        return False

    @classmethod
    def _listar_arquivos_pasta_drive(cls, folder_id, visitados=None, nivel=0, resourcekey=None):
        if visitados is None:
            visitados = set()
        if folder_id in visitados or nivel > 6:
            return [], None
        visitados.add(folder_id)

        arquivos = []
        subpastas = []
        ids_subpastas = set()
        ids_atalhos = set()
        ids_arquivos = set()
        titulo_pasta = None
        rk_header = "%s/%s" % (folder_id, resourcekey) if resourcekey else None

        # 0. Paginação completa via Google Drive API v3 (traz 100% dos arquivos sem o limite de 50 itens da página HTML)
        meta_pasta = cls._requisitar_api_drive_v3(
            "files/%s" % folder_id,
            {"fields": "id,name,mimeType", "supportsAllDrives": "true"},
            resourcekey=rk_header,
        )
        if isinstance(meta_pasta, dict) and meta_pasta.get("name"):
            titulo_pasta = meta_pasta["name"].strip()

        page_token = None
        listou_via_api = False
        for _pagina in range(50):
            params_lista = {
                "q": "'%s' in parents and trashed = false" % folder_id,
                "fields": "nextPageToken,files(id,name,mimeType,resourceKey,shortcutDetails(targetId,targetMimeType,targetResourceKey))",
                "pageSize": "1000",
                "orderBy": "folder,name_natural",
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
            }
            if page_token:
                params_lista["pageToken"] = page_token
            dados_pag = cls._requisitar_api_drive_v3("files", params_lista, resourcekey=rk_header)
            if not isinstance(dados_pag, dict) or "files" not in dados_pag:
                break
            listou_via_api = True
            for item in dados_pag.get("files") or []:
                fid = item.get("id")
                nome = (item.get("name") or "").strip()
                mime = item.get("mimeType") or ""
                if not fid:
                    continue
                if mime == "application/vnd.google-apps.folder":
                    if fid != folder_id and fid not in visitados and fid not in ids_subpastas:
                        ids_subpastas.add(fid)
                        subpastas.append((fid, nome, item.get("resourceKey")))
                elif mime == "application/vnd.google-apps.shortcut":
                    atalho = item.get("shortcutDetails") or {}
                    alvo_id = atalho.get("targetId")
                    alvo_mime = atalho.get("targetMimeType") or ""
                    alvo_rk = atalho.get("targetResourceKey")
                    if not alvo_id:
                        alvo_id, real_nome, alvo_mime = cls._resolver_atalho_drive(fid)
                        nome = real_nome or nome
                    if alvo_mime == "application/vnd.google-apps.folder":
                        if alvo_id and alvo_id != folder_id and alvo_id not in visitados and alvo_id not in ids_subpastas:
                            ids_subpastas.add(alvo_id)
                            subpastas.append((alvo_id, nome, alvo_rk))
                    elif alvo_id and cls._eh_nome_ou_mime_imagem(nome, alvo_mime):
                        if alvo_id not in ids_arquivos:
                            ids_arquivos.add(alvo_id)
                            arquivos.append((alvo_id, nome))
                elif cls._eh_nome_ou_mime_imagem(nome, mime):
                    if fid not in ids_arquivos:
                        ids_arquivos.add(fid)
                        arquivos.append((fid, nome))
            page_token = dados_pag.get("nextPageToken")
            if not page_token:
                break

        # 1. Fallback HTML (_initData e embeddedfolderview) apenas se a API v3 não respondeu
        if not listou_via_api:
            raw_html_principal = ""
            try:
                url_folder = "https://drive.google.com/drive/folders/%s?usp=sharing" % folder_id
                req = urllib.request.Request(url_folder, headers=cls._HEADERS_DRIVE)
                with urllib.request.urlopen(req, timeout=25) as resp:
                    raw_html_principal = resp.read().decode("utf-8", errors="ignore")
                if not titulo_pasta:
                    m_title = re.search(r"<title>([^<]+)</title>", raw_html_principal, re.I)
                    if m_title:
                        t_limpo = re.sub(r"\s*-\s*Google\s*Drive\s*$", "", m_title.group(1), flags=re.I).strip()
                        if t_limpo and t_limpo.lower() != "google drive":
                            titulo_pasta = t_limpo

                # Se achou uma nova chave AIza no HTML, tenta imediatamente a API paginada com ela
                novas_chaves = cls._obter_chaves_api_drive(html_ja_lido=raw_html_principal)
                dados_retry = cls._requisitar_api_drive_v3(
                    "files",
                    {
                        "q": "'%s' in parents and trashed = false" % folder_id,
                        "fields": "nextPageToken,files(id,name,mimeType,resourceKey,shortcutDetails(targetId,targetMimeType,targetResourceKey))",
                        "pageSize": "1000",
                        "orderBy": "folder,name_natural",
                        "supportsAllDrives": "true",
                        "includeItemsFromAllDrives": "true",
                    },
                    resourcekey=rk_header,
                    chaves_candidatas=novas_chaves,
                )
                if isinstance(dados_retry, dict) and "files" in dados_retry:
                    visitados.discard(folder_id)
                    return cls._listar_arquivos_pasta_drive(folder_id, visitados, nivel, resourcekey)

                s = cls._decodificar_html_drive(raw_html_principal)
                for fid, nome, mime in re.findall(
                    r'\["([a-zA-Z0-9_-]{15,})",(?:\["[^"]*"\]|null),"([^"]+)","([^"]+)"',
                    s,
                ):
                    if mime == "application/vnd.google-apps.folder":
                        if fid != folder_id and fid not in visitados and fid not in ids_subpastas:
                            ids_subpastas.add(fid)
                            subpastas.append((fid, nome, None))
                    elif mime == "application/vnd.google-apps.shortcut":
                        ids_atalhos.add(fid)
                    elif cls._eh_nome_ou_mime_imagem(nome, mime):
                        if fid not in ids_arquivos:
                            ids_arquivos.add(fid)
                            arquivos.append((fid, nome))
            except Exception:
                pass

            try:
                url_lista = "https://drive.google.com/embeddedfolderview?id=%s#list" % folder_id
                req_emb = urllib.request.Request(url_lista, headers=cls._HEADERS_DRIVE)
                with urllib.request.urlopen(req_emb, timeout=25) as resp_emb:
                    html_emb = resp_emb.read().decode("utf-8", errors="ignore")
                if not titulo_pasta:
                    m_t2 = re.search(r"<title>([^<]+)</title>", html_emb, re.I)
                    if m_t2 and m_t2.group(1).strip():
                        titulo_pasta = m_t2.group(1).strip()

                padrao_entry = re.compile(
                    r'id="entry-([a-zA-Z0-9_-]{10,})".*?<a\s+href="([^"]+)".*?class="flip-entry-title">([^<]+)<',
                    re.S,
                )
                for m in padrao_entry.finditer(html_emb):
                    eid, href, titulo = m.group(1), m.group(2), m.group(3).strip()
                    bloco = m.group(0)
                    if "/folders/" in href or "drive-sprite-folder" in bloco:
                        if eid != folder_id and eid not in visitados and eid not in ids_subpastas:
                            ids_subpastas.add(eid)
                            subpastas.append((eid, titulo, None))
                    elif "vnd.google-apps.shortcut" in bloco:
                        if eid not in ids_atalhos:
                            ids_atalhos.add(eid)
                            real_id, real_nome, real_mime = cls._resolver_atalho_drive(eid)
                            if real_id and real_id not in ids_arquivos and cls._eh_nome_ou_mime_imagem(real_nome or titulo, real_mime or "image/jpeg"):
                                ids_arquivos.add(real_id)
                                arquivos.append((real_id, real_nome or titulo))
                    elif cls._eh_nome_ou_mime_imagem(titulo, ""):
                        if eid not in ids_arquivos and eid not in ids_atalhos:
                            ids_arquivos.add(eid)
                            arquivos.append((eid, titulo))
            except Exception:
                pass

        # 2. Percorre recursivamente todas as subpastas encontradas (ex: "Álbum 20x20" dentro da pasta principal)
        for sub_item in subpastas:
            sub_id, sub_nome = sub_item[0], sub_item[1]
            sub_rk = sub_item[2] if len(sub_item) > 2 else None
            sub_arqs, _ = cls._listar_arquivos_pasta_drive(sub_id, visitados, nivel + 1, sub_rk)
            if sub_arqs and len(subpastas) == 1 and not arquivos and sub_nome:
                titulo_pasta = sub_nome
            for fid, nome in sub_arqs:
                if fid not in ids_arquivos:
                    ids_arquivos.add(fid)
                    arquivos.append((fid, nome))

        return arquivos, titulo_pasta

    @classmethod
    def _eh_conteudo_imagem_valido(cls, dados):
        if not dados or len(dados) < 256:
            return False
        # Verifica assinaturas binárias reais (Magic Bytes) para nunca aceitar JSON de erro 403/429, texto ou página HTML de cota do Google
        if dados[:3] == b"\xff\xd8\xff":  # JPEG
            return True
        if dados[:8] == b"\x89PNG\r\n\x1a\n":  # PNG
            return True
        if dados[:4] == b"RIFF" and dados[8:12] == b"WEBP":  # WebP
            return True
        if dados[:4] in (b"II*\x00", b"MM\x00*"):  # TIFF
            return True
        if dados[:2] == b"BM":  # BMP
            return True
        if dados[:6] in (b"GIF87a", b"GIF89a"):  # GIF
            return True
        cabecalho = dados[:512].lstrip().lower()
        if (
            cabecalho.startswith(b"<!doctype")
            or cabecalho.startswith(b"<html")
            or cabecalho.startswith(b"<head")
            or cabecalho.startswith(b"{")
            or cabecalho.startswith(b"[")
        ):
            return False
        return False

    @classmethod
    def _baixar_item_drive(cls, file_id, destino_arq):
        chaves = cls._obter_chaves_api_drive()
        urls_tentativa = []
        for ch in chaves[:2]:
            urls_tentativa.append(
                "https://www.googleapis.com/drive/v3/files/%s?alt=media&supportsAllDrives=true&key=%s"
                % (file_id, ch)
            )
        urls_tentativa.extend([
            "https://lh3.googleusercontent.com/d/%s=s0" % file_id,
            "https://drive.usercontent.google.com/download?id=%s&export=download&confirm=t" % file_id,
            "https://drive.google.com/uc?export=download&id=%s" % file_id,
            "https://drive.google.com/thumbnail?id=%s&sz=w4096" % file_id,
        ])

        conteudo = None
        for rodada in range(3):
            for url_dl in urls_tentativa:
                try:
                    req = urllib.request.Request(url_dl, headers=cls._HEADERS_DRIVE)
                    with urllib.request.urlopen(req, timeout=35) as resp:
                        dados = resp.read()
                    if not cls._eh_conteudo_imagem_valido(dados):
                        # Verifica se é página de confirmação do Google Drive
                        m_conf = re.search(rb"confirm=([0-9A-Za-z_-]+)", dados)
                        m_uuid = re.search(rb'name="uuid"\s+value="([^"]+)"', dados)
                        if m_uuid:
                            uuid_val = m_uuid.group(1).decode("utf-8", errors="ignore")
                            url_conf = (
                                "https://drive.usercontent.google.com/download?id=%s&export=download&confirm=t&uuid=%s"
                                % (file_id, uuid_val)
                            )
                            req2 = urllib.request.Request(url_conf, headers=cls._HEADERS_DRIVE)
                            with urllib.request.urlopen(req2, timeout=35) as resp2:
                                dados = resp2.read()
                        elif m_conf:
                            token = m_conf.group(1).decode("utf-8", errors="ignore")
                            url_conf = "https://drive.google.com/uc?export=download&id=%s&confirm=%s" % (file_id, token)
                            req2 = urllib.request.Request(url_conf, headers=cls._HEADERS_DRIVE)
                            with urllib.request.urlopen(req2, timeout=35) as resp2:
                                dados = resp2.read()
                    if cls._eh_conteudo_imagem_valido(dados):
                        conteudo = dados
                        break
                except urllib.error.HTTPError as e:
                    if e.code in (429, 500, 502, 503, 504):
                        time.sleep(0.4 * (rodada + 1))
                    continue
                except Exception:
                    continue
            if conteudo:
                break
            time.sleep(0.7 * (rodada + 1))

        if not conteudo:
            return False

        with open(destino_arq, "wb") as f:
            f.write(conteudo)
        return True

    def importar_link_drive(self):
        janela = tk.Toplevel(self.root)
        janela.title("Importar Fotos do Google Drive")
        janela.configure(bg=tema.PAINEL)
        janela.transient(self.root)

        tema.criar_cabecalho_janela(
            janela,
            "Importar Fotos ou Pastas do Google Drive",
            "Baixa todas as fotos do link em pasta temporária com limpeza automática ao fechar",
            icone="☁",
        )

        rodape = tk.Frame(janela, bg=tema.CABECALHO, highlightthickness=1, highlightbackground=tema.BORDA, padx=14, pady=8)
        rodape.pack(side="bottom", fill="x")

        quadro = ttk.Frame(janela, padding=14)
        quadro.pack(fill="both", expand=True)
        lbl_t = ttk.Label(quadro, text="Cole o link público da pasta ou foto do Google Drive:", style="Titulo.TLabel")
        lbl_t.pack(anchor="w")
        lbl_sub = ttk.Label(
            quadro,
            text="As fotos serão baixadas em uma pasta temporária e apagadas ao fechar o programa ou atingir o limite de memória.",
            style="Fraco.TLabel",
            justify="left",
        )
        lbl_sub.pack(anchor="w", pady=(2, 8))
        quadro.bind("<Configure>", lambda e: lbl_sub.configure(wraplength=max(320, e.width - 28)), add="+")

        linha = ttk.Frame(quadro)
        linha.pack(fill="x", pady=(0, 6))
        link_var = tk.StringVar()
        try:
            clip = self.root.clipboard_get().strip()
            if "drive.google.com" in clip:
                link_var.set(clip)
        except tk.TclError:
            pass

        campo = ttk.Entry(linha, textvariable=link_var, width=54)
        campo.pack(side="left", fill="x", expand=True, padx=(0, 6))
        campo.focus_set()

        def colar_clip():
            try:
                link_var.set(self.root.clipboard_get().strip())
            except tk.TclError:
                pass

        ttk.Button(linha, text="Colar", command=colar_clip).pack(side="right")

        botoes = ttk.Frame(rodape)
        botoes.pack(fill="x")

        def confirmar():
            url = link_var.get().strip()
            if not url:
                return
            try:
                janela.grab_release()
            except tk.TclError:
                pass
            janela.destroy()
            self._executar_download_drive(url)

        ttk.Button(botoes, text="Cancelar", command=janela.destroy).pack(side="right", padx=(0, 10))
        ttk.Button(botoes, text="Baixar Fotos", style="Primario.TButton", command=confirmar).pack(side="right", padx=(0, 6))
        janela.bind("<Return>", lambda _e: confirmar())
        janela.bind("<Escape>", lambda _e: janela.destroy())
        tema.configurar_janela_secundaria(
            janela,
            pai=self,
            chave="janela_drive",
            largura=600,
            altura=230,
            min_largura=440,
            min_altura=200,
        )
        janela.grab_set()

    def _executar_download_drive(self, url):
        tipo_id, drive_id, resourcekey = self._extrair_id_drive(url)
        if not drive_id:
            messagebox.showwarning("Google Drive", "Link inválido. Cole um link de pasta ou arquivo do Google Drive.", parent=self.root)
            return

        pasta_base = tempfile.mkdtemp(prefix="SmartAlbum_Drive_")
        self._pastas_temp_drive.append(pasta_base)
        estado_destino = {"pasta": Path(pasta_base) / ("Drive_%s" % drive_id[:6]), "esperadas": 0}
        estado_destino["pasta"].mkdir(parents=True, exist_ok=True)

        def trabalho(t):
            baixados = []
            pasta_destino = estado_destino["pasta"]

            if tipo_id == "arquivo":
                real_id, real_nome, _real_mime = self._resolver_atalho_drive(drive_id, resourcekey=resourcekey)
                nome_arq = re.sub(r'[<>:"/\\|?*]', "_", (real_nome or ("foto_%s.jpg" % drive_id[:8])).strip())
                if Path(nome_arq).suffix.lower() not in EXTENSOES:
                    nome_arq += ".jpg"
                dest = pasta_destino / nome_arq
                estado_destino["esperadas"] = 1
                if self._baixar_item_drive(real_id or drive_id, dest) and dest.is_file():
                    baixados.append(dest)
                return baixados

            # 1. Lista recursivamente TODAS as fotos (paginando todas as páginas da API v3 sem o limite de 50 fotos)
            achados, titulo_pasta = self._listar_arquivos_pasta_drive(drive_id, resourcekey=resourcekey)
            if titulo_pasta:
                nome_pasta_limpo = re.sub(r'[<>:"/\\|?*]', "_", titulo_pasta).strip()
                if nome_pasta_limpo:
                    nova_pasta = Path(pasta_base) / nome_pasta_limpo
                    nova_pasta.mkdir(parents=True, exist_ok=True)
                    pasta_destino = nova_pasta
                    estado_destino["pasta"] = nova_pasta

            if achados:
                import concurrent.futures

                total = len(achados)
                estado_destino["esperadas"] = total
                nomes_usados = set()
                tarefas_dl = []
                for idx, (fid, nome_arq) in enumerate(achados, start=1):
                    nome_limpo = re.sub(r'[<>:"/\\|?*]', "_", (nome_arq or ("foto_%03d.jpg" % idx)).strip())
                    if Path(nome_limpo).suffix.lower() not in EXTENSOES:
                        nome_limpo += ".jpg"
                    base_stem = Path(nome_limpo).stem
                    ext_s = Path(nome_limpo).suffix
                    cand = nome_limpo
                    contador_dup = 2
                    while cand.lower() in nomes_usados:
                        cand = "%s_%d%s" % (base_stem, contador_dup, ext_s)
                        contador_dup += 1
                    nomes_usados.add(cand.lower())
                    tarefas_dl.append((fid, pasta_destino / cand))

                concluidos = 0
                faltaram = []
                # Usa 4 workers paralelos para evitar bloqueio de taxa (HTTP 429) do Google Drive
                with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                    futuros = {
                        pool.submit(self._baixar_item_drive, fid, dest): (fid, dest)
                        for fid, dest in tarefas_dl
                    }
                    for fut in concurrent.futures.as_completed(futuros):
                        if t.parar():
                            break
                        fid, dest = futuros[fut]
                        concluidos += 1
                        ok = False
                        try:
                            if fut.result() and dest.is_file() and dest.stat().st_size > 256:
                                baixados.append(dest)
                                ok = True
                        except Exception:
                            ok = False
                        if not ok:
                            faltaram.append((fid, dest))
                        t.progresso(concluidos, total)

                # Segunda passagem sequencial de recuperação para fotos que sofreram rate-limit momentâneo
                if faltaram and not t.parar():
                    time.sleep(1.0)
                    for fid, dest in faltaram:
                        if t.parar():
                            break
                        try:
                            if self._baixar_item_drive(fid, dest) and dest.is_file() and dest.stat().st_size > 256:
                                baixados.append(dest)
                        except Exception:
                            pass

                if baixados:
                    # Ordena na mesma ordem exata da listagem da pasta
                    ordem_map = {dest: i for i, (_fid, dest) in enumerate(tarefas_dl)}
                    baixados.sort(key=lambda p: ordem_map.get(p, 999999))
                    return baixados

            # 2. Fallback com gdown (com remaining_ok=True para não parar em 50 arquivos)
            try:
                import gdown
                try:
                    arqs = gdown.download_folder(
                        id=drive_id,
                        output=str(pasta_destino),
                        quiet=True,
                        use_cookies=False,
                        remaining_ok=True,
                    ) or []
                except TypeError:
                    arqs = gdown.download_folder(
                        id=drive_id,
                        output=str(pasta_destino),
                        quiet=True,
                        use_cookies=False,
                    ) or []
                baixados = [Path(a) for a in arqs if Path(a).suffix.lower() in EXTENSOES and Path(a).is_file()]
            except Exception:
                pass
            return baixados

        def fim(baixados, erro):
            if erro or not baixados:
                try:
                    if pasta_base in self._pastas_temp_drive:
                        self._pastas_temp_drive.remove(pasta_base)
                    apagar_pasta(pasta_base, tentativas=5, pausa=0.15)
                except Exception:
                    pass
                messagebox.showwarning(
                    "Google Drive",
                    "Não foi possível baixar fotos desse link.\nVerifique se a pasta do Google Drive está com acesso 'Qualquer pessoa com o link'.",
                    parent=self.root,
                )
                return
            esperadas = estado_destino.get("esperadas") or len(baixados)
            self.registrar(
                "Google Drive: %d de %d foto(s) baixada(s) na pasta temporária (auto-excluída ao fechar)." % (len(baixados), esperadas)
            )
            self.adicionar_caminhos([str(estado_destino["pasta"])], perguntar_diagramar=True)
            self.agendar("limpeza_temp_drive", 4000, self._executar_limpeza_programada_temp_drive)

        self.executar("Baixando fotos do Google Drive", trabalho, fim)

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
        return getattr(evento, "action", "copy")

    def _drop_lamina(self, evento):
        caminhos = entrada.caminhos_do_drop(self.root, evento.data)
        if any(Path(c).is_dir() for c in caminhos):
            self.adicionar_caminhos(caminhos, None)
            return getattr(evento, "action", "copy")
        x = evento.x_root - self.canvas.winfo_rootx()
        y = evento.y_root - self.canvas.winfo_rooty()
        destino = None
        if self._shift_no_drop(evento) and len(caminhos) == 1 and Path(caminhos[0]).suffix.lower() in EXTENSOES \
                and self._dentro_da_lamina(x, y) and self._dono_do_fundo() is not None:
            self.definir_imagem_fundo(caminhos[0])
            return getattr(evento, "action", "copy")
        if isinstance(self.atual, int) and self.projeto["laminas"]:
            indice = self._espaco_em(x, y)
            if indice is not None:
                destino = ("espaco", self.atual, indice)
            elif self._dentro_da_lamina(x, y):
                destino = ("lamina", self.atual)
        self.adicionar_caminhos(caminhos, destino)
        return getattr(evento, "action", "copy")

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
        if any(Path(c).is_dir() for c in caminhos):
            self.adicionar_caminhos(caminhos, None)
            return getattr(evento, "action", "copy")
        item = self._timeline_em(self._eixo_tl_root(evento.x_root, evento.y_root))
        destino = ("lamina", item) if isinstance(item, int) else None
        self.adicionar_caminhos(caminhos, destino)
        return getattr(evento, "action", "copy")

    def _receber_drop_nativo_windows(self, caminhos_brutos, x_root, y_root):
        """Trata pastas ou fotos soltas via WM_DROPFILES nativo do Windows."""
        caminhos = entrada.caminhos_do_drop(self.root, caminhos_brutos)
        if not caminhos:
            return
        if any(Path(c).is_dir() for c in caminhos):
            self.adicionar_caminhos(caminhos, None)
            return
        alvo_w = None
        try:
            alvo_w = self.root.winfo_containing(x_root, y_root)
        except Exception:
            pass
        destino = None
        if alvo_w is self.canvas and isinstance(self.atual, int) and self.projeto["laminas"]:
            x = x_root - self.canvas.winfo_rootx()
            y = y_root - self.canvas.winfo_rooty()
            indice = self._espaco_em(x, y)
            if indice is not None:
                destino = ("espaco", self.atual, indice)
            elif self._dentro_da_lamina(x, y):
                destino = ("lamina", self.atual)
        elif alvo_w is self.canvas_timeline:
            item = self._timeline_em(self._eixo_tl_root(x_root, y_root))
            if isinstance(item, int):
                destino = ("lamina", item)
        self.adicionar_caminhos(caminhos, destino)

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
        pasta = filedialog.askdirectory(
            parent=self.root,
            title="Escolha a pasta onde estão as fotos para relinkar o projeto",
            initialdir=self.projeto.get("pasta_fotos") or "",
        )
        if not pasta:
            return
        self.marcar_historico()
        consertadas, faltam = entrada.consertar_caminhos(self.projeto, pasta, forcar_todas=True)
        self._marcar_modificado()
        messagebox.showinfo(
            "Relinkar Fotos",
            "%s foto(s) relinkadas com sucesso na pasta:\n%s%s"
            % (
                consertadas,
                pasta,
                ("\n\n%s arquivo(s) ainda não foram encontrados." % len(faltam)) if faltam else "\n\nTodas as fotos do álbum estão vinculadas!",
            ),
            parent=self.root,
        )
        self._analisar_projeto()
        self.atualizar_tudo()

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
        todas_fotos = proj.fotos_para_diagramar(self.projeto, self.info)
        if not todas_fotos:
            self.registrar("Adicione fotos antes de diagramar.")
            return
        fotos_livres = proj.fotos_para_diagramar(self.projeto, self.info, somente_nao_usadas=True)
        pastas_proj = proj.listar_pastas_projeto(self.projeto)

        cfg = self._cfg()
        paginas = max(1, int(self.produto["paginas_por_lamina"]))

        janela = tk.Toplevel(self.root)
        janela.title("Diagramação Automática · Motor Elástico & Ordem por Grupos (Photoshop CC)")
        janela.configure(bg=tema.PAINEL)
        janela.transient(self.root)

        tema.criar_cabecalho_janela(
            janela,
            "Diagramação Automática — Motor Elástico e Grupos Numerados",
            "Configure o escopo, separação por grupos de ordem/pastas, limite de corte elástico e layouts 60/40 e 70/30",
            icone="❖",
        )

        # Rodapé fixo na base da janela para continuar sempre visível ao redimensionar
        rodape_fixo = tk.Frame(janela, bg=tema.CABECALHO, highlightthickness=1, highlightbackground=tema.BORDA, padx=14, pady=9)
        rodape_fixo.pack(side="bottom", fill="x")

        quadro, _container_rol, _canvas_rol = tema.criar_corpo_rolavel(janela, padding=14)

        # --- 1. Escopo da Diagramação ---
        quadro_escopo = ttk.LabelFrame(quadro, text="1. O que você deseja diagramar?", padding=10)
        quadro_escopo.pack(fill="x", pady=(0, 8))

        filtro_atual = getattr(self, "pasta_filtro_ativa", "todas")
        escopo_inicial = "pasta" if (filtro_atual and filtro_atual != "todas" and len(pastas_proj) > 0) else "tudo"
        escopo_var = tk.StringVar(value=escopo_inicial)

        opcoes_pastas_texto = []
        mapa_texto_pasta = {}
        indice_pasta_inicial = 0
        for idx_p, (ch_norm, _cam_p, nome_p, qtd_p) in enumerate(pastas_proj):
            rotulo = "%s (%d fotos)" % (nome_p, qtd_p)
            opcoes_pastas_texto.append(rotulo)
            mapa_texto_pasta[rotulo] = ch_norm
            if filtro_atual != "todas" and ch_norm == filtro_atual:
                indice_pasta_inicial = idx_p

        pasta_escolhida_var = tk.StringVar(
            value=opcoes_pastas_texto[indice_pasta_inicial] if opcoes_pastas_texto else ""
        )

        ttk.Radiobutton(
            quadro_escopo,
            text="Álbum inteiro — substituir todas as lâminas (%d fotos no projeto)" % len(todas_fotos),
            value="tudo",
            variable=escopo_var,
        ).pack(anchor="w", pady=2)

        linha_pasta = ttk.Frame(quadro_escopo)
        linha_pasta.pack(fill="x", pady=2)
        rb_pasta = ttk.Radiobutton(
            linha_pasta,
            text="Somente a pasta:",
            value="pasta",
            variable=escopo_var,
        )
        rb_pasta.pack(side="left")
        combo_pasta = ttk.Combobox(
            linha_pasta,
            textvariable=pasta_escolhida_var,
            values=opcoes_pastas_texto,
            state="readonly" if opcoes_pastas_texto else "disabled",
            width=32,
        )
        combo_pasta.pack(side="left", padx=(8, 0))
        combo_pasta.bind("<<ComboboxSelected>>", lambda _e: escopo_var.set("pasta"))

        rb_livres = ttk.Radiobutton(
            quadro_escopo,
            text="Somente fotos ainda não usadas — criar novas lâminas no final (%d livres)" % len(fotos_livres),
            value="nao_usadas",
            variable=escopo_var,
        )
        rb_livres.pack(anchor="w", pady=2)
        if not fotos_livres:
            rb_livres.configure(state="disabled")

        # --- 2. Pastas e Ordem por Grupos Numerados ---
        quadro_grupos = ttk.LabelFrame(quadro, text="2. Ordem das Fotos, Grupos Numerados e Prioridade (Individual vs. Grupo)", padding=10)
        quadro_grupos.pack(fill="x", pady=(0, 8))

        separar_pastas_var = tk.BooleanVar(value=bool(cfg.get("separar_pastas", True)))
        respeitar_grupos_var = tk.BooleanVar(value=bool(cfg.get("respeitar_grupos_manuais", True)))
        ordem_pessoas_var = tk.StringVar(value=str(cfg.get("ordem_pessoas_grupo", "manter") or "manter"))

        ttk.Checkbutton(
            quadro_grupos,
            text="Respeitar divisão por pastas (nunca misturar pastas diferentes na mesma lâmina)",
            variable=separar_pastas_var,
        ).pack(anchor="w", pady=2)
        ttk.Checkbutton(
            quadro_grupos,
            text="Respeitar ordem e grupos numerados definidos (nunca misturar grupos diferentes na mesma lâmina)",
            variable=respeitar_grupos_var,
        ).pack(anchor="w", pady=2)

        ttk.Separator(quadro_grupos, orient="horizontal").pack(fill="x", pady=6)

        ttk.Label(
            quadro_grupos,
            text="Ordem de fotos Individuais vs. Em Grupo/Turma dentro de cada grupo numerado (permite dividir a mesma lâmina):",
            font=tema.FONTE_FORTE,
        ).pack(anchor="w", pady=(0, 3))

        ttk.Radiobutton(
            quadro_grupos,
            text="Manter a ordem manual exata definida dentro de cada grupo",
            value="manter",
            variable=ordem_pessoas_var,
        ).pack(anchor="w", pady=1)
        ttk.Radiobutton(
            quadro_grupos,
            text="👤 Fotos Individuais primeiro em cada grupo (1 pessoa → depois fotos em grupo/turma)",
            value="individuais_primeiro",
            variable=ordem_pessoas_var,
        ).pack(anchor="w", pady=1)
        ttk.Radiobutton(
            quadro_grupos,
            text="👥 Fotos em Grupo/Turma primeiro em cada grupo (2+ pessoas → depois fotos individuais)",
            value="turma_primeiro",
            variable=ordem_pessoas_var,
        ).pack(anchor="w", pady=1)

        linha_grupos_btn = ttk.Frame(quadro_grupos)
        linha_grupos_btn.pack(fill="x", pady=(6, 0))
        lbl_resumo_grupos = ttk.Label(
            linha_grupos_btn,
            text="",
            style="Fraco.TLabel",
        )
        lbl_resumo_grupos.pack(side="left")

        def _atualizar_resumo_grupos():
            grupos_atuais = proj.listar_grupos_ordem(self.projeto)
            qtd_g = len(grupos_atuais)
            nomes_g = ", ".join("%s (%d)" % (g["nome"], len(g["fotos"])) for g in grupos_atuais[:4])
            if qtd_g > 4:
                nomes_g += "…"
            lbl_resumo_grupos.configure(
                text="Grupos configurados: %d — %s" % (qtd_g, nomes_g if nomes_g else "Grupo 1")
            )

        _atualizar_resumo_grupos()

        def _abrir_janela_grupos_daqui():
            def _depois_salvar_grupos():
                nonlocal todas_fotos, fotos_livres
                todas_fotos = proj.fotos_para_diagramar(self.projeto, self.info)
                fotos_livres = proj.fotos_para_diagramar(self.projeto, self.info, somente_nao_usadas=True)
                _atualizar_resumo_grupos()
                _atualizar_previa()

            self.abrir_ordem_grupos(ao_salvar=_depois_salvar_grupos, janela_pai=janela)

        ttk.Button(
            linha_grupos_btn,
            text="🗂 Organizar Ordem e Grupos de Fotos...",
            style="PrimarioCompacto.TButton",
            command=_abrir_janela_grupos_daqui,
        ).pack(side="right")

        # --- 3. Geometria Elástica, Limite de Corte e Layouts no Meio ---
        quadro_geom = ttk.LabelFrame(
            quadro,
            text="3. Proporção Elástica (22% vs Sem Corte) e Layouts no Meio (60/40 e 70/30)",
            padding=10,
        )
        quadro_geom.pack(fill="x", pady=(0, 8))

        modo_corte_var = tk.StringVar(value=str(cfg.get("modo_corte", "elastico")))
        corte_max_pct_inicial = int(round((float(cfg.get("corte_maximo", diagramar.CORTE_MAXIMO)) - 1.0) * 100))
        corte_max_pct_var = tk.IntVar(value=max(5, min(35, corte_max_pct_inicial or 22)))
        transpasse_var = tk.BooleanVar(value=bool(cfg.get("permitir_transpasse", True)))
        meio_termo_var = tk.BooleanVar(value=bool(cfg.get("permitir_meio_termo", True)))
        sangria_total_var = tk.BooleanVar(value=bool(cfg.get("permitir_sangria_total", True)))
        rostos_var = tk.BooleanVar(value=bool(cfg.get("reconhecer_rostos", True)))
        nao_cortar_pessoas_var = tk.BooleanVar(value=bool(cfg.get("nao_cortar_pessoas", True)))
        preencher_extras_var = tk.BooleanVar(value=bool(cfg.get("preencher_espacos_extras", False)))
        repetidas_var = tk.BooleanVar(value=bool(cfg.get("excluir_repetidas", False)))

        linha_modo_corte = ttk.Frame(quadro_geom)
        linha_modo_corte.pack(fill="x", pady=2)
        ttk.Radiobutton(
            linha_modo_corte,
            text="Preencher página (corte elástico até o limite + expansão vizinha):",
            value="elastico",
            variable=modo_corte_var,
        ).pack(side="left")
        ttk.Label(linha_modo_corte, text="% máx:").pack(side="right", padx=(4, 0))
        spin_corte_pct = ttk.Spinbox(
            linha_modo_corte,
            from_=5,
            to=35,
            increment=1,
            textvariable=corte_max_pct_var,
            width=5,
        )
        spin_corte_pct.pack(side="right")

        ttk.Radiobutton(
            quadro_geom,
            text="Preservar 100% da proporção original em todas as fotos (0% de corte forçado)",
            value="sem_corte",
            variable=modo_corte_var,
        ).pack(anchor="w", pady=2)

        ttk.Separator(quadro_geom, orient="horizontal").pack(fill="x", pady=6)

        ttk.Checkbutton(
            quadro_geom,
            text="Incluir layouts Meio Termo (1 página inteira sangrada + outra dentro da Safe Zone)",
            variable=meio_termo_var,
        ).pack(anchor="w", pady=2)
        ttk.Checkbutton(
            quadro_geom,
            text="Incluir variações de Preenchimento Máximo / Sangria Total (sem respeitar linhas da Safe Zone)",
            variable=sangria_total_var,
        ).pack(anchor="w", pady=2)
        ttk.Checkbutton(
            quadro_geom,
            text="Incluir layouts 60/40, 70/30 e panorâmicos que passam do meio da lâmina",
            variable=transpasse_var,
        ).pack(anchor="w", pady=2)
        ttk.Checkbutton(
            quadro_geom,
            text="Identificar quantos rostos há na imagem e ajustar para NÃO cortar nenhuma pessoa",
            variable=nao_cortar_pessoas_var,
        ).pack(anchor="w", pady=2)
        ttk.Checkbutton(
            quadro_geom,
            text="Desviar todos os rostos detectados automaticamente da linha de dobra central",
            variable=rostos_var,
        ).pack(anchor="w", pady=2)
        ttk.Checkbutton(
            quadro_geom,
            text="Sobrepor uma foto extra na lâmina sem repetir imagens",
            variable=preencher_extras_var,
        ).pack(anchor="w", pady=2)
        ttk.Checkbutton(
            quadro_geom,
            text="Excluir fotos visualmente repetidas (mantém apenas uma cópia)",
            variable=repetidas_var,
        ).pack(anchor="w", pady=2)

        # --- 4. Quantidade e Espaços ---
        numeros = ttk.LabelFrame(quadro, text="4. Quantidade de Lâminas, Fotos por Página e Margens", padding=10)
        numeros.pack(fill="x", pady=(0, 8))

        vars_num = {
            "laminas_desejadas": tk.IntVar(value=int(cfg.get("laminas_desejadas", self.produto["laminas_min"]))),
            "fotos_por_pagina": tk.IntVar(value=max(1, int(round(cfg["fotos_por_lamina"] / float(paginas))))),
            "max_fotos_pagina": tk.IntVar(
                value=max(1, min(diagramar.MAX_POR_PAGINA, int(cfg["max_fotos_lamina"]) // paginas))
            ),
            "margem_mm": tk.DoubleVar(value=float(cfg["margem_mm"])),
            "espacamento_mm": tk.DoubleVar(value=float(cfg["espacamento_mm"])),
            "auto_salvar_minutos": tk.IntVar(value=int(cfg.get("auto_salvar_minutos", 5))),
        }
        campos = (
            ("Lâminas desejadas", "laminas_desejadas", 1, self.produto["laminas_max"], 1),
            ("Fotos por página (meta ideal)", "fotos_por_pagina", 1, diagramar.MAX_POR_PAGINA, 1),
            ("Máximo de fotos por página", "max_fotos_pagina", 1, diagramar.MAX_POR_PAGINA, 1),
            ("Margem externa de segurança (mm)", "margem_mm", 0, 60, 1),
            ("Espaçamento entre fotos (mm)", "espacamento_mm", 0, 40, 0.5),
            ("Auto-salvamento do .album.json (min, 0 = desligado)", "auto_salvar_minutos", 0, 60, 1),
        )
        for texto, chave, minimo, maximo, passo in campos:
            linha = ttk.Frame(numeros)
            linha.pack(fill="x", pady=2)
            ttk.Label(linha, text=texto).pack(side="left")
            ttk.Spinbox(
                linha,
                from_=minimo,
                to=maximo,
                increment=passo,
                textvariable=vars_num[chave],
                width=8,
            ).pack(side="right")

        # --- 5. Prévia ao Vivo da Diagramação ---
        quadro_previa = ttk.LabelFrame(quadro, text="Prévia ao vivo do resultado", padding=10)
        quadro_previa.pack(fill="x", pady=(0, 6))
        lbl_previa_1 = ttk.Label(quadro_previa, text="", font=("Segoe UI", 9, "bold"), justify="left")
        lbl_previa_1.pack(anchor="w", fill="x")
        lbl_previa_2 = ttk.Label(quadro_previa, text="", style="Fraco.TLabel", justify="left")
        lbl_previa_2.pack(anchor="w", fill="x", pady=(2, 0))

        def _ajustar_wraps_diag(evento):
            larg = max(360, int(evento.width) - 48)
            lbl_previa_1.configure(wraplength=larg)
            lbl_previa_2.configure(wraplength=larg)

        quadro.bind("<Configure>", _ajustar_wraps_diag, add="+")

        def _obter_fotos_escopo():
            esc = escopo_var.get()
            if esc == "nao_usadas":
                return fotos_livres
            if esc == "pasta":
                ch_p = mapa_texto_pasta.get(pasta_escolhida_var.get())
                return proj.fotos_para_diagramar(self.projeto, self.info, pasta_alvo=ch_p) if ch_p else todas_fotos
            return todas_fotos

        def _montar_cfg_atual():
            try:
                f_pag = max(1, int(vars_num["fotos_por_pagina"].get()))
            except (tk.TclError, ValueError):
                f_pag = max(1, int(round(cfg["fotos_por_lamina"] / float(paginas))))
            try:
                max_pag = max(f_pag, int(vars_num["max_fotos_pagina"].get()))
            except (tk.TclError, ValueError):
                max_pag = max(f_pag, int(cfg["max_fotos_lamina"]) // paginas)
            try:
                lam_des = max(1, int(vars_num["laminas_desejadas"].get()))
            except (tk.TclError, ValueError):
                lam_des = int(self.produto["laminas_min"])
            try:
                marg = max(0.0, float(vars_num["margem_mm"].get()))
            except (tk.TclError, ValueError):
                marg = float(cfg["margem_mm"])
            try:
                esp = max(0.0, float(vars_num["espacamento_mm"].get()))
            except (tk.TclError, ValueError):
                esp = float(cfg["espacamento_mm"])
            try:
                pct_c = max(5, min(35, int(corte_max_pct_var.get())))
            except (tk.TclError, ValueError):
                pct_c = 22

            try:
                auto_min = max(0, min(60, int(vars_num["auto_salvar_minutos"].get())))
            except (tk.TclError, ValueError):
                auto_min = int(cfg.get("auto_salvar_minutos", 5))

            cfg_temp = dict(cfg)
            cfg_temp.update({
                "laminas_desejadas": lam_des,
                "fotos_por_lamina": f_pag * paginas,
                "max_fotos_pagina": max_pag,
                "max_fotos_lamina": max_pag * paginas,
                "margem_mm": marg,
                "espacamento_mm": esp,
                "auto_salvar_minutos": auto_min,
                "separar_pastas": bool(separar_pastas_var.get()),
                "respeitar_grupos_manuais": bool(respeitar_grupos_var.get()),
                "ordem_pessoas_grupo": str(ordem_pessoas_var.get() or "manter"),
                "modo_corte": modo_corte_var.get(),
                "corte_maximo": 1.0 + (pct_c / 100.0),
                "permitir_transpasse": bool(transpasse_var.get()),
                "permitir_meio_termo": bool(meio_termo_var.get()),
                "permitir_sangria_total": bool(sangria_total_var.get()),
                "reconhecer_rostos": bool(rostos_var.get() or nao_cortar_pessoas_var.get()),
                "nao_cortar_pessoas": bool(nao_cortar_pessoas_var.get()),
                "preencher_espacos_extras": bool(preencher_extras_var.get()),
                "excluir_repetidas": bool(repetidas_var.get()),
            })
            return cfg_temp

        def _atualizar_previa(*_args):
            fotos_esc = _obter_fotos_escopo()
            cfg_t = _montar_cfg_atual()
            esc = escopo_var.get()
            prod_p = dict(self.produto)
            if esc in ("pasta", "nao_usadas"):
                prod_p["laminas_min"] = 0
                cfg_t["laminas_desejadas"] = max(1, int(math.ceil(len(fotos_esc) / float(max(1, cfg_t["fotos_por_lamina"])))))
            prev = diagramar.previa_diagramacao(fotos_esc, prod_p, cfg_t)
            lbl_previa_1.configure(
                text="Fotos: %d (👤 %d ind. · 👥 %d grupo)  |  Grupos: %d  |  Pastas: %d  →  %d lâmina(s)"
                % (
                    prev["total_fotos"],
                    prev.get("fotos_individuais", 0),
                    prev.get("fotos_em_grupo", 0),
                    prev.get("grupos", prev.get("cenarios", 1)),
                    prev["pastas"],
                    prev["laminas_estimadas"],
                )
            )
            detalhes = "Média de %.1f fotos/lâmina (%d lâminas com fotos" % (
                prev["media_fotos_lamina"],
                prev["laminas_com_fotos"],
            )
            if prev["laminas_extras_vazias"] > 0:
                detalhes += " + %d vazia(s) p/ atingir a meta" % prev["laminas_extras_vazias"]
            detalhes += ")."
            if respeitar_grupos_var.get():
                detalhes += " Grupos numerados isolados (sem misturar grupos)."
            modo_p = ordem_pessoas_var.get()
            if modo_p == "individuais_primeiro":
                detalhes += " Ordem: Individuais primeiro em cada grupo."
            elif modo_p == "turma_primeiro":
                detalhes += " Ordem: Fotos em Grupo/Turma primeiro em cada grupo."
            if nao_cortar_pessoas_var.get():
                detalhes += " Proteção ativa: nenhuma pessoa será cortada."
            if prev["sobras"] > 0:
                detalhes += " Atenção: %d foto(s) excedem o limite máximo de lâminas." % prev["sobras"]
            lbl_previa_2.configure(text=detalhes)

        for v in (
            escopo_var,
            pasta_escolhida_var,
            separar_pastas_var,
            respeitar_grupos_var,
            ordem_pessoas_var,
            modo_corte_var,
            corte_max_pct_var,
            transpasse_var,
            rostos_var,
            nao_cortar_pessoas_var,
            preencher_extras_var,
            repetidas_var,
            *vars_num.values(),
        ):
            v.trace_add("write", _atualizar_previa)

        _atualizar_previa()

        botoes = ttk.Frame(rodape_fixo)
        botoes.pack(fill="x")

        def _calcular_laminas_ideais():
            fotos_esc = _obter_fotos_escopo()
            try:
                f_pag = max(1, int(vars_num["fotos_por_pagina"].get()))
            except (tk.TclError, ValueError):
                f_pag = 2
            por_lam = max(1, f_pag * paginas)
            ideal = max(self.produto["laminas_min"], int(math.ceil(len(fotos_esc) / float(por_lam))))
            ideal = min(self.produto["laminas_max"], ideal)
            vars_num["laminas_desejadas"].set(ideal)

        ttk.Button(
            botoes,
            text="Calcular nº ideal de lâminas",
            command=_calcular_laminas_ideais,
        ).pack(side="left")

        def iniciar():
            try:
                f_pag = int(vars_num["fotos_por_pagina"].get())
                max_pag = int(vars_num["max_fotos_pagina"].get())
                lam_des = int(vars_num["laminas_desejadas"].get())
                _m = float(vars_num["margem_mm"].get())
                _e = float(vars_num["espacamento_mm"].get())
            except (tk.TclError, ValueError):
                messagebox.showwarning("Diagramação", "Confira os valores numéricos informados.", parent=janela)
                return
            if max_pag < f_pag:
                messagebox.showwarning(
                    "Diagramação",
                    "O máximo de fotos por página deve ser igual ou maior que a quantidade desejada.",
                    parent=janela,
                )
                return
            esc = escopo_var.get()
            pasta_alvo_ch = mapa_texto_pasta.get(pasta_escolhida_var.get()) if esc == "pasta" else None
            fotos_esc = _obter_fotos_escopo()
            if not fotos_esc:
                messagebox.showinfo("Diagramação", "Não há fotos disponíveis para o escopo selecionado.", parent=janela)
                return
            if esc == "tudo" and any(e.get("foto") for e in proj.todos_espacos(self.projeto)):
                if not messagebox.askyesno(
                    "Refazer diagramação",
                    "As lâminas existentes serão substituídas pela nova diagramação. É possível desfazer depois (Ctrl+Z).",
                    parent=janela,
                ):
                    return
            cfg_novo = _montar_cfg_atual()
            cfg_novo["laminas_desejadas"] = lam_des
            try:
                janela.grab_release()
            except tk.TclError:
                pass
            janela.destroy()
            self.diagramar_tudo(
                confirmar=False,
                configuracao=cfg_novo,
                excluir_repetidas=bool(repetidas_var.get()),
                escopo=esc,
                pasta_alvo=pasta_alvo_ch,
            )

        ttk.Button(botoes, text="Cancelar", command=janela.destroy).pack(side="right", padx=(0, 10))
        ttk.Button(botoes, text="Diagramar Agora", style="Primario.TButton", command=iniciar).pack(
            side="right", padx=(0, 6)
        )
        janela.bind("<Escape>", lambda _e: janela.destroy())
        janela.bind("<Return>", lambda _e: iniciar())
        tema.configurar_janela_secundaria(
            janela,
            pai=self,
            chave="janela_diagramar",
            largura=720,
            altura=640,
            min_largura=490,
            min_altura=350,
        )
        janela.grab_set()
        janela.focus_set()

    def mover_fotos_para_grupo(self, fotos_caminhos, numero_grupo=None):
        """Move fotos selecionadas para um grupo numerado (ou cria um novo grupo se numero_grupo for None)."""
        if not fotos_caminhos:
            return
        grupos = proj.listar_grupos_ordem(self.projeto)
        chaves_alvo = {normalizar_caminho(c) for c in fotos_caminhos}
        fotos_extraidas = []
        for g in grupos:
            restantes = []
            for f in g["fotos"]:
                if normalizar_caminho(f["caminho"]) in chaves_alvo:
                    fotos_extraidas.append(f)
                else:
                    restantes.append(f)
            g["fotos"] = restantes

        if not fotos_extraidas:
            return

        if numero_grupo is None:
            novo_num = len(grupos) + 1
            grupos.append({
                "grupo_ordem": novo_num,
                "nome": "Grupo %d" % novo_num,
                "fotos": fotos_extraidas,
            })
        else:
            encontrado = False
            for g in grupos:
                if int(g.get("grupo_ordem", 1)) == int(numero_grupo):
                    g["fotos"].extend(fotos_extraidas)
                    encontrado = True
                    break
            if not encontrado:
                grupos.append({
                    "grupo_ordem": int(numero_grupo),
                    "nome": "Grupo %d" % int(numero_grupo),
                    "fotos": fotos_extraidas,
                })

        grupos_limpos = [g for g in grupos if g["fotos"]]
        if not grupos_limpos:
            return
        self.marcar_historico()
        proj.aplicar_grupos_ordem(self.projeto, grupos_limpos)
        self._marcar_modificado()
        self.atualizar_fotos()
        self.registrar("%d foto(s) atribuída(s) ao grupo de ordem (sem alterar arquivos no disco)." % len(fotos_extraidas))

    def definir_tipo_pessoas_fotos(self, fotos_caminhos, tipo_pessoas="auto"):
        """Define manualmente se as fotos selecionadas são 'individual', 'grupo' ou 'auto' (pelo nº de rostos)."""
        if not fotos_caminhos:
            return
        chaves = {normalizar_caminho(c) for c in fotos_caminhos}
        tp = str(tipo_pessoas or "auto").strip().lower()
        alteradas = 0
        self.marcar_historico()
        for f in self.projeto.get("fotos", []):
            if normalizar_caminho(f["caminho"]) in chaves:
                if tp in ("individual", "grupo"):
                    f["tipo_pessoas"] = tp
                else:
                    f.pop("tipo_pessoas", None)
                alteradas += 1
        if alteradas:
            self._marcar_modificado()
            self.atualizar_fotos()
            rotulo = (
                "Foto Individual (1 pessoa)"
                if tp == "individual"
                else ("Foto em Grupo / Turma (2+ pessoas)" if tp == "grupo" else "Automático (pelo nº de rostos)")
            )
            self.registrar("%d foto(s) marcada(s) como: %s." % (alteradas, rotulo))

    def abrir_ordem_grupos(self, ao_salvar=None, janela_pai=None):
        """
        Janela dedicada para definir a ordem das fotos e organizá-las em grupos numerados
        (Grupo 1, Grupo 2, Grupo 3...) sem renomear nem alterar os arquivos originais no PC.
        Suporta:
        - Fechar/recolher (▶) e abrir/expandir (▼) a aba de cada grupo para visualizar todos os grupos na tela
        - Soltar fotos diretamente sobre grupos recolhidos ou abertos
        - Atalho Ctrl + Scroll para aumentar/diminuir o tamanho das miniaturas
        - Classificação Individual (👤 1p) vs. Em Grupo/Turma (👥 2+p) automática e manual
        """
        if not self.projeto.get("fotos"):
            self.registrar("Adicione fotos ao projeto antes de organizar a ordem e os grupos.")
            return

        pai_win = janela_pai if (janela_pai is not None and janela_pai.winfo_exists()) else self.root
        janela = tk.Toplevel(pai_win)
        janela.title("Ordem das Fotos e Grupos Numerados · Smart Album")
        janela.configure(bg=tema.PAINEL)
        janela.transient(pai_win)

        tema.criar_cabecalho_janela(
            janela,
            "Ordem das Fotos e Grupos Numerados (Sem Alterar Arquivos)",
            "Clique em ▼/▶ no título de cada grupo para fechar/abrir a aba do grupo. Use Ctrl + Scroll para aumentar/diminuir as fotos.",
            icone="🗂",
        )

        # Estado de trabalho em memória (lista de dicts: {"nome": str, "fotos": [dict_foto, ...], "recolhido": bool})
        grupos_iniciais = proj.listar_grupos_ordem(self.projeto)
        estado_grupos = [
            {
                "nome": str(g.get("nome") or ("Grupo %d" % (idx + 1))),
                "fotos": [dict(f) for f in g.get("fotos", [])],
                "recolhido": False,
            }
            for idx, g in enumerate(grupos_iniciais)
        ]
        if not estado_grupos:
            estado_grupos = [{
                "nome": "Grupo 1",
                "fotos": [dict(f) for f in self.projeto.get("fotos", [])],
                "recolhido": False,
            }]

        grupo_ativo_idx = [0]
        fotos_sel_chaves = set()
        ultima_clicada_chave = [None]
        arrasto_interno = {"ativo": False, "chaves": [], "x0": 0, "y0": 0}
        escala_card = [112]  # largura do card (64 a 220 px), controlada via Ctrl + Scroll ou botões - / +
        cache_mini_grupos = OrderedDict()

        # Rodapé fixo
        rodape = tk.Frame(
            janela,
            bg=tema.CABECALHO,
            highlightthickness=1,
            highlightbackground=tema.BORDA,
            padx=14,
            pady=9,
        )
        rodape.pack(side="bottom", fill="x")

        # Barra de ações de topo (compacta para caber em 1440x900 e 1366x768)
        barra_acoes = tk.Frame(janela, bg=tema.PAINEL_2, padx=8, pady=6, highlightthickness=1, highlightbackground=tema.BORDA)
        barra_acoes.pack(side="top", fill="x")

        ttk.Button(
            barra_acoes,
            text="＋ Novo Grupo",
            style="PrimarioCompacto.TButton",
            command=lambda: _criar_novo_grupo(),
        ).pack(side="left", padx=(0, 2))
        ttk.Button(
            barra_acoes,
            text="✎ Renomear",
            style="Compacto.TButton",
            command=lambda: _renomear_grupo_ativo(),
        ).pack(side="left", padx=1)
        ttk.Button(
            barra_acoes,
            text="▲ Subir Grupo",
            style="Compacto.TButton",
            command=lambda: _mover_grupo(-1),
        ).pack(side="left", padx=1)
        ttk.Button(
            barra_acoes,
            text="▼ Descer Grupo",
            style="Compacto.TButton",
            command=lambda: _mover_grupo(1),
        ).pack(side="left", padx=1)
        ttk.Button(
            barra_acoes,
            text="🗑 Excluir/Mesclar",
            style="Compacto.TButton",
            command=lambda: _excluir_grupo_ativo(),
        ).pack(side="left", padx=1)

        tk.Frame(barra_acoes, bg=tema.BORDA, width=1, height=18).pack(side="left", padx=4)

        ttk.Button(
            barra_acoes,
            text="▶ Fechar Abas",
            style="Compacto.TButton",
            command=lambda: _alternar_todos_grupos(True),
        ).pack(side="left", padx=1)
        ttk.Button(
            barra_acoes,
            text="▼ Abrir Abas",
            style="Compacto.TButton",
            command=lambda: _alternar_todos_grupos(False),
        ).pack(side="left", padx=1)

        tk.Frame(barra_acoes, bg=tema.BORDA, width=1, height=18).pack(side="left", padx=4)

        ttk.Button(
            barra_acoes,
            text="📁 1 Grupo/Pasta",
            style="Compacto.TButton",
            command=lambda: _dividir_por_pastas(),
        ).pack(side="left", padx=1)
        ttk.Button(
            barra_acoes,
            text="↺ Tudo no Grupo 1",
            style="Compacto.TButton",
            command=lambda: _juntar_tudo(),
        ).pack(side="left", padx=1)

        lbl_status_topo = ttk.Label(barra_acoes, text="", style="Fraco.TLabel")
        lbl_status_topo.pack(side="right", padx=(4, 0))

        # Barra secundária de movimentação de fotos selecionadas, classificação Individual/Grupo e Zoom
        barra_fotos_op = tk.Frame(janela, bg=tema.PAINEL, padx=8, pady=5)
        barra_fotos_op.pack(side="top", fill="x")

        ttk.Label(barra_fotos_op, text="Ordem:", style="Fraco.TLabel").pack(side="left", padx=(0, 2))
        ttk.Button(
            barra_fotos_op,
            text="⏮",
            style="Compacto.TButton",
            command=lambda: _deslocar_fotos_no_grupo(-9999),
        ).pack(side="left", padx=1)
        ttk.Button(
            barra_fotos_op,
            text="◀",
            style="Compacto.TButton",
            command=lambda: _deslocar_fotos_no_grupo(-1),
        ).pack(side="left", padx=1)
        ttk.Button(
            barra_fotos_op,
            text="▶",
            style="Compacto.TButton",
            command=lambda: _deslocar_fotos_no_grupo(1),
        ).pack(side="left", padx=1)
        ttk.Button(
            barra_fotos_op,
            text="⏭",
            style="Compacto.TButton",
            command=lambda: _deslocar_fotos_no_grupo(9999),
        ).pack(side="left", padx=1)

        tk.Frame(barra_fotos_op, bg=tema.BORDA, width=1, height=18).pack(side="left", padx=4)
        ttk.Label(barra_fotos_op, text="Mover p/:", style="Fraco.TLabel").pack(side="left", padx=(0, 2))
        destino_grupo_var = tk.StringVar(value="")
        combo_destino = ttk.Combobox(barra_fotos_op, textvariable=destino_grupo_var, state="readonly", width=16)
        combo_destino.pack(side="left", padx=(0, 2))
        ttk.Button(
            barra_fotos_op,
            text="↪ Mover",
            style="Compacto.TButton",
            command=lambda: _mover_selecao_para_combo(),
        ).pack(side="left", padx=1)
        ttk.Button(
            barra_fotos_op,
            text="＋ Grupo c/ Sel.",
            style="Compacto.TButton",
            command=lambda: _criar_novo_grupo(com_selecao=True),
        ).pack(side="left", padx=1)

        tk.Frame(barra_fotos_op, bg=tema.BORDA, width=1, height=18).pack(side="left", padx=4)
        ttk.Label(barra_fotos_op, text="Tipo:", style="Fraco.TLabel").pack(side="left", padx=(0, 2))
        ttk.Button(
            barra_fotos_op,
            text="👤 1p",
            style="Compacto.TButton",
            command=lambda: _definir_tipo_selecao("individual"),
        ).pack(side="left", padx=1)
        ttk.Button(
            barra_fotos_op,
            text="👥 Grupo",
            style="Compacto.TButton",
            command=lambda: _definir_tipo_selecao("grupo"),
        ).pack(side="left", padx=1)
        ttk.Button(
            barra_fotos_op,
            text="Auto",
            style="Compacto.TButton",
            command=lambda: _definir_tipo_selecao("auto"),
        ).pack(side="left", padx=1)

        # Controles de Zoom à direita da barra secundária
        quadro_zoom = tk.Frame(barra_fotos_op, bg=tema.PAINEL)
        quadro_zoom.pack(side="right")
        ttk.Button(
            quadro_zoom,
            text="－",
            style="Compacto.TButton",
            command=lambda: _ajustar_zoom_miniaturas(-16),
        ).pack(side="left", padx=1)
        lbl_zoom_pct = ttk.Label(quadro_zoom, text="100%", width=5, anchor="center", style="Fraco.TLabel")
        lbl_zoom_pct.pack(side="left", padx=2)
        ttk.Button(
            quadro_zoom,
            text="＋",
            style="Compacto.TButton",
            command=lambda: _ajustar_zoom_miniaturas(16),
        ).pack(side="left", padx=1)

        # Canvas principal onde os grupos e suas fotos são desenhados e suportam drag-and-drop
        quadro_canvas = tk.Frame(janela, bg=tema.FUNDO, highlightthickness=1, highlightbackground=tema.BORDA)
        quadro_canvas.pack(side="top", fill="both", expand=True, padx=12, pady=(2, 8))

        barra_rol_v = ttk.Scrollbar(quadro_canvas, orient="vertical")
        barra_rol_v.pack(side="right", fill="y")
        canvas_g = tk.Canvas(
            quadro_canvas,
            bg=tema.FUNDO,
            highlightthickness=0,
            yscrollcommand=barra_rol_v.set,
        )
        canvas_g.pack(side="left", fill="both", expand=True)
        barra_rol_v.configure(command=canvas_g.yview)

        regioes_grupos = []   # [{"idx_g": int, "y0": int, "y1": int, "cab_y1": int}]
        regioes_fotos = []    # [{"idx_g": int, "idx_f": int, "chave": str, "x0": int, "y0": int, "x1": int, "y1": int}]

        CORES_GRUPOS = ("#2563eb", "#059669", "#d97706", "#7c3aed", "#db2777", "#0891b2", "#4f46e5", "#ea580c")

        def _miniatura_grupo_tk(caminho, lado_alvo):
            norm = normalizar_caminho(caminho)
            lado_limpo = max(44, min(200, int(lado_alvo)))
            chave_z = (norm, self.versao_fotos, lado_limpo)
            guardada = cache_mini_grupos.get(chave_z)
            if guardada is not None:
                cache_mini_grupos.move_to_end(chave_z)
                return guardada
            try:
                img_pil = self._miniatura_pil(caminho, lado_limpo, usada=False)
                foto_tk = ImageTk.PhotoImage(img_pil)
                cache_mini_grupos[chave_z] = foto_tk
                while len(cache_mini_grupos) > 600:
                    cache_mini_grupos.popitem(last=False)
                return foto_tk
            except Exception:
                return self._miniatura_tk(caminho, usada=False)

        def _info_pessoas_foto(f):
            ch_f = normalizar_caminho(f["caminho"])
            dados = self.info.get(ch_f) or {}
            rosto = f.get("rosto") or dados.get("rosto")
            qtd_r = max(1, int(rosto.get("qtd") or 1)) if isinstance(rosto, dict) else 0
            tp = str(f.get("tipo_pessoas") or "auto").strip().lower()
            em_grupo = proj.eh_foto_em_grupo(f, self.info)
            manual = tp in ("individual", "grupo")
            if em_grupo:
                etq = ("👥 %dp*" % max(2, qtd_r)) if (manual and qtd_r >= 2) else ("👥 2+p*" if manual else ("👥 %dp" % max(2, qtd_r)))
                cor_bg = "#5b21b6" if manual else "#3b0764"
                cor_fg = "#e9d5ff"
            else:
                etq = "👤 1p*" if manual else ("👤 %dp" % qtd_r if qtd_r == 1 else "👤 1p")
                cor_bg = "#1e3a8a" if manual else "#0e2942"
                cor_fg = "#93c5fd"
            return em_grupo, etq, cor_bg, cor_fg

        def _atualizar_combo_destino():
            opcoes = ["%d. %s (%d fotos)" % (i + 1, g["nome"], len(g["fotos"])) for i, g in enumerate(estado_grupos)]
            combo_destino["values"] = opcoes
            if opcoes:
                idx_at = min(len(opcoes) - 1, max(0, grupo_ativo_idx[0]))
                destino_grupo_var.set(opcoes[idx_at])

        def _redesenhar_canvas(_ev=None):
            if not canvas_g.winfo_exists():
                return
            canvas_g.delete("all")
            regioes_grupos.clear()
            regioes_fotos.clear()
            _atualizar_combo_destino()

            larg = max(560, canvas_g.winfo_width())
            margem_x = 12
            y_cursor = 10
            card_w = max(64, min(220, int(escala_card[0])))
            card_h = int(round(card_w * 0.88)) + 34
            lado_img = max(44, card_w - 14)
            esp_x = max(6, min(12, card_w // 12))
            esp_y = max(6, min(12, card_w // 12))
            colunas = max(2, (larg - margem_x * 2 - 20) // (card_w + esp_x))

            pct_z = int(round((card_w / 112.0) * 100))
            lbl_zoom_pct.configure(text="%d%%" % pct_z)

            total_fotos_proj = sum(len(g["fotos"]) for g in estado_grupos)
            recolhidos_qtd = sum(1 for g in estado_grupos if g.get("recolhido"))
            txt_rec = (" · %d fechado(s)" % recolhidos_qtd) if recolhidos_qtd else ""
            lbl_status_topo.configure(
                text="%d grupo(s)%s · %d foto(s) · %d sel."
                % (len(estado_grupos), txt_rec, total_fotos_proj, len(fotos_sel_chaves))
            )

            seq_global = 0
            for idx_g, g in enumerate(estado_grupos):
                cor_g = CORES_GRUPOS[idx_g % len(CORES_GRUPOS)]
                ativo = (idx_g == grupo_ativo_idx[0])
                recolhido = bool(g.get("recolhido", False))
                fotos_g = g["fotos"]
                qtd_em_grupo = sum(1 for f in fotos_g if proj.eh_foto_em_grupo(f, self.info))
                qtd_indiv = len(fotos_g) - qtd_em_grupo

                alt_cab = 36
                if recolhido:
                    altura_bloco = alt_cab
                elif not fotos_g:
                    altura_bloco = alt_cab + 52
                else:
                    linhas = max(1, int(math.ceil(len(fotos_g) / float(colunas))))
                    altura_bloco = alt_cab + 10 + linhas * (card_h + esp_y) + 6

                y0 = y_cursor
                y1 = y0 + altura_bloco

                canvas_g.create_rectangle(
                    margem_x,
                    y0,
                    larg - margem_x,
                    y1,
                    fill=tema.PAINEL if not ativo else "#1e2736",
                    outline=cor_g if ativo else tema.BORDA,
                    width=2 if ativo else 1,
                )
                # Cabeçalho clicável do grupo (permite recolher/expandir ou soltar fotos)
                canvas_g.create_rectangle(
                    margem_x,
                    y0,
                    larg - margem_x,
                    y0 + alt_cab,
                    fill=tema.PAINEL_2 if not recolhido else "#252932",
                    outline=cor_g if ativo else tema.BORDA,
                    width=1,
                )
                # Botão de recolher/expandir [▼] / [▶]
                bx0, by0, bx1, by1 = margem_x + 7, y0 + 6, margem_x + 33, y0 + 30
                canvas_g.create_rectangle(
                    bx0,
                    by0,
                    bx1,
                    by1,
                    fill=tema.PAINEL_3 if not ativo else cor_g,
                    outline=tema.BORDA_CLARA,
                    width=1,
                )
                canvas_g.create_text(
                    (bx0 + bx1) // 2,
                    (by0 + by1) // 2,
                    text="▶" if recolhido else "▼",
                    fill="#ffffff",
                    font=tema.FONTE_FORTE,
                )

                # Etiqueta colorida GRUPO N
                canvas_g.create_rectangle(
                    margem_x + 38,
                    y0 + 6,
                    margem_x + 118,
                    y0 + 30,
                    fill=cor_g,
                    outline="",
                )
                canvas_g.create_text(
                    margem_x + 78,
                    y0 + 18,
                    text="GRUPO %d" % (idx_g + 1),
                    fill="#ffffff",
                    font=tema.FONTE_FORTE,
                )

                resumo_g = "%s  —  %d foto(s) (👤 %d ind. · 👥 %d grupo)" % (
                    g["nome"],
                    len(fotos_g),
                    qtd_indiv,
                    qtd_em_grupo,
                )
                if recolhido:
                    resumo_g += "  ·  [Aba fechada — solte fotos aqui ou clique p/ abrir]"

                canvas_g.create_text(
                    margem_x + 128,
                    y0 + 18,
                    anchor="w",
                    text=resumo_g,
                    fill=tema.TEXTO if not recolhido else "#cbd5e1",
                    font=tema.FONTE_FORTE if ativo else tema.FONTE,
                )

                # Indicador à direita no cabeçalho
                txt_acao_cab = "▼ Abrir aba" if recolhido else "▲ Fechar aba"
                canvas_g.create_text(
                    larg - margem_x - 10,
                    y0 + 18,
                    anchor="e",
                    text=txt_acao_cab,
                    fill=tema.DESTAQUE if ativo else tema.TEXTO_FRACO,
                    font=tema.FONTE_PEQUENA,
                )

                regioes_grupos.append({
                    "idx_g": idx_g,
                    "y0": y0,
                    "y1": y1,
                    "cab_y0": y0,
                    "cab_y1": y0 + alt_cab,
                    "x0": margem_x,
                    "x1": larg - margem_x,
                })

                if recolhido:
                    seq_global += len(fotos_g)
                elif not fotos_g:
                    canvas_g.create_text(
                        larg // 2,
                        y0 + alt_cab + 26,
                        text="Arraste fotos para cá ou use '↪ Mover' para colocar fotos no Grupo %d" % (idx_g + 1),
                        fill=tema.TEXTO_FRACO,
                        font=tema.FONTE,
                    )
                else:
                    for idx_f, f in enumerate(fotos_g):
                        seq_global += 1
                        col = idx_f % colunas
                        lin = idx_f // colunas
                        fx0 = margem_x + 12 + col * (card_w + esp_x)
                        fy0 = y0 + alt_cab + 8 + lin * (card_h + esp_y)
                        fx1 = fx0 + card_w
                        fy1 = fy0 + card_h
                        ch_f = normalizar_caminho(f["caminho"])
                        sel = ch_f in fotos_sel_chaves

                        canvas_g.create_rectangle(
                            fx0,
                            fy0,
                            fx1,
                            fy1,
                            fill=tema.DESTAQUE_ESCURO if sel else tema.PAINEL_2,
                            outline=tema.DESTAQUE if sel else tema.BORDA,
                            width=2 if sel else 1,
                        )
                        img_tk = _miniatura_grupo_tk(f["caminho"], lado_img)
                        cx = (fx0 + fx1) // 2
                        cy = fy0 + 18 + (card_h - 46) // 2
                        if img_tk is not None:
                            canvas_g.create_image(cx, cy, image=img_tk)
                        else:
                            canvas_g.create_rectangle(
                                fx0 + 8,
                                fy0 + 20,
                                fx1 - 8,
                                fy1 - 26,
                                fill=tema.PAINEL_3,
                                outline="",
                            )
                            canvas_g.create_text(cx, cy, text="...", fill=tema.TEXTO_FRACO)

                        # Badge da ordem dentro do grupo (#1, #2...)
                        canvas_g.create_rectangle(fx0 + 3, fy0 + 3, fx0 + 44, fy0 + 18, fill=cor_g, outline="")
                        canvas_g.create_text(
                            fx0 + 23,
                            fy0 + 10,
                            text="%dº (G%d)" % (idx_f + 1, idx_g + 1),
                            fill="#ffffff",
                            font=("Segoe UI", 7, "bold"),
                        )

                        # Badge de Individual vs. Grupo no canto superior direito do card
                        _em_g, etq_p, cor_bg_p, cor_fg_p = _info_pessoas_foto(f)
                        larg_etq = max(34, min(52, len(etq_p) * 6 + 6))
                        canvas_g.create_rectangle(
                            fx1 - larg_etq - 3,
                            fy0 + 3,
                            fx1 - 3,
                            fy0 + 18,
                            fill=cor_bg_p,
                            outline="",
                        )
                        canvas_g.create_text(
                            fx1 - 3 - larg_etq // 2,
                            fy0 + 10,
                            text=etq_p,
                            fill=cor_fg_p,
                            font=("Segoe UI", 7, "bold"),
                        )

                        max_chars = max(8, (card_w - 10) // 6)
                        nome_arq = Path(f["caminho"]).name
                        curto = nome_arq if len(nome_arq) <= max_chars else nome_arq[: max_chars - 1] + "…"
                        canvas_g.create_text(
                            cx,
                            fy1 - 17,
                            text=curto,
                            fill=tema.TEXTO if sel else tema.TEXTO_FRACO,
                            font=tema.FONTE_PEQUENA,
                        )
                        nome_p = proj.nome_pasta_da_foto(f)
                        curto_p = nome_p if len(nome_p) <= max_chars else nome_p[: max_chars - 1] + "…"
                        canvas_g.create_text(
                            cx,
                            fy1 - 6,
                            text="📁 " + curto_p,
                            fill=tema.TEXTO_FRACO,
                            font=("Segoe UI", 7),
                        )

                        regioes_fotos.append({
                            "idx_g": idx_g,
                            "idx_f": idx_f,
                            "chave": ch_f,
                            "x0": fx0,
                            "y0": fy0,
                            "x1": fx1,
                            "y1": fy1,
                        })

                y_cursor = y1 + 10

            alt_canvas = max(320, canvas_g.winfo_height())
            canvas_g.configure(scrollregion=(0, 0, larg, max(alt_canvas, y_cursor + 16)))

        def _achar_foto_em(x_c, y_c):
            for r in regioes_fotos:
                if r["x0"] <= x_c <= r["x1"] and r["y0"] <= y_c <= r["y1"]:
                    return r
            return None

        def _achar_regiao_grupo_em(y_c):
            for rg in regioes_grupos:
                if rg["y0"] <= y_c <= rg["y1"]:
                    return rg
            return None

        def _achar_grupo_em(y_c):
            rg = _achar_regiao_grupo_em(y_c)
            if rg is not None:
                return rg["idx_g"]
            if regioes_grupos:
                if y_c < regioes_grupos[0]["y0"]:
                    return 0
                return regioes_grupos[-1]["idx_g"]
            return 0

        def _todas_chaves_ordenadas():
            saida = []
            for g in estado_grupos:
                for f in g["fotos"]:
                    saida.append(normalizar_caminho(f["caminho"]))
            return saida

        def _alternar_todos_grupos(recolher):
            for g in estado_grupos:
                g["recolhido"] = bool(recolher)
            _redesenhar_canvas()

        def _ajustar_zoom_miniaturas(delta_px):
            novo = max(64, min(220, int(escala_card[0]) + int(delta_px)))
            if novo != escala_card[0]:
                escala_card[0] = novo
                _redesenhar_canvas()

        def _definir_tipo_selecao(novo_tipo):
            if not fotos_sel_chaves:
                messagebox.showinfo(
                    "Classificação de Pessoas",
                    "Selecione uma ou mais fotos primeiro para definir como Individual (1p), Em Grupo (2+p) ou Automático.",
                    parent=janela,
                )
                return
            tp = str(novo_tipo or "auto").strip().lower()
            for g in estado_grupos:
                for f in g["fotos"]:
                    if normalizar_caminho(f["caminho"]) in fotos_sel_chaves:
                        if tp in ("individual", "grupo"):
                            f["tipo_pessoas"] = tp
                        else:
                            f.pop("tipo_pessoas", None)
            _redesenhar_canvas()

        def _ao_clicar_canvas(evento):
            canvas_g.focus_set()
            x_c = canvas_g.canvasx(evento.x)
            y_c = canvas_g.canvasy(evento.y)
            rf = _achar_foto_em(x_c, y_c)
            rg = _achar_regiao_grupo_em(y_c)
            rg_idx = rg["idx_g"] if rg is not None else _achar_grupo_em(y_c)
            grupo_ativo_idx[0] = rg_idx

            # Verifica se clicou no cabeçalho do grupo para recolher/expandir a aba
            if rf is None and rg is not None and rg["cab_y0"] <= y_c <= rg["cab_y1"]:
                # Clicar no botão ▼/▶ (esquerda), no texto "Fechar/Abrir aba" (direita) ou em um grupo já recolhido alterna a aba
                clicou_toggle = (x_c <= rg["x0"] + 40) or (x_c >= rg["x1"] - 115) or bool(estado_grupos[rg_idx].get("recolhido"))
                if clicou_toggle:
                    estado_grupos[rg_idx]["recolhido"] = not bool(estado_grupos[rg_idx].get("recolhido", False))
                _redesenhar_canvas()
                return

            if rf is None:
                if not (evento.state & 0x0004) and not (evento.state & 0x0001):
                    fotos_sel_chaves.clear()
                _redesenhar_canvas()
                return

            ch = rf["chave"]
            if evento.state & 0x0004:  # Ctrl
                if ch in fotos_sel_chaves:
                    fotos_sel_chaves.discard(ch)
                else:
                    fotos_sel_chaves.add(ch)
            elif evento.state & 0x0001 and ultima_clicada_chave[0]:  # Shift
                todas_ch = _todas_chaves_ordenadas()
                if ultima_clicada_chave[0] in todas_ch and ch in todas_ch:
                    i0, i1 = sorted((todas_ch.index(ultima_clicada_chave[0]), todas_ch.index(ch)))
                    for c_it in todas_ch[i0:i1 + 1]:
                        fotos_sel_chaves.add(c_it)
                else:
                    fotos_sel_chaves.add(ch)
            else:
                if ch not in fotos_sel_chaves:
                    fotos_sel_chaves.clear()
                    fotos_sel_chaves.add(ch)
            ultima_clicada_chave[0] = ch
            arrasto_interno["ativo"] = False
            arrasto_interno["chaves"] = [c for c in _todas_chaves_ordenadas() if c in fotos_sel_chaves]
            arrasto_interno["x0"] = evento.x
            arrasto_interno["y0"] = evento.y
            _redesenhar_canvas()

        def _ao_duplo_clique_canvas(evento):
            x_c = canvas_g.canvasx(evento.x)
            y_c = canvas_g.canvasy(evento.y)
            rf = _achar_foto_em(x_c, y_c)
            rg = _achar_regiao_grupo_em(y_c)
            if rf is None and rg is not None and rg["cab_y0"] <= y_c <= rg["cab_y1"]:
                idx_g = rg["idx_g"]
                estado_grupos[idx_g]["recolhido"] = not bool(estado_grupos[idx_g].get("recolhido", False))
                _redesenhar_canvas()

        def _ao_menu_contexto_canvas(evento):
            x_c = canvas_g.canvasx(evento.x)
            y_c = canvas_g.canvasy(evento.y)
            rf = _achar_foto_em(x_c, y_c)
            if rf is not None:
                ch = rf["chave"]
                if ch not in fotos_sel_chaves:
                    fotos_sel_chaves.clear()
                    fotos_sel_chaves.add(ch)
                    ultima_clicada_chave[0] = ch
                    _redesenhar_canvas()
            if not fotos_sel_chaves:
                return
            qtd_s = len(fotos_sel_chaves)
            menu = tk.Menu(janela, tearoff=0)
            menu.add_command(
                label="👤 Marcar %d foto(s) como Individual (1 pessoa)" % qtd_s,
                command=lambda: _definir_tipo_selecao("individual"),
            )
            menu.add_command(
                label="👥 Marcar %d foto(s) como Em Grupo / Turma (2+ pessoas)" % qtd_s,
                command=lambda: _definir_tipo_selecao("grupo"),
            )
            menu.add_command(
                label="↺ Usar detecção automática de rostos (%d foto(s))" % qtd_s,
                command=lambda: _definir_tipo_selecao("auto"),
            )
            menu.add_separator()
            for i_g, g_it in enumerate(estado_grupos):
                menu.add_command(
                    label="↪ Mover seleção para Grupo %d — %s" % (i_g + 1, g_it["nome"]),
                    command=lambda dest=i_g: _mover_selecao_para_indice(dest),
                )
            menu.add_separator()
            menu.add_command(
                label="＋ Criar Novo Grupo com a seleção",
                command=lambda: _criar_novo_grupo(com_selecao=True),
            )
            menu.tk_popup(evento.x_root, evento.y_root)

        def _ao_arrastar_canvas(evento):
            if not arrasto_interno["chaves"]:
                return
            if abs(evento.x - arrasto_interno["x0"]) + abs(evento.y - arrasto_interno["y0"]) >= 6:
                arrasto_interno["ativo"] = True
                canvas_g.configure(cursor="fleur")

        def _ao_soltar_canvas(evento):
            canvas_g.configure(cursor="")
            if not arrasto_interno["ativo"] or not arrasto_interno["chaves"]:
                arrasto_interno["ativo"] = False
                return
            arrasto_interno["ativo"] = False
            x_c = canvas_g.canvasx(evento.x)
            y_c = canvas_g.canvasy(evento.y)
            rf_alvo = _achar_foto_em(x_c, y_c)
            rg_destino = rf_alvo["idx_g"] if rf_alvo is not None else _achar_grupo_em(y_c)
            pos_destino = rf_alvo["idx_f"] if rf_alvo is not None else len(estado_grupos[rg_destino]["fotos"])

            chaves_mov = set(arrasto_interno["chaves"])
            extraidas = []
            for g in estado_grupos:
                novas_f = []
                for f in g["fotos"]:
                    if normalizar_caminho(f["caminho"]) in chaves_mov:
                        extraidas.append(f)
                    else:
                        novas_f.append(f)
                g["fotos"] = novas_f

            if not extraidas:
                return
            fotos_dest = estado_grupos[rg_destino]["fotos"]
            pos_inserir = max(0, min(len(fotos_dest), pos_destino))
            estado_grupos[rg_destino]["fotos"] = fotos_dest[:pos_inserir] + extraidas + fotos_dest[pos_inserir:]
            grupo_ativo_idx[0] = rg_destino
            _redesenhar_canvas()

        def _criar_novo_grupo(com_selecao=False):
            novo_num = len(estado_grupos) + 1
            extraidas = []
            if com_selecao and fotos_sel_chaves:
                for g in estado_grupos:
                    rest = []
                    for f in g["fotos"]:
                        if normalizar_caminho(f["caminho"]) in fotos_sel_chaves:
                            extraidas.append(f)
                        else:
                            rest.append(f)
                    g["fotos"] = rest
            estado_grupos.append({
                "nome": "Grupo %d" % novo_num,
                "fotos": extraidas,
                "recolhido": False,
            })
            grupo_ativo_idx[0] = len(estado_grupos) - 1
            _redesenhar_canvas()
            canvas_g.update_idletasks()
            canvas_g.yview_moveto(1.0)

        def _renomear_grupo_ativo():
            idx = grupo_ativo_idx[0]
            if not (0 <= idx < len(estado_grupos)):
                return
            atual_n = estado_grupos[idx]["nome"]
            novo_n = simpledialog.askstring(
                "Renomear Grupo %d" % (idx + 1),
                "Nome de identificação para o Grupo %d:" % (idx + 1),
                initialvalue=atual_n,
                parent=janela,
            )
            if novo_n and novo_n.strip():
                estado_grupos[idx]["nome"] = novo_n.strip()
                _redesenhar_canvas()

        def _mover_grupo(delta):
            idx = grupo_ativo_idx[0]
            novo_idx = idx + delta
            if not (0 <= idx < len(estado_grupos)) or not (0 <= novo_idx < len(estado_grupos)):
                return
            estado_grupos[idx], estado_grupos[novo_idx] = estado_grupos[novo_idx], estado_grupos[idx]
            grupo_ativo_idx[0] = novo_idx
            _redesenhar_canvas()

        def _excluir_grupo_ativo():
            if len(estado_grupos) <= 1:
                messagebox.showinfo("Grupos", "É necessário manter pelo menos 1 grupo.", parent=janela)
                return
            idx = grupo_ativo_idx[0]
            g_rem = estado_grupos.pop(idx)
            destino = max(0, idx - 1)
            if g_rem["fotos"]:
                estado_grupos[destino]["fotos"].extend(g_rem["fotos"])
            grupo_ativo_idx[0] = destino
            _redesenhar_canvas()

        def _dividir_por_pastas():
            todas = [f for g in estado_grupos for f in g["fotos"]]
            mapa_p = {}
            ordem_p = []
            for f in todas:
                ch_p = proj.pasta_da_foto(f)
                if ch_p not in mapa_p:
                    mapa_p[ch_p] = []
                    ordem_p.append((ch_p, proj.nome_pasta_da_foto(f)))
                mapa_p[ch_p].append(f)
            if not ordem_p:
                return
            estado_grupos.clear()
            for idx_p, (ch_p, nome_p) in enumerate(ordem_p):
                estado_grupos.append({
                    "nome": "Grupo %d — %s" % (idx_p + 1, nome_p),
                    "fotos": mapa_p[ch_p],
                    "recolhido": False,
                })
            grupo_ativo_idx[0] = 0
            _redesenhar_canvas()

        def _juntar_tudo():
            todas = [f for g in estado_grupos for f in g["fotos"]]
            estado_grupos.clear()
            estado_grupos.append({"nome": "Grupo 1", "fotos": todas, "recolhido": False})
            grupo_ativo_idx[0] = 0
            _redesenhar_canvas()

        def _mover_selecao_para_indice(idx_dest):
            if not fotos_sel_chaves or not (0 <= idx_dest < len(estado_grupos)):
                return
            extraidas = []
            for g in estado_grupos:
                rest = []
                for f in g["fotos"]:
                    if normalizar_caminho(f["caminho"]) in fotos_sel_chaves:
                        extraidas.append(f)
                    else:
                        rest.append(f)
                g["fotos"] = rest
            estado_grupos[idx_dest]["fotos"].extend(extraidas)
            grupo_ativo_idx[0] = idx_dest
            _redesenhar_canvas()

        def _mover_selecao_para_combo():
            if not fotos_sel_chaves:
                messagebox.showinfo("Mover Fotos", "Selecione uma ou mais fotos clicando nelas primeiro.", parent=janela)
                return
            opcoes = list(combo_destino["values"])
            escolha = destino_grupo_var.get()
            idx_dest = opcoes.index(escolha) if escolha in opcoes else grupo_ativo_idx[0]
            _mover_selecao_para_indice(idx_dest)

        def _deslocar_fotos_no_grupo(passo):
            if not fotos_sel_chaves:
                return
            for g in estado_grupos:
                lista = g["fotos"]
                n = len(lista)
                if n <= 1:
                    continue
                indices_sel = [i for i, f in enumerate(lista) if normalizar_caminho(f["caminho"]) in fotos_sel_chaves]
                if not indices_sel:
                    continue
                if passo <= -999:
                    sel_f = [lista[i] for i in indices_sel]
                    resto_f = [lista[i] for i in range(n) if i not in set(indices_sel)]
                    g["fotos"] = sel_f + resto_f
                elif passo >= 999:
                    sel_f = [lista[i] for i in indices_sel]
                    resto_f = [lista[i] for i in range(n) if i not in set(indices_sel)]
                    g["fotos"] = resto_f + sel_f
                elif passo < 0:
                    for i in indices_sel:
                        if i > 0 and normalizar_caminho(lista[i - 1]["caminho"]) not in fotos_sel_chaves:
                            lista[i - 1], lista[i] = lista[i], lista[i - 1]
                elif passo > 0:
                    for i in reversed(indices_sel):
                        if i < n - 1 and normalizar_caminho(lista[i + 1]["caminho"]) not in fotos_sel_chaves:
                            lista[i + 1], lista[i] = lista[i], lista[i + 1]
            _redesenhar_canvas()

        def _ao_roda_mouse_grupos(evento):
            # Se Ctrl estiver pressionado (bit 0x0004), faz zoom das miniaturas
            ctrl_ativo = bool(getattr(evento, "state", 0) & 0x0004)
            delta = getattr(evento, "delta", 0)
            num = getattr(evento, "num", None)
            if ctrl_ativo:
                if delta > 0 or num == 4:
                    _ajustar_zoom_miniaturas(14)
                elif delta < 0 or num == 5:
                    _ajustar_zoom_miniaturas(-14)
                return "break"
            if num == 4:
                canvas_g.yview_scroll(-3, "units")
            elif num == 5:
                canvas_g.yview_scroll(3, "units")
            elif delta:
                passos = -int(delta / 120) if abs(delta) >= 120 else (-1 if delta > 0 else 1)
                canvas_g.yview_scroll(passos * 2, "units")
            return "break"

        canvas_g.bind("<ButtonPress-1>", _ao_clicar_canvas)
        canvas_g.bind("<Double-Button-1>", _ao_duplo_clique_canvas)
        canvas_g.bind("<Button-3>", _ao_menu_contexto_canvas)
        canvas_g.bind("<B1-Motion>", _ao_arrastar_canvas)
        canvas_g.bind("<ButtonRelease-1>", _ao_soltar_canvas)
        canvas_g.bind("<Configure>", _redesenhar_canvas)
        canvas_g.bind("<MouseWheel>", _ao_roda_mouse_grupos)
        canvas_g.bind("<Control-MouseWheel>", lambda e: (_ajustar_zoom_miniaturas(14 if e.delta > 0 else -14), "break")[1])
        canvas_g.bind("<Button-4>", _ao_roda_mouse_grupos)
        canvas_g.bind("<Button-5>", _ao_roda_mouse_grupos)
        canvas_g.bind("<Control-Button-4>", lambda _e: (_ajustar_zoom_miniaturas(14), "break")[1])
        canvas_g.bind("<Control-Button-5>", lambda _e: (_ajustar_zoom_miniaturas(-14), "break")[1])

        def _salvar_grupos(diagramar_agora=False):
            grupos_validos = [g for g in estado_grupos if g["fotos"]]
            if not grupos_validos:
                grupos_validos = estado_grupos[:1]
            self.marcar_historico()
            proj.aplicar_grupos_ordem(self.projeto, grupos_validos)
            self.projeto.setdefault("config", {})["respeitar_grupos_manuais"] = True
            self.ordem_var.set("grupos")
            self._marcar_modificado()
            self.atualizar_fotos()
            self.registrar(
                "Ordem salva: %d grupo(s) numerado(s) (%d fotos). Arquivos originais intactos."
                % (len(grupos_validos), len(self.projeto.get("fotos", [])))
            )
            if callable(ao_salvar):
                try:
                    ao_salvar()
                except Exception:
                    pass
            try:
                janela.grab_release()
            except tk.TclError:
                pass
            janela.destroy()
            if diagramar_agora:
                self.diagramar_tudo(confirmar=True)

        ttk.Label(
            rodape,
            text="Dica: Clique em ▼/▶ para fechar/abrir grupos · Ctrl + Scroll muda o tamanho das miniaturas · Botão direito classifica 1p/Grupo.",
            style="Fraco.TLabel",
        ).pack(side="left")

        ttk.Button(rodape, text="Cancelar", command=janela.destroy).pack(side="right", padx=(0, 8))
        ttk.Button(
            rodape,
            text="✦ Salvar e Diagramar Agora",
            style="Primario.TButton",
            command=lambda: _salvar_grupos(diagramar_agora=True),
        ).pack(side="right", padx=(0, 6))
        ttk.Button(
            rodape,
            text="✓ Salvar Ordem e Grupos",
            style="Compacto.TButton",
            command=lambda: _salvar_grupos(diagramar_agora=False),
        ).pack(side="right", padx=(0, 6))

        janela.bind("<Escape>", lambda _e: janela.destroy())
        tema.configurar_janela_secundaria(
            janela,
            pai=self,
            chave="janela_ordem_grupos",
            largura=860,
            altura=620,
            min_largura=540,
            min_altura=380,
        )
        janela.grab_set()
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

    def diagramar_tudo(self, confirmar=True, configuracao=None, excluir_repetidas=False, escopo="tudo", pasta_alvo=None):
        somente_livres = escopo == "nao_usadas"
        fotos = proj.fotos_para_diagramar(
            self.projeto,
            self.info,
            somente_nao_usadas=somente_livres,
            pasta_alvo=pasta_alvo if escopo == "pasta" else None,
        )
        if not fotos:
            self.registrar("Não há fotos disponíveis para diagramar neste escopo.")
            return
        if confirmar and escopo == "tudo" and any(e.get("foto") for e in proj.todos_espacos(self.projeto)):
            if not messagebox.askyesno(
                "Diagramar",
                "Refazer o álbum inteiro? Os cortes e trocas feitos na mão serão perdidos (dá para desfazer).",
                parent=self.root,
            ):
                return
        produto = dict(self.produto)
        cfg = dict(self._cfg())
        if configuracao:
            cfg.update(configuracao)
        if escopo in ("pasta", "nao_usadas"):
            produto["laminas_min"] = 0
            cfg["laminas_desejadas"] = max(
                1, int(math.ceil(len(fotos) / float(max(1, int(cfg.get("fotos_por_lamina", 4))))))
            )
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
            (novas_laminas, sobras), removidas = resultado
            self.marcar_historico()
            if configuracao:
                cfg_salvar = dict(configuracao)
                if escopo in ("pasta", "nao_usadas") and "laminas_desejadas" in self.projeto.get("config", {}):
                    cfg_salvar["laminas_desejadas"] = self.projeto["config"]["laminas_desejadas"]
                self.projeto.setdefault("config", {}).update(cfg_salvar)

            if escopo == "nao_usadas":
                self.projeto["laminas"].extend(novas_laminas)
                self.atual = max(0, len(self.projeto["laminas"]) - len(novas_laminas))
                texto = "%s lâmina(s) adicionada(s) com as fotos não usadas." % len(novas_laminas)
            elif escopo == "pasta" and pasta_alvo:
                ch_alvo = normalizar_caminho(pasta_alvo)
                caminhos_pasta = {
                    normalizar_caminho(f["caminho"])
                    for f in self.projeto.get("fotos", [])
                    if proj.pertence_a_pasta(proj.pasta_da_foto(f), ch_alvo)
                }
                atuais = self.projeto.get("laminas", [])
                pos_insercao = None
                mantidas = []
                for idx_l, lam in enumerate(atuais):
                    fotos_lam = [normalizar_caminho(e["foto"]) for e in lam.get("espacos", []) if e.get("foto")]
                    pertence = (lam.get("pasta") and proj.pertence_a_pasta(lam["pasta"], ch_alvo)) or (
                        fotos_lam and all(c in caminhos_pasta for c in fotos_lam)
                    )
                    if pertence:
                        if pos_insercao is None:
                            pos_insercao = len(mantidas)
                    else:
                        mantidas.append(lam)
                if pos_insercao is None:
                    pos_insercao = len(mantidas)
                self.projeto["laminas"] = mantidas[:pos_insercao] + novas_laminas + mantidas[pos_insercao:]
                self.atual = min(pos_insercao, max(0, len(self.projeto["laminas"]) - 1))
                texto = "Pasta rediagramada: %s lâmina(s) atualizada(s)." % len(novas_laminas)
            else:
                self.projeto["laminas"] = novas_laminas
                self.atual = 0
                texto = "Álbum diagramado: %s lâminas." % len(novas_laminas)

            self.espaco_sel = None
            if hasattr(self, "espacos_sel"):
                self.espacos_sel.clear()
            if removidas:
                texto += " %s foto(s) repetida(s) ignorada(s)." % len(removidas)
            self.registrar(texto)
            if sobras:
                messagebox.showinfo(
                    "Diagramar",
                    "%s foto(s) não couberam no máximo de %s lâminas do produto."
                    % (len(sobras), self.produto["laminas_max"]),
                    parent=self.root,
                )
            self.atualizar_tudo()

        self.executar("Diagramando", trabalho, fim)

    def distribuir_nao_usadas(self):
        self.diagramar_tudo(confirmar=False, escopo="nao_usadas")

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
        ch_l, nm_l, _dist = proj.pasta_da_lamina(self.projeto, lamina)
        if ch_l:
            lamina["pasta"] = ch_l
            lamina["nome_pasta"] = nm_l

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
        total = len(self.projeto.get("laminas", []))
        selecionadas = sorted(
            i for i in getattr(self, "laminas_sel", set())
            if isinstance(i, int) and 0 <= i < total
        )
        if len(selecionadas) > 1:
            self.marcar_historico()
            for idx_l in selecionadas:
                self.relayout(idx_l, marcar=False)
            self.espaco_sel = None
            self.espacos_sel.clear()
            self.atualizar_tudo()
            self.registrar("%d lâminas selecionadas foram rediagramadas." % len(selecionadas))
            return
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
            cfg = {
                "margem_mm": max(0.0, float(self.margem_var.get())),
                "espacamento_mm": max(0.0, float(self.espacamento_var.get())),
                "fotos_por_lamina": max(1, int(self.fotos_lamina_var.get())),
                "auto_salvar_minutos": max(0, int(self.auto_salvar_var.get())),
            }
        except (tk.TclError, ValueError):
            return
        atual = self._cfg()
        mudou_auto = int(cfg["auto_salvar_minutos"]) != int(atual.get("auto_salvar_minutos", 5))
        mudou_geometria = (
            abs(float(cfg["margem_mm"]) - float(atual.get("margem_mm", 0))) > 1e-6
            or abs(float(cfg["espacamento_mm"]) - float(atual.get("espacamento_mm", 0))) > 1e-6
        )
        if any(abs(float(cfg[k]) - float(atual.get(k, 0))) > 1e-9 for k in cfg):
            self.projeto.setdefault("config", {}).update(cfg)
            self._marcar_modificado()
            if mudou_auto:
                self._reagendar_auto_salvar()
                if cfg["auto_salvar_minutos"] > 0:
                    self.registrar("Auto-salvamento do .album.json definido para cada %d min." % cfg["auto_salvar_minutos"])
                else:
                    self.registrar("Auto-salvamento do .album.json desativado.")
            if mudou_geometria:
                lamina = self._lamina()
                if lamina is not None and not self.editar_layout.get() and lamina.get("espacos"):
                    diagramar.ajustar_espacos_lamina(lamina, self._mapa_proporcoes(lamina), self.produto, self._cfg())
                self.desenhar_lamina()
                self.agendar("config_timeline", 180, self.desenhar_timeline)
            self.atualizar_sugestoes()

    def _alternou_protecao_rostos(self):
        if getattr(self, "_carregando", False):
            return
        ativo = bool(self.proteger_rostos_var.get())
        cfg_proj = self.projeto.setdefault("config", {})
        cfg_proj["reconhecer_rostos"] = ativo
        cfg_proj["nao_cortar_pessoas"] = ativo
        self._chave_cache_sugestoes = None
        self._chave_cache_modelos_pag = None
        self._marcar_modificado()
        lamina = self._lamina()
        if lamina is not None and any(e.get("foto") for e in lamina.get("espacos", [])):
            for e in lamina.get("espacos", []):
                ed = e.setdefault("edicao", {})
                ed["ignorar_rosto"] = not ativo
                ed.pop("corte", None)
            if ativo:
                fotos_l = self._fotos_info([e["foto"] for e in lamina["espacos"] if e.get("foto")], lamina)
                esp_seg, _pen = diagramar._proteger_espacos_contra_corte_pessoas(
                    lamina["espacos"], fotos_l, self.produto, self._cfg()
                )
                lamina["espacos"] = esp_seg
        self._cache_laminas.clear()
        self.atualizar_lamina_atual()
        self.atualizar_sugestoes()
        self.registrar(
            "Identificação de rostos ativada: quadros e enquadramentos ajustados para não cortar nenhuma pessoa."
            if ativo
            else "Proteção automática de rostos desativada na lâmina atual."
        )

    def _alternou_separar_pastas(self):
        if getattr(self, "_carregando", False):
            return
        ativo = bool(self.separar_pastas_var.get())
        cfg_proj = self.projeto.setdefault("config", {})
        cfg_proj["separar_pastas"] = ativo
        self._marcar_modificado()
        if ativo:
            misturadas = proj.laminas_com_pastas_misturadas(self.projeto)
            if misturadas:
                self.separar_laminas_por_pasta()
                return
            self.registrar("Separação por pastas carregadas ATIVADA: fotos de pastas diferentes não se misturam.")
        else:
            self.registrar("Separação estrita por pastas carregadas desativada.")
        self.atualizar_fotos()
        self.desenhar_timeline()

    def separar_laminas_por_pasta(self):
        """
        Garante que nenhuma lâmina misture fotos de pastas carregadas diferentes.
        Se houver lâminas misturadas, divide cada pasta em sua própria lâmina; se houver fotos livres
        ou múltiplas pastas carregadas sem lâminas, diagrama separando por pasta.
        """
        cfg_proj = self.projeto.setdefault("config", {})
        cfg_proj["separar_pastas"] = True
        if hasattr(self, "separar_pastas_var"):
            self._carregando = True
            self.separar_pastas_var.set(True)
            self._carregando = False

        laminas_atuais = self.projeto.get("laminas", [])
        if not laminas_atuais:
            if self.projeto.get("fotos"):
                self.diagramar_tudo(confirmar=False, escopo="tudo")
            else:
                self.registrar("Carregue uma ou mais pastas de fotos primeiro.")
            return

        misturadas = proj.laminas_com_pastas_misturadas(self.projeto)
        if not misturadas:
            # Verifica se há mais de uma pasta carregada e oferece rediagramar mantendo cada pasta em seu bloco
            pastas = proj.listar_pastas_projeto(self.projeto)
            for lam in laminas_atuais:
                ch_l, nm_l, _dist = proj.pasta_da_lamina(self.projeto, lam)
                if ch_l:
                    lam["pasta"] = ch_l
                    lam["nome_pasta"] = nm_l
            self.desenhar_timeline()
            self.atualizar_fotos()
            if len(pastas) > 1:
                self.registrar("As %d lâminas já estão 100%% separadas nas %d pastas carregadas." % (len(laminas_atuais), len(pastas)))
            else:
                self.registrar("Separação por pastas ativa (%d pasta carregada)." % max(1, len(pastas)))
            return

        self.marcar_historico()
        novas_laminas = []
        cfg = self._cfg()
        modelos = self._modelos()
        laminas_divididas = 0
        for lam in laminas_atuais:
            ch_l, nm_l, distintas = proj.pasta_da_lamina(self.projeto, lam)
            if len(distintas) <= 1:
                if ch_l:
                    lam["pasta"] = ch_l
                    lam["nome_pasta"] = nm_l
                novas_laminas.append(lam)
                continue
            laminas_divididas += 1
            # Separa as fotos desta lâmina por pasta carregada na ordem em que aparecem
            por_pasta = OrderedDict()
            for esp in lam.get("espacos", []):
                cam = esp.get("foto")
                if not cam:
                    continue
                reg = proj.registro_foto(self.projeto, cam)
                ch_p = normalizar_caminho(proj.pasta_da_foto(reg if reg else cam))
                nm_p = proj.nome_pasta_da_foto(reg if reg else cam)
                por_pasta.setdefault(ch_p, (nm_p, []))[1].append(cam)
            for ch_p, (nm_p, cams_p) in por_pasta.items():
                infos_p = self._fotos_info(cams_p, lam)
                if not infos_p:
                    continue
                melhor = diagramar.opcoes_lamina(infos_p, self.produto, cfg, modelos, maximo=1)[0]
                nova_l = diagramar.lamina_vazia(cfg)
                nova_l["fundo"] = lam.get("fundo", cfg.get("fundo", "#ffffff"))
                if lam.get("fundo_imagem"):
                    nova_l["fundo_imagem"] = copy.deepcopy(lam["fundo_imagem"])
                self._aplicar_opcao(nova_l, melhor)
                nova_l["pasta"] = ch_p
                nova_l["nome_pasta"] = nm_p
                novas_laminas.append(nova_l)

        self.projeto["laminas"] = novas_laminas
        self.atual = min(self.atual if isinstance(self.atual, int) else 0, max(0, len(novas_laminas) - 1))
        self.laminas_sel = {self.atual}
        self._cache_laminas.clear()
        self.atualizar_tudo()
        self.registrar(
            "%d lâmina(s) que misturavam pastas foram separadas em lâminas exclusivas por pasta (%d lâminas no total)."
            % (laminas_divididas, len(novas_laminas))
        )

    def diagramar_pasta_selecionada(self, ch_pasta=None):
        """Diagrama somente uma pasta carregada específica em lâminas exclusivas."""
        alvo = ch_pasta or (self.pasta_filtro.get() if hasattr(self, "pasta_filtro") else "todas")
        if not alvo or alvo == "todas":
            pastas = proj.listar_pastas_projeto(self.projeto)
            if len(pastas) == 1:
                alvo = pastas[0][0]
            else:
                self.abrir_diagramacao()
                return
        self.diagramar_tudo(confirmar=False, escopo="pasta", pasta_alvo=alvo)

    def selecionar_laminas_da_pasta(self, ch_pasta):
        """Seleciona na linha do tempo todas as lâminas que pertencem à pasta carregada `ch_pasta`."""
        alvo = normalizar_caminho(ch_pasta)
        indices = set()
        nome_p = Path(ch_pasta).name or "Pasta"
        for idx, lam in enumerate(self.projeto.get("laminas", [])):
            ch_l, nm_l, distintas = proj.pasta_da_lamina(self.projeto, lam)
            if ch_l == alvo or alvo in distintas:
                indices.add(idx)
                if nm_l:
                    nome_p = nm_l
        if not indices:
            self.registrar("Nenhuma lâmina encontrada com fotos da pasta '%s'." % nome_p)
            return
        ordenados = sorted(indices)
        self.atual = ordenados[0]
        self.laminas_sel = indices
        self.ultima_lamina_clicada = ordenados[0]
        self.espaco_sel = None
        self.atualizar_tudo()
        self._mostrar_na_timeline(self.atual)
        self.registrar("%d lâmina(s) da pasta '%s' selecionada(s) em grupo." % (len(indices), nome_p))

    def remover_pasta_carregada(self, ch_pasta):
        """Remove do projeto todas as fotos e lâminas exclusivas de uma pasta carregada."""
        nome_p = Path(ch_pasta).name or str(ch_pasta)
        pastas = proj.listar_pastas_projeto(self.projeto)
        qtd_fotos = next((q for ch, _p, _n, q in pastas if ch == normalizar_caminho(ch_pasta)), 0)
        if not messagebox.askyesno(
            "Remover Pasta do Projeto",
            "Deseja remover a pasta '%s' (%d foto(s)) e suas lâminas exclusivas do projeto?\n(Os arquivos originais no computador não serão apagados.)"
            % (nome_p, qtd_fotos),
            parent=self.root,
        ):
            return
        self.marcar_historico()
        n_fotos, n_lams = proj.remover_pasta_projeto(self.projeto, ch_pasta, remover_laminas_da_pasta=True)
        if hasattr(self, "pasta_filtro") and normalizar_caminho(self.pasta_filtro.get()) == normalizar_caminho(ch_pasta):
            self.pasta_filtro.set("todas")
        self.pastas_recolhidas.discard(normalizar_caminho(ch_pasta))
        self.pastas_recolhidas.discard(ch_pasta)
        self._cache_laminas.clear()
        self.atualizar_tudo()
        self.registrar("Pasta '%s' removida do projeto (%d foto(s) e %d lâmina(s) removida(s))." % (nome_p, n_fotos, n_lams))

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
        ch_l, nm_l, _dist = proj.pasta_da_lamina(self.projeto, lamina)
        if ch_l:
            lamina["pasta"] = ch_l
            lamina["nome_pasta"] = nm_l
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
        # Não recalcula o layout da lâmina ao trocar duas fotos da mesma lâmina (mantém os quadros intactos)
        if not self.editar_layout.get() and lb is not la:
            diagramar.ajustar_espacos_lamina(la, self._mapa_proporcoes(la), self.produto, self._cfg())
            diagramar.ajustar_espacos_lamina(lb, self._mapa_proporcoes(lb), self.produto, self._cfg())
        self.atual, self.espaco_sel = lj, b
        self.atualizar_tudo()
        if antes is not None:
            self._animar_transicao_layout(antes, lb.get("espacos", []))

    def tirar_foto(self, li, ei, marcar=True):
        """Remove a foto da lâmina `li` e reajusta as fotos restantes na mesma lâmina sem mudar de lâmina."""
        lamina = self._lamina(li)
        if lamina is None or not (0 <= ei < len(lamina["espacos"])):
            return
        lamina_aberta = self.atual
        antes = [dict(e) for e in lamina.get("espacos", [])] if li == lamina_aberta else None
        if marcar:
            self.marcar_historico()
        if len(lamina["espacos"]) > 1 and not self.editar_layout.get():
            del lamina["espacos"][ei]
            lamina["modelo"] = None
            diagramar.ajustar_espacos_lamina(lamina, self._mapa_proporcoes(lamina), self.produto, self._cfg())
        else:
            lamina["espacos"][ei]["foto"] = None
            lamina["espacos"][ei]["edicao"] = {}
        self.atual = lamina_aberta
        self.espaco_sel = None
        self.atualizar_lamina_atual()
        if antes is not None and lamina.get("espacos"):
            self._animar_transicao_layout(antes, lamina.get("espacos", []))

    def tirar_foto_selecionada(self):
        if isinstance(self.atual, int) and self.espaco_sel is not None:
            self.tirar_foto(self.atual, self.espaco_sel)

    def apagar_espaco(self, li, ei):
        """Remove o quadro `ei` da lâmina `li`, reajusta as fotos restantes e permanece na mesma lâmina."""
        lamina = self._lamina(li)
        if lamina is None or not (0 <= ei < len(lamina["espacos"])):
            return
        lamina_aberta = self.atual
        antes = [dict(e) for e in lamina.get("espacos", [])] if li == lamina_aberta else None
        self.marcar_historico()
        del lamina["espacos"][ei]
        lamina["modelo"] = None
        if lamina["espacos"]:
            diagramar.ajustar_espacos_lamina(lamina, self._mapa_proporcoes(lamina), self.produto, self._cfg())
        self.atual = lamina_aberta
        self.espaco_sel = None
        self.atualizar_lamina_atual()
        if antes is not None and lamina.get("espacos"):
            self._animar_transicao_layout(antes, lamina.get("espacos", []))

    def adicionar_lamina(self):
        self.marcar_historico()
        posicao = (self.atual + 1) if isinstance(self.atual, int) else 0
        self.projeto["laminas"].insert(posicao, diagramar.lamina_vazia(self._cfg()))
        self.atual, self.espaco_sel = posicao, None
        self.atualizar_tudo()

    def remover_lamina(self, indice=None):
        """
        Remove a lâmina indicada ou todas as lâminas selecionadas em grupo (self.laminas_sel) de uma só vez.
        """
        total = len(self.projeto.get("laminas", []))
        if total == 0:
            return
        selecionadas = sorted(
            i for i in getattr(self, "laminas_sel", set())
            if isinstance(i, int) and 0 <= i < total
        )
        if indice is not None and isinstance(indice, int) and 0 <= indice < total:
            if indice in selecionadas and len(selecionadas) > 1:
                alvos = selecionadas
            else:
                alvos = [indice]
        elif selecionadas:
            alvos = selecionadas
        elif isinstance(self.atual, int) and 0 <= self.atual < total:
            alvos = [self.atual]
        else:
            return

        self.marcar_historico()
        for idx_del in reversed(alvos):
            del self.projeto["laminas"][idx_del]

        restantes = len(self.projeto["laminas"])
        if restantes > 0:
            novo_atual = max(0, min(alvos[0], restantes - 1))
            self.atual = novo_atual
            self.laminas_sel = {novo_atual}
            self.ultima_lamina_clicada = novo_atual
        else:
            self.atual = 0
            self.laminas_sel = set()
            self.ultima_lamina_clicada = None
        self.espaco_sel = None
        self.espacos_sel.clear()
        self.atualizar_tudo()
        if len(alvos) > 1:
            self.registrar("%d lâminas selecionadas foram excluídas de uma vez." % len(alvos))
        else:
            self.registrar("Lâmina %d excluída." % (alvos[0] + 1))

    def mover_lamina(self, de, para):
        laminas = self.projeto["laminas"]
        if de == para or not (0 <= de < len(laminas)) or not (0 <= para < len(laminas)):
            return
        self.marcar_historico()
        laminas.insert(para, laminas.pop(de))
        self.atual, self.espaco_sel = para, None
        self.laminas_sel = {para}
        self.ultima_lamina_clicada = para
        self.atualizar_tudo()
        self._mostrar_na_timeline(para)
        self.registrar("Lâmina movida para a posição %s de %s." % (para + 1, len(laminas)))

    def mover_lamina_passo(self, passo):
        total = len(self.projeto.get("laminas", []))
        selecionadas = sorted(
            i for i in getattr(self, "laminas_sel", set())
            if isinstance(i, int) and 0 <= i < total
        )
        if len(selecionadas) > 1 and passo in (-1, 1):
            if (passo < 0 and selecionadas[0] == 0) or (passo > 0 and selecionadas[-1] >= total - 1):
                return
            self.marcar_historico()
            laminas = self.projeto["laminas"]
            novas_sel = set()
            ordem = selecionadas if passo < 0 else list(reversed(selecionadas))
            for idx_l in ordem:
                destino = idx_l + passo
                laminas.insert(destino, laminas.pop(idx_l))
                novas_sel.add(destino)
            self.laminas_sel = novas_sel
            if isinstance(self.atual, int):
                self.atual = max(0, min(total - 1, self.atual + passo))
            self.espaco_sel = None
            self.atualizar_tudo()
            self._mostrar_na_timeline(self.atual)
            self.registrar("%d lâminas selecionadas movidas em grupo." % len(novas_sel))
            return
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
        total = len(self.projeto.get("laminas", []))
        selecionadas = sorted(
            i for i in getattr(self, "laminas_sel", set())
            if isinstance(i, int) and 0 <= i < total
        )
        if len(selecionadas) > 1 and (indice is None or indice in selecionadas):
            self.marcar_historico()
            pos_insercao = selecionadas[-1] + 1
            novas_sel = set()
            for offset, idx_orig in enumerate(selecionadas):
                nova = proj.copia(self.projeto["laminas"][idx_orig])
                nova["id"] = diagramar.novo_id()
                self.projeto["laminas"].insert(pos_insercao + offset, nova)
                novas_sel.add(pos_insercao + offset)
            self.atual = pos_insercao
            self.laminas_sel = novas_sel
            self.ultima_lamina_clicada = pos_insercao
            self.espaco_sel = None
            self.atualizar_tudo()
            self._mostrar_na_timeline(self.atual)
            self.registrar("%d lâminas duplicadas em grupo." % len(selecionadas))
            return

        indice = self.atual if indice is None else indice
        lamina = self._lamina(indice) if isinstance(indice, int) else None
        if lamina is None:
            return
        self.marcar_historico()
        nova = proj.copia(lamina)
        nova["id"] = diagramar.novo_id()
        self.projeto["laminas"].insert(indice + 1, nova)
        self.atual, self.espaco_sel = indice + 1, None
        self.laminas_sel = {indice + 1}
        self.ultima_lamina_clicada = indice + 1
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

    def _particionar_espacos_pagina(self, lamina, pagina):
        """
        Divide os espaços da lâmina entre:
        - `saem`: espaços exclusivos da `pagina` (0 = Esquerda, 1 = Direita) que serão diagramados.
        - `ficam`: espaços da outra página (`1 - pagina`) E fotos que passam do meio da página (`x0 < 0.495 and x1 > 0.505`).
          Quando uma foto passa do meio, ela permanece intacta em `ficam` e o sistema diagrama apenas
          as demais fotos no espaço livre restante da página!
        """
        if self.produto.get("paginas_por_lamina", 1) < 2:
            return list(lamina.get("espacos", [])), []
        preservar_atravessadas = bool(self.so_compativeis.get()) if hasattr(self, "so_compativeis") else True
        saem, ficam = [], []
        for e in lamina.get("espacos", []):
            x0 = float(e.get("x", 0.0))
            x1 = x0 + float(e.get("w", 0.0))
            atravessa = x0 < 0.495 and x1 > 0.505
            if atravessa:
                if preservar_atravessadas:
                    # Mantém intacta a foto que passou do meio; o restante da página será diagramado ao redor dela
                    ficam.append(e)
                else:
                    saem.append(e)
            elif self._pagina_do_espaco(e) == pagina:
                saem.append(e)
            else:
                ficam.append(e)
        return saem, ficam

    def _mudar_qtd_fotos_pagina(self, delta):
        """Adiciona ou remove 1 espaço na página selecionada (Esquerda ou Direita) sem alterar a outra página nem a foto que passou do meio."""
        lamina = self._lamina()
        if lamina is None:
            return
        pagina = self.pagina_modelo.get() if self.produto.get("paginas_por_lamina", 1) > 1 else 0
        saem, ficam = self._particionar_espacos_pagina(lamina, pagina)
        if delta < 0 and len(saem) <= 1:
            self.registrar("A área restante da página precisa ter pelo menos 1 espaço.")
            return
        antes = [dict(e) for e in lamina.get("espacos", [])]
        self.marcar_historico()
        fotos_atuais = []
        for e in saem:
            if e.get("foto"):
                infos = self._fotos_info([e["foto"]], lamina)
                if infos:
                    fotos_atuais.append(infos[0])
                    continue
            lw, lh = lamina_mm(self.produto)
            prop = max(0.2, (e["w"] * lw) / max(1.0, e["h"] * lh))
            fotos_atuais.append({"caminho": None, "proporcao": prop, "edicao": e.get("edicao") or {}})

        if delta > 0:
            fotos_atuais.append({"caminho": None, "proporcao": 1.5, "edicao": {}})
        else:
            # Remove preferencialmente um espaço vazio; se todos tiverem foto, remove o último
            idx_rem = next((idx for idx in range(len(fotos_atuais) - 1, -1, -1) if not fotos_atuais[idx].get("caminho")), len(fotos_atuais) - 1)
            fotos_atuais.pop(idx_rem)

        opcoes = diagramar.opcoes_pagina_unica(fotos_atuais, self.produto, pagina, self._cfg(), maximo=1, espacos_fixos=ficam)
        if opcoes:
            novos = opcoes[0]["espacos"]
            lamina["espacos"] = (novos + ficam) if pagina == 0 else (ficam + novos)
            lamina["modelo"] = None
            self.espaco_sel = None
            self.atualizar_lamina_atual()
            self._animar_transicao_layout(antes, lamina.get("espacos", []))
            self.desenhar_modelos()

    def aplicar_modelo_selecionado(self):
        lamina = self._lamina()
        if lamina is None or not self._modelo_sel:
            self.registrar("Escolha uma lâmina e um modelo de página.")
            return
        antes = [dict(e) for e in lamina.get("espacos", [])]
        pagina = self.pagina_modelo.get() if self.produto.get("paginas_por_lamina", 1) > 1 else 0

        # 1. Modelo dinâmico de página única (Safe Zone, Sangria Total da Página ou Sem Corte)
        if isinstance(self._modelo_sel, str) and self._modelo_sel.startswith("__pagina__:"):
            try:
                idx_op = int(self._modelo_sel.split(":")[1])
            except (IndexError, ValueError):
                return
            opcoes_pag = getattr(self, "_opcoes_pagina_modelos", [])
            if not (0 <= idx_op < len(opcoes_pag)):
                return
            opcao = opcoes_pag[idx_op]
            _saem, ficam = self._particionar_espacos_pagina(lamina, pagina)
            self.marcar_historico()
            novos = [proj.copia(e) for e in opcao["espacos"]]
            lamina["espacos"] = (novos + ficam) if pagina == 0 else (ficam + novos)
            lamina["modelo"] = None
            self.espaco_sel = None
            self.atualizar_lamina_atual()
            self._animar_transicao_layout(antes, lamina.get("espacos", []))
            nome_lado = "Página Esquerda" if pagina == 0 else "Página Direita"
            self.registrar("Layout '%s' aplicado na %s (a outra página foi mantida intacta)." % (opcao.get("nome", "Página"), nome_lado))
            return

        # 2. Modelo salvo ou molde PNG
        modelo = modelos_lamina.carregar_modelo(self._modelo_sel) if self._modelo_sel else None
        if modelo is None:
            self.registrar("Escolha uma lâmina e um modelo.")
            return
        self.marcar_historico()
        if modelo.get("escopo") == "pagina" and self.produto["paginas_por_lamina"] > 1:
            saem, ficam = self._particionar_espacos_pagina(lamina, pagina)
            fotos = self._fotos_info([e["foto"] for e in saem if e.get("foto")], lamina)
            novos, sobre, sobras = diagramar.espacos_com_modelo(modelo, fotos, self.produto, pagina, self._cfg())
            lamina["espacos"] = (novos + ficam) if pagina == 0 else (ficam + novos)
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

    def selecionar_lamina(self, item, manter_selecao=False):
        self._cancelar_animacao_layout()
        self.atual = item
        if isinstance(item, int):
            if not manter_selecao:
                self.laminas_sel = {item}
            else:
                self.laminas_sel.add(item)
            self.ultima_lamina_clicada = item
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
        itens_set = set(itens)
        self.laminas_sel = {i for i in getattr(self, "laminas_sel", set()) if i in itens_set}
        if isinstance(self.atual, int) and self.atual in itens_set and not self.laminas_sel:
            self.laminas_sel = {self.atual}
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
        laminas_dict = {}
        if isinstance(self.atual, int) and 0 <= self.atual < n:
            laminas_dict[self.atual] = copy.deepcopy(self.projeto["laminas"][self.atual])
        return {"capa": None, "laminas": laminas_dict}

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
            self._desenhar_vazio(somente_sem_laminas=True)
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
        texto = "Lâmina %s/%s · %s foto(s)%s" % (self.atual + 1, len(self.projeto["laminas"]), n_fotos,
                                                 (" · %s vazio(s)" % vazios) if vazios else "")
        self.rotulo_lamina.configure(text=texto)

    def _desenhar_vazio(self, somente_sem_laminas=False):
        cw = max(480, self.canvas.winfo_width())
        ch = max(340, self.canvas.winfo_height())
        cx, cy = cw // 2, ch // 2
        self._botoes_vazio = []
        self.rotulo_lamina.configure(text="")

        if somente_sem_laminas:
            w_box, h_box = min(520, cw - 60), 210
            x0, y0 = cx - w_box // 2, cy - h_box // 2
            x1, y1 = x0 + w_box, y0 + h_box
            self.canvas.create_rectangle(x0, y0, x1, y1, fill=tema.PAINEL, outline=tema.BORDA, width=1)
            self.canvas.create_text(
                cx, y0 + 42,
                text="%d foto(s) pronta(s) no projeto" % len(self.projeto.get("fotos", [])),
                fill=tema.TEXTO, font=("Segoe UI Semibold", 15),
            )
            self.canvas.create_text(
                cx, y0 + 72,
                text="Clique abaixo para criar as lâminas automaticamente separadas por pastas e cenários:",
                fill=tema.TEXTO_FRACO, font=tema.FONTE,
            )
            bx0, by0, bx1, by1 = cx - 150, y0 + 112, cx + 150, y0 + 158
            self.canvas.create_rectangle(bx0, by0, bx1, by1, fill=tema.DESTAQUE, outline="#31a8ff", width=1)
            self.canvas.create_text(
                (bx0 + bx1) // 2, (by0 + by1) // 2,
                text="✦ Diagramar Álbum Agora",
                fill="#ffffff", font=tema.FONTE_FORTE,
            )
            self._botoes_vazio.append((bx0, by0, bx1, by1, lambda: self.diagramar_tudo(False)))
            return

        w_card = min(720, cw - 48)
        h_card = min(400, ch - 36)
        x0, y0 = cx - w_card // 2, cy - h_card // 2
        x1, y1 = x0 + w_card, y0 + h_card

        self.canvas.create_rectangle(x0, y0, x1, y1, fill=tema.PAINEL, outline=tema.BORDA, width=1)
        self.canvas.create_rectangle(x0 + 14, y0 + 14, x1 - 14, y1 - 14, outline=tema.BORDA, dash=(5, 5), width=1)

        self.canvas.create_text(
            cx, y0 + 46,
            text="Bem-vindo ao Smart Album Studio",
            fill=tema.TEXTO, font=("Segoe UI Semibold", 17),
        )
        self.canvas.create_text(
            cx, y0 + 76,
            text="Arraste fotos ou pastas do Windows Explorer para cá, ou escolha uma opção abaixo para começar:",
            fill=tema.TEXTO_FRACO, font=tema.FONTE,
        )

        # 4 Cartões de Ação Clicáveis na Tela Inicial
        acoes = [
            ("📁 Carregar Pasta", "Separa cada pasta carregada", self.escolher_pasta, True),
            ("☁ Link do Drive", "Baixa da nuvem em pasta temp.", self.importar_link_drive, False),
            ("🖼 Fotos Avulsas", "Selecionar JPG, PNG ou TIFF", self.escolher_fotos, False),
            ("📂 Abrir Projeto", "Continuar arquivo .album.json", self.abrir_projeto, False),
        ]
        n_bt = len(acoes)
        esp_bt = 12
        larg_total_bt = min(w_card - 64, 620)
        w_bt = (larg_total_bt - esp_bt * (n_bt - 1)) // n_bt
        h_bt = 78
        bx_ini = cx - larg_total_bt // 2
        by0 = y0 + 108
        by1 = by0 + h_bt

        for idx_b, (titulo_b, sub_b, cmd_b, primario) in enumerate(acoes):
            bx0 = bx_ini + idx_b * (w_bt + esp_bt)
            bx1 = bx0 + w_bt
            cor_fundo = tema.DESTAQUE_ESCURO if primario else tema.PAINEL_2
            cor_borda = tema.DESTAQUE if primario else tema.BORDA
            self.canvas.create_rectangle(bx0, by0, bx1, by1, fill=cor_fundo, outline=cor_borda, width=1)
            self.canvas.create_text(
                (bx0 + bx1) // 2, by0 + 28,
                text=titulo_b, fill="#ffffff" if primario else tema.TEXTO, font=tema.FONTE_FORTE,
            )
            self.canvas.create_text(
                (bx0 + bx1) // 2, by0 + 54,
                text=sub_b, fill="#7dd3fc" if primario else tema.TEXTO_FRACO, font=tema.FONTE_PEQUENA,
            )
            self._botoes_vazio.append((bx0, by0, bx1, by1, cmd_b))

        # Resumo rápido em 4 passos para usuários novos
        y_passos = by1 + 32
        if y_passos + 95 < y1:
            self.canvas.create_line(x0 + 36, y_passos - 12, x1 - 36, y_passos - 12, fill=tema.BORDA)
            self.canvas.create_text(
                cx, y_passos + 6,
                text="COMO FUNCIONA EM 4 PASSOS SIMPLES",
                fill=tema.DESTAQUE, font=tema.FONTE_FORTE,
            )
            passos = [
                ("1. Carregue Pastas", "As pastas não se misturam"),
                ("2. Diagramação IA", "Agrupa cenários e protege rostos"),
                ("3. Ajuste Livre", "Arraste fotos ou dê duplo clique"),
                ("4. Exporte", "JPG, PSD em camadas ou PDF"),
            ]
            larg_p = larg_total_bt // 4
            for i_p, (t_p, d_p) in enumerate(passos):
                px = bx_ini + i_p * larg_p + larg_p // 2
                self.canvas.create_text(px, y_passos + 34, text=t_p, fill=tema.TEXTO, font=tema.FONTE_FORTE)
                self.canvas.create_text(px, y_passos + 54, text=d_p, fill=tema.TEXTO_FRACO, font=tema.FONTE_PEQUENA)

    def _acao_vazio_em(self, x, y):
        if self.projeto.get("laminas"):
            return None
        for bx0, by0, bx1, by1, cmd in getattr(self, "_botoes_vazio", []):
            if bx0 <= x <= bx1 and by0 <= y <= by1:
                return cmd
        return None

    def mostrar_guia_rapido(self):
        """Janela amigável e redimensionável com o passo a passo e atalhos principais para usuários novos."""
        jan = tk.Toplevel(self.root)
        jan.title("Guia Rápido — Como usar o Smart Album")
        jan.configure(bg=tema.PAINEL)
        jan.transient(self.root)

        tema.criar_cabecalho_janela(
            jan,
            "Bem-vindo ao Smart Album Studio",
            "Monte álbuns profissionais em poucos cliques seguindo este fluxo rápido",
            icone="✦",
        )

        rodape_fixo = tk.Frame(jan, bg=tema.CABECALHO, highlightthickness=1, highlightbackground=tema.BORDA, padx=14, pady=9)
        rodape_fixo.pack(side="bottom", fill="x")

        quadro, _container_rol, _canvas_rol = tema.criar_corpo_rolavel(jan, padding=16)

        etapas = [
            (
                "1. Carregar Pastas ou Link do Google Drive",
                "• Use '📁 Pasta' (ou arraste várias pastas do Explorer) para importar suas fotos.\n"
                "• Com '📁 Separar Pastas' ativo, cada pasta carregada fica em lâminas exclusivas sem nunca misturar eventos.\n"
                "• Use '☁ Link Drive' para baixar pastas compartilhadas diretamente em uma pasta temporária auto-limpa.",
            ),
            (
                "2. Diagramação Inteligente (Cenários + Proteção de Rostos)",
                "• O motor identifica automaticamente a quantidade de rostos (👤 Rostos) para evitar cortes em pessoas e desviar rostos da dobra central.\n"
                "• Clique com o botão direito sobre o cabeçalho de qualquer pasta na galeria para diagramar somente aquela pasta.",
            ),
            (
                "3. Edição Rápida na Lâmina e Seleção em Grupo",
                "• Clique em uma foto e arraste para outra posição na lâmina para trocar as duas fotos mantendo o layout.\n"
                "• Dê duplo clique na lâmina (ou clique em '⬚') para mover e redimensionar os quadros livremente.\n"
                "• Na linha do tempo de lâminas, use Ctrl+Clique ou Shift+Clique para selecionar várias lâminas e duplicar ou excluir todas de uma vez (Del).",
            ),
            (
                "4. Integração com Photoshop e Exportação Final",
                "• Clique em '✦ Ps/Lr' para editar a foto no Photoshop — ao salvar (Ctrl+S), ela atualiza sozinha no álbum.\n"
                "• Clique em '⤓ Exportar' para gravar o álbum finalizado em JPG, PSD (em camadas editáveis) e/ou PDF.",
            ),
        ]

        labels_etapas = []
        for titulo_e, texto_e in etapas:
            bloco = tk.Frame(quadro, bg=tema.PAINEL_2, highlightthickness=1, highlightbackground=tema.BORDA, padx=12, pady=8)
            bloco.pack(fill="x", pady=4)
            tk.Label(bloco, text=titulo_e, bg=tema.PAINEL_2, fg=tema.DESTAQUE, font=tema.FONTE_FORTE, anchor="w").pack(fill="x")
            lbl_txt = tk.Label(bloco, text=texto_e, bg=tema.PAINEL_2, fg=tema.TEXTO, font=tema.FONTE, justify="left", anchor="w")
            lbl_txt.pack(fill="x", pady=(3, 0))
            labels_etapas.append(lbl_txt)

        def _ajustar_wraps_guia(evento):
            larg = max(360, int(evento.width) - 56)
            for lb in labels_etapas:
                lb.configure(wraplength=larg)

        quadro.bind("<Configure>", _ajustar_wraps_guia, add="+")

        rodape = ttk.Frame(rodape_fixo)
        rodape.pack(fill="x")
        self._botao(rodape, "📁 Carregar Pasta Agora", lambda: (jan.destroy(), self.escolher_pasta()),
                    estilo="PrimarioCompacto.TButton").pack(side="left")
        self._botao(rodape, "☁ Importar do Google Drive", lambda: (jan.destroy(), self.importar_link_drive()),
                    estilo="Compacto.TButton").pack(side="left", padx=6)
        self._botao(rodape, "Fechar", jan.destroy, estilo="Compacto.TButton").pack(side="right", padx=(0, 10))
        jan.bind("<Escape>", lambda _e: jan.destroy())
        tema.configurar_janela_secundaria(
            jan,
            pai=self,
            chave="guia_rapido",
            largura=740,
            altura=540,
            min_largura=480,
            min_altura=340,
        )

    def _desenhar_anel_brilho(self, ocultar=None):
        """Desenha o efeito suave de brilho (ring effect) ao redor de um ou múltiplos espaços selecionados."""
        c = self.canvas
        c.delete("anel_brilho")
        if self._geo is None:
            return
        lamina = self._lamina()
        if lamina is None:
            return
        selecionados = self._indices_espacos_selecionados()
        if not selecionados and self.espaco_sel is not None and 0 <= self.espaco_sel < len(lamina.get("espacos", [])):
            selecionados = {self.espaco_sel}
        if not selecionados:
            return

        dw, dh, ox, oy = self._geo
        editando = self.editar_layout.get()
        multi = len(selecionados) > 1
        agora = time.perf_counter()
        inicio = getattr(self, "_anel_brilho_inicio", agora)
        decorrido = max(0.0, agora - inicio)

        # Fase 1 (0..0.42s): Onda de expansão suave (ripple ring) ao selecionar
        t_onda = min(1.0, decorrido / 0.42)
        suave_onda = 1.0 - (1.0 - t_onda) ** 3

        # Fase 2: Respiração suave contínua (senoide de 2.0s)
        pulso = 0.5 + 0.5 * math.sin(decorrido * 3.14159)

        # Paleta de cores interpoladas para o halo externo, anel médio e núcleo brilhante
        cores_onda = ("#e0f2fe", "#7dd3fc", "#38bdf8", "#0d99ff", "#0369a1", "#0c4a6e")
        idx_cor_onda = min(len(cores_onda) - 1, int(suave_onda * len(cores_onda)))
        cor_onda = cores_onda[idx_cor_onda]

        if multi or editando:
            cor_halo_ext = "#0369a1" if pulso < 0.5 else "#0284c7"
            cor_halo_med = "#0ea5e9" if pulso < 0.5 else "#38bdf8"
            cor_nucleo = "#00e5ff" if pulso < 0.5 else "#7dd3fc"
        else:
            cor_halo_ext = "#075985" if pulso < 0.5 else "#0369a1"
            cor_halo_med = "#0284c7" if pulso < 0.5 else "#0ea5e9"
            cor_nucleo = tema.DESTAQUE if pulso < 0.5 else "#38bdf8"

        ordem_lista = sorted(selecionados)
        for pos_sel, i in enumerate(ordem_lista, start=1):
            if i < 0 or i >= len(lamina["espacos"]):
                continue
            if ("espaco", i) == ocultar or (isinstance(ocultar, tuple) and ocultar[0] == "grupo" and i in ocultar[1]):
                continue
            e = lamina["espacos"][i]
            x0, y0 = ox + e["x"] * dw, oy + e["y"] * dh
            x1, y1 = x0 + e["w"] * dw, y0 + e["h"] * dh

            # Onda expansiva inicial (ripple) ao clicar/selecionar
            if t_onda < 1.0:
                dist_onda = 1.5 + 7.5 * suave_onda
                larg_onda = 2 if t_onda < 0.55 else 1
                c.create_rectangle(
                    x0 - dist_onda, y0 - dist_onda, x1 + dist_onda, y1 + dist_onda,
                    outline=cor_onda, width=larg_onda, tags=("espaco", "anel_brilho")
                )

            # Camada 1: Halo externo difuso pulsante
            d_ext = 3.0 + 1.5 * pulso
            c.create_rectangle(
                x0 - d_ext, y0 - d_ext, x1 + d_ext, y1 + d_ext,
                outline=cor_halo_ext, width=1, tags=("espaco", "anel_brilho")
            )
            # Camada 2: Anel luminoso intermediário
            d_med = 1.5 + 0.8 * pulso
            c.create_rectangle(
                x0 - d_med, y0 - d_med, x1 + d_med, y1 + d_med,
                outline=cor_halo_med, width=2, tags=("espaco", "anel_brilho")
            )
            # Camada 3: Anel principal nítido
            c.create_rectangle(
                x0, y0, x1, y1,
                outline=cor_nucleo, width=2 if (i == self.espaco_sel or multi) else 1,
                tags=("espaco", "anel_brilho")
            )
            # Camada 4: Filete interno de alto contraste
            if (x1 - x0) > 16 and (y1 - y0) > 16:
                c.create_rectangle(
                    x0 + 1, y0 + 1, x1 - 1, y1 - 1,
                    outline="#e0f2fe" if pulso > 0.65 else "#bae6fd",
                    width=1, tags=("espaco", "anel_brilho")
                )

            # Selo indicador quando múltiplos espaços estão selecionados
            if multi and (x1 - x0) > 48 and (y1 - y0) > 28:
                txt_sel = "✦ %d/%d" % (pos_sel, len(ordem_lista))
                tw = max(38, len(txt_sel) * 6 + 8)
                bx1, by0 = x1 - 5, y0 + 5
                bx0, by1 = bx1 - tw, by0 + 17
                c.create_rectangle(
                    bx0, by0, bx1, by1,
                    fill="#002f4b", outline=cor_nucleo, width=1,
                    tags=("espaco", "anel_brilho")
                )
                c.create_text(
                    (bx0 + bx1) / 2, (by0 + by1) / 2,
                    text=txt_sel, fill="#7dd3fc", font=tema.FONTE_PEQUENA,
                    tags=("espaco", "anel_brilho")
                )

        # Brilho ao redor da caixa de grupo quando múltiplos espaços estão selecionados
        if multi and not (isinstance(ocultar, tuple) and ocultar[0] == "grupo"):
            grupo = self._caixa_selecao_espacos(selecionados)
            if grupo:
                gx0, gy0 = ox + grupo["x"] * dw, oy + grupo["y"] * dh
                gx1, gy1 = gx0 + grupo["w"] * dw, gy0 + grupo["h"] * dh
                d_g = 2.0 + 1.5 * pulso
                c.create_rectangle(
                    gx0 - d_g, gy0 - d_g, gx1 + d_g, gy1 + d_g,
                    outline=cor_halo_ext, width=1, dash=(6, 4),
                    tags=("espaco", "anel_brilho")
                )
                c.create_rectangle(
                    gx0, gy0, gx1, gy1,
                    outline=cor_nucleo, width=1, dash=(4, 3),
                    tags=("espaco", "anel_brilho")
                )

    def _iniciar_animacao_anel_brilho(self):
        """Inicia ou reinicia a animação suave de brilho (ring effect) para os espaços selecionados."""
        self._anel_brilho_inicio = time.perf_counter()
        self._passo_anel_brilho()

    def _passo_anel_brilho(self):
        if self._geo is None or self._lamina() is None:
            return
        selecionados = self._indices_espacos_selecionados()
        if not selecionados and self.espaco_sel is None:
            self.canvas.delete("anel_brilho")
            return
        ocultar = None
        if getattr(self, "_acao_layout", None):
            ocultar = self._acao_layout.get("alvo")
        self._desenhar_anel_brilho(ocultar=ocultar)
        decorrido = time.perf_counter() - getattr(self, "_anel_brilho_inicio", 0.0)
        # Mantém a animação fluida a ~30fps durante a seleção (4.5s de ciclo suave)
        if decorrido < 4.5:
            intervalo = 24 if decorrido < 0.45 else 45
            self.agendar("anel_brilho", intervalo, self._passo_anel_brilho)

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

        assinatura_sel = ( self.atual, tuple(sorted(selecionados)), self.espaco_sel )
        if selecionados and assinatura_sel != getattr(self, "_ultima_assinatura_anel", None):
            self._ultima_assinatura_anel = assinatura_sel
            self._anel_brilho_inicio = time.perf_counter()
            self.agendar("anel_brilho", 24, self._passo_anel_brilho)
        elif not selecionados:
            self._ultima_assinatura_anel = None

        # Linhas guias contínuas ciano no estilo Adobe Photoshop (Safe Zone e Sangria)
        if self.guias.get():
            lw, lh = self._mm()
            b = float(self.produto["sangria_mm"])
            margem_cfg = float(self._cfg()["margem_mm"])
            # Linha de Sangria (ciano discreto contínuo)
            sx0, sy0 = ox + (b / lw) * dw, oy + (b / lh) * dh
            sx1, sy1 = ox + ((lw - b) / lw) * dw, oy + ((lh - b) / lh) * dh
            c.create_rectangle(sx0, sy0, sx1, sy1, outline="#00afdb", width=1, tags="espaco")
            # Linhas imaginárias da Safe Zone (ciano vivo contínuo estilo Photoshop #00e5ff)
            for pag in range(self.produto["paginas_por_lamina"]):
                ux, uy, uw, uh = area_util_pagina_mm(self.produto, pag, margem_cfg, respeitar_seguranca=True)
                gx0, gy0 = ox + (ux / lw) * dw, oy + (uy / lh) * dh
                gx1, gy1 = ox + ((ux + uw) / lw) * dw, oy + ((uy + uh) / lh) * dh
                c.create_rectangle(gx0, gy0, gx1, gy1, outline="#00e5ff", width=1, tags="espaco")
            if self.produto["paginas_por_lamina"] > 1:
                xm = ox + dw / 2.0
                c.create_line(xm, oy, xm, oy + dh, fill="#00cfe8", width=1, tags="espaco")

        if editando:
            for i, s in enumerate(lamina.get("sobreposicoes") or []):
                if ("molde", i) == ocultar:
                    continue
                x0, y0 = ox + s["x"] * dw, oy + s["y"] * dh
                x1, y1 = x0 + s["w"] * dw, y0 + s["h"] * dh
                if ("molde", i) == alvo:
                    c.create_rectangle(x0, y0, x1, y1, outline=tema.ALERTA, width=2, tags="espaco")
        for i, e in enumerate(lamina["espacos"]):
            if ("espaco", i) == ocultar or (isinstance(ocultar, tuple) and ocultar[0] == "grupo" and i in ocultar[1]):
                continue
            x0, y0 = ox + e["x"] * dw, oy + e["y"] * dh
            x1, y1 = x0 + e["w"] * dw, y0 + e["h"] * dh
            if not e.get("foto"):
                c.create_rectangle(x0, y0, x1, y1, fill=tema.PAINEL_3, outline=tema.BORDA, tags="espaco")
                c.create_text((x0 + x1) / 2, (y0 + y1) / 2, text="+", fill=tema.TEXTO_FRACO,
                              font=("Segoe UI", 18), tags="espaco")

        # Desenha o anel de brilho animado (ring effect) nos espaços selecionados
        self._desenhar_anel_brilho(ocultar=ocultar)

        if editando and len(selecionados) > 1 and not (isinstance(ocultar, tuple) and ocultar[0] == "grupo"):
            grupo = self._caixa_selecao_espacos(selecionados)
            for hx, hy in self._alcas(grupo).values():
                c.create_rectangle(hx - ALCA, hy - ALCA, hx + ALCA, hy + ALCA, fill="#ffffff",
                                   outline="#00e5ff", width=1, tags="espaco")
        elif editando and alvo and alvo != ocultar:
            cor = tema.DESTAQUE if alvo[0] == "espaco" else tema.ALERTA
            for hx, hy in self._alcas(self._item_layout(alvo, lamina)).values():
                c.create_rectangle(hx - ALCA, hy - ALCA, hx + ALCA, hy + ALCA, fill="#ffffff",
                                   outline=cor, width=1, tags="espaco")
        self._atualizar_numeros()
        self._atualizar_enquadramento_inline()
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
        for pos_g, tk_g in r.get("itens_grupo") or []:
            self.canvas.create_image(pos_g[0], pos_g[1], image=tk_g, anchor="nw", tags="remendo")

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
        if not self.projeto.get("laminas"):
            acao_v = self._acao_vazio_em(evento.x, evento.y)
            self.canvas.configure(cursor="hand2" if acao_v is not None else "")
            return
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
        if not self.projeto.get("laminas"):
            acao_v = self._acao_vazio_em(evento.x, evento.y)
            if acao_v is not None:
                acao_v()
            return
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
        """
        Linhas (mm) onde as bordas grudam com ímã suave estilo Photoshop:
        Separa (xs_safe, ys_safe, xs_sangria, ys_sangria) para que as linhas de sangria
        e a margem interna entre as páginas tenham tolerância mínima e nunca travem
        o redimensionamento perto ou através do meio da lâmina.
        """
        p = self.produto
        lw, lh = self._mm()
        b = float(p["sangria_mm"])
        margem = float(self._cfg()["margem_mm"])
        esp = float(self._cfg()["espacamento_mm"])
        n_pags = int(p.get("paginas_por_lamina", 1))
        meio_x = lw / 2.0

        xs_sangria = {0.0, lw, b, lw - b, meio_x}
        ys_sangria = {0.0, lh, b, lh - b}
        xs_safe = set()
        ys_safe = {lh / 2.0}

        faixa_meio_min = meio_x
        faixa_meio_max = meio_x
        for pagina in range(n_pags):
            ux, uy, uw, uh = area_util_pagina_mm(p, pagina, margem, respeitar_seguranca=True)
            ys_safe |= {uy, uy + uh / 2.0, uy + uh}
            xs_safe.add(ux + uw / 2.0)
            if n_pags > 1 and pagina == 0:
                xs_safe.add(ux)
                xs_sangria.add(ux + uw)
                faixa_meio_min = min(faixa_meio_min, ux + uw)
            elif n_pags > 1 and pagina == n_pags - 1:
                xs_sangria.add(ux)
                xs_safe.add(ux + uw)
                faixa_meio_max = max(faixa_meio_max, ux)
            else:
                xs_safe |= {ux, ux + uw}

        indices_ignorar = set()
        if isinstance(ignorar, tuple):
            if ignorar[0] == "espaco":
                indices_ignorar.add(ignorar[1])
            elif ignorar[0] == "grupo":
                indices_ignorar.update(ignorar[1])
        for j, outro in enumerate(lamina["espacos"]):
            if j in indices_ignorar:
                continue
            ex, ey, ew, eh = self._espaco_mm(outro)
            for cand_x in (ex, ex + ew / 2.0, ex + ew, ex - esp, ex + ew + esp):
                if n_pags > 1 and (faixa_meio_min - 1.0) <= cand_x <= (faixa_meio_max + 1.0):
                    xs_sangria.add(cand_x)
                else:
                    xs_safe.add(cand_x)
            ys_safe |= {ey, ey + eh / 2.0, ey + eh, ey - esp, ey + eh + esp}
        return sorted(xs_safe), sorted(ys_safe), sorted(xs_sangria), sorted(ys_sangria)

    def _limitar_caixa_vizinhas(self, lamina, indices_ignorados, caixa_ini, esq, topo, dir_, base, alca, guias_x, guias_y):
        """
        Mantido por compatibilidade: o espaçamento entre fotos vizinhas agora atua via
        ímã suave (_alvos_ima) sem criar parede/trava rígida ao redimensionar entre páginas.
        """
        return esq, topo, dir_, base

    def _limitar_movimento_vizinhas(self, lamina, indices_ignorados, caixa_ini, x, y, w, h, guias_x, guias_y):
        """
        Mantido por compatibilidade: o alinhamento de espaçamento ao mover atua via
        ímã suave (_alvos_ima) sem travar a passagem entre páginas.
        """
        return x, y

    @staticmethod
    def _imantar(valores, alvos, tolerancia, anterior=None, valores_sangria=None, alvos_sangria=None, tol_sangria=None):
        """
        Ímã suave estilo Photoshop sem histerese pegajosa:
        Prioriza Safe Zone e fotos vizinhas (tolerância normal), e aplica ímã ultraleve
        nas linhas de sangria e margem central (tol_sangria) para nunca travar.
        """
        melhor = None
        for v in valores:
            for a in alvos:
                d = a - v
                if abs(d) <= tolerancia and (melhor is None or abs(d) < abs(melhor[0])):
                    melhor = (d, a)
        if melhor is not None:
            return melhor
        if valores_sangria and alvos_sangria and tol_sangria:
            for v in valores_sangria:
                for a in alvos_sangria:
                    d = a - v
                    if abs(d) <= tol_sangria and (melhor is None or abs(d) < abs(melhor[0])):
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
        - Por padrão, arrastar as alças de canto mantém a proporção original do espaço sem travar nas bordas
          nem na margem entre as páginas.
        - Ímã suave nas linhas guias (Safe Zone, dobra central, sangria e espaçamento de fotos vizinhas).
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
        tol_sangria = IMA_SANGRIA_PX * k
        xs, ys, xs_sang, ys_sang = a["alvos"] if ima else ([], [], [], [])
        memoria = a["ima"] if ima else {}
        x0, y0, w0, h0 = a["inicio"]
        guias_x, guias_y = [], []
        alca = a["alca"]

        def imantar(chave, valores, alvos, val_sang=None, alv_sang=None, ignorar_orig=None):
            alvos_f = [v for v in alvos if ignorar_orig is None or abs(v - ignorar_orig) > 0.6]
            alv_sang_f = [v for v in (alv_sang or []) if ignorar_orig is None or abs(v - ignorar_orig) > 0.6] if alv_sang else None
            d, linha = self._imantar(valores, alvos_f, tolerancia, memoria.get(chave), val_sang, alv_sang_f, tol_sangria)
            memoria[chave] = linha
            return d, linha

        if not alca:
            x = min(max(x0 + dx, 0.0), lw - w0)
            y = min(max(y0 + dy, 0.0), lh - h0)
            if ima:
                d, linha = imantar("x", [x, x + w0 / 2, x + w0], xs, [x, x + w0], xs_sang)
                if linha is not None and 0 <= x + d <= lw - w0:
                    x += d
                    guias_x.append(linha)
                d, linha = imantar("y", [y, y + h0 / 2, y + h0], ys, [y, y + h0], ys_sang)
                if linha is not None and 0 <= y + d <= lh - h0:
                    y += d
                    guias_y.append(linha)
            w, h = w0, h0
        else:
            esq_raw, topo_raw, dir_raw, base_raw = x0, y0, x0 + w0, y0 + h0
            if "w" in alca:
                esq_raw = x0 + dx
            if "e" in alca:
                dir_raw = x0 + w0 + dx
            if "n" in alca:
                topo_raw = y0 + dy
            if "s" in alca:
                base_raw = y0 + h0 + dy

            manter_proporcao = (not proporcao_livre) and (len(alca) == 2) and w0 > 0 and h0 > 0
            if manter_proporcao:
                razao = w0 / float(h0)
                ax = (x0 + w0) if "w" in alca else x0
                ay = (y0 + h0) if "n" in alca else y0
                max_w = ax if "w" in alca else (lw - ax)
                max_h = ay if "n" in alca else (lh - ay)

                w_in = max(LADO_MINIMO_MM, (ax - esq_raw) if "w" in alca else (dir_raw - ax))
                h_in = max(LADO_MINIMO_MM, (ay - topo_raw) if "n" in alca else (base_raw - ay))

                if abs(w_in - w0) / max(1.0, w0) >= abs(h_in - h0) / max(1.0, h0):
                    w_desejado = w_in
                    h_desejado = w_desejado / razao
                else:
                    h_desejado = h_in
                    w_desejado = h_desejado * razao

                if ima:
                    lado_x = "w" if "w" in alca else "e"
                    borda_x = (ax - w_desejado) if lado_x == "w" else (ax + w_desejado)
                    orig_x = x0 if lado_x == "w" else (x0 + w0)
                    ign_x = orig_x if abs(dx) < tolerancia * 1.5 else None
                    dx_ima, linha_x = imantar(lado_x, [borda_x], xs, [borda_x], xs_sang, ignorar_orig=ign_x)
                    if linha_x is not None:
                        borda_x_nova = borda_x + dx_ima
                        w_snap = (ax - borda_x_nova) if lado_x == "w" else (borda_x_nova - ax)
                        h_snap = w_snap / razao
                        if LADO_MINIMO_MM <= w_snap <= max_w and LADO_MINIMO_MM <= h_snap <= max_h:
                            w_desejado, h_desejado = w_snap, h_snap
                            guias_x.append(linha_x)

                    lado_y = "n" if "n" in alca else "s"
                    borda_y = (ay - h_desejado) if lado_y == "n" else (ay + h_desejado)
                    orig_y = y0 if lado_y == "n" else (y0 + h0)
                    ign_y = orig_y if abs(dy) < tolerancia * 1.5 else None
                    dy_ima, linha_y = imantar(lado_y, [borda_y], ys, [borda_y], ys_sang, ignorar_orig=ign_y)
                    if linha_y is not None and not guias_x:
                        borda_y_nova = borda_y + dy_ima
                        h_snap = (ay - borda_y_nova) if lado_y == "n" else (borda_y_nova - ay)
                        w_snap = h_snap * razao
                        if LADO_MINIMO_MM <= w_snap <= max_w and LADO_MINIMO_MM <= h_snap <= max_h:
                            w_desejado, h_desejado = w_snap, h_snap
                            guias_y.append(linha_y)

                if w_desejado > max_w:
                    w_desejado = max_w
                    h_desejado = w_desejado / razao
                if h_desejado > max_h:
                    h_desejado = max_h
                    w_desejado = h_desejado * razao

                w_desejado = max(LADO_MINIMO_MM, w_desejado)
                h_desejado = max(LADO_MINIMO_MM, h_desejado)

                esq = max(0.0, ax - w_desejado) if "w" in alca else ax
                dir_ = ax if "w" in alca else min(lw, ax + w_desejado)
                topo = max(0.0, ay - h_desejado) if "n" in alca else ay
                base = ay if "n" in alca else min(lh, ay + h_desejado)
            else:
                esq, topo, dir_, base = esq_raw, topo_raw, dir_raw, base_raw
                if ima:
                    for lado in ("w", "e"):
                        if lado in alca:
                            v_lado = [esq if lado == "w" else dir_]
                            orig_x = x0 if lado == "w" else (x0 + w0)
                            ign_x = orig_x if abs(dx) < tolerancia * 1.5 else None
                            d, linha = imantar(lado, v_lado, xs, v_lado, xs_sang, ignorar_orig=ign_x)
                            if linha is not None:
                                guias_x.append(linha)
                                if lado == "w":
                                    esq += d
                                else:
                                    dir_ += d
                    for lado in ("n", "s"):
                        if lado in alca:
                            v_lado = [topo if lado == "n" else base]
                            orig_y = y0 if lado == "n" else (y0 + h0)
                            ign_y = orig_y if abs(dy) < tolerancia * 1.5 else None
                            d, linha = imantar(lado, v_lado, ys, v_lado, ys_sang, ignorar_orig=ign_y)
                            if linha is not None:
                                guias_y.append(linha)
                                if lado == "n":
                                    topo += d
                                else:
                                    base += d

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

            x, y, w, h = esq, topo, max(LADO_MINIMO_MM, dir_ - esq), max(LADO_MINIMO_MM, base - topo)
        self._definir_espaco_mm(item, x, y, w, h)
        a["guias"] = (guias_x, guias_y)
        self._pedir_previa()
        self.tempos_arrasto.append(time.perf_counter() - inicio_t)
        del self.tempos_arrasto[:-2000]

    @staticmethod
    def _agrupar_intervalos(intervalos, tol=1.5):
        """Agrupa intervalos 1D sobrepostos [(ini, fim)] em faixas ordenadas."""
        if not intervalos:
            return []
        ordenados = sorted(intervalos, key=lambda it: (it[0], it[1]))
        faixas = [[ordenados[0][0], ordenados[0][1]]]
        for a, b in ordenados[1:]:
            if a <= faixas[-1][1] - tol:
                faixas[-1][1] = max(faixas[-1][1], b)
            else:
                faixas.append([a, b])
        return [(f[0], f[1]) for f in faixas]

    def _layout_mover_grupo(self, evento, arrasto):
        """
        Movimentação e redimensionamento de várias fotos selecionadas ao mesmo tempo:
        - Respeita as linhas da lâmina (Safe Zone, dobra central, sangria e fotos vizinhas)
          com ímã inteligente e linhas guias visuais (#ff3fb4), sem travar na margem entre páginas.
        - Mantém o espaçamento estabelecido (espacamento_mm) entre fotos adjacentes do grupo
          e preserva a margem entre páginas quando o grupo abrange as duas páginas.
        - Cada foto selecionada é redimensionada na sua própria proporção individual (w0 / h0).
        - Segurando Shift libera Proporção Livre; segurando Alt desliga o ímã.
        """
        inicio_t = time.perf_counter()
        lamina = self._lamina(arrasto["lamina"])
        if lamina is None or arrasto["lamina"] != self.atual or self._geo is None:
            return
        if not arrasto["ativo"]:
            if abs(evento.x - arrasto["x"]) + abs(evento.y - arrasto["y"]) < 3:
                return
            arrasto["ativo"] = True
            self.marcar_historico()
            lamina["modelo"] = None
            arrasto["alvos"] = self._alvos_ima(lamina, arrasto["alvo"])
            self.tempo_inicio_arrasto = inicio_t
            self._iniciar_previa_grupo(arrasto, lamina)

        lw, lh = self._mm()
        dw, dh = self._geo[0], self._geo[1]
        k = lw / float(dw)
        dx = (evento.x - arrasto["x"]) * k
        dy = (evento.y - arrasto["y"]) * (lh / float(dh))
        estado = getattr(evento, "state", 0)
        ima = not (estado & ALT)
        proporcao_livre = bool(estado & SHIFT)
        tolerancia = IMA_PX * k
        tol_sangria = IMA_SANGRIA_PX * k
        xs, ys, xs_sang, ys_sang = arrasto.get("alvos") if (ima and arrasto.get("alvos")) else ([], [], [], [])
        memoria = arrasto["ima"] if ima else {}
        guias_x, guias_y = [], []

        x0, y0, x1, y1 = arrasto["caixa_grupo"]
        gw = max(1.0, x1 - x0)
        gh = max(1.0, y1 - y0)
        alca = arrasto["alca"]
        geometrias = arrasto["grupo"]
        esp = float(self._cfg()["espacamento_mm"])
        meio_x = lw / 2.0

        def imantar(chave, valores, alvos, val_sang=None, alv_sang=None, ignorar_orig=None):
            alvos_f = [v for v in alvos if ignorar_orig is None or abs(v - ignorar_orig) > 0.6]
            alv_sang_f = [v for v in (alv_sang or []) if ignorar_orig is None or abs(v - ignorar_orig) > 0.6] if alv_sang else None
            d, linha = self._imantar(valores, alvos_f, tolerancia, memoria.get(chave), val_sang, alv_sang_f, tol_sangria)
            memoria[chave] = linha
            return d, linha

        novas = {}
        if not alca:
            gx = min(max(x0 + dx, 0.0), lw - gw)
            gy = min(max(y0 + dy, 0.0), lh - gh)
            if ima:
                d, linha = imantar("gx", [gx, gx + gw / 2.0, gx + gw], xs, [gx, gx + gw], xs_sang)
                if linha is not None and 0.0 <= gx + d <= lw - gw:
                    gx += d
                    guias_x.append(linha)
                d, linha = imantar("gy", [gy, gy + gh / 2.0, gy + gh], ys, [gy, gy + gh], ys_sang)
                if linha is not None and 0.0 <= gy + d <= lh - gh:
                    gy += d
                    guias_y.append(linha)
            mx = gx - x0
            my = gy - y0
            for i, (x, y, w, h) in geometrias.items():
                novas[i] = (x + mx, y + my, w, h)
        else:
            esq, topo, dir_, base = x0, y0, x1, y1
            if "w" in alca:
                esq = x0 + dx
            if "e" in alca:
                dir_ = x1 + dx
            if "n" in alca:
                topo = y0 + dy
            if "s" in alca:
                base = y1 + dy

            if ima:
                for lado in ("w", "e"):
                    if lado in alca:
                        v_lado = [esq if lado == "w" else dir_]
                        orig_x = x0 if lado == "w" else x1
                        ign_x = orig_x if abs(dx) < tolerancia * 1.5 else None
                        d, linha = imantar(lado, v_lado, xs, v_lado, xs_sang, ignorar_orig=ign_x)
                        if linha is not None:
                            guias_x.append(linha)
                            if lado == "w":
                                esq += d
                            else:
                                dir_ += d
                for lado in ("n", "s"):
                    if lado in alca:
                        v_lado = [topo if lado == "n" else base]
                        orig_y = y0 if lado == "n" else y1
                        ign_y = orig_y if abs(dy) < tolerancia * 1.5 else None
                        d, linha = imantar(lado, v_lado, ys, v_lado, ys_sang, ignorar_orig=ign_y)
                        if linha is not None:
                            guias_y.append(linha)
                            if lado == "n":
                                topo += d
                            else:
                                base += d

            esq = max(0.0, min(esq, lw - LADO_MINIMO_MM))
            dir_ = min(lw, max(dir_, LADO_MINIMO_MM))
            topo = max(0.0, min(topo, lh - LADO_MINIMO_MM))
            base = min(lh, max(base, LADO_MINIMO_MM))

            # Faixas (colunas e linhas) do grupo preservando espacamento_mm nas adjacentes
            # e preservando a distância real quando há margem entre páginas
            colunas_x = self._agrupar_intervalos([(x, x + w) for (x, _y, w, _h) in geometrias.values()])
            linhas_y = self._agrupar_intervalos([(y, y + h) for (_x, y, _w, h) in geometrias.values()])

            gaps_x_orig = []
            for k_c in range(len(colunas_x) - 1):
                c_fim = colunas_x[k_c][1]
                c_ini_prox = colunas_x[k_c + 1][0]
                g0 = max(0.0, c_ini_prox - c_fim)
                atravessa_dobra = (c_fim <= meio_x + 2.0 and c_ini_prox >= meio_x - 2.0 and g0 > esp + 1.5)
                if not atravessa_dobra and abs(g0 - esp) <= max(esp * 1.5, 4.0):
                    gaps_x_orig.append(esp)
                else:
                    gaps_x_orig.append(g0)

            gaps_y_orig = []
            for k_r in range(len(linhas_y) - 1):
                r_fim = linhas_y[k_r][1]
                r_ini_prox = linhas_y[k_r + 1][0]
                g0 = max(0.0, r_ini_prox - r_fim)
                if abs(g0 - esp) <= max(esp * 1.5, 4.0):
                    gaps_y_orig.append(esp)
                else:
                    gaps_y_orig.append(g0)

            soma_gaps_x = sum(gaps_x_orig)
            soma_gaps_y = sum(gaps_y_orig)
            w_util_orig = max(1.0, sum(cx1 - cx0 for cx0, cx1 in colunas_x))
            h_util_orig = max(1.0, sum(ry1 - ry0 for ry0, ry1 in linhas_y))

            min_gw = max(LADO_MINIMO_MM, len(colunas_x) * LADO_MINIMO_MM + len(gaps_x_orig) * esp)
            min_gh = max(LADO_MINIMO_MM, len(linhas_y) * LADO_MINIMO_MM + len(gaps_y_orig) * esp)
            if "w" in alca:
                esq = min(esq, dir_ - min_gw)
            else:
                dir_ = max(dir_, esq + min_gw)
            if "n" in alca:
                topo = min(topo, base - min_gh)
            else:
                base = max(base, topo + min_gh)

            ngw_alvo = max(min_gw, dir_ - esq)
            ngh_alvo = max(min_gh, base - topo)

            # Se o grupo encolher além do espaço dos gaps grandes (ex.: margem entre páginas), comprime os gaps maiores até esp
            disp_fotos_x = ngw_alvo - soma_gaps_x
            if disp_fotos_x < len(colunas_x) * LADO_MINIMO_MM and soma_gaps_x > len(gaps_x_orig) * esp:
                excesso_possivel = soma_gaps_x - len(gaps_x_orig) * esp
                falta = len(colunas_x) * LADO_MINIMO_MM - disp_fotos_x
                fator_red = max(0.0, 1.0 - falta / max(1.0, excesso_possivel))
                gaps_x = [esp + (g - esp) * fator_red if g > esp else g for g in gaps_x_orig]
                soma_gaps_x = sum(gaps_x)
            else:
                gaps_x = list(gaps_x_orig)

            disp_fotos_y = ngh_alvo - soma_gaps_y
            if disp_fotos_y < len(linhas_y) * LADO_MINIMO_MM and soma_gaps_y > len(gaps_y_orig) * esp:
                excesso_possivel = soma_gaps_y - len(gaps_y_orig) * esp
                falta = len(linhas_y) * LADO_MINIMO_MM - disp_fotos_y
                fator_red = max(0.0, 1.0 - falta / max(1.0, excesso_possivel))
                gaps_y = [esp + (g - esp) * fator_red if g > esp else g for g in gaps_y_orig]
                soma_gaps_y = sum(gaps_y)
            else:
                gaps_y = list(gaps_y_orig)

            sx = max(0.05, (ngw_alvo - soma_gaps_x) / w_util_orig)
            sy = max(0.05, (ngh_alvo - soma_gaps_y) / h_util_orig)

            lim_e, lim_t, lim_d, lim_b = (0.0, 0.0, lw, lh)
            ancora_x = x1 if "w" in alca else (x0 if "e" in alca else (x0 + x1) / 2.0)
            ancora_y = y1 if "n" in alca else (y0 if "s" in alca else (y0 + y1) / 2.0)
            if "w" in alca:
                max_gw_disp = max(min_gw, ancora_x - lim_e)
            elif "e" in alca:
                max_gw_disp = max(min_gw, lim_d - ancora_x)
            else:
                max_gw_disp = max(min_gw, 2.0 * min(ancora_x - lim_e, lim_d - ancora_x))
            if "n" in alca:
                max_gh_disp = max(min_gh, ancora_y - lim_t)
            elif "s" in alca:
                max_gh_disp = max(min_gh, lim_b - ancora_y)
            else:
                max_gh_disp = max(min_gh, 2.0 * min(ancora_y - lim_t, lim_b - ancora_y))

            max_sx = max(0.05, (max_gw_disp - soma_gaps_x) / w_util_orig)
            max_sy = max(0.05, (max_gh_disp - soma_gaps_y) / h_util_orig)

            igualar = bool(getattr(self, "igualar_no_grupo", None) and self.igualar_no_grupo.get())
            w_medio = sum(w for _x, _y, w, _h in geometrias.values()) / float(len(geometrias))
            h_medio = sum(h for _x, _y, _w, h in geometrias.values()) / float(len(geometrias))
            min_sx = max((LADO_MINIMO_MM / (w_medio if igualar else max(1.0, w))) for _x, _y, w, _h in geometrias.values())
            min_sy = max((LADO_MINIMO_MM / (h_medio if igualar else max(1.0, h))) for _x, _y, _w, h in geometrias.values())

            if not proporcao_livre:
                if len(alca) == 2:
                    escala_u = sx if abs(sx - 1.0) >= abs(sy - 1.0) else sy
                elif alca in ("e", "w"):
                    escala_u = sx
                else:
                    escala_u = sy
                escala_u = max(max(min_sx, min_sy), min(escala_u, max_sx, max_sy))
                sx = sy = escala_u
            else:
                if alca in ("e", "w"):
                    sy = 1.0
                elif alca in ("n", "s"):
                    sx = 1.0
                sx = max(min_sx, min(sx, max_sx))
                sy = max(min_sy, min(sy, max_sy))

            ngw_real = w_util_orig * sx + soma_gaps_x
            ngh_real = h_util_orig * sy + soma_gaps_y
            if "w" in alca:
                ngx0 = ancora_x - ngw_real
            elif "e" in alca:
                ngx0 = ancora_x
            else:
                ngx0 = ancora_x - ngw_real / 2.0
            if "n" in alca:
                ngy0 = ancora_y - ngh_real
            elif "s" in alca:
                ngy0 = ancora_y
            else:
                ngy0 = ancora_y - ngh_real / 2.0

            ngx0 = min(max(lim_e, ngx0), max(lim_e, lim_d - ngw_real))
            ngy0 = min(max(lim_t, ngy0), max(lim_t, lim_b - ngh_real))

            # Posiciona cada coluna e linha preservando os gaps calculados
            novos_cx0 = []
            cur_x = ngx0
            for idx_c, (cx0, cx1) in enumerate(colunas_x):
                novos_cx0.append(cur_x)
                gap_c = gaps_x[idx_c] if idx_c < len(gaps_x) else esp
                cur_x += (cx1 - cx0) * sx + gap_c

            novos_ry0 = []
            cur_y = ngy0
            for idx_r, (ry0, ry1) in enumerate(linhas_y):
                novos_ry0.append(cur_y)
                gap_r = gaps_y[idx_r] if idx_r < len(gaps_y) else esp
                cur_y += (ry1 - ry0) * sy + gap_r

            for i, (x, y, w, h) in geometrias.items():
                idx_c = 0
                for k_c, (cx0, cx1) in enumerate(colunas_x):
                    if x >= cx0 - 1.5 and (x + w) <= cx1 + 1.5:
                        idx_c = k_c
                        break
                idx_r = 0
                for k_r, (ry0, ry1) in enumerate(linhas_y):
                    if y >= ry0 - 1.5 and (y + h) <= ry1 + 1.5:
                        idx_r = k_r
                        break
                cx0_orig = colunas_x[idx_c][0]
                ry0_orig = linhas_y[idx_r][0]
                if not igualar:
                    nw = max(LADO_MINIMO_MM, w * sx)
                    nh = max(LADO_MINIMO_MM, h * sy)
                    nx = novos_cx0[idx_c] + (x - cx0_orig) * sx
                    ny = novos_ry0[idx_r] + (y - ry0_orig) * sy
                else:
                    nw = max(LADO_MINIMO_MM, w_medio * sx)
                    nh = max(LADO_MINIMO_MM, h_medio * sy)
                    nx = novos_cx0[idx_c] + (x - cx0_orig) * sx
                    ny = novos_ry0[idx_r] + (y - ry0_orig) * sy
                novas[i] = [nx, ny, nw, nh]

            # Garante gap fixo (espacamento_mm) apenas entre sub-colunas e sub-linhas adjacentes na mesma página
            ordem_x = sorted(geometrias.keys(), key=lambda idx_k: geometrias[idx_k][0])
            for a_idx in range(len(ordem_x)):
                i_a = ordem_x[a_idx]
                xa, ya, wa, ha = geometrias[i_a]
                for b_idx in range(a_idx + 1, len(ordem_x)):
                    i_b = ordem_x[b_idx]
                    xb, yb, wb, hb = geometrias[i_b]
                    g0_ab = xb - (xa + wa)
                    atravessa_ab = ((xa + wa) <= meio_x + 2.0 and xb >= meio_x - 2.0 and g0_ab > esp + 1.5)
                    if not atravessa_ab and min(ya + ha, yb + hb) - max(ya, yb) > 1.0 and abs(g0_ab - esp) <= max(esp * 1.5, 4.0):
                        novas[i_b][0] = novas[i_a][0] + novas[i_a][2] + esp

            ordem_y = sorted(geometrias.keys(), key=lambda idx_k: geometrias[idx_k][1])
            for a_idx in range(len(ordem_y)):
                i_a = ordem_y[a_idx]
                xa, ya, wa, ha = geometrias[i_a]
                for b_idx in range(a_idx + 1, len(ordem_y)):
                    i_b = ordem_y[b_idx]
                    xb, yb, wb, hb = geometrias[i_b]
                    g0_ab = yb - (ya + ha)
                    if min(xa + wa, xb + wb) - max(xa, xb) > 1.0 and abs(g0_ab - esp) <= max(esp * 1.5, 4.0):
                        novas[i_b][1] = novas[i_a][1] + novas[i_a][3] + esp

            min_x = min(v[0] for v in novas.values())
            min_y = min(v[1] for v in novas.values())
            max_x = max(v[0] + v[2] for v in novas.values())
            max_y = max(v[1] + v[3] for v in novas.values())
            if "w" in alca:
                dx_alinhar = (ancora_x - max_x) if abs(max_x - ancora_x) > 0.01 else 0.0
            else:
                dx_alinhar = 0.0
            if "n" in alca:
                dy_alinhar = (ancora_y - max_y) if abs(max_y - ancora_y) > 0.01 else 0.0
            else:
                dy_alinhar = 0.0
            for v in novas.values():
                v[0] += dx_alinhar
                v[1] += dy_alinhar

            min_x = min(v[0] for v in novas.values())
            min_y = min(v[1] for v in novas.values())
            max_x = max(v[0] + v[2] for v in novas.values())
            max_y = max(v[1] + v[3] for v in novas.values())
            mover_x = (lim_e - min_x) if min_x < lim_e else ((lim_d - max_x) if max_x > lim_d else 0.0)
            mover_y = (lim_t - min_y) if min_y < lim_t else ((lim_b - max_y) if max_y > lim_b else 0.0)
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
        arrasto["guias"] = (guias_x, guias_y)
        self._pedir_previa()
        self.tempos_arrasto.append(time.perf_counter() - inicio_t)
        del self.tempos_arrasto[:-2000]

    def _iniciar_previa_grupo(self, a, lamina):
        """
        Prepara a imagem base limpa e as fotos pré-carregadas para redimensionar várias fotos a 60fps
        sem piscar preto (mantendo referências de PhotoImage e desenhando o estado inicial antes de trocar).
        """
        c = self.canvas
        dw, dh, ox, oy = self._geo
        lado = max(dw, dh)
        a["base_limpa_tk"] = None
        a["previas_grupo"] = {}
        a["tks_grupo"] = []
        a["itens_grupo_pos"] = []
        indices_grupo = set(a.get("grupo", {}).keys())
        px_mm = dw / float(lamina_mm(self.produto)[0])
        fundo_rgb = cor_rgb(lamina.get("fundo"))
        for idx in indices_grupo:
            item = lamina["espacos"][idx]
            foto = item.get("foto")
            if not foto:
                continue
            try:
                ed = item.get("edicao") or {}
                img = recorte.transformar(analise.imagem_para(foto, lado), ed)
                if max(img.size) > lado:
                    escala = lado / float(max(img.size))
                    img = img.resize((max(1, round(img.width * escala)), max(1, round(img.height * escala))), Image.BILINEAR)
                a["previas_grupo"][idx] = {
                    "img": recorte.aplicar_ajustes(img, ed),
                    "edicao": ed,
                    "rosto_t": recorte.rosto_transformado(rosto_de(self.info, foto), ed),
                    "moldura": ed.get("moldura"),
                    "px_mm": px_mm,
                    "fundo": fundo_rgb,
                }
            except (OSError, ValueError):
                pass
        try:
            lamina_sem = dict(lamina)
            lamina_sem["espacos"] = [e for idx, e in enumerate(lamina.get("espacos", [])) if idx not in indices_grupo]
            img_limpa = exportar.renderizar_lamina(
                lamina_sem, self.produto, self.info, largura=dw, guias=self.guias.get(), rapido=True
            )
            a["base_limpa_tk"] = ImageTk.PhotoImage(img_limpa)
            # Desenha primeiro a prévia do grupo e só então substitui a base para nunca piscar preto
            self._desenhar_previa()
            c.delete("img")
            c.delete("remendo")
            c.create_image(ox, oy, image=a["base_limpa_tk"], anchor="nw", tags="img")
            c.tag_raise("arrasto")
            c.tag_raise("espaco")
        except Exception:
            pass
        self._desenhar_espacos(ocultar=("grupo", tuple(sorted(indices_grupo))))

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
        lamina = self._lamina(a["lamina"])
        if lamina is None:
            return
        c = self.canvas
        dw, dh, ox, oy = self._geo
        etiqueta = ("arrasto", "arrasto_item")

        if a.get("grupo") is not None:
            tks_grupo = []
            itens_grupo_pos = []
            previas_g = a.get("previas_grupo") or {}
            indices_g = sorted(a["grupo"].keys())
            novos_ids_canvas = []
            for idx in indices_g:
                if not (0 <= idx < len(lamina["espacos"])):
                    continue
                item = lamina["espacos"][idx]
                x0, y0 = int(round(ox + item["x"] * dw)), int(round(oy + item["y"] * dh))
                x1, y1 = int(round(ox + (item["x"] + item["w"]) * dw)), int(round(oy + (item["y"] + item["h"]) * dh))
                pw, ph = max(1, x1 - x0), max(1, y1 - y0)
                previa = previas_g.get(idx)
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
                    tk_img = ImageTk.PhotoImage(pronta)
                    tks_grupo.append(tk_img)
                    itens_grupo_pos.append(((x0, y0), tk_img))
                    novos_ids_canvas.append(c.create_image(x0, y0, image=tk_img, anchor="nw", tags=etiqueta))
                else:
                    novos_ids_canvas.append(c.create_rectangle(x0, y0, x1, y1, fill=tema.PAINEL_3, outline=tema.BORDA, tags=etiqueta))
                novos_ids_canvas.append(c.create_rectangle(x0, y0, x1, y1, outline="#00e5ff", width=2 if idx == self.espaco_sel else 1, tags=etiqueta))
            caixa_g = self._caixa_selecao_espacos(set(indices_g))
            if caixa_g:
                gx0, gy0 = ox + caixa_g["x"] * dw, oy + caixa_g["y"] * dh
                gx1, gy1 = gx0 + caixa_g["w"] * dw, gy0 + caixa_g["h"] * dh
                novos_ids_canvas.append(c.create_rectangle(gx0, gy0, gx1, gy1, outline="#00e5ff", width=1, tags=etiqueta))
                for hx, hy in self._alcas(caixa_g).values():
                    novos_ids_canvas.append(c.create_rectangle(hx - ALCA, hy - ALCA, hx + ALCA, hy + ALCA, fill="#ffffff", outline=tema.DESTAQUE, tags=etiqueta))
            # Remove apenas os itens do frame anterior depois de desenhar os novos (zero piscada!)
            for cid_ant in a.get("ids_canvas_grupo") or []:
                c.delete(cid_ant)
            a["ids_canvas_grupo"] = novos_ids_canvas
            a["tks_grupo"] = tks_grupo
            a["itens_grupo_pos"] = itens_grupo_pos
            self._desenhar_guias_ima(*a.get("guias", ([], [])))
            self._atualizar_numeros()
            return

        c.delete("arrasto_item")
        item = self._item_layout(a["alvo"], lamina)
        if item is None:
            return
        x0, y0 = int(round(ox + item["x"] * dw)), int(round(oy + item["y"] * dh))
        x1, y1 = int(round(ox + (item["x"] + item["w"]) * dw)), int(round(oy + (item["y"] + item["h"]) * dh))
        pw, ph = max(1, x1 - x0), max(1, y1 - y0)
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
                c.create_rectangle(x0, y0, x1, y1, fill=tema.PAINEL_3, outline=tema.BORDA, tags=etiqueta)
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
        self._desenhar_previa_final(a)
        self._remendo = {
            "item": self.atual,
            "geo": self._geo,
            "base_limpa_tk": a.get("base_limpa_tk"),
            "tk": a.get("tk"),
            "pos": a.get("pos", (0, 0)),
            "itens_grupo": list(a.get("itens_grupo_pos") or []),
        }
        self._desenhar_remendo()
        self.canvas.delete("arrasto")
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
        self.cancelar_agendado(chave)

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
        elif self.espaco_sel is not None or getattr(self, "espacos_sel", None):
            self.tirar_foto_selecionada()
        elif getattr(self, "laminas_sel", None) and len(self.projeto.get("laminas", [])) > 0:
            self.remover_lamina()

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
            if acao in ("pagina_util", "pagina"):
                # Botão Preencher Página: passa pelas linhas da Safe Zone e preenche 100% da página até a sangria
                # ignorando a trava de 22% (suporta 1 ou mais fotos selecionadas)
                alvos_p = sorted(selecionados) if selecionados and alvo[0] == "espaco" else None
                if alvos_p:
                    for idx_p in alvos_p:
                        esp_p = l["espacos"][idx_p]
                        pag_p = self._pagina_do_espaco(esp_p)
                        px, py, pw_p, ph_p = area_pagina_mm(self.produto, pag_p)
                        self._definir_espaco_mm(esp_p, px, py, pw_p, ph_p)
                    return
                x, y, w, h = area_pagina_mm(self.produto, pagina)
                self._definir_espaco_mm(e, x, y, w, h)
                return
            x, y, w, h = self._espaco_mm(e)
            if acao == "centro_h":
                x = ux + (uw - w) / 2
            elif acao == "centro_v":
                y = uy + (uh - h) / 2
            elif acao == "proporcao_foto":
                if e.get("foto"):
                    prop = self._proporcao_efetiva(e["foto"], e.get("edicao"))
                    x, y, w, h = diagramar.caixa_proporcao_padrao(prop, (x, y, w, h))
            elif acao == "pagina_safe":
                x, y, w, h = ux, uy, uw, uh
                if e.get("foto"):
                    prop = self._proporcao_efetiva(e["foto"], e.get("edicao"))
                    if diagramar._corte_quadro(prop, (x, y, w, h)) > diagramar.CORTE_MAXIMO:
                        x, y, w, h = diagramar.caixa_proporcao_padrao(prop, (ux, uy, uw, uh))
            elif acao == "lamina":
                x, y, w, h = 0.0, 0.0, lw, lh
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

        msg_acao = {
            "igualar": "Tamanho das fotos igualado.",
            "pagina_util": "Foto expandida 100% na página até a sangria (ultrapassando a Safe Zone).",
            "pagina": "Foto expandida 100% na página até a sangria (ultrapassando a Safe Zone).",
            "pagina_safe": "Foto encaixada dentro das linhas da Safe Zone.",
            "lamina": "Foto expandida 100% na lâmina inteira até a sangria.",
        }.get(acao)
        self._mudar_espacos(mudar, msg_acao)

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

    # ---------------- enquadramento direto na aba Editar (sem duplo clique) ----------------

    def _espaco_enquadramento_ativo(self):
        lamina = self._lamina()
        if lamina is None or not lamina.get("espacos"):
            return None, None
        idx = self.espaco_sel
        if idx is None or not (0 <= idx < len(lamina["espacos"])):
            return None, None
        return idx, lamina["espacos"][idx]

    def _corte_atual_espaco(self, espaco):
        foto = espaco.get("foto")
        if not foto:
            return {"cx": 0.5, "cy": 0.5, "zoom": 1.0}, None, 1.0
        ed = espaco.get("edicao") or {}
        lw, lh = self._mm()
        aspecto = max(0.1, (espaco["w"] * lw) / max(1.0, espaco["h"] * lh))
        ch_cache = (
            normalizar_caminho(foto),
            self.versao_fotos,
            int(ed.get("rot", 0)) % 360,
            bool(ed.get("espelhar", False)),
            bool(ed.get("espelhar_v", False)),
        )
        cache_enq = getattr(self, "_cache_img_enq_inline", None)
        if cache_enq and cache_enq[0] == ch_cache:
            img_t = cache_enq[1]
            W, H = img_t.size
        else:
            try:
                img_base = analise.miniatura(foto)
                img_t = recorte.transformar(img_base, ed)
                if img_t is img_base:
                    img_t = img_base.copy()
                self._cache_img_enq_inline = (ch_cache, img_t)
                W, H = img_t.size
            except Exception:
                return {"cx": 0.5, "cy": 0.5, "zoom": 1.0}, None, aspecto
        dobra_x = None
        if espaco["x"] < 0.5 < espaco["x"] + espaco["w"] and espaco["w"] > 0.05:
            dobra_x = (0.5 - espaco["x"]) / float(espaco["w"])
        rosto_t = recorte.rosto_transformado(rosto_de(self.info, foto), ed)
        corte = recorte.corte_do_espaco(W, H, aspecto, ed, rosto_t, rosto_ja_transformado=True, dobra_x=dobra_x)
        return dict(corte), img_t, aspecto

    def _atualizar_enquadramento_inline(self):
        if not hasattr(self, "canvas_enquadramento"):
            return
        c = self.canvas_enquadramento
        c.delete("all")
        cw = max(120, c.winfo_width())
        ch = max(90, c.winfo_height())
        idx, esp = self._espaco_enquadramento_ativo()
        if esp is None or not esp.get("foto"):
            self._carregando_enq = True
            self.enq_zoom_var.set(1.0)
            self.enq_cx_var.set(50.0)
            self.enq_cy_var.set(50.0)
            for ch_k, rot in getattr(self, "rotulos_enq", {}).items():
                rot.configure(text="1.0x" if ch_k == "zoom" else "50%")
            for esc in getattr(self, "escalas_enq", {}).values():
                esc.configure(state="disabled")
            self._carregando_enq = False
            if hasattr(self, "rotulo_enq_status"):
                self.rotulo_enq_status.configure(
                    text="Selecione uma foto na lâmina" if esp is None else "Espaço vazio (sem foto)"
                )
            c.create_text(
                cw // 2,
                ch // 2,
                text="Clique em qualquer foto na lâmina\npara ajustar o enquadramento aqui\n(arraste a imagem ou use o zoom)",
                fill=tema.TEXTO_FRACO,
                font=tema.FONTE_PEQUENA,
                justify="center",
            )
            return

        for esc in getattr(self, "escalas_enq", {}).values():
            esc.configure(state="normal")

        corte, img_t, aspecto = self._corte_atual_espaco(esp)
        zoom = max(1.0, min(3.0, float(corte.get("zoom", 1.0))))
        cx = max(0.0, min(1.0, float(corte.get("cx", 0.5))))
        cy = max(0.0, min(1.0, float(corte.get("cy", 0.5))))

        self._carregando_enq = True
        self.enq_zoom_var.set(zoom)
        self.enq_cx_var.set(cx * 100.0)
        self.enq_cy_var.set(cy * 100.0)
        if "zoom" in self.rotulos_enq:
            self.rotulos_enq["zoom"].configure(text="%.2fx" % zoom)
        if "cx" in self.rotulos_enq:
            self.rotulos_enq["cx"].configure(text="%d%%" % int(round(cx * 100)))
        if "cy" in self.rotulos_enq:
            self.rotulos_enq["cy"].configure(text="%d%%" % int(round(cy * 100)))
        self._carregando_enq = False

        nome_arq = Path(esp["foto"]).name
        if hasattr(self, "rotulo_enq_status"):
            self.rotulo_enq_status.configure(
                text="Foto #%d: %s" % (idx + 1, nome_arq if len(nome_arq) <= 18 else nome_arq[:16] + "…")
            )

        if img_t is None:
            return

        # Desenha a janela proporcional do espaço no centro do canvas_enquadramento
        margem_c = 10
        disp_w = max(40, cw - 2 * margem_c)
        disp_h = max(40, ch - 2 * margem_c)
        if disp_w / float(disp_h) > aspecto:
            qh = disp_h
            qw = max(24, int(round(qh * aspecto)))
        else:
            qw = disp_w
            qh = max(24, int(round(qw / aspecto)))
        qx0 = (cw - qw) // 2
        qy0 = (ch - qh) // 2

        W, H = img_t.size
        caixa_c = recorte.caixa_corte(W, H, aspecto, {"cx": cx, "cy": cy, "zoom": zoom})
        rec = recorte.recortar(img_t, caixa_c, (qw, qh), (24, 24, 27), rapido=True)
        self._tk_enq_inline = ImageTk.PhotoImage(rec)
        c.create_image(qx0, qy0, image=self._tk_enq_inline, anchor="nw")
        # Grade de terços discreta + contorno ciano
        c.create_line(qx0 + qw / 3.0, qy0, qx0 + qw / 3.0, qy0 + qh, fill="#00e5ff", stipple="gray50")
        c.create_line(qx0 + 2 * qw / 3.0, qy0, qx0 + 2 * qw / 3.0, qy0 + qh, fill="#00e5ff", stipple="gray50")
        c.create_line(qx0, qy0 + qh / 3.0, qx0 + qw, qy0 + qh / 3.0, fill="#00e5ff", stipple="gray50")
        c.create_line(qx0, qy0 + 2 * qh / 3.0, qx0 + qw, qy0 + 2 * qh / 3.0, fill="#00e5ff", stipple="gray50")
        c.create_rectangle(qx0, qy0, qx0 + qw, qy0 + qh, outline="#00e5ff", width=2)
        self._geo_enq_inline = (qx0, qy0, qw, qh)

    def _aplicar_corte_inline(self, cx, cy, zoom, final=False):
        _idx, esp = self._espaco_enquadramento_ativo()
        if esp is None or not esp.get("foto"):
            return
        ed = dict(esp.get("edicao") or {})
        ed["corte"] = {
            "cx": max(0.0, min(1.0, float(cx))),
            "cy": max(0.0, min(1.0, float(cy))),
            "zoom": max(1.0, min(3.0, float(zoom))),
        }
        esp["edicao"] = ed
        self._marcar_modificado()
        self._atualizar_enquadramento_inline()
        if final:
            self.atualizar_lamina_atual(timeline=True)
        elif self._geo:
            dw, dh = self._geo[:2]
            self.agendar("render_enq_inline", 16, lambda: self._pedir_render(dw, dh))

    def _mudou_enquadramento_slider(self, _chave, final=False):
        if getattr(self, "_carregando_enq", False):
            return
        zoom = float(self.enq_zoom_var.get())
        cx = float(self.enq_cx_var.get()) / 100.0
        cy = float(self.enq_cy_var.get()) / 100.0
        self._aplicar_corte_inline(cx, cy, zoom, final=final)

    def _enquadramento_inicio(self, evento):
        _idx, esp = self._espaco_enquadramento_ativo()
        if esp is None or not esp.get("foto"):
            return
        self.marcar_historico()
        corte, _img_t, _asp = self._corte_atual_espaco(esp)
        self._arrasto_enq = {
            "x": evento.x,
            "y": evento.y,
            "cx": float(corte.get("cx", 0.5)),
            "cy": float(corte.get("cy", 0.5)),
            "zoom": float(corte.get("zoom", 1.0)),
        }

    def _enquadramento_arrastar(self, evento):
        st = getattr(self, "_arrasto_enq", None)
        if not st:
            return
        geo = getattr(self, "_geo_enq_inline", None)
        qw = max(40, geo[2]) if geo else 120
        qh = max(40, geo[3]) if geo else 90
        zoom = max(1.0, st["zoom"])
        # Arrastar a imagem para a direita move o centro do corte para a esquerda (estilo Photoshop/Lightroom)
        sens = 1.0 / max(0.6, zoom * 0.85)
        ncx = max(0.0, min(1.0, st["cx"] - ((evento.x - st["x"]) / float(qw)) * sens))
        ncy = max(0.0, min(1.0, st["cy"] - ((evento.y - st["y"]) / float(qh)) * sens))
        self._aplicar_corte_inline(ncx, ncy, zoom, final=False)

    def _enquadramento_soltar(self, _evento):
        if getattr(self, "_arrasto_enq", None) is not None:
            self._arrasto_enq = None
            self.atualizar_lamina_atual(timeline=True)

    def _enquadramento_roda(self, evento):
        _idx, esp = self._espaco_enquadramento_ativo()
        if esp is None or not esp.get("foto"):
            return "break"
        self.marcar_historico()
        corte, _img_t, _asp = self._corte_atual_espaco(esp)
        fator = 1.08 if evento.delta > 0 else (1.0 / 1.08)
        novo_zoom = max(1.0, min(3.0, float(corte.get("zoom", 1.0)) * fator))
        self._aplicar_corte_inline(corte.get("cx", 0.5), corte.get("cy", 0.5), novo_zoom, final=True)
        return "break"

    def _resetar_enquadramento_inline(self):
        _idx, esp = self._espaco_enquadramento_ativo()
        if esp is None or not esp.get("foto"):
            return
        self.marcar_historico()
        ed = dict(esp.get("edicao") or {})
        ed.pop("corte", None)
        esp["edicao"] = ed
        self._atualizar_enquadramento_inline()
        self.atualizar_lamina_atual(timeline=True)
        self.registrar("Enquadramento da foto restaurado (centralizado em 1.0x).")

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
        for ch_pasta, _cam_pasta, nome_pasta, qtd in pastas:
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
            if pasta_alvo != "todas" and normalizar_caminho(proj.pasta_da_foto(foto)) != normalizar_caminho(pasta_alvo):
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
            usadas_p = sum(1 for _i, _f, u in fotos_p if u)
            if mostrar_divisores:
                itens.append({
                    "tipo": "pasta",
                    "pasta": ch_p,
                    "nome": nome_p,
                    "qtd": len(fotos_p),
                    "usadas": usadas_p,
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
        self._xs_painel_fotos = [it["x0"] for it in itens]
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
        norm = normalizar_caminho(caminho)
        lado = self._celula[2]
        chave = (norm, self.versao_fotos, lado, bool(usada))
        guardada = self._tk_miniaturas.get(chave)
        if guardada is not None:
            self._tk_miniaturas.move_to_end(chave)
            return guardada
        if norm not in self.info:
            return None
        # Se a miniatura base já estiver em RAM no cache do módulo analise, gera rápido;
        # caso contrário, agenda em segundo plano via Pintor para não travar a rolagem em álbuns grandes
        ja_em_ram = False
        try:
            ja_em_ram = analise._memoria_miniatura.pegar((norm, analise.LADO_MINIATURA)) is not None
        except Exception:
            ja_em_ram = False
        if ja_em_ram:
            try:
                img = self._miniatura_pil(caminho, lado, usada)
                foto = ImageTk.PhotoImage(img)
                self._tk_miniaturas[chave] = foto
                while len(self._tk_miniaturas) > 1200:
                    self._tk_miniaturas.popitem(last=False)
                return foto
            except Exception:
                return None
        self._pedir_miniatura_foto(caminho, chave, lado, bool(usada))
        return None

    def _pedir_miniatura_foto(self, caminho, chave, lado, usada):
        if chave in self._miniaturas_pendentes:
            return
        self._miniaturas_pendentes.add(chave)

        def trabalho():
            try:
                img = self._miniatura_pil(caminho, lado, usada)
                self.fila.put(("miniatura_foto", chave, img))
            except Exception:
                self.fila.put(("miniatura_foto", chave, None))

        self.pintor.pedir("ft:" + str(chave[0]) + ":" + str( int(usada) ), trabalho)

    def _receber_miniatura_foto(self, chave, img):
        self._miniaturas_pendentes.discard(chave)
        if img is not None:
            try:
                self._tk_miniaturas[chave] = ImageTk.PhotoImage(img)
                while len(self._tk_miniaturas) > 1200:
                    self._tk_miniaturas.popitem(last=False)
                self.agendar("fotos", 16, self._desenhar_fotos_visiveis)
            except tk.TclError:
                pass

    def _desenhar_fotos_visiveis(self):
        c = self.canvas_fotos
        c.delete("all")
        itens = getattr(self, "_itens_painel_fotos", [])
        xs = getattr(self, "_xs_painel_fotos", [])
        cw, ch, lado = self._celula
        if not itens:
            c.create_text(12, ch // 2, anchor="w", fill=tema.TEXTO_FRACO,
                          text="Sem fotos aqui. Arraste fotos ou várias pastas do Explorer (elas não se misturam).")
            return
        x0 = c.canvasx(0)
        larg_visivel = max(200, c.winfo_width())
        x1 = x0 + larg_visivel
        letras = max(10, (cw - 6) // 6)
        # Virtualização via busca binária O(log N): percorre apenas a janela visível + overscan de 3 células
        margem_overscan = max(cw * 3, 180)
        idx_ini = max(0, bisect.bisect_right(xs, x0 - margem_overscan) - 1) if xs else 0
        idx_fim = min(len(itens), bisect.bisect_right(xs, x1 + margem_overscan) + 1) if xs else len(itens)
        for idx_it in range(idx_ini, idx_fim):
            item = itens[idx_it]
            if item["x1"] < x0 - margem_overscan or item["x0"] > x1 + margem_overscan:
                continue
            if item["tipo"] == "pasta":
                ix0, ix1 = item["x0"], item["x1"]
                cx = (ix0 + ix1) // 2
                recolhida = item["recolhida"]
                c.create_rectangle(ix0 + 3, 4, ix1 - 3, ch - 4,
                                   fill=tema.PAINEL_2 if not recolhida else tema.DESTAQUE_ESCURO,
                                   outline=tema.DESTAQUE if not recolhida else tema.BORDA, width=1)
                seta = "▶" if recolhida else "▼"
                c.create_text(cx, 15, text="📁 %s" % seta, fill=tema.ALERTA, font=tema.FONTE_FORTE)
                nome_p = item["nome"]
                curto_p = nome_p if len(nome_p) <= 12 else nome_p[:10] + "…"
                c.create_text(cx, ch // 2 - 2, text=curto_p, fill=tema.TEXTO, font=tema.FONTE_PEQUENA, width=ix1 - ix0 - 8)
                usadas_p = item.get("usadas", 0)
                c.create_text(cx, ch - 14, text="%d/%d" % (usadas_p, item["qtd"]), fill=tema.VERDE if usadas_p == item["qtd"] else tema.TEXTO_FRACO, font=tema.FONTE_PEQUENA)
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
            if foto.get("grupo_ordem"):
                num_g = max(1, int(foto.get("grupo_ordem") or 1))
                gx0 = cx - lado // 2 + 2
                gy0 = 4
                gx1 = gx0 + (22 if num_g < 10 else 28)
                gy1 = 18
                c.create_rectangle(gx0, gy0, gx1, gy1, fill="#1e3a8a", outline="#60a5fa", width=1)
                c.create_text((gx0 + gx1) / 2.0, (gy0 + gy1) / 2.0, text="G%d" % num_g, fill="#ffffff", font=("Segoe UI", 7, "bold"))
            # Selo com a quantidade de rostos / classificação Individual vs. Grupo (ex.: 👤 1 ou 👥 5)
            dados_f = self.info.get(normalizar_caminho(foto["caminho"]))
            rosto_f = dados_f.get("rosto") if isinstance(dados_f, dict) else None
            tp_f = str(foto.get("tipo_pessoas") or "auto").strip().lower()
            if tp_f in ("individual", "grupo") or isinstance(rosto_f, dict):
                qtd_r = max(1, int(rosto_f.get("qtd") or 1)) if isinstance(rosto_f, dict) else (2 if tp_f == "grupo" else 1)
                em_g = proj.eh_foto_em_grupo(foto, self.info)
                icone_r = "👥" if em_g else "👤"
                etq_r = ("%d*" % qtd_r) if tp_f in ("individual", "grupo") else ("%d" % qtd_r)
                bx0 = cx - lado // 2 + 2
                by1 = ch - 18
                by0 = by1 - 14
                bx1 = bx0 + (26 if len(etq_r) <= 2 else 32)
                c.create_rectangle(
                    bx0,
                    by0,
                    bx1,
                    by1,
                    fill="#3b0764" if em_g else "#0e2942",
                    outline="#a855f7" if em_g else "#31a8ff",
                    width=1,
                )
                c.create_text(
                    (bx0 + bx1) / 2.0,
                    (by0 + by1) / 2.0,
                    text=icone_r + etq_r,
                    fill="#e9d5ff" if em_g else "#7dd3fc",
                    font=("Segoe UI", 7, "bold"),
                )
            nome = Path(foto["caminho"]).name
            c.create_text(cx, ch - 9, text=nome if len(nome) <= letras else nome[:letras - 2] + "…",
                          fill=tema.TEXTO if selecionada else tema.TEXTO_FRACO, font=tema.FONTE_PEQUENA)

    def _item_painel_fotos_em(self, evento):
        x = self.canvas_fotos.canvasx(evento.x)
        itens = getattr(self, "_itens_painel_fotos", [])
        xs = getattr(self, "_xs_painel_fotos", [])
        if not itens:
            return None
        if xs:
            idx = bisect.bisect_right(xs, x) - 1
            if 0 <= idx < len(itens):
                item = itens[idx]
                if item["x0"] <= x < item["x1"]:
                    return item
            return None
        for item in itens:
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
            menu.add_command(
                label="⚡ Diagramar somente a pasta '%s' (lâminas separadas)" % nome_pasta,
                command=lambda: self.diagramar_pasta_selecionada(ch_pasta),
            )
            menu.add_command(
                label="📁 Selecionar lâminas da pasta '%s' na linha do tempo" % nome_pasta,
                command=lambda: self.selecionar_laminas_da_pasta(ch_pasta),
            )
            menu.add_command(
                label="📁 Separar todas as pastas carregadas nas lâminas",
                command=self.separar_laminas_por_pasta,
            )
            menu.add_separator()
            menu.add_command(label="Selecionar todas as fotos da pasta '%s'" % nome_pasta,
                             command=lambda: self._selecionar_pasta_inteira(ch_pasta))
            menu.add_command(label="Filtrar somente a pasta '%s'" % nome_pasta,
                             command=lambda: (self.pasta_filtro.set(ch_pasta), self.atualizar_fotos()))
            menu.add_command(label="Mostrar todas as pastas",
                             command=lambda: (self.pasta_filtro.set("todas"), self.atualizar_fotos()))
            menu.add_separator()
            menu.add_command(
                label="🗑 Remover pasta '%s' do projeto" % nome_pasta,
                command=lambda: self.remover_pasta_carregada(ch_pasta),
            )
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
        menu_grupos = tk.Menu(menu, tearoff=0)
        for g_info in proj.listar_grupos_ordem(self.projeto):
            num_g = int(g_info.get("grupo_ordem", 1))
            nome_g = g_info.get("nome") or ("Grupo %d" % num_g)
            menu_grupos.add_command(
                label="Mover para %s (%d fotos)" % (nome_g, len(g_info.get("fotos", []))),
                command=lambda ng=num_g: self.mover_fotos_para_grupo(fotos, ng),
            )
        menu_grupos.add_separator()
        menu_grupos.add_command(
            label="＋ Criar Novo Grupo com %s foto(s)" % quantidade,
            command=lambda: self.mover_fotos_para_grupo(fotos, None),
        )
        menu_grupos.add_separator()
        menu_grupos.add_command(
            label="👤 Marcar como Foto Individual (1 pessoa)",
            command=lambda: self.definir_tipo_pessoas_fotos(fotos, "individual"),
        )
        menu_grupos.add_command(
            label="👥 Marcar como Foto em Grupo / Turma (2+ pessoas)",
            command=lambda: self.definir_tipo_pessoas_fotos(fotos, "grupo"),
        )
        menu_grupos.add_command(
            label="↺ Classificação Automática (pelo nº de rostos)",
            command=lambda: self.definir_tipo_pessoas_fotos(fotos, "auto"),
        )
        menu_grupos.add_separator()
        menu_grupos.add_command(
            label="🗂 Abrir Janela de Ordem e Grupos...",
            command=self.abrir_ordem_grupos,
        )
        menu.add_cascade(label="🗂 Ordem e Grupos (%s foto(s))" % quantidade, menu=menu_grupos)
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
        selecionadas = {i for i in getattr(self, "laminas_sel", set()) if i in itens}
        if isinstance(self.atual, int) and self.atual in itens and not selecionadas:
            selecionadas = {self.atual}
            self.laminas_sel = selecionadas
        if len(selecionadas) > 1:
            self.rotulo_timeline.configure(text="Lâminas  %s  (%d sel.)" % (len(self.projeto["laminas"]), len(selecionadas)))
        else:
            self.rotulo_timeline.configure(text="Lâminas  %s" % len(self.projeto["laminas"]))
        vertical = bool(self._tl_vertical)
        larg, alt, altura = self._medidas_timeline()
        self._tl_larg, self._tl_alt, self._tl_altura = larg, alt, altura
        self._tl_passo_v = alt + 28
        self._tl_dim = c.winfo_width() if vertical else altura
        passo = max(1, self._tl_passo())
        if vertical:
            largura = max(larg + 16, c.winfo_width())
            area_tl = (0, 0, largura, max(1, len(itens) * passo))
            v0 = c.canvasy(0)
            v1 = v0 + max(160, c.winfo_height())
        else:
            area_tl = (0, 0, max(1, len(itens) * passo), altura)
            v0 = c.canvasx(0)
            v1 = v0 + max(240, c.winfo_width())
        c.configure(scrollregion=area_tl)

        # Virtualização da linha do tempo: renderiza e pede miniaturas apenas para a janela visível (+ overscan de 3 lâminas)
        overscan = 3
        pos_ini = max(0, int(v0 // passo) - overscan)
        pos_fim = min(len(itens), int(v1 // passo) + 1 + overscan)

        for posicao in range(pos_ini, pos_fim):
            item = itens[posicao]
            if vertical:
                x, y = 8, posicao * passo + 6
            else:
                x, y = posicao * passo + 6, 6
            w_mm, h_mm = self._tamanho_item(item)
            h = min(int(larg * h_mm / w_mm), alt)
            w = int(h * w_mm / h_mm) if h == alt else larg
            xi = x + (larg - w) // 2
            yi = y + (alt - h) // 2
            ativa = item == self.atual
            em_multi = item in selecionadas
            if ativa or em_multi:
                cor_fundo_sel = tema.DESTAQUE_ESCURO if ativa else tema.MULTI_SEL_ESCURO
                cor_borda_sel = tema.DESTAQUE if ativa else tema.MULTI_SEL
                if vertical:
                    c.create_rectangle(3, y - 4, largura - 3, y + passo - 7, fill=cor_fundo_sel, outline=cor_borda_sel, width=2 if ativa else 1)
                else:
                    c.create_rectangle(x - 4, 2, x + larg + 4, altura - 2, fill=cor_fundo_sel, outline=cor_borda_sel, width=2 if ativa else 1)
            chave = self._chave_timeline(item)
            assinatura = "%s:%d" % (self._assinatura(item), w)
            pronta = self._tl_pil.get(chave)
            if pronta and pronta[0] == assinatura:
                self._tl_pil.move_to_end(chave)
                if chave not in self._tl_tk or self._tl_tk[chave][0] != assinatura:
                    self._tl_tk[chave] = (assinatura, ImageTk.PhotoImage(pronta[1]))
                    while len(self._tl_tk) > 240:
                        self._tl_tk.popitem(last=False)
                else:
                    self._tl_tk.move_to_end(chave)
                c.create_image(xi, yi, image=self._tl_tk[chave][1], anchor="nw")
            else:
                c.create_rectangle(xi, yi, xi + w, yi + h, fill="#ffffff", outline="")
                antiga = self._tl_tk.get(chave)
                if antiga and antiga[1].width() == w:
                    c.create_image(xi, yi, image=antiga[1], anchor="nw")
                self._pedir_timeline(item, chave, assinatura, w)
            if self.produto["paginas_por_lamina"] > 1:
                c.create_line(xi + w // 2, yi, xi + w // 2, yi + h, fill="#999999")
            if len(selecionadas) > 1 and em_multi:
                c.create_rectangle(xi + 2, yi + 2, xi + 16, yi + 15, fill=tema.DESTAQUE if ativa else tema.MULTI_SEL, outline="#ffffff", width=1)
                c.create_text(xi + 9, yi + 8, text="✓", fill="#ffffff", font=("Segoe UI", 7, "bold"))
            ch_pasta_lam, nome_pasta_lam, distintas_lam = proj.pasta_da_lamina(self.projeto, self.projeto["laminas"][item])
            pasta_ant = ""
            if posicao > 0:
                pasta_ant, _nm_ant, _d_ant = proj.pasta_da_lamina(self.projeto, self.projeto["laminas"][itens[posicao - 1]])
            mudou_pasta = bool(ch_pasta_lam and posicao > 0 and pasta_ant and ch_pasta_lam != pasta_ant)
            if mudou_pasta:
                if vertical:
                    c.create_line(4, y - 3, largura - 4, y - 3, fill=tema.ALERTA, width=2)
                else:
                    c.create_line(x - 4, 4, x - 4, altura - 4, fill=tema.ALERTA, width=2)
            if nome_pasta_lam:
                curto_pl = nome_pasta_lam if len(nome_pasta_lam) <= 10 else nome_pasta_lam[:8] + "…"
                cor_etq = "#f5a623" if len(distintas_lam) > 1 else ("#38c172" if (posicao == 0 or mudou_pasta) else "#9aa3ad")
                rotulo = "%d · 📁%s" % (item + 1, curto_pl)
                c.create_text(x + larg // 2, y + alt + 10, text=rotulo,
                              fill="#ffffff" if (ativa or em_multi) else cor_etq,
                              font=tema.FONTE_PEQUENA)
            else:
                rotulo = str(item + 1)
                c.create_text(x + larg // 2, y + alt + 10, text=rotulo,
                              fill="#ffffff" if (ativa or em_multi) else tema.TEXTO_FRACO,
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
            self.agendar("timeline", 50, self.desenhar_timeline)

    def _timeline_roda(self, evento):
        passos = -int(evento.delta / 40)
        if self._tl_vertical:
            self.canvas_timeline.yview_scroll(passos, "units")
        else:
            self.canvas_timeline.xview_scroll(passos, "units")
        self.agendar("timeline", 14, self.desenhar_timeline)

    def _pedir_timeline(self, item, chave, assinatura, largura):
        mini = {"capa": None, "laminas": {item: copy.deepcopy(self.projeto["laminas"][item])}}
        produto, info = self.produto, self.info

        def trabalho():
            img = exportar.renderizar_item(mini, produto, info, item, largura=largura, rapido=True)
            self.fila.put(("miniatura_timeline", chave, assinatura, img))

        self.pintor.pedir("tl:" + chave, trabalho)

    def _receber_timeline(self, chave, assinatura, img):
        self._tl_pil[chave] = (assinatura, img)
        self._tl_pil.move_to_end(chave)
        while len(self._tl_pil) > 240:
            self._tl_pil.popitem(last=False)
        self.agendar("timeline", 45, self.desenhar_timeline)

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
        ctrl = bool(evento.state & 0x0004)
        shift = bool(evento.state & 0x0001)
        if isinstance(item, int):
            if ctrl:
                if item in self.laminas_sel and len(self.laminas_sel) > 1:
                    self.laminas_sel.discard(item)
                    if self.atual == item:
                        self.atual = sorted(self.laminas_sel)[0]
                else:
                    self.laminas_sel.add(item)
                    self.atual = item
                self.ultima_lamina_clicada = item
                self.selecionar_lamina(self.atual, manter_selecao=True)
            elif shift:
                ancora = self.ultima_lamina_clicada if isinstance(self.ultima_lamina_clicada, int) else (self.atual if isinstance(self.atual, int) else item)
                a, b = sorted((ancora, item))
                self.laminas_sel.update(range(a, b + 1))
                self.selecionar_lamina(item, manter_selecao=True)
            else:
                self.laminas_sel = {item}
                self.ultima_lamina_clicada = item
                self.selecionar_lamina(item, manter_selecao=False)
            self._arrasto = {"tipo": "lamina", "item": item, "x": evento.x_root, "y": evento.y_root, "ativo": False}
        else:
            self.selecionar_lamina(item)

    def selecionar_todas_laminas(self):
        total = len(self.projeto.get("laminas", []))
        if total > 0:
            self.laminas_sel = set(range(total))
            self.espaco_sel = None
            self.espacos_sel.clear()
            self.desenhar_timeline()
            self._atualizar_fundo_ui()
            self.registrar("Todas as %d lâminas selecionadas em grupo." % total)

    def alternar_selecao_todas_laminas(self):
        total = len(self.projeto.get("laminas", []))
        if total <= 1:
            return
        if len(getattr(self, "laminas_sel", set())) >= total:
            self.limpar_selecao_laminas()
        else:
            self.selecionar_todas_laminas()

    def limpar_selecao_laminas(self):
        if isinstance(self.atual, int):
            self.laminas_sel = {self.atual}
        else:
            self.laminas_sel.clear()
        self.desenhar_timeline()
        self._atualizar_fundo_ui()

    def _timeline_menu(self, evento):
        item = self._timeline_em(self._eixo_tl(evento.x, evento.y))
        if not isinstance(item, int):
            return
        if item not in getattr(self, "laminas_sel", set()):
            self.selecionar_lamina(item, manter_selecao=False)
        else:
            self.selecionar_lamina(item, manter_selecao=True)
        n = len(self.projeto["laminas"])
        qtd_sel = len(self.laminas_sel)
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="Nova lâmina depois desta", command=self.adicionar_lamina)
        menu.add_command(
            label=("Duplicar as %d lâminas selecionadas" % qtd_sel) if qtd_sel > 1 else "Duplicar lâmina",
            command=lambda: self.duplicar_lamina(item),
        )
        menu.add_command(
            label=("Diagramar de novo as %d lâminas selecionadas" % qtd_sel) if qtd_sel > 1 else "Diagramar de novo",
            command=self.rediagramar_lamina,
        )
        menu.add_separator()
        menu.add_command(
            label="Aplicar fundo atual nas %d lâmina(s) selecionada(s)" % max(1, qtd_sel),
            command=self.fundo_em_selecionadas,
        )
        ch_pasta_tl, nome_pasta_tl, _dist_tl = proj.pasta_da_lamina(self.projeto, self.projeto["laminas"][item])
        if ch_pasta_tl:
            menu.add_command(
                label="📁 Selecionar todas as lâminas da pasta '%s'" % nome_pasta_tl,
                command=lambda: self.selecionar_laminas_da_pasta(ch_pasta_tl),
            )
            menu.add_command(
                label="⚡ Rediagramar somente a pasta '%s'" % nome_pasta_tl,
                command=lambda: self.diagramar_pasta_selecionada(ch_pasta_tl),
            )
        menu.add_command(
            label="📁 Separar pastas carregadas em lâminas exclusivas",
            command=self.separar_laminas_por_pasta,
        )
        menu.add_command(label="Selecionar todas as lâminas (%d)" % n, command=self.selecionar_todas_laminas)
        if qtd_sel > 1:
            menu.add_command(label="Manter apenas esta lâmina selecionada", command=self.limpar_selecao_laminas)
        menu.add_separator()

        def estado(ok):
            return "normal" if ok else "disabled"

        menu.add_command(label="Mover para antes (Ctrl+←)", state=estado(item > 0),
                         command=lambda: self.mover_lamina_passo(-1) if qtd_sel > 1 else self.mover_lamina(item, item - 1))
        menu.add_command(label="Mover para depois (Ctrl+→)", state=estado(item < n - 1),
                         command=lambda: self.mover_lamina_passo(1) if qtd_sel > 1 else self.mover_lamina(item, item + 1))
        menu.add_command(label="Mover para o início", state=estado(item > 0),
                         command=lambda: self.mover_lamina_para("inicio", item))
        menu.add_command(label="Mover para o fim", state=estado(item < n - 1),
                         command=lambda: self.mover_lamina_para("fim", item))
        menu.add_command(label="Mover para a posição...", state=estado(n > 1),
                         command=lambda: self.mover_lamina_para("perguntar", item))
        menu.add_separator()
        menu.add_command(
            label=("🗑 Apagar as %d lâminas selecionadas de uma vez (Del)" % qtd_sel) if qtd_sel > 1 else "Apagar lâmina (Del)",
            command=lambda: self.remover_lamina(item),
        )
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
        self.agendar("sugestoes", 60, self._calcular_sugestoes)

    def _calcular_sugestoes(self):
        lamina = self._lamina()
        if lamina is None:
            self._sugestoes = []
            self._chave_cache_sugestoes = None
        else:
            caminhos_f = tuple(
                (normalizar_caminho(e["foto"]), int((e.get("edicao") or {}).get("rot", 0)) % 360)
                for e in lamina.get("espacos", [])
                if e.get("foto")
            )
            cfg_atual = self._cfg()
            chave_sug = (
                self. produto["nome"],
                caminhos_f,
                round(float(cfg_atual.get("margem_mm", 0)), 2),
                round(float(cfg_atual.get("espacamento_mm", 0)), 2),
                len(self._modelos()),
                self.versao_fotos,
            )
            if getattr(self, "_chave_cache_sugestoes", None) != chave_sug:
                self._chave_cache_sugestoes = chave_sug
                fotos = self._fotos_info([e["foto"] for e in lamina["espacos"] if e.get("foto")], lamina)
                self._sugestoes = (
                    diagramar.opcoes_lamina(fotos, self.produto, cfg_atual, self._modelos(), maximo=12)
                    if fotos
                    else []
                )
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
            "meio_termo_esq": "Meio Termo (Esq. Sangrada + Safe)",
            "meio_termo_dir": "Meio Termo (Safe + Dir. Sangrada)",
            "sangria_total": "Preenchimento Máx. (Sangria Total)",
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
        lamina = self._lamina()
        pagina = self.pagina_modelo.get() if self.produto.get("paginas_por_lamina", 1) > 1 else 0
        nome_lado = "Página Esquerda" if pagina == 0 else "Página Direita"

        saem, ficam = ([], [])
        atravessadas = 0
        if lamina is not None:
            for e in lamina.get("espacos", []):
                if float(e.get("x", 0.0)) < 0.495 and (float(e.get("x", 0.0)) + float(e.get("w", 0.0))) > 0.505:
                    atravessadas += 1
            saem, ficam = self._particionar_espacos_pagina(lamina, pagina)

        fotos_pag = []
        lw, lh = lamina_mm(self.produto)
        for e in saem:
            if e.get("foto"):
                infos = self._fotos_info([e["foto"]], lamina)
                if infos:
                    fotos_pag.append(dict(infos[0], edicao=e.get("edicao") or {}))
                    continue
            prop = max(0.2, (e["w"] * lw) / max(1.0, e["h"] * lh))
            fotos_pag.append({"caminho": None, "proporcao": prop, "edicao": e.get("edicao") or {}})

        if hasattr(self, "rotulo_info_pagina_modelo"):
            txt_info = "%s: %d foto(s)" % (nome_lado, len(fotos_pag))
            if atravessadas and self.so_compativeis.get():
                txt_info += " (%d no meio preservada)" % atravessadas
            self.rotulo_info_pagina_modelo.configure(text=txt_info)

        if lamina is None:
            self._opcoes_pagina_modelos = []
            self._chave_cache_modelos_pag = None
        else:
            cfg_m = self._cfg()
            chave_mod = (
                self.produto["nome"],
                pagina,
                bool(self.so_compativeis.get()),
                round(float(cfg_m.get("margem_mm", 0)), 2),
                round(float(cfg_m.get("espacamento_mm", 0)), 2),
                tuple(
                    (
                        normalizar_caminho(f.get("caminho") or ""),
                        round(float(f.get("proporcao", 1.5)), 3),
                        int((f.get("edicao") or {}).get("rot", 0)) % 360,
                    )
                    for f in fotos_pag
                ),
                tuple(
                    (round(float(ef["x"]), 3), round(float(ef["y"]), 3), round(float(ef["w"]), 3), round(float(ef["h"]), 3))
                    for ef in ficam
                ),
            )
            if getattr(self, "_chave_cache_modelos_pag", None) != chave_mod:
                self._chave_cache_modelos_pag = chave_mod
                self._opcoes_pagina_modelos = diagramar.opcoes_pagina_unica(
                    fotos_pag, self.produto, pagina, cfg_m, maximo=14, espacos_fixos=ficam
                )

        self._areas_modelos = []
        largura = max(105, (c.winfo_width() - 18) // 2)
        y = 4

        # Cabeçalho 1: Layouts automáticos exclusivos para a página selecionada (Esquerda ou Direita)
        c.create_text(
            6,
            y,
            anchor="nw",
            text="LAYOUTS PARA A %s (%d FOTO%s)" % (nome_lado.upper(), max(1, len(fotos_pag)), "S" if len(fotos_pag) != 1 else ""),
            fill=tema.DESTAQUE,
            font=tema.FONTE_PEQUENA,
        )
        y += 18

        cores_estilo = {
            "safe_zone": "#5b8cc4",
            "sangria_pagina": "#d97706",
            "sem_corte": "#10b981",
        }

        for idx_op, opcao in enumerate(self._opcoes_pagina_modelos):
            coluna = idx_op % 2
            x = 4 + coluna * (largura + 10)
            id_op = "__pagina__:%d" % idx_op
            selecionado = self._modelo_sel == id_op
            h = largura * lh / lw

            # Desenha a lâmina inteira mostrando a página oposta preservada e a página escolhida destacada
            c.create_rectangle(
                x, y, x + largura, y + h,
                fill="#f4f4f4",
                outline=tema.DESTAQUE if selecionado else tema.BORDA,
                width=2 if selecionado else 1,
            )
            if self.produto.get("paginas_por_lamina", 1) > 1:
                px_ativa = x if pagina == 0 else x + largura / 2.0
                c.create_rectangle(px_ativa + 1, y + 1, px_ativa + largura / 2.0 - 1, y + h - 1, fill="#e8f1fb", outline="")
                c.create_line(x + largura / 2.0, y, x + largura / 2.0, y + h, fill="#999999")

            # Quadros da outra página (intactos, em cinza suave)
            for e_fixo in ficam:
                c.create_rectangle(
                    x + e_fixo["x"] * largura,
                    y + e_fixo["y"] * h,
                    x + (e_fixo["x"] + e_fixo["w"]) * largura,
                    y + (e_fixo["y"] + e_fixo["h"]) * h,
                    fill="#cbd5e1",
                    outline="#f4f4f4",
                )

            # Quadros novos da página selecionada
            cor_q = cores_estilo.get(opcao.get("estilo"), "#5b8cc4")
            for e_novo in opcao["espacos"]:
                c.create_rectangle(
                    x + e_novo["x"] * largura,
                    y + e_novo["y"] * h,
                    x + (e_novo["x"] + e_novo["w"]) * largura,
                    y + (e_novo["y"] + e_novo["h"]) * h,
                    fill=cor_q,
                    outline="#ffffff",
                )

            nome_op = opcao.get("nome", "Página")
            c.create_text(
                x + largura / 2.0,
                y + h + 9,
                text=nome_op if len(nome_op) < 26 else nome_op[:24] + "…",
                fill=tema.TEXTO if selecionado else tema.TEXTO_FRACO,
                font=tema.FONTE_PEQUENA,
            )
            self._areas_modelos.append((x, y, x + largura, y + h + 18, id_op))
            if coluna == 1 or idx_op == len(self._opcoes_pagina_modelos) - 1:
                y += h + 28

        # Cabeçalho 2: Modelos salvos pelo usuário e Moldes PNG
        modelos = self._modelos()
        if modelos:
            y += 4
            c.create_text(6, y, anchor="nw", text="MODELOS SALVOS E MOLDES PNG", fill=tema.TEXTO_FRACO, font=tema.FONTE_PEQUENA)
            y += 18
            for i, modelo in enumerate(modelos):
                coluna = i % 2
                x = 4 + coluna * (largura + 10)
                if modelo.get("escopo") == "pagina" and self.produto["paginas_por_lamina"] > 1:
                    espacos, _s = modelos_lamina.espacos_do_modelo(modelo, self.produto, 0)
                    espacos = [dict(e, x=e["x"] * 2, w=e["w"] * 2) for e in espacos]
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

        c.configure(scrollregion=(0, 0, c.winfo_width(), y + 16))

    def _modelo_clicado(self, evento):
        y = self.canvas_modelos.canvasy(evento.y)
        for x0, y0, x1, y1, id_modelo in getattr(self, "_areas_modelos", []):
            if x0 <= evento.x <= x1 and y0 <= y <= y1:
                self._modelo_sel = id_modelo
                self.desenhar_modelos()
                # Se for um modelo dinâmico de página única, aplica imediatamente ao clicar (como na aba Sugestões)
                if isinstance(id_modelo, str) and id_modelo.startswith("__pagina__:"):
                    self.aplicar_modelo_selecionado()
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
        cor = tema.escolher_cor_photoshop(
            self.root,
            cor_inicial=cor_atual,
            titulo="Cor de Fundo · Seletor Photoshop",
        )
        if cor:
            self.definir_cor_fundo(cor)

    def capturar_cor_fundo_tela(self):
        dono = self._dono_do_fundo()
        if dono is None:
            return
        tema.ContaGotasTela(self.root, lambda c: self.definir_cor_fundo(c) if c else None)

    def definir_cor_fundo(self, cor):
        dono = self._dono_do_fundo()
        if dono is None or not cor:
            return
        self.marcar_historico()
        ch_cor, _ch_img = self._chaves_fundo_alvo()
        dono[ch_cor] = cor_hex(cor)
        if ch_cor == "fundo":
            self.projeto.setdefault("config", {})["fundo"] = cor_hex(cor)
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

    def _indices_laminas_selecionadas(self):
        total = len(self.projeto.get("laminas", []))
        sel = sorted(i for i in getattr(self, "laminas_sel", set()) if isinstance(i, int) and 0 <= i < total)
        if not sel and isinstance(self.atual, int) and 0 <= self.atual < total:
            sel = [self.atual]
        return sel

    def fundo_em_selecionadas(self):
        """Aplica o fundo e todas as suas configurações da lâmina aberta nas lâminas selecionadas (Ctrl/Shift+Clique)."""
        dono = self._dono_do_fundo()
        alvos = self._indices_laminas_selecionadas()
        if dono is None or not alvos:
            return
        self.marcar_historico()
        chaves_copiar = ("fundo", "fundo_esquerda", "fundo_direita", "fundo_fusao",
                         "fundo_imagem", "fundo_imagem_esquerda", "fundo_imagem_direita")
        for idx in alvos:
            outra = self.projeto["laminas"][idx]
            if outra is dono:
                continue
            for ch in chaves_copiar:
                if dono.get(ch) is not None:
                    outra[ch] = proj.copia(dono[ch])
                else:
                    outra.pop(ch, None)
        self.atualizar_tudo()
        self.registrar("Fundo e configurações aplicados nas %s lâmina(s) selecionada(s)." % len(alvos))

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
        Põe a imagem como fundo do alvo selecionado (lâmina inteira, página esquerda ou página direita)
        com ajustes zerados exclusivos para este fundo (não herda ajustes de outros fundos).
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
        dados["caminho"] = str(Path(caminho))
        dono[ch_img] = dados
        self._fundos_avisados.discard(normalizar_caminho(caminho))
        self._atualizar_fundo_ui()
        self.atualizar_lamina_atual()
        nome_alvo = {"fundo_imagem": "lâmina %s" % (item + 1),
                     "fundo_imagem_esquerda": "lâmina %s (pág. esquerda)" % (item + 1),
                     "fundo_imagem_direita": "lâmina %s (pág. direita)" % (item + 1)}[ch_img]
        self.registrar("Fundo da %s: %s" % (nome_alvo, Path(caminho).name))
        return True

    def aplicar_fundo_em_selecionadas(self, caminho):
        alvos = self._indices_laminas_selecionadas()
        if not alvos:
            return
        self.marcar_historico()
        _ch_cor, ch_img = self._chaves_fundo_alvo()
        for idx in alvos:
            lamina = self.projeto["laminas"][idx]
            dados = fundo_imagem_padrao()
            dados["caminho"] = str(Path(caminho))
            lamina[ch_img] = dados
        self.atualizar_tudo()
        self.registrar("Fundo %s aplicado em %s lâmina(s) selecionada(s)." % (Path(caminho).name, len(alvos)))

    def aplicar_fundo_em_todas(self, caminho):
        if not self.projeto["laminas"]:
            return
        self.marcar_historico()
        _ch_cor, ch_img = self._chaves_fundo_alvo()
        for lamina in self.projeto["laminas"]:
            dados = fundo_imagem_padrao()
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
        qtd_sel = len(self._indices_laminas_selecionadas())
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="Aplicar no alvo atual (%s)" % self.fundo_alvo.get(), command=lambda: self.aplicar_fundo_em(caminho))
        menu.add_command(label="Aplicar na Lâmina inteira", command=lambda: (self.fundo_alvo.set("lamina"), self.aplicar_fundo_em(caminho, alvo="lamina")))
        menu.add_command(label="Aplicar na Página Esquerda", command=lambda: (self.fundo_alvo.set("esquerda"), self.aplicar_fundo_em(caminho, alvo="esquerda")))
        menu.add_command(label="Aplicar na Página Direita", command=lambda: (self.fundo_alvo.set("direita"), self.aplicar_fundo_em(caminho, alvo="direita")))
        menu.add_separator()
        menu.add_command(label="Aplicar nas %d lâmina(s) selecionada(s)" % max(1, qtd_sel), command=lambda: self.aplicar_fundo_em_selecionadas(caminho))
        menu.add_command(label="Aplicar em todas as lâminas", command=lambda: self.aplicar_fundo_em_todas(caminho))
        menu.add_command(label="Ajustar cor / Tingir...", command=lambda: self.abrir_editor_fundo(caminho))
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
            self.cancelar_agendado("lamina_fundo")
            self._cancelar_previa_fundo()
            self._atualizar_fundo_ui()
            self.desenhar_lamina()
        else:
            self._pedir_previa_fundo()
            self.agendar("lamina_fundo", 280, self.desenhar_lamina)
        self.agendar("timeline_fundo", 500, self.desenhar_timeline)

    def _pedir_previa_fundo(self):
        espera = 0.016 - (time.perf_counter() - self._previa_fundo_ultima)
        if espera <= 0:
            self._cancelar_previa_fundo()
            self._previa_fundo_rapida()
        elif self._previa_fundo_agendada is None:
            self._previa_fundo_agendada = self.root.after(max(1, int(espera * 1000)), self._previa_fundo_rapida)

    def _cancelar_previa_fundo(self):
        if self._previa_fundo_agendada is not None:
            try:
                self.root.after_cancel(self._previa_fundo_agendada)
            except tk.TclError:
                pass
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

    def _alternar_espelho_fundo(self, chave):
        dono = self._dono_do_fundo()
        _ch_cor, ch_img = self._chaves_fundo_alvo()
        dados = (dono or {}).get(ch_img)
        if not dados:
            return
        self.marcar_historico()
        dados[chave] = not bool(dados.get(chave, False))
        self._atualizar_fundo_ui()
        self.fundo_ajustado(final=True)

    def _girar_fundo_90(self):
        dono = self._dono_do_fundo()
        _ch_cor, ch_img = self._chaves_fundo_alvo()
        dados = (dono or {}).get(ch_img)
        if not dados:
            return
        self.marcar_historico()
        dados["giro"] = (int(dados.get("giro") or 0) + 90) % 360
        self._atualizar_fundo_ui()
        self.fundo_ajustado(final=True)

    def _mudou_fundo_imagem(self, marcar):
        if self._carregando_fundo:
            return
        dono = self._dono_do_fundo()
        _ch_cor, ch_img = self._chaves_fundo_alvo()
        dados = (dono or {}).get(ch_img)
        if "Zoom" in self._rotulos_fundo:
            self._rotulos_fundo["Zoom"].configure(text="%d%%" % round(self.fundo_escala.get()))
        self._rotulos_fundo["Opacidade"].configure(text="%d%%" % round(self.fundo_opacidade.get()))
        self._rotulos_fundo["Desfoque"].configure(text="%d" % round(self.fundo_desfoque.get()))
        if not dados:
            return
        escala_nova = max(1.0, min(3.0, round(self.fundo_escala.get()) / 100.0))
        novo = dict(
            dados,
            modo=self.fundo_modo.get(),
            opacidade=int(round(self.fundo_opacidade.get())),
            desfoque=int(round(self.fundo_desfoque.get())),
            escala=escala_nova,
            pb=bool(self.fundo_pb.get()),
        )
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
                self.fundo_escala.set(int(round(float(base.get("escala") or 1.0) * 100)))
                self.fundo_pb.set(bool(base["pb"]))
                self.fundo_espelhar_h.set(bool(base.get("espelhar_h")))
                self.fundo_espelhar_v.set(bool(base.get("espelhar_v")))
                giro_val = int(base.get("giro") or 0) % 360
                self.fundo_giro.set(giro_val)
                if hasattr(self, "rotulo_info_tf_fundo"):
                    partes_tf = ["%d°" % giro_val]
                    if base.get("espelhar_h"):
                        partes_tf.append("⇄H")
                    if base.get("espelhar_v"):
                        partes_tf.append("⇅V")
                    self.rotulo_info_tf_fundo.configure(text=" ".join(partes_tf))
            else:
                if hasattr(self, "rotulo_info_tf_fundo"):
                    self.rotulo_info_tf_fundo.configure(text="0°")
                if alvo in ("esquerda", "direita") and not (dono or {}).get("fundo_" + alvo):
                    self.rotulo_fundo_imagem.configure(text="Usando o fundo da lâmina inteira (escolha cor ou imagem para separar esta página).")
                else:
                    self.rotulo_fundo_imagem.configure(text="Sem imagem: só a cor.")
            for controle in self._controles_fundo:
                controle.state(["!disabled"] if (dados and dados.get("caminho")) else ["disabled"])
            if hasattr(self, "botao_fundo_selecionadas"):
                qtd_sel = len(self._indices_laminas_selecionadas())
                self.botao_fundo_selecionadas.configure(text="Aplicar nas selecionadas (%d)" % max(1, qtd_sel))
        finally:
            self._carregando_fundo = False
        if "Zoom" in self._rotulos_fundo:
            self._rotulos_fundo["Zoom"].configure(text="%d%%" % round(self.fundo_escala.get()))
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

    def _restaurar_janela_smart_album(self, modificadas=None, motivo="salvo"):
        """Restaura a janela do Smart Album para o primeiro plano assim que a edição no Photoshop termina (Ctrl+S ou fechar)."""
        estado_ant = getattr(self, "_estado_janela_antes_ps", "zoomed")
        maximizar = (estado_ant == "zoomed")
        try:
            self.root.deiconify()
            self.root.state("zoomed" if maximizar else "normal")
            self.root.update_idletasks()
            photoshop.trazer_janela_para_frente(self.root.winfo_id(), maximizar=maximizar)
            self.root.lift()
            self.root.attributes("-topmost", True)
            self.root.after(300, lambda: self.root.attributes("-topmost", False))
            self.root.focus_force()
        except Exception:
            pass
        if getattr(self, "_tratamento_oculto_ps", False):
            self._tratamento_oculto_ps = False
            if self.janela_tratamento is not None and self.janela_tratamento.winfo_exists():
                try:
                    self.janela_tratamento.deiconify()
                    self.janela_tratamento.lift()
                except Exception:
                    pass
        for c in (modificadas or []):
            self._foto_trocada(c, c)
        if modificadas:
            self.registrar("Foto atualizada pelo Photoshop (%d arquivo(s)) · Smart Album restaurado." % len(modificadas))
        else:
            self.registrar("Retornado do Photoshop para o Smart Album.")

    def editar_no_photoshop(self, caminhos):
        caminhos = [c for c in caminhos if c]
        if not caminhos:
            self.registrar("Selecione uma ou mais fotos.")
            return
        for caminho in caminhos:
            self.observador.vigiar(caminho)
        try:
            est = self.root.state()
            if est in ("zoomed", "normal"):
                self._estado_janela_antes_ps = est
        except Exception:
            self._estado_janela_antes_ps = "zoomed"

        def trabalho(t):
            photoshop.abrir_para_editar(caminhos)

        def fim(_r, erro):
            if erro:
                self._restaurar_janela_smart_album()
                messagebox.showerror("Photoshop", str(erro), parent=self.root)
            else:
                # Minimiza o Smart Album primeiro para liberar o foco do Windows e ativa o Photoshop
                self._tratamento_oculto_ps = False
                if self.janela_tratamento is not None and self.janela_tratamento.winfo_exists() and self.janela_tratamento.winfo_ismapped():
                    try:
                        self.janela_tratamento.withdraw()
                        self._tratamento_oculto_ps = True
                    except Exception:
                        pass
                try:
                    self.root.iconify()
                    self.root.update_idletasks()
                except Exception:
                    pass
                photoshop.ativar_janela_photoshop()
                self.registrar("Editando no Photoshop... Ao salvar (Ctrl+S) ou fechar a foto, o Smart Album volta automaticamente.")
                photoshop.monitorar_edicao_photoshop(
                    caminhos,
                    ao_salvar=lambda c: self.fila.put(("chamar", lambda cam=c: self._foto_trocada(cam, cam))),
                    ao_terminar=lambda mods, mot: self.fila.put(("chamar", lambda m=mods, mt=mot: self._restaurar_janela_smart_album(m, mt))),
                )

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
        pasta_salva = op.get("pasta") or self.projeto.get("pasta_saida") or ""
        if pasta_salva and Path(pasta_salva).name.lower() in ("saida", "saída") and not Path(pasta_salva).exists():
            pasta_salva = str(Path(pasta_salva).parent)
        op["pasta"] = pasta_salva or self.projeto.get("pasta_fotos") or str(Path.home())
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

    def parar_fila_exportacao(self):
        """Cancela a exportação atual em background e esvazia os jobs pendentes da fila de exportação."""
        if hasattr(self, "fila_jobs_exportacao"):
            self.fila_jobs_exportacao.clear()
        tarefa_exp = getattr(self, "_tarefa_exportacao_atual", None)
        if tarefa_exp is not None:
            tarefa_exp.parada.set()
            self.registrar("Parando fila de exportação em background...")
        if self.tarefa is not None:
            self.parar_tarefa()

    def _iniciar_proximo_job_exportacao(self):
        if getattr(self, "exportacao_worker_ativo", False):
            return
        fila_jobs = getattr(self, "fila_jobs_exportacao", None)
        if not fila_jobs:
            self.exportacao_worker_ativo = False
            self._tarefa_exportacao_atual = None
            self._job_exportacao_atual = None
            self._atualizar_barra_abas_albuns()
            return

        job = fila_jobs.pop(0)
        self.exportacao_worker_ativo = True
        self._job_exportacao_atual = job
        tarefa = Tarefa(self)
        self._tarefa_exportacao_atual = tarefa
        self.botao_parar.configure(state="normal")
        self._atualizar_barra_abas_albuns()
        self.registrar("⚡ [Fila Worker] Iniciando exportação isolada de %s (%d threads)..." % (job["nome_album"], job["workers"]))

        def _rodar_worker_job():
            gravados, erro = [], None
            try:
                gravados = job["trabalho"](tarefa)
            except Exception as e:
                erro = e
                self.fila.put(("aviso", "ERRO na exportação: %s" % e))
                traceback.print_exc()

            def _concluir_na_ui():
                self.exportacao_worker_ativo = False
                self._tarefa_exportacao_atual = None
                self._job_exportacao_atual = None
                if not getattr(self, "fila_jobs_exportacao", []):
                    self.botao_parar.configure(state="disabled")
                    self.barra.configure(value=0)
                self._atualizar_barra_abas_albuns()
                try:
                    job["fim"](gravados, erro)
                finally:
                    self._iniciar_proximo_job_exportacao()

            self.fila.put(("chamar", _concluir_na_ui))

        threading.Thread(target=_rodar_worker_job, daemon=True, name="SmartAlbumExportQueueDispatcher").start()

    def executar_exportacao(self, op, janela=None):
        """
        Enfileira um job de exportação na Fila de Processamento Worker-based em background:
        - Congela um snapshot imutável (copy.deepcopy) de projeto, produto, info e opções no instante do clique
        - Permite ao usuário criar ou alternar para outra aba de álbum e editar livremente sem confundir páginas
        - Cria a subpasta exclusiva 'Album <TAMANHO> - <Nome do Álbum>' na pasta base
        - Dentro dela cria APENAS as subpastas dos formatos marcados ('JPG', 'PSD', 'PDF').
        """
        ativos = exportar.formatos_ativos(op)
        if not ativos:
            messagebox.showwarning("Exportar", "Marque pelo menos um formato de saída (JPG, PSD ou PDF).", parent=janela or self.root)
            return False
        try:
            indices = janela_exportar.indices_escolhidos(op, self.projeto, self.atual)
            itens = exportar.itens_exportacao(self.projeto, indices, op["padrao"], self.produto)
        except ValueError as e:
            messagebox.showwarning("Exportar", str(e), parent=janela or self.root)
            return False
        if not op.get("pasta"):
            op["pasta"] = self.projeto.get("pasta_fotos") or (
                str(Path(self.caminho_projeto).parent) if self.caminho_projeto else str(Path.home())
            )
        # Snapshot imutável (deep copy) antes de qualquer troca de aba ou edição subsequente
        copia_projeto = copy.deepcopy(self.projeto)
        copia_produto = copy.deepcopy(self.produto)
        copia_info = copy.deepcopy(self.info)
        pasta_album = janela_exportar.pasta_final(op, copia_projeto, copia_produto)
        previstos = janela_exportar.arquivos_previstos(op, copia_projeto, copia_produto, itens)
        existentes = [arq for arq in previstos if (pasta_album / arq).exists()]
        modo = op["existentes"]
        if modo == "perguntar" and existentes:
            resposta = messagebox.askyesnocancel(
                "Exportar", "%s arquivo(s) já existem em\n%s\n\nSim: substituir   ·   Não: pular os que existem"
                % (len(existentes), pasta_album), parent=janela or self.root)
            if resposta is None:
                return False
            modo = "substituir" if resposta else "pular"
        elif modo == "perguntar":
            modo = "substituir"
        self.guardar_opcoes_exportacao(op)
        janela_exportar.lembrar_pasta(self.arquivo_exportacao(), op["pasta"])
        num_workers = max(1, min(8, int(op.get("workers") or min(4, max(2, os.cpu_count() or 2)))))
        opcoes = copy.deepcopy(dict(op, indices=list(indices), existentes=modo, workers=num_workers))

        if not hasattr(self, "fila_jobs_exportacao"):
            self.fila_jobs_exportacao = []
        if not hasattr(self, "exportacao_worker_ativo"):
            self.exportacao_worker_ativo = False

        if janela is not None:
            self.janela_tarefa = janela

        id_aba_origem = None
        if hasattr(self, "abas_albuns") and 0 <= getattr(self, "aba_album_ativa", 0) < len(self.abas_albuns):
            id_aba_origem = self.abas_albuns[self.aba_album_ativa].get("id")

        def trabalho(t):
            gravados = []
            pasta_album.mkdir(parents=True, exist_ok=True)
            if "jpg" in ativos and not t.parar():
                pasta_jpg = janela_exportar.pasta_do_formato(pasta_album, "jpg")
                pasta_jpg.mkdir(parents=True, exist_ok=True)
                gravados += exportar.exportar_jpg(copia_projeto, copia_produto, copia_info, pasta_jpg, t.aviso, t.parar, t.progresso, opcoes)
            if "psd" in ativos and not t.parar():
                import exportar_psd
                pasta_psd = janela_exportar.pasta_do_formato(pasta_album, "psd")
                pasta_psd.mkdir(parents=True, exist_ok=True)
                gravados += exportar_psd.exportar_psd(copia_projeto, copia_produto, copia_info, pasta_psd, t.aviso, t.parar, t.progresso, opcoes)
            if "pdf" in ativos and not t.parar():
                pasta_pdf = janela_exportar.pasta_do_formato(pasta_album, "pdf")
                pasta_pdf.mkdir(parents=True, exist_ok=True)
                gravados += exportar.exportar_pdf(copia_projeto, copia_produto, copia_info, pasta_pdf, t.aviso, t.parar, t.progresso, opcoes)
            if not t.parar():
                caminho_json_alb = pasta_album / exportar.nome_json_projeto(copia_projeto, copia_produto)
                proj.salvar(copia_projeto, caminho_json_alb)
                gravados.append(caminho_json_alb)
            return gravados

        def fim(gravados, erro):
            if janela is not None and janela.winfo_exists():
                janela.terminou(gravados, erro, pasta_exportada=pasta_album)
            elif erro:
                messagebox.showerror("Exportar", str(erro), parent=self.root)
            elif gravados:
                self.registrar("✓ Exportação em background concluída: %d arquivo(s) em %s" % (len(gravados), pasta_album))

        self.fila_jobs_exportacao.append({
            "id_aba": id_aba_origem,
            "nome_album": pasta_album.name,
            "pasta_album": str(pasta_album),
            "workers": num_workers,
            "trabalho": trabalho,
            "fim": fim,
        })
        self._atualizar_barra_abas_albuns()
        if janela is not None and janela.winfo_exists():
            janela.aviso(
                "⚡ Job isolado adicionado à Fila (%s · %d Workers · %s)"
                % (pasta_album.name, num_workers, ", ".join(f.upper() for f in ativos))
            )
        self._iniciar_proximo_job_exportacao()
        return True

    def exportar(self, tipo=None):
        """Sem tipo abre a janela Exportar; com tipo exporta direto com as opções guardadas no álbum."""
        if tipo is None:
            return self.abrir_exportar()
        if not self.projeto["laminas"]:
            self.registrar("Não há lâminas para exportar.")
            return False
        op = dict(
            self.opcoes_exportacao(),
            formato=tipo,
            exportar_jpg=(tipo in ("jpg", "ambos", "jpg_pdf")),
            exportar_psd=(tipo in ("psd", "ambos")),
            exportar_pdf=(tipo in ("pdf", "jpg_pdf")),
        )
        avisos = janela_exportar.conferir(self.projeto, janela_exportar.indices_escolhidos(op, self.projeto, self.atual))
        if avisos and not messagebox.askyesno("Exportar", "\n".join(avisos) + "\n\nExportar mesmo assim?", parent=self.root):
            return False
        return self.executar_exportacao(op)


def main():
    try:
        argumentos = [a for a in sys.argv[1:] if a]
        root = None
        if TkinterDnD is not None:
            try:
                root = TkinterDnD.Tk()
            except Exception:
                root = None
        if root is None:
            root = tk.Tk()
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
