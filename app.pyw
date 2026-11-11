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
import random
import re
import subprocess
import sys
import threading
import time
import traceback
import tkinter as tk
from collections import OrderedDict
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PIL import Image, ImageEnhance, ImageTk

import analise
import capa as atalho_capa
import diagramar
import entrada
import exportar
import ia_cena
import ia_modelos
import produtos as modelos_lamina
import photoshop
import projeto as proj
import recorte
import tema
from comum import (
    EXTENSOES, apagar_pasta, cor_hex, cor_rgb, gravar_json, gravar_texto, ler_json,
    limpar_fundos_temporarios, limpar_temporarios_disco, listar_fundos_temporarios,
    normalizar_caminho, pasta_dados, pasta_fundos_temporarios, registrar_erro, texto_json,
)
import fundos
import janela_drive
import janela_exportar
import janela_tratamento
from composicao import (
    CATALOGO_TEMAS, ESTILOS_MOLDURA, MAPA_TEMAS, MODOS_FUNDO,
    NOMES_ESTILOS_MOLDURA, PRESETS_SOMBRA_PROJETADA, aplicar_moldura,
    compor_fundo_completo, degrade_padrao, eh_fundo_gerado, extrair_paleta_fotos,
    fundo_imagem_padrao, gerar_galeria_fundos_procedurais,
    materializar_fundo_gerado_em_arquivo, rosto_de, sombra_padrao,
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
LARGURA_MAX_RENDER = 3000
LIMITE_CACHE_LAMINAS = 12
PIXELS_CACHE_LAMINAS = 16_000_000
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
VERSAO_ARRANJO = 6
ARRANJO_PADRAO = {
    "versao": VERSAO_ARRANJO,
    "geometria": "1280x760+30+20",
    "maximizada": True,
    "mostrar_timeline": True,
    "mostrar_fotos": True,
    "mostrar_lateral": True,
    "timeline": "baixo",
    "fotos": "baixo",
    "lateral": "direita",
    "largura_timeline": 360,
    "altura_timeline": 114,
    "altura_fotos": 118,
    "largura_fotos": 290,
    "largura_lateral": 310,
    "lateral_conteudo_aberto": True,
    "secoes_recolhidas": {},
}
LADOS_TIMELINE = ("esquerda", "direita", "cima", "baixo")
CHAVES_TIMELINE_V1 = ("timeline", "altura_timeline", "largura_timeline", "altura_fotos", "largura_fotos", "largura_lateral")
MINIMOS = {"timeline": 76, "timeline_vertical": 110, "fotos": 84, "fotos_vertical": 210, "lateral": 265, "centro": 280, "meio": 220}
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


def _baixar_prioridade_thread():
    """Thread da IA abaixo do normal no Windows: a janela e a análise normal continuam na frente."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        k32.SetThreadPriority(k32.GetCurrentThread(), -1)
    except Exception:
        pass


def definir_cursor(widget, cursor):
    """Só reconfigura quando muda: configure() no canvas força redesenho e aviso de rolagem."""
    try:
        if str(widget.cget("cursor")) != cursor:
            widget.configure(cursor=cursor)
    except tk.TclError:
        pass


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

    def __init__(self, ao_descartar=None):
        self.ao_descartar = ao_descartar
        self.pedidos_principal = OrderedDict()
        self.pedidos_tl = OrderedDict()
        self.cond_principal = threading.Condition()
        self.cond_tl = threading.Condition()
        threading.Thread(target=self._rodar_principal, daemon=True).start()
        for _ in range(2):
            threading.Thread(target=self._rodar_tl, daemon=True).start()

    def pedir(self, chave, funcao):
        eh_principal = chave in ("principal", "frente")
        descartados = []
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
                    descartados.append(self.pedidos_tl.popitem(last=False)[0])
                self.cond_tl.notify()
        if self.ao_descartar is not None:
            for chave_d in descartados:
                self.ao_descartar(chave_d)

    def _rodar_principal(self):
        while True:
            with self.cond_principal:
                while not self.pedidos_principal:
                    self.cond_principal.wait()
                chave, funcao = self.pedidos_principal.popitem(last=False)
            try:
                funcao()
            except Exception:
                registrar_erro("pintor '%s'" % chave)

    def _rodar_tl(self):
        while True:
            with self.cond_tl:
                while not self.pedidos_tl:
                    self.cond_tl.wait()
                chave, funcao = self.pedidos_tl.popitem(last=True)
            try:
                funcao()
            except Exception:
                registrar_erro("pintor '%s'" % chave[:120])


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
        root.title("Diagramador Inteligente")
        root.minsize(900, 540)
        self.arranjo = ler_arranjo()
        self.arranjo["maximizada"] = True
        root.geometry(geometria_valida(self.arranjo.get("geometria"), root) or ARRANJO_PADRAO["geometria"])
        self._tela_cheia_total = False
        try:
            root.state("zoomed")
        except tk.TclError:
            try:
                root.attributes("-zoomed", True)
            except tk.TclError:
                pass
        root.after(60, self._garantir_tela_cheia_inicial)
        root.bind("<F11>", lambda _e: self.alternar_tela_cheia())

        self.fila = queue.Queue()
        self._instalar_registro_erros(root)
        self.pintor = Pintor(ao_descartar=lambda chave: self.fila.put(("descartado", chave)))
        self._pedidos_miniatura = {}
        self.observador = Observador(lambda c: self.fila.put(("arquivo_mudou", c)))
        self.produtos = listar_produtos()
        self.produto = self.produtos[0] if self.produtos else None
        self.projeto = proj.novo(self.produto["nome"] if self.produto else "")
        self.caminho_projeto = None
        self.modificado = False
        self._versao_modificacao = 0
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
        self._lista_fotos = []
        self._itens_painel_fotos = []
        self._xs_painel_fotos = []
        self._miniaturas_pendentes = {}
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
        self.contagem_desenhos = {"timeline": 0, "galeria": 0}
        self._paineis_pendentes = set()
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
        # Sobras de uma sessão fechada à força; síncrono para não apagar um fundo recém-gerado pela UI.
        self._limpar_fundos_gerados()
        threading.Thread(
            target=lambda: limpar_temporarios_disco(pastas_protegidas=self._pastas_temp_drive, limpar_autosave=False),
            daemon=True,
        ).start()
        self.atalhos_mapa = self._carregar_atalhos()
        self._gravando_atalho_acao = None
        self._botoes_atalhos_ui = {}
        self._teclas_vinculadas = []

        self._ia_iniciar()
        self._montar()
        self.root.bind("<Map>", self._algo_apareceu, add="+")
        self._atalhos()
        self._registrar_drop()
        self.root.protocol("WM_DELETE_WINDOW", self.sair)
        self.root.after(30, self._ler_fila)
        self._carregar_ui_do_projeto()
        self.agendar("limpeza_temp_drive", 30000, self._executar_limpeza_programada_temp_drive)
        self.atualizar_tudo()
        threading.Thread(target=entrada.registrar_associacao_arquivos_windows, daemon=True).start()
        if argumentos:
            self.root.after(300, lambda: self.adicionar_caminhos(list(argumentos), vindo_linha_comando=True))

    @staticmethod
    def _instalar_registro_erros(root):
        """Erros de callbacks do Tk e de threads também vão para erros.log (sem tirar o tratamento que já existe)."""
        anterior = root.report_callback_exception

        def ao_erro_callback(tipo, valor, tb):
            registrar_erro("callback do Tk", (tipo, valor, tb), eco=False)
            anterior(tipo, valor, tb)

        root.report_callback_exception = ao_erro_callback
        if not getattr(threading.excepthook, "_smart_album", False):
            hook_anterior = threading.excepthook

            def ao_erro_thread(args):
                if args.exc_type is not SystemExit:
                    nome = getattr(args.thread, "name", "?")
                    registrar_erro("thread '%s'" % nome, (args.exc_type, args.exc_value, args.exc_traceback), eco=False)
                hook_anterior(args)

            ao_erro_thread._smart_album = True
            threading.excepthook = ao_erro_thread

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

        self.barra_opcoes = tk.Frame(self.root, bg=tema.CABECALHO, height=24)
        self.barra_opcoes.pack(side="top", fill="x")
        self._montar_barra_opcoes()
        tk.Frame(self.root, bg=tema.BORDA, height=1).pack(side="top", fill="x")

        barra_abas_albuns = tk.Frame(self.root, bg=tema.PAINEL, padx=6, pady=2)
        barra_abas_albuns.pack(side="top", fill="x")
        self.barra_abas_albuns = barra_abas_albuns
        self._atualizar_barra_abas_albuns()

        tk.Frame(self.root, bg=tema.BORDA, height=1).pack(side="top", fill="x")

        status = ttk.Frame(self.root, padding=(10, 3))
        status.pack(side="bottom", fill="x")
        tk.Frame(self.root, bg=tema.BORDA, height=1).pack(side="bottom", fill="x")
        self._montar_status(status)

        corpo = ttk.Frame(self.root)
        corpo.pack(side="top", fill="both", expand=True)

        # Barra vertical esquerda de ferramentas estilo Adobe Photoshop CC
        self.barra_ferramentas = tk.Frame(corpo, bg=tema.PAINEL, width=tema.px_ferramenta(34),
                                          padx=2, pady=tema.px_ferramenta(4))
        self.barra_ferramentas.pack(side="left", fill="y")
        self._montar_ferramentas_esquerda(self.barra_ferramentas)
        tk.Frame(corpo, bg=tema.BORDA, width=1).pack(side="left", fill="y")

        opcoes = dict(bg=tema.BORDA, bd=0, sashwidth=2, sashrelief="flat", showhandle=False, opaqueresize=True)
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
        self.canvas.bind("<ButtonPress-2>", self._scroll_click_inicio)
        self.canvas.bind("<B2-Motion>", self._scroll_mover)
        self.canvas.bind("<ButtonRelease-2>", self._scroll_soltar)
        self.canvas.bind("<Motion>", self._lamina_passou)

    def _botao(self, pai, texto, comando, dica=None, estilo="Compacto.TButton", **opcoes):
        botao = ttk.Button(pai, text=texto, command=comando, style=estilo, **opcoes)
        if dica:
            tema.dica(botao, dica)
        return botao

    def _garantir_tela_cheia_inicial(self):
        try:
            self.root.state("zoomed")
        except tk.TclError:
            try:
                self.root.attributes("-zoomed", True)
            except tk.TclError:
                pass

    def alternar_tela_cheia(self):
        self._tela_cheia_total = not getattr(self, "_tela_cheia_total", False)
        try:
            self.root.attributes("-fullscreen", self._tela_cheia_total)
            if not self._tela_cheia_total:
                self.root.state("zoomed")
        except tk.TclError:
            pass

    def _montar_topo(self, topo):
        a = self.arranjo
        self.var_mostrar = {
            chave: tk.BooleanVar(value=a["mostrar_" + chave]) for chave in ("timeline", "fotos", "lateral")
        }
        self.var_lado_timeline = tk.StringVar(value=a.get("timeline", "baixo"))
        self.var_lado_fotos = tk.StringVar(value=a.get("fotos", "baixo"))
        self.var_lado_lateral = tk.StringVar(value=a.get("lateral", "direita"))
        for var_cfg in (self.margem_var, self.espacamento_var, self.fotos_lamina_var, self.auto_salvar_var):
            var_cfg.trace_add("write", lambda *args: self.agendar("config", 160, self._mudou_config))

        selo = tk.Label(
            topo,
            text="DI",
            bg="#001e36",
            fg="#31a8ff",
            font=("Segoe UI Black", 9),
            bd=1,
            relief="solid",
            highlightbackground="#31a8ff",
            highlightthickness=1,
            padx=5,
            pady=1,
        )
        selo.pack(side="left", padx=(2, 6))
        ttk.Label(topo, text="Diagramador Inteligente", style="Logo.TLabel").pack(side="left", padx=(0, 8))

        arquivo = ttk.Menubutton(topo, text="Arquivo", style="Compacto.TMenubutton")
        menu = tk.Menu(arquivo, tearoff=0)
        menu.add_command(label="📁 Carregar pasta de fotos...", accelerator="Ctrl+P", command=self.escolher_pasta)
        menu.add_command(label="☁ Importar link do Google Drive...", accelerator="Ctrl+G", command=self.importar_link_drive)
        menu.add_command(label="🖼 Carregar fotos avulsas...", accelerator="Ctrl+I", command=self.escolher_fotos)
        menu.add_command(label="📋 Colar fotos do Explorer", accelerator="Ctrl+V", command=self.colar)
        menu.add_separator()
        menu.add_command(label="Nova aba de álbum", accelerator="Ctrl+T", command=self.nova_aba_album)
        menu.add_command(label="Novo álbum (nesta aba)", accelerator="Ctrl+N", command=self.novo_projeto)
        menu.add_command(label="📂 Abrir projeto (.album.json)...", accelerator="Ctrl+O", command=self.abrir_projeto)
        menu.add_command(label="Abrir projeto em nova aba...", command=lambda: self.abrir_projeto(em_nova_aba=True))
        self.menu_recentes = tk.Menu(menu, tearoff=0, postcommand=self._montar_recentes)
        menu.add_cascade(label="Recentes", menu=self.menu_recentes)
        menu.add_separator()
        menu.add_command(label="💾 Salvar projeto", accelerator="Ctrl+S", command=self.salvar_projeto)
        menu.add_command(label="💾 Salvar projeto como...", accelerator="Ctrl+Shift+S", command=self.salvar_projeto_como)
        menu.add_command(label="🔗 Relinkar fotos movidas...", command=self.consertar_caminhos)
        menu.add_separator()
        menu.add_command(label="Escolher programa de capas...", command=self.escolher_programa_capa)
        menu.add_command(label='Criar atalho em "Enviar para"', command=self.criar_enviar_para)
        menu.add_command(
            label='Associar arquivos e pastas ao EXE no Windows ("Abrir com...")',
            command=self.associar_arquivos_windows,
        )
        menu.add_separator()
        menu.add_command(label="Sair", command=self.sair)
        arquivo["menu"] = menu
        arquivo.pack(side="left", padx=2)
        self.botao_arquivo = arquivo

        ttk.Frame(topo, width=6).pack(side="left")
        self.rotulo_nome = ttk.Label(topo, text="", style="Titulo.TLabel")
        self.rotulo_nome.pack(side="left", padx=(0, 6))
        self.rotulo_lamina = ttk.Label(topo, text="", style="Fraco.TLabel")
        self.rotulo_lamina.pack(side="left", padx=(4, 4))

        self.botao_exportar = self._botao(topo, "⤓ Exportar", self.abrir_exportar,
                                          "Exportar álbum (JPG, PSD ou PDF)", "PrimarioCompacto.TButton")
        self.botao_exportar.pack(side="right", padx=(2, 0))
        self.botao_capa = self._botao(topo, "◧ Capa", self.abrir_programa_capa,
                                      "Abrir programa de capas", "Compacto.TButton")
        self.botao_capa.pack(side="right", padx=1)

        ttk.Frame(topo, width=4).pack(side="right")
        self._botao(topo, "? Guia", self.mostrar_guia_rapido,
                    "Guia rápido passo a passo e dicas de uso", "Compacto.TButton").pack(side="right", padx=1)
        self._botao(topo, "↷", self.refazer, "Refazer (Ctrl+Y)", "Icone.TButton", width=3).pack(side="right", padx=1)
        self._botao(topo, "↶", self.desfazer, "Desfazer (Ctrl+Z)", "Icone.TButton", width=3).pack(side="right", padx=1)

        ttk.Frame(topo, width=4).pack(side="right")
        self._botao(topo, "✦ Diagramar", self.abrir_diagramacao,
                    "Diagramação automática (F5)", "PrimarioCompacto.TButton").pack(side="right", padx=(2, 2))

        # Botões sem texto (apenas ícone + explicativo no tooltip) e seleção de Tamanho ao lado do Diagramar
        bt_cfg = self._botao(
            topo,
            "⚙",
            self.abrir_janela_configuracoes,
            "Configurações de Margem, Espaçamento, Fotos por Lâmina, Regras e Posição dos Painéis",
            "Icone.TButton",
            width=3,
        )
        bt_cfg.pack(side="right", padx=1)

        guias = ttk.Checkbutton(
            topo,
            text="⌗",
            variable=self.guias,
            command=self.desenhar_lamina,
            style="Alternar.Toolbutton",
            width=3,
        )
        guias.pack(side="right", padx=1)
        tema.dica(guias, "Mostrar ou ocultar as linhas guias de corte, sangria e dobra central (Tecla H)")

        self.rotulo_produto = ttk.Label(topo, text="", style="Fraco.TLabel")
        self.rotulo_produto.pack(side="right", padx=(2, 4))
        self.dica_produto = tema.Dica(self.rotulo_produto, "")

        self.produto_var = tk.StringVar(value=self.produto["nome"] if self.produto else "")
        self.combo_produto = ttk.Combobox(
            topo,
            textvariable=self.produto_var,
            state="readonly",
            width=9,
            values=[p["nome"] for p in self.produtos],
        )
        self.combo_produto.pack(side="right", padx=(0, 3))
        self.combo_produto.bind("<<ComboboxSelected>>", self._trocou_produto)
        tema.dica(self.combo_produto, "Selecionar o tamanho e formato do álbum")

        rot_alb = ttk.Label(topo, text="Álbum:", style="Fraco.TLabel")
        rot_alb.pack(side="right", padx=(4, 3))

    def _montar_barra_opcoes(self):
        barra = self.barra_opcoes
        self._quadro_opcoes_normal = tk.Frame(barra, bg=tema.CABECALHO)
        self._quadro_opcoes_layout = tk.Frame(barra, bg=tema.CABECALHO)
        ttk.Label(self._quadro_opcoes_normal, text="Visualização:", style="BarraOpcoes.TLabel").pack(
            side="left", padx=(8, 4), pady=2)
        self._botao(self._quadro_opcoes_normal, "−", lambda: self.mudar_zoom(1 / 1.12),
                    "Diminuir zoom (roda do mouse)", "Icone.TButton", width=2).pack(side="left", padx=1)
        self.rotulo_zoom_opcoes = ttk.Label(self._quadro_opcoes_normal, text="100%", style="BarraOpcoes.TLabel", width=5)
        self.rotulo_zoom_opcoes.pack(side="left")
        self._botao(self._quadro_opcoes_normal, "+", lambda: self.mudar_zoom(1.12),
                    "Aumentar zoom (roda do mouse)", "Icone.TButton", width=2).pack(side="left", padx=1)
        self._botao(self._quadro_opcoes_normal, "1:1", lambda: self.mudar_zoom(None),
                    "Ajustar zoom a 100%", "Compacto.TButton").pack(side="left", padx=(6, 2))
        guias_op = ttk.Checkbutton(
            self._quadro_opcoes_normal, text="Guias", variable=self.guias, command=self.desenhar_lamina,
        )
        guias_op.pack(side="left", padx=(10, 2))
        tema.dica(guias_op, "Mostrar ou ocultar linhas guias de corte, sangria e dobra (Tecla H)")
        self.rotulo_opcoes_produto = ttk.Label(self._quadro_opcoes_normal, text="", style="BarraOpcoes.TLabel")
        self.rotulo_opcoes_produto.pack(side="right", padx=8, pady=2)

        ttk.Label(self._quadro_opcoes_layout, text="Edição de layout:", style="BarraOpcoes.TLabel").pack(
            side="left", padx=(8, 4), pady=2)
        ttk.Label(
            self._quadro_opcoes_layout,
            text="Alt desliga o ímã · Shift = proporção livre",
            style="BarraOpcoes.TLabel",
        ).pack(side="left", padx=2)
        for txt, cmd, dica in (
            ("＋", self.adicionar_espaco, "Novo espaço (N)"),
            ("❐", self.duplicar_espaco, "Duplicar espaço (Ctrl+D)"),
            ("🗑", self._tecla_apagar, "Apagar espaço / foto (Delete)"),
        ):
            self._botao(self._quadro_opcoes_layout, txt, cmd, dica, "Icone.TButton", width=2).pack(side="left", padx=1)
        pos_op = ttk.Menubutton(self._quadro_opcoes_layout, text="Alinhar", style="Compacto.TMenubutton")
        menu_op = tk.Menu(pos_op, tearoff=0)
        for texto, acao in (
            ("⇕ Preencher página", "pagina_util"),
            ("＝ Igualar tamanho", "igualar"),
            ("⊡ Proporção original", "proporcao_foto"),
        ):
            menu_op.add_command(label=texto, command=lambda a=acao: self.alinhar_espaco(a))
        pos_op["menu"] = menu_op
        pos_op.pack(side="left", padx=(8, 4))
        self._atualizar_barra_opcoes()

    def _atualizar_barra_opcoes(self):
        if not getattr(self, "barra_opcoes", None):
            return
        for quadro in (getattr(self, "_quadro_opcoes_normal", None), getattr(self, "_quadro_opcoes_layout", None)):
            if quadro is not None:
                quadro.pack_forget()
        if self.editar_layout.get():
            if getattr(self, "_quadro_opcoes_layout", None):
                self._quadro_opcoes_layout.pack(fill="x")
        elif getattr(self, "_quadro_opcoes_normal", None):
            self._quadro_opcoes_normal.pack(fill="x")
        if getattr(self, "rotulo_zoom_opcoes", None):
            self.rotulo_zoom_opcoes.configure(text="%d%%" % int(round(self.zoom * 100)))
        if getattr(self, "rotulo_opcoes_produto", None) and self.produto:
            self.rotulo_opcoes_produto.configure(text=self.produto.get("nome", ""))

    def abrir_janela_configuracoes(self):
        """Janela redimensionável que reúne Margem, Espaçamento, Regras e opções do antigo menu Exibir."""
        if getattr(self, "janela_configuracoes", None) is not None:
            try:
                if self.janela_configuracoes.winfo_exists():
                    self.janela_configuracoes.deiconify()
                    self.janela_configuracoes.lift()
                    self.janela_configuracoes.focus_force()
                    return
            except tk.TclError:
                pass

        jan = tk.Toplevel(self.root)
        jan.title("Configurações & Exibição · Diagramador Inteligente")
        jan.configure(bg=tema.PAINEL)
        jan.transient(self.root)
        self.janela_configuracoes = jan

        tema.criar_cabecalho_janela(
            jan,
            "Configurações da Lâmina & Exibição de Painéis",
            "Ajuste margens, espaçamento, regras automáticas e posição dos painéis na tela",
            icone="⚙",
        )

        rodape = tk.Frame(jan, bg=tema.CABECALHO, highlightthickness=1, highlightbackground=tema.BORDA, padx=12, pady=8)
        rodape.pack(side="bottom", fill="x")
        ttk.Button(rodape, text="Restaurar Layout Padrão", style="Compacto.TButton", command=self.restaurar_arranjo).pack(side="left")
        ttk.Button(rodape, text="Concluir (OK)", style="PrimarioCompacto.TButton", command=jan.destroy).pack(side="right")

        corpo, _cont, _cv = tema.criar_corpo_rolavel(jan, padding=12)

        sec_med = ttk.LabelFrame(corpo, text="Margens, Espaçamento e Regras da Diagramação", padding=8)
        sec_med.pack(fill="x", pady=(0, 8))

        for rot_txt, dica_txt, var, de, ate, passo, sufixo in (
            ("Margem externa:", "Margem externa da página em milímetros", self.margem_var, 0, 60, 1, "mm"),
            ("Espaçamento entre fotos:", "Distância entre as fotos em milímetros", self.espacamento_var, 0, 40, 0.5, "mm"),
            ("Mínimo de fotos por lâmina:", "A diagramação nunca põe menos fotos que isto numa lâmina (salvo uma pasta/grupo menor)", self.fotos_lamina_var, 1, 12, 1, "fotos"),
            ("Auto-salvar projeto:", "Intervalo de auto-salvamento local em minutos (0 = desativado)", self.auto_salvar_var, 0, 60, 1, "min"),
        ):
            linha_m = ttk.Frame(sec_med)
            linha_m.pack(fill="x", pady=2)
            r = ttk.Label(linha_m, text=rot_txt, width=24)
            r.pack(side="left")
            tema.dica(r, dica_txt)
            sp = ttk.Spinbox(linha_m, from_=de, to=ate, increment=passo, textvariable=var, width=6, command=self._mudou_config)
            sp.pack(side="left", padx=(4, 4))
            tema.dica(sp, dica_txt)
            if sufixo:
                ttk.Label(linha_m, text=sufixo, style="Fraco.TLabel").pack(side="left")

        ttk.Button(
            sec_med,
            text="⊞ Aplicar margem e espaçamento em todas as lâminas (Ctrl+R)",
            style="Compacto.TButton",
            command=self.rediagramar_todas,
        ).pack(fill="x", pady=(6, 4))

        ttk.Checkbutton(
            sec_med,
            text="👤 Proteger Rostos (ajustar enquadramento para não cortar pessoas)",
            variable=self.proteger_rostos_var,
            command=self._alternou_protecao_rostos,
        ).pack(anchor="w", pady=2)
        ttk.Checkbutton(
            sec_med,
            text="📁 Separar Pastas (fotos de pastas diferentes nunca se misturam na mesma lâmina)",
            variable=self.separar_pastas_var,
            command=self._alternou_separar_pastas,
        ).pack(anchor="w", pady=2)

        self._montar_config_ia(corpo, jan)

        sec_exibir = ttk.LabelFrame(corpo, text="Exibir e Posição dos Painéis", padding=8)
        sec_exibir.pack(fill="x", pady=(0, 8))

        for chave, texto in (
            ("timeline", "Mostrar Linha do Tempo (Lâminas)"),
            ("fotos", "Mostrar Painel de Fotos"),
            ("lateral", "Mostrar Painel de Abas Fixado na Lateral"),
        ):
            ttk.Checkbutton(
                sec_exibir,
                text=texto,
                variable=self.var_mostrar[chave],
                command=self._mudou_exibir,
            ).pack(anchor="w", pady=2)

        ttk.Separator(sec_exibir, orient="horizontal").pack(fill="x", pady=6)
        ttk.Label(sec_exibir, text="Posição do Painel de Fotos:", style="Fraco.TLabel").pack(anchor="w", pady=(0, 3))
        grade_ft = ttk.Frame(sec_exibir)
        grade_ft.pack(fill="x", pady=(0, 4))
        for idx_f, (valor_f, texto_f) in enumerate((
            ("baixo", "Embaixo (Faixa)"),
            ("direita", "À direita (Vertical)"),
            ("esquerda", "À esquerda (Vertical)"),
        )):
            rb_ft = ttk.Radiobutton(
                grade_ft,
                text=texto_f,
                value=valor_f,
                variable=self.var_lado_fotos,
                style="Filtro.TRadiobutton",
                command=self._mudou_exibir,
            )
            rb_ft.grid(row=0, column=idx_f, sticky="ew", padx=1)
            grade_ft.columnconfigure(idx_f, weight=1)

        ttk.Label(sec_exibir, text="Posição da Linha do Tempo (Lâminas):", style="Fraco.TLabel").pack(anchor="w", pady=(4, 3))
        grade_tl = ttk.Frame(sec_exibir)
        grade_tl.pack(fill="x", pady=(0, 4))
        for idx_l, (valor, texto) in enumerate((
            ("baixo", "Embaixo"),
            ("cima", "Em cima"),
            ("esquerda", "À esquerda"),
            ("direita", "À direita"),
        )):
            rb_tl = ttk.Radiobutton(
                grade_tl,
                text=texto,
                value=valor,
                variable=self.var_lado_timeline,
                style="Filtro.TRadiobutton",
                command=self._mudou_exibir,
            )
            rb_tl.grid(row=0, column=idx_l, sticky="ew", padx=1)
            grade_tl.columnconfigure(idx_l, weight=1)

        ttk.Label(sec_exibir, text="Lado do Painel de Abas Fixado:", style="Fraco.TLabel").pack(anchor="w", pady=(4, 3))
        linha_lat = ttk.Frame(sec_exibir)
        linha_lat.pack(fill="x", pady=(0, 6))
        for valor, texto in (("direita", "Painel fixado à direita"), ("esquerda", "Painel fixado à esquerda")):
            ttk.Radiobutton(
                linha_lat,
                text=texto,
                value=valor,
                variable=self.var_lado_lateral,
                style="Filtro.TRadiobutton",
                command=self._mudou_exibir,
            ).pack(side="left", fill="x", expand=True, padx=1)

        linha_tam = ttk.Frame(sec_exibir)
        linha_tam.pack(fill="x", pady=(2, 4))
        ttk.Button(linha_tam, text="Lâminas +", style="Compacto.TButton", command=lambda: self.mudar_tamanho_painel("timeline", 30)).pack(side="left", fill="x", expand=True, padx=(0, 2))
        ttk.Button(linha_tam, text="Lâminas −", style="Compacto.TButton", command=lambda: self.mudar_tamanho_painel("timeline", -30)).pack(side="left", fill="x", expand=True, padx=2)
        ttk.Button(linha_tam, text="Fotos +", style="Compacto.TButton", command=lambda: self.mudar_tamanho_painel("fotos", 30)).pack(side="left", fill="x", expand=True, padx=2)
        ttk.Button(linha_tam, text="Fotos −", style="Compacto.TButton", command=lambda: self.mudar_tamanho_painel("fotos", -30)).pack(side="left", fill="x", expand=True, padx=(2, 0))

        ttk.Button(
            sec_exibir,
            text="⛶ Alternar Modo Tela Cheia Total (F11)",
            style="Compacto.TButton",
            command=self.alternar_tela_cheia,
        ).pack(fill="x", pady=(4, 0))

        jan.bind("<Escape>", lambda _e: jan.destroy())
        tema.configurar_janela_secundaria(
            jan,
            pai=self.root,
            chave="janela_configuracoes",
            largura=460,
            altura=560,
            min_largura=360,
            min_altura=380,
        )

    def _montar_config_ia(self, corpo, jan):
        sec_ia = ttk.LabelFrame(corpo, text="🤖 Inteligência Artificial (offline · nenhuma foto sai do computador)", padding=8)
        sec_ia.pack(fill="x", pady=(0, 8))
        cfg = ia_modelos.config()
        variaveis = {chave: tk.BooleanVar(value=bool(cfg.get(chave))) for chave in ia_modelos.CONFIG_PADRAO}

        def mudou():
            novo = ia_modelos.salvar_config({k: v.get() for k, v in variaveis.items()})
            self._aplicar_cfg_ia()
            caixa_tema.state(["!disabled"] if novo.get("reconhecer_cenas") else ["disabled"])
            if not novo.get("modo_leve") and (novo.get("reconhecer_cenas") or novo.get("edicao_ia")):
                self._agendar_ia_fundo([f["caminho"] for f in self.projeto.get("fotos", [])])
            self.registrar("Configurações de IA salvas.")

        for chave, texto, dica in (
            ("reconhecer_cenas", "Reconhecer cenas (Places365: palco, baile, igreja, externa, estúdio...)",
             "Mostra a cena na dica da foto e no editor, ajuda a separar eventos e sugere o nome do evento (você aceita ou não)."),
            ("cena_no_tema", "    Usar a cena para escolher o tema do fundo quando a pasta não tem nome de evento",
             "Desligado = os temas automáticos continuam vindo só do nome da pasta/grupo, como antes."),
            ("edicao_ia", "Edição automática por IA (botões 🤖 Auto IA no editor e no lote)",
             "3D LUT adaptativa pré-treinada (estilo de editores profissionais do MIT-Adobe FiveK)."),
            ("modo_leve", "Modo PC leve (1 thread; analisa só a foto aberta, não na importação)",
             "Para computadores fracos: nada roda em segundo plano ao carregar as fotos."),
        ):
            caixa = ttk.Checkbutton(sec_ia, text=texto, variable=variaveis[chave], command=mudou)
            caixa.pack(anchor="w", pady=1)
            tema.dica(caixa, dica)
            if chave == "cena_no_tema":
                caixa_tema = caixa
        if not cfg.get("reconhecer_cenas"):
            caixa_tema.state(["disabled"])

        rotulo_estado = ttk.Label(sec_ia, text="Verificando os modelos...", style="Fraco.TLabel", justify="left")
        rotulo_estado.pack(anchor="w", pady=(6, 4))

        def atualizar_estado(recalcular=False):
            def ler():
                try:
                    ia_modelos.estado_modelos(recalcular=recalcular)
                    texto = ia_modelos.texto_estado()
                except Exception as e:
                    texto = "Não deu para verificar os modelos: %s" % e

                def mostrar():
                    try:
                        if rotulo_estado.winfo_exists():
                            rotulo_estado.configure(text=texto)
                    except tk.TclError:
                        pass

                self.fila.put(("chamar", mostrar))

            threading.Thread(target=ler, daemon=True, name="ia_estado_cfg").start()

        linha = ttk.Frame(sec_ia)
        linha.pack(fill="x")
        ttk.Button(linha, text="📦 Importar modelos de um arquivo (.zip)...", style="Compacto.TButton",
                   command=lambda: self.importar_modelos_ia(jan, ao_terminar=lambda: atualizar_estado(True))).pack(side="left")
        ttk.Button(linha, text="↻ Verificar", style="Compacto.TButton",
                   command=lambda: atualizar_estado(True)).pack(side="left", padx=(4, 0))
        atualizar_estado()

    def _montar_ferramentas_esquerda(self, barra):
        """Barra vertical esquerda unificando ferramentas de edição de foto/espaço e os botões das abas."""
        esp = tema.px_ferramenta(2)
        bt_mover = ttk.Checkbutton(barra, text="⬚", variable=self.editar_layout,
                                   style="Ferramenta.Toolbutton", command=self._mudou_modo_layout, width=2)
        bt_mover.pack(fill="x", pady=esp)
        tema.dica(bt_mover, "Mover / redimensionar espaços (Tecla L)")

        estilo_ic = "Ferramenta.TButton"
        self._botao(barra, "✂", self.editar_foto_selecionada,
                    "Editor de Foto, 1-Clique e Edição em Lote (Enter)", estilo_ic, width=2).pack(fill="x", pady=esp)
        self._botao(barra, "↻", self.girar_foto_selecionada,
                    "Girar foto 90°", estilo_ic, width=2).pack(fill="x", pady=esp)
        self._botao(barra, "＝", lambda: self.alinhar_espaco("igualar"),
                    "Igualar tamanho das fotos selecionadas", estilo_ic, width=2).pack(fill="x", pady=esp)
        self._botao(barra, "⊡", lambda: self.alinhar_espaco("proporcao_foto"),
                    "Proporção original da foto (0% corte)", estilo_ic, width=2).pack(fill="x", pady=esp)
        self._botao(barra, "⇕", lambda: self.alinhar_espaco("pagina_util"),
                    "Preencher página 100% até a sangria", estilo_ic, width=2).pack(fill="x", pady=esp)
        self._botao(barra, "＋", self.adicionar_espaco,
                    "Novo espaço na página", estilo_ic, width=2).pack(fill="x", pady=esp)
        self._botao(barra, "❐", self.duplicar_espaco,
                    "Duplicar espaço selecionado", estilo_ic, width=2).pack(fill="x", pady=esp)
        self._botao(barra, "↑", lambda: self.ordem_espaco(True),
                    "Trazer para a frente", estilo_ic, width=2).pack(fill="x", pady=esp)
        self._botao(barra, "↓", lambda: self.ordem_espaco(False),
                    "Mandar para trás", estilo_ic, width=2).pack(fill="x", pady=esp)
        self._botao(barra, "🗑", self._tecla_apagar,
                    "Tirar foto / apagar espaço selecionado", estilo_ic, width=2).pack(fill="x", pady=esp)

        tk.Frame(barra, bg=tema.BORDA, height=1).pack(fill="x", padx=1, pady=tema.px_ferramenta(4))

        self.botoes_abas_esquerda = {}
        self.vars_abas_esquerda = {}
        definicoes_abas = (
            ("Layouts", "⊞", "Janela Layouts (Sugestões, Páginas e Editar) — Abrir/Fechar"),
            ("Fundo", "🖼", "Janela Fundo (Cores, Gerador pelas Fotos e Pasta) — Abrir/Fechar"),
            ("Degradê", "◐", "Janela Degradê (Máscara de Opacidade) — Abrir/Fechar"),
            ("Moldura", "▭", "Janela Moldura (12 Estilos: Royal, Degradê e Bordas) — Abrir/Fechar"),
            ("Sombra", "❒", "Janela Sombra Projetada (Sincronizada com Molduras) — Abrir/Fechar"),
            ("Temas", "🎨", "Janela Temas da Lâmina (Natal, Páscoa, Casamento, Formatura...) — Abrir/Fechar"),
            ("⌨ Atalhos", "⌨", "Janela Atalhos de Teclado — Abrir/Fechar"),
        )
        for chave_aba, icone_aba, dica_aba in definicoes_abas:
            var_aba = tk.BooleanVar(value=(chave_aba == "Layouts"))
            self.vars_abas_esquerda[chave_aba] = var_aba
            bt_aba = ttk.Checkbutton(
                barra,
                text=icone_aba,
                variable=var_aba,
                style="Ferramenta.Toolbutton",
                width=2,
                command=lambda c=chave_aba: self.alternar_aba_lateral(c),
            )
            bt_aba.pack(fill="x", pady=esp)
            tema.dica(bt_aba, dica_aba)
            self.botoes_abas_esquerda[chave_aba] = bt_aba

    def _montar_status(self, status):
        self.rotulo_status = ttk.Label(status, text="Arraste fotos ou uma pasta para a janela.", style="Fraco.TLabel")
        self.rotulo_status.pack(side="left", fill="x", expand=True)
        self._botao(status, "☰", self.mostrar_registro, "Registro de avisos e histórico de eventos", "Icone.TButton", width=3).pack(side="right", padx=(4, 0))
        self.botao_parar = self._botao(status, "■", self.parar_tarefa, "Parar a tarefa em andamento", "Icone.TButton", width=3, state="disabled")
        self.botao_parar.pack(side="right", padx=(4, 0))
        self.barra = ttk.Progressbar(status, length=180, mode="determinate")
        self.barra.pack(side="right")

    def _mudou_filtro_fotos_menu(self):
        mapa_txt = {
            "todas": "▾ Filtro: Todas",
            "nao_usadas": "▾ Filtro: Não usadas",
            "usadas": "▾ Filtro: Usadas",
        }
        if hasattr(self, "botao_filtro_fotos"):
            self.botao_filtro_fotos.configure(text=mapa_txt.get(self.filtro.get(), "▾ Filtro: Todas"))
        self.atualizar_fotos()

    def alternar_posicao_fotos(self):
        atual_pos = self.var_lado_fotos.get() if hasattr(self, "var_lado_fotos") else self.arranjo.get("fotos", "baixo")
        nova_pos = "direita" if atual_pos == "baixo" else "baixo"
        if hasattr(self, "var_lado_fotos"):
            self.var_lado_fotos.set(nova_pos)
        self.arranjo["fotos"] = nova_pos
        self._mudou_exibir()

    def _montar_fotos(self, pai):
        caixa = ttk.Frame(pai, padding=(8, 4, 8, 0))
        caixa.pack(fill="both", expand=True)
        self.caixa_fotos = caixa
        cabeca = ttk.Frame(caixa)
        cabeca.pack(side="top", fill="x")
        self.cabeca_fotos = cabeca
        self.rotulo_fotos = ttk.Label(cabeca, text="Fotos", style="Titulo.TLabel")
        self.rotulo_fotos.pack(side="left")

        self.botao_filtro_fotos = ttk.Menubutton(cabeca, text="▾ Filtro: Todas", style="Compacto.TMenubutton")
        menu_filtro = tk.Menu(self.botao_filtro_fotos, tearoff=0)
        for valor, texto in (
            ("todas", "Todas as fotos"),
            ("nao_usadas", "Não usadas no álbum"),
            ("usadas", "Usadas no álbum"),
        ):
            menu_filtro.add_radiobutton(
                label=texto,
                value=valor,
                variable=self.filtro,
                command=self._mudou_filtro_fotos_menu,
            )
        self.botao_filtro_fotos["menu"] = menu_filtro
        self.botao_filtro_fotos.pack(side="left", padx=(6, 2))
        tema.dica(self.botao_filtro_fotos, "Escolher filtro de exibição de fotos (Todas, Não usadas ou Usadas)")

        ttk.Label(cabeca, text="📂", style="Fraco.TLabel").pack(side="left", padx=(4, 2))
        self.combo_pasta_filtro = ttk.Combobox(cabeca, state="readonly", width=13,
                                               values=["Todas as pastas"])
        self.combo_pasta = self.combo_pasta_filtro
        self.combo_pasta_filtro.set("Todas as pastas")
        self.combo_pasta_filtro.pack(side="left", padx=(0, 3))
        self.combo_pasta_filtro.bind("<<ComboboxSelected>>", self._mudou_filtro_pasta)
        tema.dica(self.combo_pasta_filtro, "Filtrar visualização por pasta carregada")

        self.ordem_var = tk.StringVar(value="grupos")
        combo = ttk.Combobox(cabeca, textvariable=self.ordem_var, state="readonly", width=7,
                             values=["grupos", "nome", "data"])
        combo.pack(side="left", padx=(3, 0))
        combo.bind("<<ComboboxSelected>>", lambda e: self.ordenar_fotos())
        tema.dica(combo, "Ordenar fotos por grupos numerados/ordem manual, nome ou data (sem renomear arquivos)")
        self._botao(cabeca, "🗂 Ordem / Grupos", self.abrir_ordem_grupos,
                    "Organizar fotos em grupos numerados (Grupo 1, Grupo 2...) e escolher qual foto vem primeiro sem alterar arquivos",
                    "Compacto.TButton").pack(side="left", padx=(5, 0))

        self._botao(cabeca, "🗑", self.remover_fotos_selecionadas,
                    "Remover fotos selecionadas do projeto", "Icone.TButton", width=3).pack(side="right", padx=(2, 1))
        self._botao(cabeca, "⇉", self.distribuir_nao_usadas,
                    "Distribuir fotos livres em novas lâminas", "Icone.TButton", width=3).pack(side="right", padx=2)
        self.botao_pos_fotos = self._botao(
            cabeca,
            "◨",
            self.alternar_posicao_fotos,
            "Alternar posição do Painel de Fotos entre À Direita (Vertical) e Embaixo (Horizontal)",
            "Icone.TButton",
            width=3,
        )
        self.botao_pos_fotos.pack(side="right", padx=2)

        self.barra_fotos_h = ttk.Scrollbar(caixa, orient="horizontal", command=self._rolar_fotos_h)
        self.barra_fotos_v = ttk.Scrollbar(caixa, orient="vertical", command=self._rolar_fotos_v)
        self.barra_fotos_h.pack(side="bottom", fill="x")
        self.canvas_fotos = tk.Canvas(caixa, height=82, width=240, bg=tema.PAINEL, highlightthickness=0)
        self.canvas_fotos.pack(side="top", fill="both", expand=True, pady=(3, 0))
        self.canvas_fotos.configure(
            xscrollcommand=lambda a, b: (self.barra_fotos_h.set(a, b), self.agendar("fotos", 15, self._desenhar_fotos_visiveis)),
            yscrollcommand=lambda a, b: (self.barra_fotos_v.set(a, b), self.agendar("fotos", 15, self._desenhar_fotos_visiveis)),
            xscrollincrement=20,
            yscrollincrement=20,
        )
        self._fotos_vert_ativo = False
        self.canvas_fotos.bind("<Configure>", self._mudou_tamanho_fotos)
        self.canvas_fotos.bind("<MouseWheel>", self._fotos_roda)
        self.canvas_fotos.bind("<ButtonPress-1>", self._fotos_pressionou)
        self.canvas_fotos.bind("<B1-Motion>", self._mover_arrasto)
        self.canvas_fotos.bind("<ButtonRelease-1>", self._soltar_arrasto)
        self.canvas_fotos.bind("<Double-Button-1>", self._fotos_duplo)
        self.canvas_fotos.bind("<Button-3>", self._fotos_menu)
        self.canvas_fotos.bind("<Motion>", self._fotos_passou)
        for sequencia in ("<Leave>", "<ButtonPress>", "<MouseWheel>"):
            self.canvas_fotos.bind(sequencia, self._esconder_dica_foto_painel, add="+")

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
        self._tl_vista = {"x": None, "y": None}
        self.canvas_timeline.configure(
            xscrollcommand=lambda a, b: self._timeline_rolou("x", self.barra_tl_h, a, b),
            yscrollcommand=lambda a, b: self._timeline_rolou("y", self.barra_tl_v, a, b),
            xscrollincrement=20,
            yscrollincrement=20,
        )
        self._tl_vertical = None
        self.canvas_timeline.bind("<Configure>", self._mudou_tamanho_timeline)
        self.canvas_timeline.bind("<MouseWheel>", self._timeline_roda)
        self.canvas_timeline.bind("<ButtonPress-1>", self._timeline_pressionou)
        self.canvas_timeline.bind("<B1-Motion>", self._mover_arrasto)
        self.canvas_timeline.bind("<ButtonRelease-1>", self._soltar_arrasto)
        self.canvas_timeline.bind("<ButtonPress-2>", self._timeline_scroll_pressionou)
        self.canvas_timeline.bind("<Button-3>", self._timeline_menu)

    def _timeline_rolou(self, eixo, barra, a, b):
        barra.set(a, b)
        if self._tl_vista.get(eixo) != (a, b):
            self._tl_vista[eixo] = (a, b)
            self.agendar("timeline", 14, self.desenhar_timeline)

    # ---------------- arranjo dos painéis ----------------

    def _timeline_vertical(self):
        return self.arranjo.get("timeline", "baixo") in ("esquerda", "direita")

    def _fotos_vertical(self):
        return self.arranjo.get("fotos", "baixo") in ("esquerda", "direita")

    def _orientar_fotos(self, vertical):
        if vertical == getattr(self, "_fotos_vert_ativo", None):
            return
        self._fotos_vert_ativo = vertical
        self.barra_fotos_h.pack_forget()
        self.barra_fotos_v.pack_forget()
        self.canvas_fotos.pack_forget()
        if vertical:
            self.barra_fotos_v.pack(side="right", fill="y", pady=(3, 0))
            if hasattr(self, "botao_pos_fotos"):
                self.botao_pos_fotos.configure(text="⬓")
        else:
            self.barra_fotos_h.pack(side="bottom", fill="x")
            if hasattr(self, "botao_pos_fotos"):
                self.botao_pos_fotos.configure(text="◨")
        self.canvas_fotos.pack(side="top", fill="both", expand=True, pady=(3, 0))
        self.canvas_fotos.xview_moveto(0)
        self.canvas_fotos.yview_moveto(0)
        self._celula = self._medidas_fotos()
        self._construir_itens_painel_fotos()
        self._area_fotos()
        self.agendar("fotos", 25, self._desenhar_fotos_visiveis)

    def _ler_tamanhos(self):
        """Passa para o arranjo o tamanho atual dos painéis (o usuário pode ter arrastado as divisórias)."""
        fotos_vert = self._fotos_vertical()
        em_baixo_junto = (self.arranjo.get("timeline") == "baixo"
                          and not fotos_vert
                          and self.arranjo.get("mostrar_timeline")
                          and self.arranjo.get("mostrar_fotos"))
        usa_largura_tl = self._tl_vertical or em_baixo_junto
        chave_tl = "largura_timeline" if usa_largura_tl else "altura_timeline"
        chave_ft = "largura_fotos" if fotos_vert else "altura_fotos"
        for chave, widget, altura in ((chave_tl, self.quadro_timeline, not usa_largura_tl),
                                      (chave_ft, self.quadro_fotos, not fotos_vert),
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
        vertical_tl = self._timeline_vertical()
        vertical_ft = self._fotos_vertical()
        self._orientar_timeline(vertical_tl)
        self._orientar_fotos(vertical_ft)
        _wx, _wy, sw, sh = tema.area_util_tela(self.root)
        # Limita proporcionalmente a altura/largura dos painéis periféricos em telas compactas
        larg_lat = min(int(a.get("largura_lateral", 310)), max(MINIMOS["lateral"], int(sw * 0.25)))
        larg_tl = min(int(a.get("largura_timeline", 360)), max(220, int(sw * 0.30)))
        larg_ft = min(int(a.get("largura_fotos", 290)), max(MINIMOS["fotos_vertical"], int(sw * 0.28)))
        alt_tl = min(int(a.get("altura_timeline", 114)), max(MINIMOS["timeline"], int(sh * 0.16)))
        alt_fotos = min(int(a.get("altura_fotos", 118)), max(MINIMOS["fotos"], int(sh * 0.17)))

        lateral = dict(width=larg_lat, minsize=MINIMOS["lateral"], stretch="never")
        tl_lado = dict(width=larg_tl, minsize=MINIMOS["timeline_vertical"], stretch="never")
        ft_lado = dict(width=larg_ft, minsize=MINIMOS["fotos_vertical"], stretch="never")
        mostrar_tl = a.get("mostrar_timeline", True)
        mostrar_lat = a.get("mostrar_lateral", True)
        mostrar_ft = a.get("mostrar_fotos", True)
        pos_fotos = a.get("fotos", "baixo")

        if mostrar_lat and a.get("lateral") == "esquerda":
            h.add(self.quadro_lateral, **lateral)
        if mostrar_tl and a.get("timeline") == "esquerda":
            h.add(self.quadro_timeline, **tl_lado)
        if mostrar_ft and pos_fotos == "esquerda":
            h.add(self.quadro_fotos, **ft_lado)
        h.add(self.quadro_centro, minsize=MINIMOS["centro"], stretch="always")
        if mostrar_tl and a.get("timeline") == "direita":
            h.add(self.quadro_timeline, **tl_lado)
        if mostrar_lat and a.get("lateral") == "direita":
            h.add(self.quadro_lateral, **lateral)
        if mostrar_ft and pos_fotos == "direita":
            h.add(self.quadro_fotos, **ft_lado)

        tl_faixa = dict(height=alt_tl, minsize=MINIMOS["timeline"], stretch="never")
        if mostrar_tl and a.get("timeline") == "cima":
            v.add(self.quadro_timeline, **tl_faixa)
        v.add(h, minsize=MINIMOS["meio"], stretch="always")
        if mostrar_tl and a.get("timeline") == "baixo" and mostrar_ft and pos_fotos == "baixo":
            b.add(self.quadro_timeline, width=max(220, larg_tl), minsize=200, stretch="never")
            b.add(self.quadro_fotos, minsize=240, stretch="always")
            v.add(b, height=alt_fotos, minsize=MINIMOS["fotos"], stretch="never")
        else:
            if mostrar_ft and pos_fotos == "baixo":
                v.add(self.quadro_fotos, height=alt_fotos, minsize=MINIMOS["fotos"], stretch="never")
            if mostrar_tl and a.get("timeline") == "baixo":
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
        if hasattr(self, "var_lado_fotos"):
            self.arranjo["fotos"] = self.var_lado_fotos.get()
        self.arranjo["lateral"] = self.var_lado_lateral.get()
        self._aplicar_arranjo()
        self.agendar("gravar_arranjo", 300, self.gravar_arranjo)

    def mostrar_painel(self, chave, mostrar=True):
        self.var_mostrar[chave].set(bool(mostrar))
        self._mudou_exibir()

    def posicionar_painel(self, chave, lado):
        if chave == "timeline":
            self.var_lado_timeline.set(lado)
        elif chave == "fotos" and hasattr(self, "var_lado_fotos"):
            self.var_lado_fotos.set(lado)
        else:
            self.var_lado_lateral.set(lado)
        self._mudou_exibir()

    def mudar_tamanho_painel(self, chave, diferenca):
        self._ler_tamanhos()
        minimo = MINIMOS[chave]
        if chave == "lateral":
            campo = "largura_lateral"
        elif chave == "timeline" and self._tl_vertical:
            campo, minimo = "largura_timeline", MINIMOS["timeline_vertical"]
        elif chave == "fotos" and self._fotos_vertical():
            campo, minimo = "largura_fotos", MINIMOS["fotos_vertical"]
        else:
            campo = "altura_" + chave
        self.arranjo[campo] = max(minimo, int(self.arranjo.get(campo, minimo)) + diferenca)
        self.var_mostrar[chave].set(True)
        self._mudou_exibir()

    def restaurar_arranjo(self):
        for chave, valor in ARRANJO_PADRAO.items():
            if chave not in ("geometria", "maximizada"):
                self.arranjo[chave] = valor
        for chave, var in self.var_mostrar.items():
            var.set(self.arranjo["mostrar_" + chave])
        self.var_lado_timeline.set(self.arranjo["timeline"])
        if hasattr(self, "var_lado_fotos"):
            self.var_lado_fotos.set(self.arranjo.get("fotos", "baixo"))
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
        self.aba_fixada_ativa = "Layouts"
        self.janelas_abas_flutuantes = {}
        self.quadros_abas = {}
        self._funcoes_montar_abas = {
            "Layouts": (self._aba_layouts, False, "⊞ Layouts"),
            "Fundo": (self._aba_fundo, True, "🖼 Fundo"),
            "Degradê": (self._aba_degrade, True, "◐ Degradê"),
            "Moldura": (self._aba_moldura, True, "▭ Moldura"),
            "Sombra": (self._aba_sombra, True, "❒ Sombra Projetada"),
            "Temas": (self._aba_temas, True, "🎨 Temas da Lâmina"),
            "⌨ Atalhos": (self._aba_atalhos, False, "⌨ Atalhos"),
        }

        # Cabeçalho compacto da aba fixada na lateral (com botão Janela para desafixar e fechar)
        cab = tk.Frame(direita, bg=tema.CABECALHO, highlightthickness=0, padx=4, pady=3)
        cab.pack(side="top", fill="x")
        self.cabecalho_aba_lateral = cab
        self._lateral_conteudo_aberto = bool(self.arranjo.get("lateral_conteudo_aberto", True))
        self.bt_recolher_lateral = tk.Label(
            cab, text="▾" if self._lateral_conteudo_aberto else "▸",
            bg=tema.CABECALHO, fg=tema.TEXTO_FRACO, font=tema.FONTE_ICONE, cursor="hand2",
        )
        self.bt_recolher_lateral.pack(side="left", padx=(2, 4))
        self.bt_recolher_lateral.bind("<Button-1>", lambda _e: self._alternar_conteudo_lateral())
        self.rotulo_aba_lateral = tk.Label(
            cab, text="⊞ Layouts", bg=tema.CABECALHO, fg=tema.TEXTO_FRACO, font=tema.FONTE_PEQUENA,
        )
        self.rotulo_aba_lateral.pack(side="left")

        bt_fechar = self._botao(
            cab,
            "✕",
            self.fechar_aba_fixada,
            "Fechar aba lateral para ampliar a área da lâmina",
            "Mini.TButton",
            width=2,
        )
        bt_fechar.pack(side="right", padx=(2, 0))

        bt_desafixar = self._botao(
            cab,
            "📌 Janela",
            lambda: self.desafixar_aba_em_janela(self.aba_fixada_ativa),
            "Desafixar esta aba em uma janela flutuante redimensionável",
            "Compacto.TButton",
        )
        bt_desafixar.pack(side="right", padx=2)

        self.container_abas_lateral = ttk.Frame(direita, padding=2)
        if self._lateral_conteudo_aberto:
            self.container_abas_lateral.pack(side="top", fill="both", expand=True)

        for titulo in self._funcoes_montar_abas:
            self._criar_quadro_aba_lateral(titulo)

        self._exibir_aba_fixada(self.aba_fixada_ativa)

    def _alternar_conteudo_lateral(self):
        self._lateral_conteudo_aberto = not getattr(self, "_lateral_conteudo_aberto", True)
        self.arranjo["lateral_conteudo_aberto"] = self._lateral_conteudo_aberto
        if self._lateral_conteudo_aberto:
            self.container_abas_lateral.pack(side="top", fill="both", expand=True)
            self.bt_recolher_lateral.configure(text="▾")
        else:
            self.container_abas_lateral.pack_forget()
            self.bt_recolher_lateral.configure(text="▸")
        self.agendar("gravar_arranjo", 400, self.gravar_arranjo)

    def _criar_quadro_aba_lateral(self, titulo):
        montar_fn, rolavel, _rotulo = self._funcoes_montar_abas[titulo]
        anterior = self.quadros_abas.get(titulo)
        if anterior is not None:
            try:
                anterior.destroy()
            except tk.TclError:
                pass
        if rolavel:
            quadro = ttk.Frame(self.container_abas_lateral, padding=0)
            interno, _cont, _cv = tema.criar_corpo_rolavel(quadro, padding=6)
            montar_fn(interno)
        else:
            quadro = ttk.Frame(self.container_abas_lateral, padding=6)
            montar_fn(quadro)
        self.quadros_abas[titulo] = quadro
        return quadro

    def _atualizar_botoes_abas_esquerda(self):
        if not hasattr(self, "vars_abas_esquerda"):
            return
        lat_visivel = bool(self.var_mostrar["lateral"].get()) and bool(self.aba_fixada_ativa)
        for titulo, var_aba in self.vars_abas_esquerda.items():
            em_janela = titulo in self.janelas_abas_flutuantes
            ativa_fixa = lat_visivel and self.aba_fixada_ativa == titulo
            var_aba.set(bool(ativa_fixa or em_janela))

    def _exibir_aba_fixada(self, titulo):
        self.aba_fixada_ativa = titulo
        for nome_t, q in list(self.quadros_abas.items()):
            try:
                q.pack_forget()
            except tk.TclError:
                pass
        if titulo and titulo in self._funcoes_montar_abas:
            if titulo not in self.quadros_abas or not self.quadros_abas[titulo].winfo_exists():
                self._criar_quadro_aba_lateral(titulo)
            _fn, _rol, rotulo_cab = self._funcoes_montar_abas[titulo]
            self.rotulo_aba_lateral.configure(text=rotulo_cab)
            self.quadros_abas[titulo].pack(side="top", fill="both", expand=True)
            if not self.var_mostrar["lateral"].get():
                self.var_mostrar["lateral"].set(True)
                self._mudou_exibir()
            self._aba_mudou(titulo)
        self._atualizar_botoes_abas_esquerda()

    def alternar_aba_lateral(self, titulo, forcar_abrir=False):
        """Abre ou fecha a aba pela barra lateral esquerda (ou traz/fecha sua janela flutuante)."""
        if titulo in self.janelas_abas_flutuantes:
            jan = self.janelas_abas_flutuantes.get(titulo)
            if jan is not None and jan.winfo_exists():
                if forcar_abrir:
                    jan.deiconify()
                    jan.lift()
                    jan.focus_force()
                    self._atualizar_botoes_abas_esquerda()
                    return
                self.fechar_janela_aba(titulo)
                return
            else:
                self.janelas_abas_flutuantes.pop(titulo, None)

        lat_visivel = bool(self.var_mostrar["lateral"].get())
        if not forcar_abrir and lat_visivel and self.aba_fixada_ativa == titulo:
            self.fechar_aba_fixada()
            return
        self._exibir_aba_fixada(titulo)

    def fechar_aba_fixada(self):
        self.aba_fixada_ativa = None
        if self.var_mostrar["lateral"].get():
            self.var_mostrar["lateral"].set(False)
            self._mudou_exibir()
        self._atualizar_botoes_abas_esquerda()

    def desafixar_aba_em_janela(self, titulo):
        """Desafixa a aba lateral e a abre como uma janela flutuante redimensionável."""
        if not titulo or titulo not in self._funcoes_montar_abas:
            return
        if titulo in self.janelas_abas_flutuantes:
            jan_exist = self.janelas_abas_flutuantes[titulo]
            if jan_exist.winfo_exists():
                jan_exist.deiconify()
                jan_exist.lift()
                return

        # Se era a aba fixada ativa, fecha o painel lateral (ou mostra outra aba fixada se houver)
        q_ant = self.quadros_abas.pop(titulo, None)
        if q_ant is not None:
            try:
                q_ant.destroy()
            except tk.TclError:
                pass
        if self.aba_fixada_ativa == titulo:
            self.fechar_aba_fixada()

        montar_fn, rolavel, rotulo_cab = self._funcoes_montar_abas[titulo]
        jan = tk.Toplevel(self.root)
        jan.title("%s · Diagramador Inteligente" % rotulo_cab)
        jan.configure(bg=tema.PAINEL)
        jan.transient(self.root)
        self.janelas_abas_flutuantes[titulo] = jan

        topo_j = tk.Frame(jan, bg=tema.CABECALHO, highlightthickness=1, highlightbackground=tema.BORDA, padx=10, pady=6)
        topo_j.pack(side="top", fill="x")
        tk.Label(topo_j, text=rotulo_cab, bg=tema.CABECALHO, fg=tema.TEXTO, font=tema.FONTE_CABECALHO).pack(side="left")

        self._botao(
            topo_j,
            "✕",
            lambda t=titulo: self.fechar_janela_aba(t),
            "Fechar janela da aba",
            "Mini.TButton",
            width=2,
        ).pack(side="right", padx=(2, 0))

        self._botao(
            topo_j,
            "📌 Fixar na Lateral",
            lambda t=titulo: self.fixar_aba_na_lateral(t),
            "Encaixar novamente esta aba fixada na lateral da janela principal",
            "PrimarioCompacto.TButton",
        ).pack(side="right", padx=2)

        corpo_j = ttk.Frame(jan, padding=2)
        corpo_j.pack(side="top", fill="both", expand=True)
        if rolavel:
            interno_j, _c_j, _cv_j = tema.criar_corpo_rolavel(corpo_j, padding=8)
            montar_fn(interno_j)
        else:
            quadro_j = ttk.Frame(corpo_j, padding=8)
            quadro_j.pack(fill="both", expand=True)
            montar_fn(quadro_j)

        jan.protocol("WM_DELETE_WINDOW", lambda t=titulo: self.fechar_janela_aba(t))
        jan.bind("<Escape>", lambda _e, t=titulo: self.fechar_janela_aba(t))
        chave_geo = "aba_flutuante_" + re.sub(r"[^a-zA-Z0-9]+", "_", titulo.lower())
        tema.configurar_janela_secundaria(
            jan,
            pai=self.root,
            chave=chave_geo,
            largura=370,
            altura=620,
            min_largura=290,
            min_altura=320,
        )
        self._aba_mudou(titulo)
        self._atualizar_botoes_abas_esquerda()

    def fixar_aba_na_lateral(self, titulo):
        """Fecha a janela flutuante da aba e volta a fixá-la na lateral principal."""
        jan = self.janelas_abas_flutuantes.pop(titulo, None)
        if jan is not None:
            try:
                jan.destroy()
            except tk.TclError:
                pass
        self._criar_quadro_aba_lateral(titulo)
        self._exibir_aba_fixada(titulo)

    def fechar_janela_aba(self, titulo):
        """Fecha a janela flutuante e recria o quadro oculto na lateral para uso futuro."""
        jan = self.janelas_abas_flutuantes.pop(titulo, None)
        if jan is not None:
            try:
                jan.destroy()
            except tk.TclError:
                pass
        self._criar_quadro_aba_lateral(titulo)
        self._atualizar_botoes_abas_esquerda()

    def abrir_aba_degrade(self):
        self.alternar_aba_lateral("Degradê", forcar_abrir=True)
        self._atualizar_degrade_ui()

    def abrir_aba_sombra(self):
        self.alternar_aba_lateral("Sombra", forcar_abrir=True)
        self._atualizar_sombra_ui()

    def abrir_aba_temas(self):
        self.alternar_aba_lateral("Temas", forcar_abrir=True)
        self._atualizar_temas_ui()

    def abrir_aba_atalhos(self):
        self.alternar_aba_lateral("⌨ Atalhos", forcar_abrir=True)

    def _aba_mudou(self, titulo_alvo=None):
        selecionada = titulo_alvo if isinstance(titulo_alvo, str) else self.aba_fixada_ativa
        if selecionada == "Layouts":
            self.desenhar_sugestoes()
            if getattr(self, "secao_layouts", None) and self.secao_layouts.get() == "modelos":
                self.desenhar_modelos()
        elif selecionada == "Fundo":
            self.atualizar_fundos()
        elif selecionada == "Degradê":
            self._atualizar_degrade_ui()
        elif selecionada == "Moldura":
            self._atualizar_moldura_ui()
        elif selecionada == "Sombra":
            self._atualizar_sombra_ui()
        elif selecionada == "Temas":
            self._atualizar_temas_ui()

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
        self.canvas_sugestoes.bind("<Configure>", lambda e: self.agendar("desenhar_sugestoes", 40, self.desenhar_sugestoes))
        self.canvas_sugestoes.bind("<MouseWheel>", lambda e: (self.canvas_sugestoes.yview_scroll(-int(e.delta / 120), "units"), "break")[1])

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
        self.canvas_modelos.bind("<MouseWheel>", lambda e: (self.canvas_modelos.yview_scroll(-int(e.delta / 120), "units"), "break")[1])
        self.canvas_modelos.bind("<Configure>", lambda e: self.agendar("desenhar_modelos", 40, self.desenhar_modelos))

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
            enq, bg="#262626", highlightthickness=1, highlightbackground=tema.BORDA, height=104, cursor="fleur"
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

        # Painel de Melhoria Rápida 1-Clique & Lote (Luz, Cor e Saturação)
        quadro_1c = ttk.LabelFrame(quadro, text="✨ Melhoria 1-Clique & Lote (Luz, Cor e Saturação)", padding=6)
        quadro_1c.pack(side="top", fill="x", pady=(4, 0))
        self.escopo_1c_painel = tk.StringVar(value="auto")
        linha_escopo_1c = ttk.Frame(quadro_1c)
        linha_escopo_1c.pack(fill="x", pady=(0, 4))
        ttk.Label(linha_escopo_1c, text="Alvo:", style="Fraco.TLabel").pack(side="left", padx=(0, 4))
        for val_esc, txt_esc in (
            ("auto", "Foto/Sel."),
            ("lamina", "Lâmina"),
            ("album", "Álbum"),
        ):
            ttk.Radiobutton(
                linha_escopo_1c,
                text=txt_esc,
                value=val_esc,
                variable=self.escopo_1c_painel,
            ).pack(side="left", padx=2)
        self._botao(
            linha_escopo_1c,
            "🎛 Lote...",
            self.abrir_tratamento,
            "Abrir janela completa de edição em lote com controles finos de Luz, Cor, Saturação e opção de gravar nas originais",
            "Compacto.TButton",
        ).pack(side="right")

        grade_1c = ttk.Frame(quadro_1c)
        grade_1c.pack(fill="x")
        for col_i in range(4):
            grade_1c.columnconfigure(col_i, weight=1)
        self._botao(
            grade_1c,
            "⚡ Leve",
            lambda: self.aplicar_lote_inteligente(escopo=self.escopo_1c_painel.get(), nivel="leve"),
            "Ajuste sutil automático de luz, cor e vivacidade (38%)",
            "Compacto.TButton",
        ).grid(row=0, column=0, sticky="ew", padx=(0, 2))
        self._botao(
            grade_1c,
            "✦ Médio",
            lambda: self.aplicar_lote_inteligente(escopo=self.escopo_1c_painel.get(), nivel="medio"),
            "Correção equilibrada automática de exposição, sombras, cor e saturação (72%)",
            "PrimarioCompacto.TButton",
        ).grid(row=0, column=1, sticky="ew", padx=2)
        self._botao(
            grade_1c,
            "🔥 Pesado",
            lambda: self.aplicar_lote_inteligente(escopo=self.escopo_1c_painel.get(), nivel="pesado"),
            "Recuperação intensa em 1 clique para fotos escuras, estouradas, lavadas ou sem vida (100%)",
            "Compacto.TButton",
        ).grid(row=0, column=2, sticky="ew", padx=2)
        self._botao(
            grade_1c,
            "↺ Orig.",
            lambda: self.restaurar_luz_cor_lote(escopo=self.escopo_1c_painel.get()),
            "Restaurar luz, cor e saturação originais no alvo selecionado (suporta Ctrl+Z)",
            "Compacto.TButton",
        ).grid(row=0, column=3, sticky="ew", padx=(2, 0))

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

        # --- Gerador pelas Fotos (Padrão Preto + Cores & Branco + Cores) ---
        gerador = ttk.LabelFrame(quadro, text="Gerar pelas Fotos (Preto/Branco + Cores 15-25%)", padding=6)
        gerador.pack(side="top", fill="x", pady=(2, 4))
        self.estilo_gerador_fundo = tk.StringVar(value="ambos")
        self.padrao_gerador_fundo = tk.StringVar(value="ambos")
        self._seed_fundos_gerados = 1
        self._chave_cache_gerados = None

        linha_g1 = ttk.Frame(gerador)
        linha_g1.pack(fill="x", pady=(0, 3))
        self.rotulo_escopo_gerador = ttk.Label(
            linha_g1, text="Analisando: Lâmina inteira", style="Fraco.TLabel"
        )
        self.rotulo_escopo_gerador.pack(side="left")

        linha_g_filtros = ttk.Frame(gerador)
        linha_g_filtros.pack(fill="x", pady=(0, 3))
        combo_pad_ger = ttk.Combobox(
            linha_g_filtros,
            state="readonly",
            width=19,
            values=("Preto + Branco (Auto)", "Padrão Preto + Cores", "Padrão Branco + Cores"),
        )
        combo_pad_ger.set("Preto + Branco (Auto)")
        combo_pad_ger.pack(side="left", fill="x", expand=True, padx=(0, 2))
        tema.dica(
            combo_pad_ger,
            "Detecta se as fotos têm mais tons pretos ou brancos e mostra ambos os padrões na galeria (mistura sutil de 15% a 25% das cores das fotos)",
        )

        combo_est_ger = ttk.Combobox(
            linha_g_filtros,
            state="readonly",
            width=15,
            values=("Degradê + Abstrato", "Só Degradê", "Só Abstrato"),
        )
        combo_est_ger.set("Degradê + Abstrato")
        combo_est_ger.pack(side="right", fill="x", expand=True, padx=(2, 0))

        def _mudou_estilo_gerador(_e=None):
            mapa_est = {
                "Degradê + Abstrato": "ambos",
                "Só Degradê": "degrade",
                "Só Abstrato": "abstrato",
            }
            mapa_pad = {
                "Preto + Branco (Auto)": "ambos",
                "Padrão Preto + Cores": "preto",
                "Padrão Branco + Cores": "branco",
            }
            self.estilo_gerador_fundo.set(mapa_est.get(combo_est_ger.get(), "ambos"))
            self.padrao_gerador_fundo.set(mapa_pad.get(combo_pad_ger.get(), "ambos"))
            self._chave_cache_gerados = None
            self._atualizar_galeria_gerados(forcar=True)

        combo_est_ger.bind("<<ComboboxSelected>>", _mudou_estilo_gerador)
        combo_pad_ger.bind("<<ComboboxSelected>>", _mudou_estilo_gerador)

        linha_g2 = ttk.Frame(gerador)
        linha_g2.pack(fill="x", pady=(0, 3))
        self._botao(
            linha_g2,
            "🎲 Novas Variações",
            self.sortear_fundos_gerados,
            "Gera novas variações de fundos nos padrões Preto + Cores (15-25%) e Branco + Cores (15-25%) e salva na pasta temporária",
            "PrimarioCompacto.TButton",
        ).pack(side="left", fill="x", expand=True, padx=(0, 2))
        self._botao(
            linha_g2,
            "✨ Auto Fundos (Álbum)",
            lambda: self.executar_auto_fundos(apenas_atual=False),
            "Executa o Motor de Fundos em todo o álbum detectando automaticamente Preto + Cores ou Branco + Cores em cada lâmina",
            "Compacto.TButton",
        ).pack(side="left", fill="x", expand=True, padx=(2, 0))

        # Linha da Pasta Temporária de Fundos Gerados (permite editar os arquivos e limpar quando quiser)
        linha_temp_g = ttk.Frame(gerador)
        linha_temp_g.pack(fill="x", pady=(0, 4))
        self.rotulo_temp_fundos = ttk.Label(
            linha_temp_g, text="Temp: 0 salvos", style="Fraco.TLabel"
        )
        self.rotulo_temp_fundos.pack(side="left")
        self._botao(
            linha_temp_g,
            "🗑 Limpar Temporários",
            self.limpar_fundos_temporarios_ui,
            "Remove os arquivos de fundos gerados acumulados na pasta temporária",
            "Compacto.TButton",
        ).pack(side="right", padx=(2, 0))
        self._botao(
            linha_temp_g,
            "📂 Pasta Temp",
            self.abrir_pasta_fundos_temporarios,
            "Abre a pasta temporária onde os fundos gerados pelo programa são salvos como arquivos .png editáveis",
            "Compacto.TButton",
        ).pack(side="right")

        self.galeria_fundos_gerados = fundos.Galeria(
            gerador,
            self._fundo_atual_normalizado,
            self._galeria_gerados_pressionou,
            lambda _c: self.abrir_editor_fundo(),
            self._galeria_gerados_menu,
            contagem=self.contagem_desenhos,
        )
        self.galeria_fundos_gerados.canvas.configure(height=118)
        self.galeria_fundos_gerados.pack(side="top", fill="x")

        pasta = ttk.LabelFrame(quadro, text="Pasta de fundos (Sugestões & Padrões)", padding=6)
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

        linha_sug = ttk.Frame(pasta)
        linha_sug.pack(side="top", fill="x", pady=(4, 2))
        self.filtro_sugeridos_pasta = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            linha_sug,
            text="Ordenar por sugestão (cores das fotos + meus padrões ★)",
            variable=self.filtro_sugeridos_pasta,
            command=lambda: self.atualizar_fundos(forcar=True),
        ).pack(side="left")

        self.rotulo_pasta_fundos = ttk.Label(pasta, text="", style="Fraco.TLabel", wraplength=300, justify="left")
        self.rotulo_pasta_fundos.pack(side="top", anchor="w", pady=(2, 2))
        ttk.Label(pasta, text="Clique aplica · botão direito marca ★ Fundo Padrão · arraste para uma lâmina",
                  style="Fraco.TLabel", wraplength=300, justify="left").pack(side="bottom", anchor="w", pady=(4, 0))
        self.galeria_fundos = fundos.Galeria(
            pasta,
            self._fundo_atual_normalizado,
            self._galeria_pressionou,
            self.abrir_editor_fundo,
            self._galeria_menu,
            eh_favorito=lambda c: fundos.eh_favorito_fundo(ARQUIVO_FUNDOS, c),
            favoritos=lambda: fundos.favoritos_fundo(ARQUIVO_FUNDOS),
            contagem=self.contagem_desenhos,
        )
        self.galeria_fundos.pack(side="top", fill="both", expand=True)
        self.galeria_fundos.canvas.bind("<B1-Motion>", self._mover_arrasto)
        self.galeria_fundos.canvas.bind("<ButtonRelease-1>", self._soltar_arrasto)

    def _aba_moldura(self, quadro):
        ttk.Label(quadro, text="Molduras Especiais (12 Estilos)", style="Titulo.TLabel").pack(anchor="w")
        ttk.Label(quadro, text="Traçado e ornamentos ao redor das fotos selecionadas (Royal, Degradê, Metálica e mais).",
                  style="Fraco.TLabel", wraplength=290).pack(anchor="w", pady=(2, 6))
        self.moldura_ativa = tk.BooleanVar(value=False)
        self.moldura_cor = tk.StringVar(value="#ffffff")
        self.moldura_cor_secundaria = tk.StringVar(value="#d4af37")
        self.moldura_espessura = tk.DoubleVar(value=0.6)
        self.moldura_estilo = tk.StringVar(value="simples")
        self.moldura_sinc_sombra = tk.BooleanVar(value=True)
        self._carregando_moldura = False
        self._moldura_alvo = None

        linha_topo_m = ttk.Frame(quadro)
        linha_topo_m.pack(fill="x", pady=(0, 6))
        self.check_moldura = ttk.Checkbutton(linha_topo_m, text="Ativar moldura", variable=self.moldura_ativa,
                                             command=lambda: self._alterar_moldura(marcar=True))
        self.check_moldura.pack(side="left")
        self._botao(
            linha_topo_m,
            "❒ Sombra Projetada...",
            self.abrir_aba_sombra,
            "Abrir janela de opções da Sombra Projetada sincronizada com as molduras",
            "Compacto.TButton",
        ).pack(side="right")

        linha = ttk.Frame(quadro)
        linha.pack(fill="x", pady=2)
        ttk.Label(linha, text="Estilo (12)").pack(side="left")
        self.combo_moldura = ttk.Combobox(
            linha,
            textvariable=self.moldura_estilo,
            state="readonly",
            width=18,
            values=[ch for ch, _nm in ESTILOS_MOLDURA],
        )
        self.combo_moldura.pack(side="right")
        self.combo_moldura.bind("<<ComboboxSelected>>", self._moldura_estilo_mudou)

        grade_est = ttk.LabelFrame(quadro, text="Catálogo Rápido (12 Molduras)", padding=4)
        grade_est.pack(fill="x", pady=(4, 6))
        atalhos_estilos = (
            ("Simples", "simples"),
            ("Dupla", "dupla"),
            ("Pontilhada", "pontilhada"),
            ("♛ Royal", "royal"),
            ("◐ Degradê", "degrade"),
            ("✦ Metálica", "degrade_real"),
            ("⌜ Cantoneira", "cantoneira"),
            ("◈ Chanfrada", "chanfrada"),
            ("▣ Galeria", "passepartout"),
            ("≡ Tripla", "tripla"),
            ("╬ Cruzada", "cruzada"),
            ("◎ Halo", "halo"),
        )
        for idx_e, (rot_e, val_e) in enumerate(atalhos_estilos):
            bt_e = ttk.Button(
                grade_est,
                text=rot_e,
                style="Compacto.TButton",
                command=lambda v=val_e: self._escolher_estilo_moldura_rapido(v),
            )
            bt_e.grid(row=idx_e // 3, column=idx_e % 3, sticky="ew", padx=1, pady=1)
            tema.dica(bt_e, NOMES_ESTILOS_MOLDURA.get(val_e, rot_e))
            grade_est.columnconfigure(idx_e % 3, weight=1)

        linha_c1 = ttk.Frame(quadro)
        linha_c1.pack(fill="x", pady=2)
        ttk.Label(linha_c1, text="Cor principal").pack(side="left")
        self.amostra_moldura = tk.Label(linha_c1, width=4, bg=self.moldura_cor.get(), relief="solid", bd=1)
        self.amostra_moldura.pack(side="right", padx=(4, 0))
        self.botao_cor_moldura = ttk.Button(linha_c1, text="Cor 1...", style="Compacto.TButton", command=self._escolher_cor_moldura)
        self.botao_cor_moldura.pack(side="right")

        linha_c2 = ttk.Frame(quadro)
        linha_c2.pack(fill="x", pady=2)
        ttk.Label(linha_c2, text="Cor 2 (Royal / Degradê)").pack(side="left")
        self.amostra_moldura_sec = tk.Label(linha_c2, width=4, bg=self.moldura_cor_secundaria.get(), relief="solid", bd=1)
        self.amostra_moldura_sec.pack(side="right", padx=(4, 0))
        self.botao_cor_moldura_sec = ttk.Button(
            linha_c2, text="Cor 2...", style="Compacto.TButton", command=self._escolher_cor_secundaria_moldura
        )
        self.botao_cor_moldura_sec.pack(side="right")

        linha_esp = ttk.Frame(quadro)
        linha_esp.pack(fill="x", pady=(6, 2))
        self.rotulo_espessura_moldura = ttk.Label(linha_esp, text="Espessura: 0,6 mm")
        self.rotulo_espessura_moldura.pack(side="left")
        self.escala_moldura = ttk.Scale(quadro, from_=0.1, to=3.0, variable=self.moldura_espessura,
                                        command=self._moldura_escala_mudou)
        self.escala_moldura.pack(fill="x", pady=(0, 4))
        self.escala_moldura.bind("<ButtonPress-1>", lambda _e: self._inicio_ajuste_moldura())
        self.escala_moldura.bind("<ButtonRelease-1>", self._fim_ajuste_moldura)

        presets = ttk.Frame(quadro)
        presets.pack(fill="x", pady=2)
        for texto, valor in (("Fina 0,3", 0.3), ("Média 0,6", 0.6), ("Forte 1,1", 1.1), ("Larga 1,8", 1.8)):
            ttk.Button(presets, text=texto, style="Compacto.TButton", command=lambda v=valor: self._preset_moldura(v)).pack(
                side="left", fill="x", expand=True, padx=1)

        ttk.Checkbutton(
            quadro,
            text="❒ Identificar e aplicar Sombra Projetada junto com a moldura",
            variable=self.moldura_sinc_sombra,
        ).pack(anchor="w", pady=(6, 4))

        linha_ac_m = ttk.Frame(quadro)
        linha_ac_m.pack(fill="x", pady=(4, 2))
        ttk.Button(linha_ac_m, text="Aplicar às selecionadas", style="PrimarioCompacto.TButton",
                   command=self._moldura_em_todas).pack(side="left", fill="x", expand=True, padx=(0, 2))
        ttk.Button(linha_ac_m, text="Remover moldura", style="Compacto.TButton",
                   command=self._remover_moldura).pack(side="left", fill="x", expand=True, padx=(2, 0))

        auto_m = ttk.LabelFrame(quadro, text="Função 3: Molduras Automáticas (Regras)", padding=6)
        auto_m.pack(fill="x", pady=(8, 0))
        ttk.Label(
            auto_m,
            text="Aplica a moldura selecionada acima nas fotos em grade/margem (nunca em fotos sangradas 100% ou com degradê).",
            style="Fraco.TLabel",
            wraplength=280,
            justify="left",
        ).pack(anchor="w", pady=(0, 6))
        linha_am = ttk.Frame(auto_m)
        linha_am.pack(fill="x")
        self._botao(
            linha_am,
            "✨ Nesta Lâmina",
            lambda: self.executar_auto_molduras(apenas_atual=True),
            "Aplica a regra de molduras automáticas apenas na lâmina aberta",
            "PrimarioCompacto.TButton",
        ).pack(side="left", fill="x", expand=True, padx=(0, 2))
        self._botao(
            linha_am,
            "✨ No Álbum Inteiro",
            lambda: self.executar_auto_molduras(apenas_atual=False),
            "Aplica a regra de molduras automáticas em todas as lâminas do álbum",
            "Compacto.TButton",
        ).pack(side="left", fill="x", expand=True, padx=(2, 0))

        self._controles_moldura = [
            self.check_moldura, self.combo_moldura, self.botao_cor_moldura,
            self.botao_cor_moldura_sec, self.escala_moldura,
        ]

    def _escolher_estilo_moldura_rapido(self, estilo):
        self.moldura_estilo.set(estilo)
        if estilo in ("royal", "degrade_real") and self.moldura_cor.get().lower() == "#ffffff":
            self.moldura_cor.set("#d4af37")
            self.moldura_cor_secundaria.set("#fef08a")
            self.amostra_moldura.configure(bg="#d4af37")
            self.amostra_moldura_sec.configure(bg="#fef08a")
        self.moldura_ativa.set(True)
        self._alterar_moldura(marcar=True)

    def _moldura_selecionada(self):
        lamina = self._lamina()
        if lamina is None or not self._espaco_sel_valido():
            return None
        espaco = lamina["espacos"][self.espaco_sel]
        return espaco if espaco.get("foto") else None

    def _espacos_moldura_alvo(self):
        lamina = self._lamina()
        selecionados = self._indices_espacos_selecionados()
        if lamina is not None and selecionados:
            alvos_lamina = [
                lamina["espacos"][indice]
                for indice in sorted(selecionados)
                if 0 <= indice < len(lamina["espacos"]) and lamina["espacos"][indice].get("foto")
            ]
            if alvos_lamina:
                return alvos_lamina
        espaco = self._moldura_selecionada()
        if espaco:
            return [espaco]
        escolhidas = set(self.fotos_sel)
        if escolhidas:
            return [espaco for espaco in proj.todos_espacos(self.projeto)
                    if espaco.get("foto") and normalizar_caminho(espaco["foto"]) in escolhidas]
        return []

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
        self.moldura_cor_secundaria.set(dados.get("cor_secundaria", "#d4af37"))
        self.moldura_espessura.set(float(dados.get("espessura_mm", 0.6)))
        self.moldura_estilo.set(dados.get("estilo", "simples"))
        self.amostra_moldura.configure(bg=self.moldura_cor.get())
        if hasattr(self, "amostra_moldura_sec"):
            self.amostra_moldura_sec.configure(bg=self.moldura_cor_secundaria.get())
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
        return {
            "cor": self.moldura_cor.get(),
            "cor_secundaria": self.moldura_cor_secundaria.get(),
            "espessura_mm": float(self.moldura_espessura.get()),
            "estilo": self.moldura_estilo.get(),
        }

    def _alterar_moldura(self, marcar=True):
        if self._carregando_moldura:
            return
        espacos = self._espacos_moldura_alvo()
        if not espacos:
            return
        if marcar:
            self.marcar_historico()
        sinc_sombra = bool(getattr(self, "moldura_sinc_sombra", None) and self.moldura_sinc_sombra.get())
        for espaco in espacos:
            edicao = espaco.setdefault("edicao", {})
            if self.moldura_ativa.get():
                edicao["moldura"] = self._moldura_atual_dos_controles()
                if sinc_sombra and not edicao.get("sombra"):
                    edicao["sombra"] = self._sombra_atual_dos_controles() if hasattr(self, "sombra_ativa") else sombra_padrao()
                    edicao["sombra"]["ativa"] = True
            else:
                edicao.pop("moldura", None)
                if sinc_sombra and (edicao.get("sombra") or {}).get("sincronizar_moldura", True):
                    edicao.pop("sombra", None)
        self._atualizar_depois_da_moldura()

    def _escolher_cor_moldura(self):
        cor = tema.escolher_cor_photoshop(self.root, cor_inicial=self.moldura_cor.get(), titulo="Cor Principal da Moldura · Photoshop")
        if cor:
            if not self.moldura_ativa.get():
                self.moldura_ativa.set(True)
            self.moldura_cor.set(cor)
            self.amostra_moldura.configure(bg=cor)
            self._alterar_moldura(marcar=True)

    def _escolher_cor_secundaria_moldura(self):
        cor = tema.escolher_cor_photoshop(self.root, cor_inicial=self.moldura_cor_secundaria.get(), titulo="Cor 2 (Royal / Degradê) · Photoshop")
        if cor:
            if not self.moldura_ativa.get():
                self.moldura_ativa.set(True)
            self.moldura_cor_secundaria.set(cor)
            self.amostra_moldura_sec.configure(bg=cor)
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
        sinc_sombra = bool(getattr(self, "moldura_sinc_sombra", None) and self.moldura_sinc_sombra.get())
        self.marcar_historico()
        for espaco in espacos:
            ed = espaco.setdefault("edicao", {})
            ed["moldura"] = dict(dados)
            if sinc_sombra and not ed.get("sombra"):
                ed["sombra"] = self._sombra_atual_dos_controles() if hasattr(self, "sombra_ativa") else sombra_padrao()
                ed["sombra"]["ativa"] = True
        self._atualizar_depois_da_moldura()

    def _remover_moldura(self):
        if not self._espacos_moldura_alvo():
            return
        self.moldura_ativa.set(False)
        self._alterar_moldura(marcar=True)

    # ---------------- Sombra Projetada (Sincronizada com Molduras) ----------------

    def _aba_sombra(self, quadro):
        ttk.Label(quadro, text="Sombra Projetada (Drop Shadow)", style="Titulo.TLabel").pack(anchor="w")
        ttk.Label(
            quadro,
            text="Projeta sombra realista sob as fotos e molduras, identificando automaticamente bordas e ignorando fotos 100% sangradas ou com degradê.",
            style="Fraco.TLabel",
            wraplength=290,
        ).pack(anchor="w", pady=(2, 6))

        self.sombra_ativa = tk.BooleanVar(value=False)
        self.sombra_sinc_moldura = tk.BooleanVar(value=True)
        self.sombra_cor = tk.StringVar(value="#000000")
        self.sombra_opacidade = tk.DoubleVar(value=50.0)
        self.sombra_desfoque = tk.DoubleVar(value=2.6)
        self.sombra_offset_x = tk.DoubleVar(value=1.4)
        self.sombra_offset_y = tk.DoubleVar(value=1.8)
        self._carregando_sombra = False

        ttk.Checkbutton(
            quadro,
            text="Ativar Sombra Projetada na(s) foto(s)",
            variable=self.sombra_ativa,
            command=lambda: self._alterar_sombra(marcar=True),
        ).pack(anchor="w", pady=(0, 3))

        ttk.Checkbutton(
            quadro,
            text="Identificar com as molduras (ignorar fotos sangradas/degradê)",
            variable=self.sombra_sinc_moldura,
            command=lambda: self._alterar_sombra(marcar=True),
        ).pack(anchor="w", pady=(0, 6))

        box_pre = ttk.LabelFrame(quadro, text="Estilos Rápidos de Sombra Projetada", padding=5)
        box_pre.pack(fill="x", pady=(0, 6))
        grade_ps = ttk.Frame(box_pre)
        grade_ps.pack(fill="x")
        for idx_p, (id_p, nome_p, cfg_p) in enumerate(PRESETS_SOMBRA_PROJETADA):
            bt_ps = ttk.Button(
                grade_ps,
                text=nome_p,
                style="Compacto.TButton",
                command=lambda c=cfg_p: self._aplicar_preset_sombra(c),
            )
            bt_ps.grid(row=idx_p // 2, column=idx_p % 2, sticky="ew", padx=1, pady=1)
            grade_ps.columnconfigure(idx_p % 2, weight=1)

        box_aj = ttk.LabelFrame(quadro, text="Ajustes Finos da Sombra", padding=6)
        box_aj.pack(fill="x", pady=(0, 6))

        linha_cs = ttk.Frame(box_aj)
        linha_cs.pack(fill="x", pady=(0, 4))
        ttk.Label(linha_cs, text="Cor da sombra").pack(side="left")
        self.amostra_sombra = tk.Label(linha_cs, width=4, bg=self.sombra_cor.get(), relief="solid", bd=1)
        self.amostra_sombra.pack(side="right", padx=(4, 0))
        ttk.Button(linha_cs, text="Escolher cor...", style="Compacto.TButton", command=self._escolher_cor_sombra).pack(side="right")

        self._rotulos_sombra = {}
        for chave_s, rot_s, var_s, de_s, ate_s, fmt_s in (
            ("opacidade", "Opacidade", self.sombra_opacidade, 5.0, 95.0, "%d%%"),
            ("desfoque", "Suavidade (Blur)", self.sombra_desfoque, 0.2, 8.0, "%.1f mm"),
            ("ox", "Deslocamento X", self.sombra_offset_x, -5.0, 5.0, "%+.1f mm"),
            ("oy", "Deslocamento Y", self.sombra_offset_y, -5.0, 5.0, "%+.1f mm"),
        ):
            linha_s = ttk.Frame(box_aj)
            linha_s.pack(fill="x", pady=2)
            ttk.Label(linha_s, text=rot_s, width=15).pack(side="left")
            lbl_v = ttk.Label(linha_s, text=fmt_s % var_s.get(), style="Fraco.TLabel", width=8, anchor="e")
            lbl_v.pack(side="right")
            self._rotulos_sombra[chave_s] = (lbl_v, var_s, fmt_s)

            def _ao_mover_sombra(_v):
                self._atualizar_rotulos_sombra()
                if not self._carregando_sombra:
                    self.sombra_ativa.set(True)
                    self._alterar_sombra(marcar=False)

            esc_s = ttk.Scale(linha_s, from_=de_s, to=ate_s, variable=var_s, command=_ao_mover_sombra)
            esc_s.pack(side="right", fill="x", expand=True, padx=4)
            esc_s.bind("<ButtonPress-1>", lambda _e: self.marcar_historico())
            esc_s.bind("<ButtonRelease-1>", lambda _e: self._atualizar_depois_da_moldura())

        linha_bt_s = ttk.Frame(quadro)
        linha_bt_s.pack(fill="x", pady=(2, 4))
        ttk.Button(
            linha_bt_s,
            text="Aplicar às selecionadas",
            style="PrimarioCompacto.TButton",
            command=lambda: (self.sombra_ativa.set(True), self._alterar_sombra(marcar=True)),
        ).pack(side="left", fill="x", expand=True, padx=(0, 2))
        ttk.Button(
            linha_bt_s,
            text="Remover sombra",
            style="Compacto.TButton",
            command=self._remover_sombra,
        ).pack(side="left", fill="x", expand=True, padx=(2, 0))

        auto_s = ttk.LabelFrame(quadro, text="Aplicar Sombra Identificando Molduras", padding=6)
        auto_s.pack(fill="x", pady=(4, 0))
        ttk.Label(
            auto_s,
            text="Aplica automaticamente nas fotos com moldura ou em grade/margem da lâmina ou do álbum:",
            style="Fraco.TLabel",
            wraplength=280,
        ).pack(anchor="w", pady=(0, 4))
        linha_as1 = ttk.Frame(auto_s)
        linha_as1.pack(fill="x", pady=1)
        self._botao(
            linha_as1,
            "✨ Fotos c/ Moldura (Lâmina)",
            lambda: self.executar_auto_sombra(apenas_atual=True, apenas_com_moldura=True),
            "Aplica sombra projetada em todas as fotos que possuem moldura na lâmina atual",
            "PrimarioCompacto.TButton",
        ).pack(side="left", fill="x", expand=True, padx=(0, 2))
        self._botao(
            linha_as1,
            "✨ Fotos c/ Moldura (Álbum)",
            lambda: self.executar_auto_sombra(apenas_atual=False, apenas_com_moldura=True),
            "Aplica sombra projetada em todas as fotos que possuem moldura no álbum inteiro",
            "Compacto.TButton",
        ).pack(side="left", fill="x", expand=True, padx=(2, 0))

        linha_as2 = ttk.Frame(auto_s)
        linha_as2.pack(fill="x", pady=(3, 0))
        self._botao(
            linha_as2,
            "✨ Todas em Margem (Lâmina)",
            lambda: self.executar_auto_sombra(apenas_atual=True, apenas_com_moldura=False),
            "Aplica sombra projetada em todas as fotos em grade/margem da lâmina atual",
            "Compacto.TButton",
        ).pack(side="left", fill="x", expand=True, padx=(0, 2))
        self._botao(
            linha_as2,
            "✨ Todas em Margem (Álbum)",
            lambda: self.executar_auto_sombra(apenas_atual=False, apenas_com_moldura=False),
            "Aplica sombra projetada em todas as fotos em grade/margem do álbum inteiro",
            "Compacto.TButton",
        ).pack(side="left", fill="x", expand=True, padx=(2, 0))

    def _atualizar_rotulos_sombra(self):
        for _ch, (lbl, var, fmt) in getattr(self, "_rotulos_sombra", {}).items():
            try:
                lbl.configure(text=fmt % float(var.get()))
            except tk.TclError:
                pass

    def _sombra_atual_dos_controles(self):
        return {
            "ativa": bool(self.sombra_ativa.get()),
            "cor": self.sombra_cor.get() or "#000000",
            "opacidade": int(round(float(self.sombra_opacidade.get()))),
            "desfoque_mm": round(float(self.sombra_desfoque.get()), 2),
            "offset_x_mm": round(float(self.sombra_offset_x.get()), 2),
            "offset_y_mm": round(float(self.sombra_offset_y.get()), 2),
            "sincronizar_moldura": bool(self.sombra_sinc_moldura.get()),
        }

    def _atualizar_sombra_ui(self):
        if not hasattr(self, "sombra_ativa"):
            return
        espacos = self._espacos_moldura_alvo()
        espaco = self._moldura_selecionada() or (espacos[0] if espacos else None)
        dados = ((espaco.get("edicao") or {}).get("sombra") or {}) if espaco else {}
        self._carregando_sombra = True
        self.sombra_ativa.set(bool(dados and dados.get("ativa", True)))
        self.sombra_sinc_moldura.set(bool(dados.get("sincronizar_moldura", True)))
        self.sombra_cor.set(dados.get("cor", "#000000"))
        self.sombra_opacidade.set(float(dados.get("opacidade", 50.0)))
        self.sombra_desfoque.set(float(dados.get("desfoque_mm", 2.6)))
        self.sombra_offset_x.set(float(dados.get("offset_x_mm", 1.4)))
        self.sombra_offset_y.set(float(dados.get("offset_y_mm", 1.8)))
        if hasattr(self, "amostra_sombra"):
            self.amostra_sombra.configure(bg=self.sombra_cor.get())
        self._atualizar_rotulos_sombra()
        self._carregando_sombra = False

    def _aplicar_preset_sombra(self, cfg_p):
        self._carregando_sombra = True
        self.sombra_ativa.set(True)
        self.sombra_cor.set(cfg_p.get("cor", "#000000"))
        self.sombra_opacidade.set(float(cfg_p.get("opacidade", 50)))
        self.sombra_desfoque.set(float(cfg_p.get("desfoque_mm", 2.6)))
        self.sombra_offset_x.set(float(cfg_p.get("offset_x_mm", 1.4)))
        self.sombra_offset_y.set(float(cfg_p.get("offset_y_mm", 1.8)))
        if hasattr(self, "amostra_sombra"):
            self.amostra_sombra.configure(bg=self.sombra_cor.get())
        self._atualizar_rotulos_sombra()
        self._carregando_sombra = False
        self._alterar_sombra(marcar=True)

    def _escolher_cor_sombra(self):
        cor = tema.escolher_cor_photoshop(self.root, cor_inicial=self.sombra_cor.get(), titulo="Cor da Sombra Projetada · Photoshop")
        if cor:
            self.sombra_ativa.set(True)
            self.sombra_cor.set(cor)
            self.amostra_sombra.configure(bg=cor)
            self._alterar_sombra(marcar=True)

    def _alterar_sombra(self, marcar=True):
        if getattr(self, "_carregando_sombra", False):
            return
        espacos = self._espacos_moldura_alvo()
        if not espacos:
            return
        if marcar:
            self.marcar_historico()
        dados_s = self._sombra_atual_dos_controles()
        for espaco in espacos:
            ed = espaco.setdefault("edicao", {})
            if self.sombra_ativa.get():
                ed["sombra"] = dict(dados_s)
            else:
                ed.pop("sombra", None)
        self._atualizar_depois_da_moldura()

    def _remover_sombra(self):
        if not self._espacos_moldura_alvo():
            return
        self.sombra_ativa.set(False)
        self._alterar_sombra(marcar=True)

    def executar_auto_sombra(self, apenas_atual=True, apenas_com_moldura=False):
        laminas = [self._lamina()] if apenas_atual else (self.projeto.get("laminas") or [])
        laminas = [l for l in laminas if isinstance(l, dict)]
        if not laminas:
            return
        self.marcar_historico()
        cfg_s = self._sombra_atual_dos_controles() if hasattr(self, "sombra_ativa") else sombra_padrao()
        cfg_s["ativa"] = True
        diagramar.aplicar_sombra_automatica(laminas, cfg_sombra=cfg_s, apenas_com_moldura=apenas_com_moldura)
        self._atualizar_sombra_ui()
        self._atualizar_depois_da_moldura()
        self.registrar("Sombra projetada aplicada automaticamente.")

    # ---------------- Temas da Lâmina (Natal, Páscoa, Casamento, Formatura...) ----------------

    def _aba_temas(self, quadro):
        ttk.Label(quadro, text="Temas Completos de Lâmina", style="Titulo.TLabel").pack(anchor="w")
        ttk.Label(
            quadro,
            text="Estiliza uma lâmina inteira ou várias lâminas selecionadas com Fundo (editável na pasta temp), Moldura + Sombra e Edição de Foto.",
            style="Fraco.TLabel",
            wraplength=290,
        ).pack(anchor="w", pady=(2, 6))

        self.tema_selecionado_var = tk.StringVar(value="colacao_grau")
        self.tema_aplicar_fundo = tk.BooleanVar(value=True)
        self.tema_aplicar_moldura = tk.BooleanVar(value=True)
        self.tema_aplicar_sombra = tk.BooleanVar(value=True)
        self.tema_aplicar_edicao = tk.BooleanVar(value=True)
        self.tema_intervalo_laminas = tk.StringVar(value="")

        box_op = ttk.LabelFrame(quadro, text="1. O que aplicar do Tema", padding=6)
        box_op.pack(fill="x", pady=(0, 6))
        ttk.Checkbutton(
            box_op,
            text="🖼 Fundo Temático (salvo em pasta temp para editar)",
            variable=self.tema_aplicar_fundo,
            command=lambda: self.executar_tema_laminas(modo="atual"),
        ).pack(anchor="w", pady=1)
        ttk.Checkbutton(
            box_op,
            text="▭ Moldura Especial do Tema (Vitral / Diamante / Galeria Slim)",
            variable=self.tema_aplicar_moldura,
            command=lambda: self.executar_tema_laminas(modo="atual"),
        ).pack(anchor="w", pady=1)
        ttk.Checkbutton(
            box_op,
            text="❒ Sombra Projetada Coordenada",
            variable=self.tema_aplicar_sombra,
            command=lambda: self.executar_tema_laminas(modo="atual"),
        ).pack(anchor="w", pady=1)
        ttk.Checkbutton(
            box_op,
            text="✨ Edição e Tom das Fotos (Color Grading do Tema)",
            variable=self.tema_aplicar_edicao,
            command=lambda: self.executar_tema_laminas(modo="atual"),
        ).pack(anchor="w", pady=1)

        box_cat = ttk.LabelFrame(quadro, text="2. Escolha o Tema / Capítulo (Atualiza na Hora)", padding=6)
        box_cat.pack(fill="x", pady=(0, 6))
        grade_t = ttk.Frame(box_cat)
        grade_t.pack(fill="x")
        for idx_t, t_info in enumerate(CATALOGO_TEMAS):
            rb_t = ttk.Radiobutton(
                grade_t,
                text=t_info["nome"],
                value=t_info["id"],
                variable=self.tema_selecionado_var,
                style="Filtro.TRadiobutton",
                command=lambda: (self._atualizar_temas_ui(), self.executar_tema_laminas(modo="atual")),
            )
            rb_t.grid(row=idx_t // 2, column=idx_t % 2, sticky="ew", padx=1, pady=1)
            tema.dica(rb_t, t_info["descricao"])
            grade_t.columnconfigure(idx_t % 2, weight=1)

        self.rotulo_resumo_tema = ttk.Label(
            box_cat,
            text="",
            style="Fraco.TLabel",
            wraplength=280,
            justify="left",
        )
        self.rotulo_resumo_tema.pack(anchor="w", pady=(5, 2))

        box_alvo = ttk.LabelFrame(quadro, text="3. Onde Aplicar (Atual ou Seleção de Várias)", padding=6)
        box_alvo.pack(fill="x", pady=(0, 6))

        self._botao(
            box_alvo,
            "🎨 Aplicar Tema na Lâmina Atual",
            lambda: self.executar_tema_laminas(modo="atual"),
            "Aplica os itens marcados do tema imediatamente na lâmina aberta",
            "PrimarioCompacto.TButton",
        ).pack(fill="x", pady=(0, 4))

        ttk.Label(
            box_alvo,
            text="Seleção de várias lâminas (ex.: 1-4, 6 ou selecione na Linha do Tempo):",
            style="Fraco.TLabel",
            wraplength=280,
        ).pack(anchor="w", pady=(2, 2))

        linha_sel_t = ttk.Frame(box_alvo)
        linha_sel_t.pack(fill="x", pady=(0, 4))
        self.entry_intervalo_tema = ttk.Entry(linha_sel_t, textvariable=self.tema_intervalo_laminas, width=14)
        self.entry_intervalo_tema.pack(side="left", fill="x", expand=True, padx=(0, 4))
        tema.dica(self.entry_intervalo_tema, "Digite números ou intervalos de lâminas (ex.: 1-3, 5, 8). Vazio = usa as lâminas selecionadas na Linha do Tempo.")
        self._botao(
            linha_sel_t,
            "Usar Timeline",
            self._preencher_intervalo_da_timeline,
            "Preenche com as lâminas atualmente selecionadas na Linha do Tempo",
            "Compacto.TButton",
        ).pack(side="right")

        linha_bt_t = ttk.Frame(box_alvo)
        linha_bt_t.pack(fill="x", pady=1)
        self._botao(
            linha_bt_t,
            "✨ Nas Lâminas Selecionadas",
            lambda: self.executar_tema_laminas(modo="selecao"),
            "Aplica o tema em todas as lâminas do intervalo digitado ou selecionadas na Linha do Tempo",
            "PrimarioCompacto.TButton",
        ).pack(side="left", fill="x", expand=True, padx=(0, 2))
        self._botao(
            linha_bt_t,
            "✨ No Álbum Inteiro",
            lambda: self.executar_tema_laminas(modo="todas"),
            "Aplica o tema em todas as lâminas do álbum",
            "Compacto.TButton",
        ).pack(side="left", fill="x", expand=True, padx=(2, 0))

        self._botao(
            box_alvo,
            "↺ Remover Tema / Restaurar Padrão nas Selecionadas",
            self.remover_tema_laminas,
            "Remove fundo de tema, moldura, sombra e edição de tema das lâminas selecionadas",
            "Compacto.TButton",
        ).pack(fill="x", pady=(6, 0))

        self._atualizar_temas_ui()

    def _preencher_intervalo_da_timeline(self):
        sel = sorted(getattr(self, "laminas_sel", set()) or ({self.atual} if isinstance(self.atual, int) else set()))
        nums = [str(i + 1) for i in sel if isinstance(i, int) and i >= 0]
        self.tema_intervalo_laminas.set(", ".join(nums) if nums else "1")

    def _resolver_laminas_alvo_tema(self, modo="atual"):
        todas = self.projeto.get("laminas") or []
        if not todas:
            return []
        if modo == "todas":
            return list(todas)
        if modo == "atual":
            lam = self._lamina()
            return [lam] if lam is not None else []
        texto = (self.tema_intervalo_laminas.get() or "").strip()
        indices = set()
        if texto:
            for parte in texto.replace(";", ",").split(","):
                parte = parte.strip()
                if not parte:
                    continue
                if "-" in parte:
                    p0, p1 = parte.split("-", 1)
                    if p0.strip().isdigit() and p1.strip().isdigit():
                        a, b = sorted((int(p0.strip()), int(p1.strip())))
                        for n in range(a, b + 1):
                            if 1 <= n <= len(todas):
                                indices.add(n - 1)
                elif parte.isdigit():
                    n = int(parte)
                    if 1 <= n <= len(todas):
                        indices.add(n - 1)
        if not indices:
            sel_tl = getattr(self, "laminas_sel", set())
            for i in sel_tl:
                if isinstance(i, int) and 0 <= i < len(todas):
                    indices.add(i)
        if not indices and isinstance(self.atual, int) and 0 <= self.atual < len(todas):
            indices.add(self.atual)
        return [todas[i] for i in sorted(indices)]

    def _atualizar_temas_ui(self):
        if not hasattr(self, "tema_selecionado_var"):
            return
        id_t = self.tema_selecionado_var.get()
        t_cfg = MAPA_TEMAS.get(id_t) or CATALOGO_TEMAS[0]
        if hasattr(self, "rotulo_resumo_tema"):
            mold_nome = NOMES_ESTILOS_MOLDURA.get((t_cfg.get("moldura") or {}).get("estilo", "royal"), "Royal")
            self.rotulo_resumo_tema.configure(
                text="%s\n• Moldura padrão: %s · Fundo editável em Pasta Temp" % (t_cfg["descricao"], mold_nome)
            )

    def executar_tema_laminas(self, modo="atual"):
        laminas_alvo = self._resolver_laminas_alvo_tema(modo=modo)
        if not laminas_alvo:
            return
        id_t = self.tema_selecionado_var.get() or "colacao_grau"
        t_cfg = MAPA_TEMAS.get(id_t) or CATALOGO_TEMAS[0]
        self.marcar_historico()
        qtd = diagramar.aplicar_tema_em_laminas(
            laminas_alvo,
            id_t,
            info=self.info,
            aplicar_fundo=bool(self.tema_aplicar_fundo.get()),
            aplicar_moldura=bool(self.tema_aplicar_moldura.get()),
            aplicar_sombra=bool(self.tema_aplicar_sombra.get()),
            aplicar_edicao=bool(self.tema_aplicar_edicao.get()),
        )
        self._atualizar_fundo_ui()
        self._atualizar_moldura_ui()
        self._atualizar_sombra_ui()
        self.atualizar_fundos_gerados(manter_seed=True)
        self._atualizar_depois_da_moldura()
        self.registrar("Tema '%s' aplicado em %d lâmina(s)." % (t_cfg["nome"], qtd))

    def remover_tema_laminas(self):
        laminas_alvo = self._resolver_laminas_alvo_tema(modo="selecao")
        if not laminas_alvo:
            return
        self.marcar_historico()
        for lam in laminas_alvo:
            lam["fundo"] = "#ffffff"
            lam.pop("fundo_imagem", None)
            lam.pop("tema_aplicado", None)
            for esp in lam.get("espacos") or []:
                ed = esp.get("edicao")
                if isinstance(ed, dict):
                    ed.pop("moldura", None)
                    ed.pop("sombra", None)
                    for ch_ed in ("brilho", "contraste", "saturacao", "matiz", "colorir_cor", "colorir_int"):
                        ed.pop(ch_ed, None)
        self._atualizar_fundo_ui()
        self._atualizar_moldura_ui()
        self._atualizar_sombra_ui()
        self._atualizar_depois_da_moldura()
        self.registrar("Tema removido das lâminas selecionadas.")

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

        auto_d = ttk.LabelFrame(quadro, text="Função 2: Degradê Automático (Regras)", padding=6)
        auto_d.pack(fill="x", pady=(8, 0))
        ttk.Label(
            auto_d,
            text="Aplica degradê de transição apenas em fotos de destaque/meio-termo, protegendo rostos e sem aplicar em grades.",
            style="Fraco.TLabel",
            wraplength=280,
            justify="left",
        ).pack(anchor="w", pady=(0, 4))
        linha_ad = ttk.Frame(auto_d)
        linha_ad.pack(fill="x")
        self._botao(
            linha_ad,
            "✨ Nesta Lâmina",
            lambda: self.executar_auto_degrade(apenas_atual=True),
            "Aplica as regras de degradê automático na lâmina aberta",
            "PrimarioCompacto.TButton",
        ).pack(side="left", fill="x", expand=True, padx=(0, 2))
        self._botao(
            linha_ad,
            "✨ No Álbum Inteiro",
            lambda: self.executar_auto_degrade(apenas_atual=False),
            "Aplica as regras de degradê automático em todas as lâminas do álbum",
            "Compacto.TButton",
        ).pack(side="left", fill="x", expand=True, padx=(2, 0))

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
        Prioriza a foto clicada na lâmina; se nenhuma estiver clicada, foca automaticamente na
        foto da lâmina atual que já tenha degradê ativo (ou na primeira foto da lâmina).
        """
        espacos = []
        lamina = self._lamina()
        if getattr(self, "degrade_em_fotos", None) and self.degrade_em_fotos.get():
            espacos = self._espacos_moldura_alvo()
            if not espacos and lamina is not None:
                com_foto = [e for e in (lamina.get("espacos") or []) if e.get("foto")]
                com_deg = [
                    e for e in com_foto
                    if isinstance((e.get("edicao") or {}).get("degrade"), dict)
                    and (e.get("edicao") or {}).get("degrade", {}).get("ativo")
                ]
                if com_deg:
                    espacos = [com_deg[0]]
                elif com_foto:
                    espacos = [com_foto[0]]
        fundos_alvos = []
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
        lamina = self._lamina()
        resumo = []
        if espacos:
            if len(espacos) == 1 and lamina is not None and espacos[0] in (lamina.get("espacos") or []):
                idx_slot = (lamina.get("espacos") or []).index(espacos[0]) + 1
                nome_arq = os.path.basename(str(espacos[0].get("foto") or ""))
                resumo.append("Quadro #%d (%s)" % (idx_slot, nome_arq[:22]))
            else:
                resumo.append("%d foto(s)" % len(espacos))
        if fundos_alvos:
            resumo.append("%d fundo(s)" % len(fundos_alvos))
        self.rotulo_alvo_degrade.configure(
            text=("Editando na lâmina: " + " + ".join(resumo) + " · Clique em outra foto na lâmina para trocar") if resumo
            else "Clique em uma foto na lâmina para editar o degradê dela."
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
                registrar_erro("agendado '%s'" % chave)

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
        self._versao_modificacao += 1
        if self.modificado:
            self.agendar("titulo", 250, self._atualizar_titulo)
            return
        self.modificado = True
        self.cancelar_agendado("titulo")
        self._atualizar_titulo()

    def _atualizar_titulo(self):
        nome = self.projeto.get("nome") or "Novo álbum"
        lim_nome = 18 if self.root.winfo_screenwidth() <= 1500 else 26
        curto = nome if len(nome) <= lim_nome else nome[:lim_nome - 2] + "…"
        self.rotulo_nome.configure(text=curto + (" *" if self.modificado else ""))
        self.root.title("%s%s - Diagramador Inteligente" % (nome, " *" if self.modificado else ""))
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

        ids_exportando = set()
        job_at = getattr(self, "_job_exportacao_atual", None)
        if isinstance(job_at, dict) and job_at.get("id_aba"):
            ids_exportando.add(job_at["id_aba"])
        for j in getattr(self, "fila_jobs_exportacao", []):
            if isinstance(j, dict) and j.get("id_aba"):
                ids_exportando.add(j["id_aba"])

        dados_abas = []
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
            dica_a = ("Clique para alternar para '%s' · Duplo clique para renomear o álbum (subpasta: '%s')"
                      % (nome_a, exportar.nome_pasta_album(prod_a, proj_a)))
            dados_abas.append((aba.get("id"), ativa, em_exp, rotulo, dica_a))

        estrutura = tuple(d[0] for d in dados_abas)
        widgets_ui = getattr(self, "_widgets_abas_albuns", None)
        if widgets_ui and widgets_ui[0] == estrutura and all(q.winfo_exists() for q, _b, _f in widgets_ui[1]):
            for (_id, ativa, em_exp, rotulo, dica_a), (quadro_aba, bt_aba, bt_fechar) in zip(dados_abas, widgets_ui[1]):
                cor = tema.DESTAQUE_ESCURO if ativa else tema.PAINEL
                quadro_aba.configure(bg=cor, highlightbackground=tema.DESTAQUE if ativa else ("#f59e0b" if em_exp else tema.BORDA))
                bt_aba.configure(text=rotulo, bg=cor, fg="#ffffff" if ativa else ("#fbbf24" if em_exp else tema.TEXTO),
                                 font=tema.FONTE_FORTE if ativa else tema.FONTE_NORMAL)
                if getattr(bt_aba, "_dica", None) is not None:
                    bt_aba._dica.texto = dica_a
                if bt_fechar is not None:
                    bt_fechar.configure(bg=cor)
            return

        for w in barra.winfo_children():
            w.destroy()
        widgets_abas = []
        self._widgets_abas_albuns = (estrutura, widgets_abas)

        tk.Label(
            barra, text="ÁLBUNS ABERTOS:", bg=tema.PAINEL_2, fg=tema.TEXTO_FRACO, font=tema.FONTE_PEQUENA
        ).pack(side="left", padx=(0, 6))

        for i, (_id, ativa, em_exp, rotulo, dica_a) in enumerate(dados_abas):
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
            tema.dica(bt_aba, dica_a)

            bt_fechar = None
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
            widgets_abas.append((quadro_aba, bt_aba, bt_fechar))

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
                try:
                    msg = self.fila.get_nowait()
                except queue.Empty:
                    break
                tipo = msg[0]
                if tipo == "progresso":
                    ultimo_progresso = msg
                    continue
                if tipo == "render":
                    ultimo_render = msg
                    continue
                try:
                    ultimo_progresso = self._tratar_mensagem(msg, ultimo_progresso)
                except tk.TclError:
                    pass
                except Exception:
                    registrar_erro("mensagem da fila '%s'" % tipo)
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
            except Exception:
                registrar_erro("fila (progresso/render)")
        finally:
            try:
                self.root.after(18, self._ler_fila)
            except tk.TclError:
                pass

    def _tratar_mensagem(self, msg, ultimo_progresso):
        tipo = msg[0]
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
                    registrar_erro("fim da tarefa")
                    messagebox.showerror("Erro", str(e), parent=self.root)
            elif erro:
                messagebox.showerror("Erro", str(erro), parent=self.root)
        elif tipo == "miniatura_timeline":
            self._receber_timeline(*msg[1:])
        elif tipo == "miniatura_foto":
            self._receber_miniatura_foto(*msg[1:])
        elif tipo == "descartado":
            self._pedido_descartado(msg[1])
        elif tipo == "foto_trocada":
            self._foto_trocada(msg[1], msg[2])
        elif tipo == "arquivo_mudou":
            self._foto_trocada(msg[1], msg[1])
        elif tipo == "analisada":
            self.info[normalizar_caminho(msg[1])] = msg[2]
            self._depois_de_trocar_foto(msg[1])
            self._agendar_ia_fundo([msg[1]])
        elif tipo == "ia_pronta":
            self._receber_ia(msg[1], msg[2])
        elif tipo == "chamar":
            msg[1]()
        return ultimo_progresso

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
                caminho_salvo = proj.caminho_projeto(caminho)
                texto = texto_json(self.projeto)
                projeto_salvo, versao_salva = self.projeto, self._versao_modificacao

                def gravar():
                    try:
                        gravar_texto(caminho_salvo, texto)
                    except Exception as e:
                        erro = e
                        self.fila.put(("chamar", lambda: self.registrar("Aviso no auto-salvamento: %s" % erro)))
                        return
                    self.fila.put(("chamar", lambda: self._auto_salvar_gravado(projeto_salvo, versao_salva, caminho_salvo)))

                threading.Thread(target=gravar, daemon=True).start()
        except Exception as e:
            self.registrar("Aviso no auto-salvamento: %s" % e)
        finally:
            self._reagendar_auto_salvar()

    def _auto_salvar_gravado(self, projeto_salvo, versao_salva, caminho_salvo):
        if projeto_salvo is not self.projeto:
            return
        if not self.caminho_projeto:
            self.caminho_projeto = str(caminho_salvo)
        if versao_salva == self._versao_modificacao:
            self.modificado = False
            self._atualizar_titulo()
        self.registrar("Auto-salvamento (.album.json): salvo em %s" % Path(caminho_salvo).name)

    def novo_projeto(self, silencioso=False):
        if not silencioso and not self._confirmar_descartar():
            return
        self._limpar_pastas_temp_drive(limpar_autosave=True)
        self._carregar_projeto(proj.novo(self.produto["nome"]))
        self.info = {}
        self.registrar("Álbum novo. Arraste fotos ou uma pasta para a janela.")

    def abrir_projeto(self, caminho=None, em_nova_aba=False):
        if not caminho:
            escolhidos = filedialog.askopenfilenames(
                parent=self.root,
                title="Abrir projeto de álbum ou fotos",
                filetypes=[
                    (
                        "Projetos e Fotos (*.album.json; *.json; *.jpg; *.png; *.webp; *.tif)",
                        "*.album.json *.json *.saproj *.jpg *.jpeg *.jfif *.png *.webp *.tif *.tiff *.bmp",
                    ),
                    ("Projeto de Álbum (*.album.json; *.json)", "*.album.json *.json *.saproj"),
                    ("Imagens (*.jpg; *.png; *.webp; *.tif)", "*.jpg *.jpeg *.jfif *.png *.webp *.tif *.tiff *.bmp"),
                    ("Todos os arquivos", "*.*"),
                ],
            )
            if not escolhidos:
                return
            fotos_sel, projetos_sel = entrada.expandir(list(escolhidos))
            if fotos_sel and not projetos_sel:
                self.adicionar_caminhos([str(f) for f in fotos_sel])
                return
            caminho = str(projetos_sel[0]) if projetos_sel else str(escolhidos[0])

        p_cam = Path(str(caminho))
        if p_cam.is_dir() or (p_cam.is_file() and p_cam.suffix.lower() in EXTENSOES and not entrada._eh_arquivo_projeto_valido(p_cam)):
            self.adicionar_caminhos([str(p_cam)])
            return

        if not em_nova_aba and not self._confirmar_descartar():
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
        caminho = proj.caminho_projeto(caminho)
        nome_antes = self.projeto.get("nome")
        self.projeto["nome"] = caminho.name[: -len(proj.EXTENSAO)]
        try:
            caminho = proj.salvar(self.projeto, caminho)
        except OSError as e:
            self.projeto["nome"] = nome_antes
            messagebox.showerror("Salvar", str(e), parent=self.root)
            return False
        self.caminho_projeto = str(caminho)
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

    def _executar_limpeza_programada_temp_drive(self, limite_mb=4096):
        """
        Limpeza programada periódica: remove do disco apenas arquivos temporários órfãos
        que JÁ FORAM REMOVIDOS do projeto pelo usuário (nunca apaga fotos presentes em
        `self.projeto["fotos"]`, nas lâminas ou em outras abas abertas).
        As pastas temporárias em uso pelo projeto ativo são apagadas somente ao fechar o
        programa ou ao iniciar/abrir outro projeto.
        """
        try:
            pastas = [p for p in list(getattr(self, "_pastas_temp_drive", [])) if p]
            if pastas and self.tarefa is None and not getattr(self, "_limpeza_temp_rodando", False):
                ativas_no_projeto = self._caminhos_em_uso_abertos()
                self._limpeza_temp_rodando = True

                def procurar():
                    orfaos = []
                    try:
                        for p in pastas:
                            if not Path(p).exists():
                                continue
                            for arq in Path(p).rglob("*"):
                                if arq.is_file():
                                    ch_norm = normalizar_caminho(arq)
                                    if ch_norm not in ativas_no_projeto:
                                        orfaos.append((str(arq), ch_norm))
                    except Exception:
                        registrar_erro("limpeza programada dos temporários do Drive")
                    self.fila.put(("chamar", lambda: self._apagar_orfaos_temp_drive(orfaos)))

                threading.Thread(target=procurar, daemon=True).start()
                return
        except Exception:
            registrar_erro("limpeza programada dos temporários do Drive")
        self.agendar("limpeza_temp_drive", 30000, self._executar_limpeza_programada_temp_drive)

    def _caminhos_em_uso_abertos(self):
        projetos_abertos = [self.projeto]
        for aba in getattr(self, "abas_albuns", []) or []:
            if isinstance(aba, dict) and isinstance(aba.get("projeto"), dict):
                projetos_abertos.append(aba["projeto"])
        ativas_no_projeto = set()
        for prj in projetos_abertos:
            for f in prj.get("fotos", []):
                if isinstance(f, dict) and f.get("caminho"):
                    ativas_no_projeto.add(normalizar_caminho(f["caminho"]))
            for e in proj.todos_espacos(prj):
                if e.get("foto"):
                    ativas_no_projeto.add(normalizar_caminho(e["foto"]))
            capa = prj.get("capa") or {}
            if capa.get("foto"):
                ativas_no_projeto.add(normalizar_caminho(capa["foto"]))
            for d in proj.fundos_imagem(prj):
                if d.get("caminho"):
                    ativas_no_projeto.add(normalizar_caminho(d["caminho"]))
        return ativas_no_projeto

    def _apagar_orfaos_temp_drive(self, orfaos):
        try:
            ativas_no_projeto = self._caminhos_em_uso_abertos() if orfaos else set()
            apagar = []
            for ch, ch_norm in orfaos:
                if ch_norm in ativas_no_projeto:
                    continue
                if hasattr(self, "observador") and self.observador:
                    self.observador.esquecer(ch)
                analise.esquecer_miniaturas(ch)
                self.info.pop(ch_norm, None)
                apagar.append(ch)
            if apagar:
                def remover():
                    for ch in apagar:
                        try:
                            Path(ch).unlink(missing_ok=True)
                        except OSError:
                            pass
                threading.Thread(target=remover, daemon=True).start()
        except Exception:
            registrar_erro("limpeza programada dos temporários do Drive")
        finally:
            self._limpeza_temp_rodando = False
            if not getattr(self, "_limpeza_temp_parada", False):
                self.agendar("limpeza_temp_drive", 30000, self._executar_limpeza_programada_temp_drive)

    def _limpar_pastas_temp_drive(self, limpar_autosave=True):
        """
        Remove imediatamente do disco todas as pastas temporárias do Google Drive e
        quaisquer arquivos temporários/caches órfãos, liberando descritores abertos no Windows.
        """
        self.cancelar_agendado("limpeza_temp_drive")
        self._limpeza_temp_parada = True
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
        self._limpar_fundos_gerados()
        analise.gravar_cache()
        self.root.destroy()

    def _limpar_fundos_gerados(self):
        """Apaga os PNGs de fundos gerados; o projeto guarda a receita e eles são recriados quando usados."""
        try:
            limpar_fundos_temporarios()
        except Exception:
            pass

    # ==================================================================
    # Fotos: entrada
    # ==================================================================

    def adicionar_caminhos(self, caminhos, destino=None, perguntar_diagramar=False, vindo_linha_comando=False):
        """
        Fotos, pastas ou projetos vindos do Explorer, do Ctrl+V, do Link do Drive ou da linha de comando.
        destino: ("espaco", lamina, espaco) ou ("lamina", lamina) para já colocar as fotos.
        """
        caminhos_limpos = []
        for c in (caminhos or []):
            limpo = entrada._limpar_caminho_individual(c) or str(c or "").strip().strip('"').strip("'")
            if limpo:
                caminhos_limpos.append(limpo)
        caminhos = caminhos_limpos
        tem_pasta_arrastada = any(Path(c).is_dir() for c in caminhos)
        if tem_pasta_arrastada:
            # Quando uma pasta inteira é arrastada do PC, nunca espreme todas as fotos dentro de um único quadro
            destino = None
        fotos, projetos = entrada.expandir(caminhos)
        if projetos and not fotos:
            self.abrir_projeto(str(projetos[0]))
            return
        if vindo_linha_comando and len(fotos) == 1 and not tem_pasta_arrastada:
            pasta_pai = Path(fotos[0]).parent
            try:
                todas_da_pasta = analise.listar_imagens(pasta_pai)
                if len(todas_da_pasta) > 1:
                    if messagebox.askyesno(
                        "Abrir Fotos da Pasta",
                        "Você abriu a foto '%s'.\nA pasta '%s' possui %d fotos no total.\n\n"
                        "Deseja carregar TODAS as %d fotos desta pasta para montar o álbum?\n"
                        "(Clique em 'Não' para carregar apenas a foto selecionada.)"
                        % (Path(fotos[0]).name, pasta_pai.name, len(todas_da_pasta), len(todas_da_pasta)),
                        parent=self.root,
                    ):
                        fotos = todas_da_pasta
                        tem_pasta_arrastada = True
            except Exception:
                pass
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
            if pasta_origem and not self._eh_pasta_temporaria_drive(pasta_origem):
                entrada.adicionar_recente("pasta", pasta_origem)
            self._carregar_ui_do_projeto()
        elif pasta_origem and not self._eh_pasta_temporaria_drive(pasta_origem):
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
            self._agendar_ia_fundo(novas)
            if destino:
                self._colocar_no_destino(novas, destino)
            elif perguntar_diagramar:
                if messagebox.askyesno(
                    "Google Drive",
                    "%d foto(s) baixada(s) na pasta temporária.\nDeseja diagramar essas fotos agora?" % len(info),
                    parent=self.root,
                ):
                    if novo or sem_laminas:
                        self.abrir_diagramacao(sugerir_ideal=True)
                    else:
                        self.diagramar_tudo(False, escopo="pasta", pasta_alvo=str(pasta_origem) if pasta_origem else None)
            elif novo or sem_laminas:
                self.abrir_diagramacao(sugerir_ideal=True)
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
            self._agendar_ia_fundo(caminhos)

        self.executar_quando_livre("Lendo as fotos do álbum", trabalho, fim)

    def escolher_fotos(self):
        caminhos = filedialog.askopenfilenames(
            parent=self.root,
            title="Adicionar fotos",
            filetypes=[
                ("Imagens", "*.jpg *.jpeg *.jfif *.png *.webp *.tif *.tiff *.bmp"),
                ("Todos os arquivos", "*.*"),
            ],
        )
        if caminhos:
            self.adicionar_caminhos(list(caminhos))

    def escolher_pasta(self):
        pasta = filedialog.askdirectory(parent=self.root, title="Adicionar pasta de fotos")
        if pasta:
            self.adicionar_caminhos([pasta])

    def importar_link_drive(self):
        janela_drive.abrir(self)

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

    def associar_arquivos_windows(self):
        ok = entrada.registrar_associacao_arquivos_windows()
        if ok:
            exe, _ = entrada.comando_programa()
            messagebox.showinfo(
                "Associação de Arquivos no Windows",
                "Pronto! O executável (%s) foi associado no Windows:\n\n"
                "• Projetos (.album.json e .saproj) abrem direto com duplo clique\n"
                "• Pastas no Explorer possuem a opção 'Abrir pasta no Smart Album' no botão direito\n"
                "• Fotos possuem a opção 'Abrir foto(s) no Smart Album' e suporte no menu 'Abrir com...'"
                % Path(exe).name,
                parent=self.root,
            )
        else:
            messagebox.showwarning(
                "Associação de Arquivos",
                "A associação automática de arquivos está disponível apenas no Windows.",
                parent=self.root,
            )

    # ==================================================================
    # Diagramação
    # ==================================================================

    def abrir_diagramacao(self, sugerir_ideal=False):
        """Janela de diagramação. Com sugerir_ideal (primeira diagramação), já traz o nº ideal de lâminas."""
        todas_fotos = proj.fotos_para_diagramar(self.projeto, self.info)
        if not todas_fotos:
            self.registrar("Adicione fotos antes de diagramar.")
            return
        fotos_livres = proj.fotos_para_diagramar(self.projeto, self.info, somente_nao_usadas=True)
        pastas_proj = proj.listar_pastas_projeto(self.projeto)

        cfg = self._cfg()
        paginas = max(1, int(self.produto["paginas_por_lamina"]))

        janela = tk.Toplevel(self.root)
        janela.title("Diagramação Automática")
        janela.configure(bg=tema.PAINEL)
        janela.transient(self.root)

        tema.criar_cabecalho_janela(
            janela,
            "Diagramação Automática",
            "Organizado em 3 abas rápidas: Básico, Grupos e Ordem, e Acabamento",
            icone="❖",
        )

        # Barra de 3 abas no topo da janela
        barra_abas = tk.Frame(
            janela,
            bg=tema.CABECALHO,
            highlightthickness=1,
            highlightbackground=tema.BORDA,
            padx=14,
            pady=6,
        )
        barra_abas.pack(side="top", fill="x")
        aba_diag_var = tk.StringVar(value="basico")

        # Rodapé fixo na base da janela com resumo ao vivo + botões de ação
        rodape_fixo = tk.Frame(
            janela,
            bg=tema.CABECALHO,
            highlightthickness=1,
            highlightbackground=tema.BORDA,
            padx=14,
            pady=10,
        )
        rodape_fixo.pack(side="bottom", fill="x")

        quadro, _container_rol, canvas_rol = tema.criar_corpo_rolavel(janela, padding=14)

        aba_basico = ttk.Frame(quadro)
        aba_grupos = ttk.Frame(quadro)
        aba_acabamento = ttk.Frame(quadro)

        def _trocar_aba_diag(*_args):
            aba = aba_diag_var.get()
            aba_basico.pack_forget()
            aba_grupos.pack_forget()
            aba_acabamento.pack_forget()
            if aba == "grupos":
                aba_grupos.pack(fill="x", expand=True)
            elif aba == "acabamento":
                aba_acabamento.pack(fill="x", expand=True)
            else:
                aba_basico.pack(fill="x", expand=True)
            try:
                canvas_rol.yview_moveto(0.0)
            except Exception:
                pass

        for val_aba, txt_aba in (
            ("basico", "1. Básico (Escopo e Medidas)"),
            ("grupos", "2. Grupos e Ordem"),
            ("acabamento", "3. Acabamento (Fundo, Degradê e Moldura)"),
        ):
            ttk.Radiobutton(
                barra_abas,
                text=txt_aba,
                value=val_aba,
                variable=aba_diag_var,
                style="Filtro.TRadiobutton",
                command=_trocar_aba_diag,
            ).pack(side="left", fill="x", expand=True, padx=2)

        # ==================================================================
        # ABA 1: BÁSICO (Escopo, Quantidades/Margens e Proporção/Layouts)
        # ==================================================================
        quadro_escopo = ttk.LabelFrame(aba_basico, text="1. O que você deseja diagramar?", padding=10)
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
            width=34,
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

        numeros = ttk.LabelFrame(aba_basico, text="2. Quantidade de Lâminas, Fotos por Página e Margens", padding=10)
        numeros.pack(fill="x", pady=(0, 8))

        vars_num = {
            "laminas_desejadas": tk.IntVar(value=int(
                diagramar.laminas_ideais(len(todas_fotos), self.produto, cfg) if sugerir_ideal
                else cfg.get("laminas_desejadas", self.produto["laminas_min"])
            )),
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
            ("Mínimo de fotos por página", "fotos_por_pagina", 1, diagramar.MAX_POR_PAGINA, 1),
            ("Máximo de fotos por página", "max_fotos_pagina", 1, diagramar.MAX_POR_PAGINA, 1),
            ("Margem externa de segurança (mm)", "margem_mm", 0, 60, 1),
            ("Espaçamento entre fotos (mm)", "espacamento_mm", 0, 40, 0.5),
            ("Auto-salvamento do projeto (min, 0 = desligado)", "auto_salvar_minutos", 0, 60, 1),
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

        quadro_geom = ttk.LabelFrame(
            aba_basico,
            text="3. Proporção Elástica e Tipos de Layout Permitidos",
            padding=10,
        )
        quadro_geom.pack(fill="x", pady=(0, 6))

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
            text="Preencher página com corte elástico controlado:",
            value="elastico",
            variable=modo_corte_var,
        ).pack(side="left")
        spin_corte_pct = ttk.Spinbox(
            linha_modo_corte,
            from_=5,
            to=35,
            increment=1,
            textvariable=corte_max_pct_var,
            width=5,
        )
        spin_corte_pct.pack(side="right")
        ttk.Label(linha_modo_corte, text="Limite máx. (%):").pack(side="right", padx=(6, 4))

        ttk.Radiobutton(
            quadro_geom,
            text="Preservar 100% da proporção original em todas as fotos (0% de corte)",
            value="sem_corte",
            variable=modo_corte_var,
        ).pack(anchor="w", pady=2)

        ttk.Separator(quadro_geom, orient="horizontal").pack(fill="x", pady=6)

        ttk.Checkbutton(
            quadro_geom,
            text="Permitir Meio Termo (1 página sangrada + outra página dentro da área segura)",
            variable=meio_termo_var,
        ).pack(anchor="w", pady=2)
        ttk.Checkbutton(
            quadro_geom,
            text="Permitir Transpasses 60/40 e 70/30 que atravessam o centro da lâmina",
            variable=transpasse_var,
        ).pack(anchor="w", pady=2)
        ttk.Checkbutton(
            quadro_geom,
            text="Permitir Sangria Total (preenchimento máximo até a borda externa)",
            variable=sangria_total_var,
        ).pack(anchor="w", pady=2)
        ttk.Checkbutton(
            quadro_geom,
            text="Detectar pessoas/rostos para nunca cortar ninguém nem posicionar rostos na dobra",
            variable=nao_cortar_pessoas_var,
            command=lambda: rostos_var.set(bool(nao_cortar_pessoas_var.get())),
        ).pack(anchor="w", pady=2)
        ttk.Checkbutton(
            quadro_geom,
            text="Excluir fotos visualmente repetidas (mantém apenas uma cópia)",
            variable=repetidas_var,
        ).pack(anchor="w", pady=2)

        # ==================================================================
        # ABA 2: GRUPOS E ORDEM (Pastas, Grupos Numerados, Cenário e Pessoas)
        # ==================================================================
        quadro_grupos = ttk.LabelFrame(
            aba_grupos,
            text="1. Divisão por Pastas, Grupos Numerados e Cenário Visual",
            padding=10,
        )
        quadro_grupos.pack(fill="x", pady=(0, 8))

        separar_pastas_var = tk.BooleanVar(value=bool(cfg.get("separar_pastas", True)))
        respeitar_grupos_var = tk.BooleanVar(value=bool(cfg.get("respeitar_grupos_manuais", True)))
        reconhecer_cenarios_var = tk.BooleanVar(value=bool(cfg.get("reconhecer_cenarios", True)))
        limiar_cenario_var = tk.DoubleVar(value=max(0.15, min(0.45, float(cfg.get("limiar_cenario", 0.28)))))
        ordem_pessoas_var = tk.StringVar(value=str(cfg.get("ordem_pessoas_grupo", "manter") or "manter"))

        ttk.Checkbutton(
            quadro_grupos,
            text="Respeitar divisão por pastas (nunca misturar pastas diferentes na mesma lâmina)",
            variable=separar_pastas_var,
        ).pack(anchor="w", pady=2)
        ttk.Checkbutton(
            quadro_grupos,
            text="Respeitar grupos numerados (nunca misturar grupos diferentes na mesma lâmina)",
            variable=respeitar_grupos_var,
        ).pack(anchor="w", pady=2)

        linha_cenario_cfg = ttk.Frame(quadro_grupos)
        linha_cenario_cfg.pack(fill="x", pady=2)
        ttk.Checkbutton(
            linha_cenario_cfg,
            text="🎨 Agrupar por cenário visual (cor/iluminação) dentro de cada grupo",
            variable=reconhecer_cenarios_var,
        ).pack(side="left")
        spin_limiar_cen = ttk.Spinbox(
            linha_cenario_cfg,
            from_=0.15,
            to=0.45,
            increment=0.02,
            format="%.2f",
            textvariable=limiar_cenario_var,
            width=6,
        )
        spin_limiar_cen.pack(side="right")
        ttk.Label(
            linha_cenario_cfg,
            text="Sensibilidade (0.28 padrão):",
            style="Fraco.TLabel",
        ).pack(side="right", padx=(6, 4))

        quadro_ordem_p = ttk.LabelFrame(
            aba_grupos,
            text="2. Prioridade de Fotos (Individuais vs. Turma) dentro de cada Grupo",
            padding=10,
        )
        quadro_ordem_p.pack(fill="x", pady=(0, 8))

        ttk.Radiobutton(
            quadro_ordem_p,
            text="Manter exatamente a ordem manual das fotos em cada grupo",
            value="manter",
            variable=ordem_pessoas_var,
        ).pack(anchor="w", pady=2)
        ttk.Radiobutton(
            quadro_ordem_p,
            text="👤 Fotos Individuais primeiro (1 pessoa → depois fotos em grupo/turma)",
            value="individuais_primeiro",
            variable=ordem_pessoas_var,
        ).pack(anchor="w", pady=2)
        ttk.Radiobutton(
            quadro_ordem_p,
            text="👥 Fotos em Grupo/Turma primeiro (2+ pessoas → depois fotos individuais)",
            value="turma_primeiro",
            variable=ordem_pessoas_var,
        ).pack(anchor="w", pady=2)

        quadro_ger_grupos = ttk.LabelFrame(
            aba_grupos,
            text="3. Editor Visual de Ordem e Grupos Numerados",
            padding=10,
        )
        quadro_ger_grupos.pack(fill="x", pady=(0, 6))

        lbl_resumo_grupos = ttk.Label(
            quadro_ger_grupos,
            text="",
            style="Fraco.TLabel",
            wraplength=580,
            justify="left",
        )
        lbl_resumo_grupos.pack(anchor="w", pady=(0, 6))

        def _atualizar_resumo_grupos():
            grupos_atuais = proj.listar_grupos_ordem(self.projeto)
            qtd_g = len(grupos_atuais)
            nomes_g = ", ".join("%s (%d fotos)" % (g["nome"], len(g["fotos"])) for g in grupos_atuais[:5])
            if qtd_g > 5:
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
            quadro_ger_grupos,
            text="🗂 Abrir Editor de Ordem e Grupos de Fotos...",
            style="PrimarioCompacto.TButton",
            command=_abrir_janela_grupos_daqui,
        ).pack(anchor="w")

        # ==================================================================
        # ABA 3: ACABAMENTO (Fundos, Degradê em Destaques e Molduras)
        # ==================================================================
        quadro_f1 = ttk.LabelFrame(
            aba_acabamento,
            text="1. Fundos Automáticos (Cores das Fotos e Pasta de Fundos)",
            padding=10,
        )
        quadro_f1.pack(fill="x", pady=(0, 8))
        auto_fundos_var = tk.BooleanVar(value=bool(cfg.get("auto_fundos", True)))
        modo_auto_fundos_var = tk.StringVar(value=str(cfg.get("modo_auto_fundos", "misturar") or "misturar"))
        estilo_fundo_gerado_var = tk.StringVar(value=str(cfg.get("estilo_fundo_gerado", "ambos") or "ambos"))
        escopo_auto_fundos_var = tk.StringVar(value=str(cfg.get("escopo_auto_fundos", "auto") or "auto"))

        ttk.Checkbutton(
            quadro_f1,
            text="Gerar ou aplicar Fundos Automáticos harmonizados com as fotos",
            variable=auto_fundos_var,
        ).pack(anchor="w", pady=(0, 6))

        def _criar_combo_acabamento(pai, rotulo_txt, var_alvo, pares_opcoes):
            linha = ttk.Frame(pai)
            linha.pack(fill="x", pady=2)
            ttk.Label(linha, text=rotulo_txt, width=26).pack(side="left")
            textos = [t for _v, t in pares_opcoes]
            val_para_txt = {v: t for v, t in pares_opcoes}
            txt_para_val = {t: v for v, t in pares_opcoes}
            txt_var = tk.StringVar(value=val_para_txt.get(var_alvo.get(), textos[0]))
            cb = ttk.Combobox(linha, textvariable=txt_var, values=textos, state="readonly")
            cb.pack(side="left", fill="x", expand=True)
            cb.bind("<<ComboboxSelected>>", lambda _e: var_alvo.set(txt_para_val.get(txt_var.get(), pares_opcoes[0][0])))
            return cb

        _criar_combo_acabamento(
            quadro_f1,
            "Origem dos fundos:",
            modo_auto_fundos_var,
            (
                ("misturar", "Misturar Gerados pelas Fotos + Pasta de Fundos"),
                ("gerados", "Somente Gerados pelas Cores das Fotos"),
                ("pasta", "Somente da Pasta de Fundos do Usuário"),
            ),
        )
        _criar_combo_acabamento(
            quadro_f1,
            "Estilo gerado pelas fotos:",
            estilo_fundo_gerado_var,
            (
                ("ambos", "Degradê Suave + Abstrato Suave"),
                ("degrade_e_abstrato", "Degradê Suave + Abstrato Suave"),
                ("degrade", "Somente Degradê Suave"),
                ("abstrato", "Somente Abstrato Suave"),
            ),
        )
        _criar_combo_acabamento(
            quadro_f1,
            "Análise de cores:",
            escopo_auto_fundos_var,
            (
                ("auto", "Automático (separa páginas quando o cenário muda)"),
                ("adaptativo", "Automático (separa páginas quando o cenário muda)"),
                ("lamina", "Um único fundo para a lâmina inteira"),
                ("pagina", "Separar fundo da Página Esquerda e Direita"),
            ),
        )

        # --- 2. Degradê Automático nas Fotos ---
        quadro_f2 = ttk.LabelFrame(
            aba_acabamento,
            text="2. Degradê Automático nas Fotos (Somente Destaques)",
            padding=10,
        )
        quadro_f2.pack(fill="x", pady=(0, 8))
        auto_degrade_var = tk.BooleanVar(value=bool(cfg.get("auto_degrade", True)))
        regra_deg_ini = str(cfg.get("regra_auto_degrade", "destaques") or "destaques")
        if regra_deg_ini not in ("destaques", "destaque_apenas", "meio_termo_apenas"):
            regra_deg_ini = "destaques"
        regra_auto_degrade_var = tk.StringVar(value=regra_deg_ini)
        intensidade_auto_degrade_var = tk.StringVar(value=str(cfg.get("intensidade_auto_degrade", "suave") or "suave"))

        ttk.Checkbutton(
            quadro_f2,
            text="Aplicar Degradê Automático apenas nas fotos de destaque (editável na aba Degradê)",
            variable=auto_degrade_var,
        ).pack(anchor="w", pady=(0, 6))

        _criar_combo_acabamento(
            quadro_f2,
            "Quando aplicar na foto:",
            regra_auto_degrade_var,
            (
                ("destaques", "Em fotos com Estrela (★) ou Sozinhas na página sangrada"),
                ("destaque_apenas", "Somente em fotos marcadas com Estrela (★)"),
                ("meio_termo_apenas", "Somente em foto sozinha na página sangrada (Meio-Termo)"),
            ),
        )
        _criar_combo_acabamento(
            quadro_f2,
            "Intensidade da transição:",
            intensidade_auto_degrade_var,
            (
                ("suave", "Suave (recomendado — protege pessoas e rostos)"),
                ("medio", "Médio"),
                ("forte", "Forte (transição mais longa)"),
            ),
        )

        # --- 3. Molduras Automáticas nas Fotos ---
        quadro_f3 = ttk.LabelFrame(
            aba_acabamento,
            text="3. Molduras Automáticas nas Fotos (Páginas Seguras)",
            padding=10,
        )
        quadro_f3.pack(fill="x", pady=(0, 6))
        auto_molduras_var = tk.BooleanVar(value=bool(cfg.get("auto_molduras", True)))
        regra_auto_molduras_var = tk.StringVar(value=str(cfg.get("regra_auto_molduras", "grades_e_margem") or "grades_e_margem"))
        cor_auto_molduras_var = tk.StringVar(value=str(cfg.get("cor_auto_molduras", "cores_do_evento") or "cores_do_evento"))
        estilo_auto_molduras_var = tk.StringVar(value=str(cfg.get("estilo_auto_molduras", "por_evento") or "por_evento"))
        espessura_auto_molduras_var = tk.DoubleVar(value=float(cfg.get("espessura_auto_molduras_mm", 0.5)))

        ttk.Checkbutton(
            quadro_f3,
            text="Aplicar Molduras Automáticas em fotos na área segura (nunca em fotos sangradas ou com degradê)",
            variable=auto_molduras_var,
        ).pack(anchor="w", pady=(0, 6))

        _criar_combo_acabamento(
            quadro_f3,
            "Onde aplicar moldura:",
            regra_auto_molduras_var,
            (
                ("grades_e_margem", "Todas as fotos na área segura (1 ou mais fotos)"),
                ("grades_com_margem", "Todas as fotos na área segura (1 ou mais fotos)"),
                ("apenas_grades", "Somente em grades (2 ou mais fotos na página)"),
            ),
        )
        _criar_combo_acabamento(
            quadro_f3,
            "Estilo da moldura:",
            estilo_auto_molduras_var,
            (("por_evento", "Por evento (dinâmico: destaque, secundárias e rodízio)"),) + tuple(ESTILOS_MOLDURA),
        )
        _criar_combo_acabamento(
            quadro_f3,
            "Cor da moldura:",
            cor_auto_molduras_var,
            (
                ("cores_do_evento", "Cores do evento (com contraste garantido)"),
                ("contraste_fundo", "Contraste automático com o fundo da página"),
                ("contraste_automatico", "Contraste automático com o fundo da página"),
                ("branca", "Branca clássica"),
                ("paleta", "Tom suave extraído da foto"),
            ),
        )

        linha_f3_esp = ttk.Frame(quadro_f3)
        linha_f3_esp.pack(fill="x", pady=2)
        ttk.Label(linha_f3_esp, text="Espessura da linha (mm):", width=26).pack(side="left")
        ttk.Spinbox(
            linha_f3_esp,
            from_=0.2,
            to=2.5,
            increment=0.1,
            textvariable=espessura_auto_molduras_var,
            width=8,
        ).pack(side="left")

        # Inicializa na primeira aba
        _trocar_aba_diag()

        # --- Resumo ao vivo e botões no Rodapé Fixo ---
        quadro_previa = tk.Frame(rodape_fixo, bg=tema.CABECALHO)
        quadro_previa.pack(fill="x", pady=(0, 8))
        lbl_previa_1 = ttk.Label(quadro_previa, text="", font=("Segoe UI", 9, "bold"), justify="left")
        lbl_previa_1.pack(anchor="w", fill="x")
        lbl_previa_2 = ttk.Label(quadro_previa, text="", style="Fraco.TLabel", justify="left")
        lbl_previa_2.pack(anchor="w", fill="x", pady=(2, 0))

        def _ajustar_wraps_diag(evento):
            larg = max(360, int(evento.width) - 36)
            lbl_previa_1.configure(wraplength=larg)
            lbl_previa_2.configure(wraplength=larg)

        rodape_fixo.bind("<Configure>", _ajustar_wraps_diag, add="+")

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

            try:
                lim_cen = max(0.15, min(0.45, float(limiar_cenario_var.get())))
            except (tk.TclError, ValueError):
                lim_cen = float(cfg.get("limiar_cenario", 0.28))

            try:
                esp_mold = max(0.1, min(3.0, float(espessura_auto_molduras_var.get())))
            except (tk.TclError, ValueError):
                esp_mold = 0.5

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
                "reconhecer_cenarios": bool(reconhecer_cenarios_var.get()),
                "limiar_cenario": lim_cen,
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
                "auto_fundos": bool(auto_fundos_var.get()),
                "modo_auto_fundos": str(modo_auto_fundos_var.get() or "misturar"),
                "estilo_fundo_gerado": str(estilo_fundo_gerado_var.get() or "ambos"),
                "escopo_auto_fundos": str(escopo_auto_fundos_var.get() or "auto"),
                "auto_degrade": bool(auto_degrade_var.get()),
                "regra_auto_degrade": str(regra_auto_degrade_var.get() or "destaques"),
                "intensidade_auto_degrade": str(intensidade_auto_degrade_var.get() or "suave"),
                "auto_molduras": bool(auto_molduras_var.get()),
                "regra_auto_molduras": str(regra_auto_molduras_var.get() or "grades_e_margem"),
                "cor_auto_molduras": str(cor_auto_molduras_var.get() or "cores_do_evento"),
                "estilo_auto_molduras": str(estilo_auto_molduras_var.get() or "por_evento"),
                "espessura_auto_molduras_mm": esp_mold,
            })
            return cfg_temp

        def _atualizar_previa(*_args):
            fotos_esc = _obter_fotos_escopo()
            cfg_t = _montar_cfg_atual()
            esc = escopo_var.get()
            prod_p = dict(self.produto)
            if esc in ("pasta", "nao_usadas"):
                prod_p["laminas_min"] = 0
                cfg_t["laminas_desejadas"] = diagramar.laminas_ideais(len(fotos_esc), prod_p, cfg_t)
            prev = diagramar.previa_diagramacao(fotos_esc, prod_p, cfg_t)
            lbl_previa_1.configure(
                text="Resumo: %d fotos (👤 %d ind. · 👥 %d grupo)  |  Grupos: %d  |  Pastas: %d  →  %d lâmina(s)"
                % (
                    prev["total_fotos"],
                    prev.get("fotos_individuais", 0),
                    prev.get("fotos_em_grupo", 0),
                    prev.get("grupos", 1),
                    prev["pastas"],
                    prev["laminas_estimadas"],
                )
            )
            detalhes = "%d a %d fotos por lâmina (pedido: %d a %d) · %d lâminas com fotos" % (
                prev.get("menor_lamina", 0),
                prev.get("maior_lamina", 0),
                prev.get("minimo_lamina", 0),
                prev.get("maximo_lamina", 0),
                prev["laminas_com_fotos"],
            )
            if prev["laminas_extras_vazias"] > 0:
                detalhes += " + %d vazia(s)" % prev["laminas_extras_vazias"]
            detalhes += "."
            acabamentos = []
            if auto_fundos_var.get():
                acabamentos.append("Fundo Auto")
            if auto_degrade_var.get():
                acabamentos.append("Degradê só em Destaques")
            if auto_molduras_var.get():
                acabamentos.append("Moldura Auto")
            if acabamentos:
                detalhes += " Acabamento: " + " · ".join(acabamentos) + "."
            for aviso in prev.get("avisos") or []:
                detalhes += "\n⚠ " + aviso
            lbl_previa_2.configure(text=detalhes)

        for v in (
            escopo_var,
            pasta_escolhida_var,
            separar_pastas_var,
            respeitar_grupos_var,
            reconhecer_cenarios_var,
            limiar_cenario_var,
            ordem_pessoas_var,
            modo_corte_var,
            corte_max_pct_var,
            transpasse_var,
            rostos_var,
            nao_cortar_pessoas_var,
            preencher_extras_var,
            repetidas_var,
            auto_fundos_var,
            auto_degrade_var,
            auto_molduras_var,
            regra_auto_degrade_var,
            *vars_num.values(),
        ):
            v.trace_add("write", _atualizar_previa)

        _atualizar_previa()

        botoes = ttk.Frame(rodape_fixo)
        botoes.pack(fill="x")

        def _calcular_laminas_ideais():
            fotos_esc = _obter_fotos_escopo()
            vars_num["laminas_desejadas"].set(diagramar.laminas_ideais(len(fotos_esc), self.produto, _montar_cfg_atual()))

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
                float(vars_num["margem_mm"].get())
                float(vars_num["espacamento_mm"].get())
            except (tk.TclError, ValueError):
                messagebox.showwarning("Diagramação", "Confira os valores numéricos informados.", parent=janela)
                return
            if max_pag < f_pag:
                messagebox.showwarning(
                    "Diagramação",
                    "O máximo de fotos por página deve ser igual ou maior que o mínimo.",
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
            largura=680,
            altura=580,
            min_largura=520,
            min_altura=420,
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
                "nome": "Evento 1",
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
            text="＋ Novo Evento",
            style="PrimarioCompacto.TButton",
            command=lambda: _criar_novo_grupo(),
        ).pack(side="left", padx=(0, 2))
        ttk.Button(
            barra_acoes,
            text="✎ Renomear",
            style="Compacto.TButton",
            command=lambda: _renomear_grupo_ativo(),
        ).pack(side="left", padx=1)

        # Seletor rápido de Tipo de Evento para o grupo ativo
        preset_evento_var = tk.StringVar(value="⚡ Tipo de Evento...")
        opcoes_preset_ev = (
            "⚡ Tipo de Evento...",
            "Colação 1",
            "Colação 2",
            "Colação 3",
            "Cerimônia do Jaleco",
            "Fotos Externas",
            "Baile de Gala",
            "Culto Ecumênico",
            "Ensaio Fotográfico",
            "Jantar / Recepção",
        )
        combo_preset_ev = ttk.Combobox(
            barra_acoes,
            textvariable=preset_evento_var,
            values=opcoes_preset_ev,
            state="readonly",
            width=18,
        )
        combo_preset_ev.pack(side="left", padx=(2, 2))

        def _aplicar_preset_evento(_ev=None):
            val_ev = preset_evento_var.get()
            if not val_ev or val_ev.startswith("⚡"):
                return
            idx_at = grupo_ativo_idx[0]
            if 0 <= idx_at < len(estado_grupos):
                estado_grupos[idx_at]["nome"] = val_ev
                preset_evento_var.set("⚡ Tipo de Evento...")
                _redesenhar_canvas()

        combo_preset_ev.bind("<<ComboboxSelected>>", _aplicar_preset_evento)

        ttk.Button(
            barra_acoes,
            text="▲ Subir Evento",
            style="Compacto.TButton",
            command=lambda: _mover_grupo(-1),
        ).pack(side="left", padx=1)
        ttk.Button(
            barra_acoes,
            text="▼ Descer Evento",
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
            text="⚡ Separar p/ Cenário",
            style="PrimarioCompacto.TButton",
            command=lambda: _separar_por_evento_automatico(),
        ).pack(side="left", padx=1)
        ttk.Button(
            barra_acoes,
            text="🎨 Ordenar p/ Cenário no Grupo",
            style="Compacto.TButton",
            command=lambda: _ordenar_por_cenario_nos_grupos(),
        ).pack(side="left", padx=1)
        ttk.Button(
            barra_acoes,
            text="🎨 Subdividir p/ Cenário",
            style="Compacto.TButton",
            command=lambda: _subdividir_grupo_por_cenario(),
        ).pack(side="left", padx=1)
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
            lim_cen_atual = float((self.projeto.get("config") or {}).get("limiar_cenario", 0.28))
            for idx_g, g in enumerate(estado_grupos):
                cor_g = CORES_GRUPOS[idx_g % len(CORES_GRUPOS)]
                ativo = (idx_g == grupo_ativo_idx[0])
                recolhido = bool(g.get("recolhido", False))
                fotos_g = g["fotos"]
                qtd_em_grupo = sum(1 for f in fotos_g if proj.eh_foto_em_grupo(f, self.info))
                qtd_indiv = len(fotos_g) - qtd_em_grupo
                # Calcula a qual cenário visual dentro do grupo cada foto pertence
                fotos_com_ass = []
                for f_it in fotos_g:
                    ch_it = normalizar_caminho(f_it["caminho"])
                    dados_it = self.info.get(ch_it) or {}
                    copia_it = dict(f_it)
                    if dados_it.get("cenario"):
                        copia_it["cenario"] = dados_it["cenario"]
                    fotos_com_ass.append(copia_it)
                _ord_cen, sub_cen = diagramar.agrupar_por_cenario_no_bloco(fotos_com_ass, limiar=lim_cen_atual)
                mapa_cen_foto = {}
                for idx_c, bloco_c in enumerate(sub_cen, start=1):
                    for fc in bloco_c:
                        mapa_cen_foto[normalizar_caminho(fc["caminho"])] = idx_c
                qtd_cen_grupo = max(1, len(sub_cen)) if fotos_g else 0

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
                    margem_x + 112,
                    y0 + 30,
                    fill=cor_g,
                    outline="",
                )
                canvas_g.create_text(
                    margem_x + 75,
                    y0 + 18,
                    text="%dº GRUPO" % (idx_g + 1),
                    fill="#ffffff",
                    font=tema.FONTE_FORTE,
                )

                # Botões diretos de Ordem [▲] e [▼] no cabeçalho deste grupo
                up_x0, up_x1 = margem_x + 116, margem_x + 140
                dn_x0, dn_x1 = margem_x + 143, margem_x + 167
                canvas_g.create_rectangle(
                    up_x0, y0 + 6, up_x1, y0 + 30,
                    fill=tema.PAINEL_3 if idx_g > 0 else tema.PAINEL,
                    outline=tema.BORDA_CLARA,
                    width=1,
                )
                canvas_g.create_text(
                    (up_x0 + up_x1) // 2, y0 + 18,
                    text="▲",
                    fill="#ffffff" if idx_g > 0 else tema.TEXTO_FRACO,
                    font=tema.FONTE_PEQUENA,
                )
                canvas_g.create_rectangle(
                    dn_x0, y0 + 6, dn_x1, y0 + 30,
                    fill=tema.PAINEL_3 if idx_g < (len(estado_grupos) - 1) else tema.PAINEL,
                    outline=tema.BORDA_CLARA,
                    width=1,
                )
                canvas_g.create_text(
                    (dn_x0 + dn_x1) // 2, y0 + 18,
                    text="▼",
                    fill="#ffffff" if idx_g < (len(estado_grupos) - 1) else tema.TEXTO_FRACO,
                    font=tema.FONTE_PEQUENA,
                )

                # Botão clicável [🎓 Opções de Evento ▾] exclusivo para este grupo
                ev_x0, ev_x1 = margem_x + 172, margem_x + 318
                canvas_g.create_rectangle(
                    ev_x0,
                    y0 + 5,
                    ev_x1,
                    y0 + 31,
                    fill="#2b2416",
                    outline="#f5a623",
                    width=1,
                )
                canvas_g.create_text(
                    (ev_x0 + ev_x1) // 2,
                    y0 + 18,
                    text="🎓 Opções de Evento ▾",
                    fill="#fde047",
                    font=tema.FONTE_FORTE,
                )

                resumo_g = "%s  —  %d foto(s) (👤 %d ind. · 👥 %d grupo · 🎨 %d cenário(s))" % (
                    g["nome"],
                    len(fotos_g),
                    qtd_indiv,
                    qtd_em_grupo,
                    qtd_cen_grupo,
                )
                sug_g = g.get("sugestao_evento")
                if sug_g and sug_g.get("nome") and sug_g["nome"] != g["nome"]:
                    resumo_g += "  ·  💡 IA sugere: %s (%d%%) — aceite em 'Opções de Evento'" % (
                        sug_g["nome"], int(round(float(sug_g.get("confianca") or 0) * 100)))
                if recolhido:
                    resumo_g += "  ·  [Aba fechada — solte fotos aqui ou clique p/ abrir]"

                canvas_g.create_text(
                    margem_x + 328,
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
                    "up_x0": up_x0,
                    "up_x1": up_x1,
                    "dn_x0": dn_x0,
                    "dn_x1": dn_x1,
                    "ev_x0": ev_x0,
                    "ev_x1": ev_x1,
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

                        # Badge do Cenário Visual identificado dentro deste grupo (🎨 C1, C2...)
                        num_cen_f = mapa_cen_foto.get(ch_f, 1)
                        etq_cen = "🎨 C%d" % num_cen_f
                        canvas_g.create_rectangle(
                            fx0 + 3,
                            fy1 - 40,
                            fx0 + 40,
                            fy1 - 26,
                            fill="#065f46" if (num_cen_f % 2 == 1) else "#92400e",
                            outline="",
                        )
                        canvas_g.create_text(
                            fx0 + 21,
                            fy1 - 33,
                            text=etq_cen,
                            fill="#ecfdf5" if (num_cen_f % 2 == 1) else "#fef3c7",
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

        OPCOES_EVENTO_MENU = (
            ("🎓 Colação 1 (Estúdio / Beca)", "Colação 1"),
            ("🎓 Colação 2 (Solenidade / Palco)", "Colação 2"),
            ("🎓 Colação 3 (Cerimônia / Canudo)", "Colação 3"),
            ("🥼 Cerimônia do Jaleco", "Cerimônia do Jaleco"),
            ("🌿 Fotos Externas / Parque", "Fotos Externas"),
            ("✨ Baile de Gala / Festa", "Baile de Gala"),
            ("🕊️ Culto Ecumênico / Missa", "Culto Ecumênico"),
            ("📸 Ensaio Fotográfico", "Ensaio Fotográfico"),
            ("🥂 Jantar / Recepção", "Jantar / Recepção"),
            ("👨‍👩‍👧 Homenagens / Família", "Homenagens / Família"),
        )

        def _definir_nome_evento_grupo(idx_alvo, novo_nome):
            if 0 <= idx_alvo < len(estado_grupos):
                estado_grupos[idx_alvo]["nome"] = str(novo_nome).strip()
                grupo_ativo_idx[0] = idx_alvo
                _redesenhar_canvas()

        def _mover_grupo_indice(idx_origem, idx_destino):
            if not (0 <= idx_origem < len(estado_grupos)) or not (0 <= idx_destino < len(estado_grupos)):
                return
            if idx_origem == idx_destino:
                return
            item = estado_grupos.pop(idx_origem)
            estado_grupos.insert(idx_destino, item)
            grupo_ativo_idx[0] = idx_destino
            _redesenhar_canvas()

        def _abrir_menu_opcoes_evento_grupo(idx_alvo, x_root, y_root):
            if not (0 <= idx_alvo < len(estado_grupos)):
                return
            grupo_ativo_idx[0] = idx_alvo
            atual_nome = estado_grupos[idx_alvo]["nome"]
            menu_ev = tk.Menu(janela, tearoff=0)
            menu_ev.add_command(
                label="Opções de Evento para o %dº Grupo (%s):" % (idx_alvo + 1, atual_nome),
                state="disabled",
            )
            menu_ev.add_separator()
            sug_ev = estado_grupos[idx_alvo].get("sugestao_evento")
            if sug_ev and sug_ev.get("nome") and sug_ev["nome"] != atual_nome:
                menu_ev.add_command(
                    label="💡 Aceitar sugestão da IA: %s (cena %s, %d%%)" % (
                        sug_ev["nome"], sug_ev.get("cena", ""), int(round(float(sug_ev.get("confianca") or 0) * 100))),
                    command=lambda v=sug_ev["nome"]: _definir_nome_evento_grupo(idx_alvo, v),
                )
                menu_ev.add_separator()
            for rotulo_op, valor_op in OPCOES_EVENTO_MENU:
                prefixo = "✓ " if atual_nome.strip().lower() == valor_op.lower() else "   "
                menu_ev.add_command(
                    label=prefixo + rotulo_op,
                    command=lambda v=valor_op: _definir_nome_evento_grupo(idx_alvo, v),
                )
            menu_ev.add_separator()
            menu_ev.add_command(
                label="📁 Evento %d (Neutro)" % (idx_alvo + 1),
                command=lambda: _definir_nome_evento_grupo(idx_alvo, "Evento %d" % (idx_alvo + 1)),
            )
            menu_ev.add_command(
                label="✎ Digitar nome personalizado...",
                command=lambda: _renomear_grupo_ativo(),
            )
            if len(estado_grupos) > 1:
                menu_ev.add_separator()
                for pos_i in range(len(estado_grupos)):
                    if pos_i != idx_alvo:
                        menu_ev.add_command(
                            label="↕ Mover este grupo para %dº lugar" % (pos_i + 1),
                            command=lambda dest=pos_i: _mover_grupo_indice(idx_alvo, dest),
                        )
            menu_ev.tk_popup(x_root, y_root)

        def _ao_clicar_canvas(evento):
            canvas_g.focus_set()
            x_c = canvas_g.canvasx(evento.x)
            y_c = canvas_g.canvasy(evento.y)
            rf = _achar_foto_em(x_c, y_c)
            rg = _achar_regiao_grupo_em(y_c)
            rg_idx = rg["idx_g"] if rg is not None else _achar_grupo_em(y_c)
            grupo_ativo_idx[0] = rg_idx

            # Verifica se clicou no cabeçalho do grupo
            if rf is None and rg is not None and rg["cab_y0"] <= y_c <= rg["cab_y1"]:
                # Clicou no botão [▲] Subir Grupo
                if rg.get("up_x0", -1) <= x_c <= rg.get("up_x1", -1):
                    _mover_grupo_indice(rg_idx, rg_idx - 1)
                    return
                # Clicou no botão [▼] Descer Grupo
                if rg.get("dn_x0", -1) <= x_c <= rg.get("dn_x1", -1):
                    _mover_grupo_indice(rg_idx, rg_idx + 1)
                    return
                # Clicou no botão [🎓 Opções de Evento ▾] deste grupo
                if rg.get("ev_x0", -1) <= x_c <= rg.get("ev_x1", -1):
                    _abrir_menu_opcoes_evento_grupo(rg_idx, evento.x_root, evento.y_root)
                    return
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
            rg = _achar_regiao_grupo_em(y_c)
            if rf is None and rg is not None and rg["cab_y0"] <= y_c <= rg["cab_y1"]:
                _abrir_menu_opcoes_evento_grupo(rg["idx_g"], evento.x_root, evento.y_root)
                return
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
                definir_cursor(canvas_g, "fleur")

        def _ao_soltar_canvas(evento):
            definir_cursor(canvas_g, "")
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

        def _ordenar_por_cenario_nos_grupos():
            lim_c = float((self.projeto.get("config") or {}).get("limiar_cenario", 0.28))
            total_cen = 0
            for g in estado_grupos:
                fotos_enriq = []
                for f_it in g["fotos"]:
                    ch_it = normalizar_caminho(f_it["caminho"])
                    dados_it = self.info.get(ch_it) or {}
                    copia_it = dict(f_it)
                    if dados_it.get("cenario"):
                        copia_it["cenario"] = dados_it["cenario"]
                    fotos_enriq.append(copia_it)
                ord_c, sub_c = diagramar.agrupar_por_cenario_no_bloco(fotos_enriq, limiar=lim_c)
                g["fotos"] = ord_c
                total_cen += len(sub_c)
            _redesenhar_canvas()
            self.registrar(
                "Fotos ordenadas por semelhança de cenário visual dentro de cada grupo (%d cenário(s) identificado(s))."
                % max(1, total_cen)
            )

        def _subdividir_grupo_por_cenario():
            idx = grupo_ativo_idx[0]
            if not (0 <= idx < len(estado_grupos)):
                return
            lim_c = float((self.projeto.get("config") or {}).get("limiar_cenario", 0.28))
            alvo_g = estado_grupos[idx]
            fotos_enriq = []
            for f_it in alvo_g["fotos"]:
                ch_it = normalizar_caminho(f_it["caminho"])
                dados_it = self.info.get(ch_it) or {}
                copia_it = dict(f_it)
                if dados_it.get("cenario"):
                    copia_it["cenario"] = dados_it["cenario"]
                fotos_enriq.append(copia_it)
            _ord_c, sub_c = diagramar.agrupar_por_cenario_no_bloco(fotos_enriq, limiar=lim_c)
            if len(sub_c) <= 1:
                messagebox.showinfo(
                    "Cenários no Grupo",
                    "Todas as fotos deste grupo já pertencem ao mesmo cenário visual (1 cenário detectado).",
                    parent=janela,
                )
                return
            nome_base = alvo_g["nome"]
            novos_sub = [
                {
                    "nome": "%s · Cenário %d" % (nome_base, ic + 1),
                    "fotos": bloco_c,
                    "recolhido": False,
                }
                for ic, bloco_c in enumerate(sub_c)
            ]
            estado_grupos[idx:idx + 1] = novos_sub
            _redesenhar_canvas()

        def _separar_por_evento_automatico():
            todas_ev = []
            usar_cena = ia_modelos.config().get("reconhecer_cenas")
            for g_ev in estado_grupos:
                for f_ev in g_ev["fotos"]:
                    ch_ev = normalizar_caminho(f_ev["caminho"])
                    dados_ev = self.info.get(ch_ev) or {}
                    copia_ev = dict(f_ev)
                    if dados_ev.get("cenario"):
                        copia_ev["cenario"] = dados_ev["cenario"]
                    if usar_cena and dados_ev.get("cena"):
                        copia_ev["cena"] = dados_ev["cena"]
                    todas_ev.append(copia_ev)
            if not todas_ev:
                return
            novos_ev = diagramar.separar_fotos_por_evento_automatico(todas_ev)
            if novos_ev:
                estado_grupos.clear()
                estado_grupos.extend(novos_ev)
                grupo_ativo_idx[0] = 0
                _redesenhar_canvas()
                self.registrar(
                    "Fotos separadas automaticamente em %d evento(s)/capítulo(s): %s"
                    % (len(novos_ev), ", ".join(g["nome"] for g in novos_ev))
                )
                sugeridos = [g for g in novos_ev if g.get("sugestao_evento")]
                if sugeridos:
                    self.registrar("💡 A IA sugeriu nome para %d grupo(s); aceite em '🎓 Opções de Evento' de cada grupo." % len(sugeridos))

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
        cfg["pasta_fundos"] = self._pasta_fundos()
        cfg["_arquivo_fundos"] = str(ARQUIVO_FUNDOS)
        cfg["_lista_fundos_pasta"] = list(getattr(self, "_fundos_lista", None) or [])
        if escopo in ("pasta", "nao_usadas"):
            produto["laminas_min"] = 0
            cfg["laminas_desejadas"] = diagramar.laminas_ideais(len(fotos), produto, cfg)
        avisos = []

        def trabalho(t):
            removidas = []
            fotos_usadas = fotos
            if excluir_repetidas:
                fotos_usadas, removidas = self._sem_repetidas(fotos, t.parar, t.progresso)
                if fotos_usadas is None:
                    return None
            resultado = diagramar.diagramar_album(fotos_usadas, produto, cfg, ao_progresso=t.progresso, avisos=avisos)
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
            for aviso in avisos:
                self.registrar("Diagramação: " + aviso)
            if avisos:
                messagebox.showinfo("Diagramar", texto + "\n\n" + "\n".join("• " + a for a in avisos), parent=self.root)
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
        mapa_registros = proj.mapa_registros(self.projeto) if caminhos else {}
        for caminho in caminhos:
            chave = normalizar_caminho(caminho)
            dados = self.info.get(chave)
            if dados:
                registro = mapa_registros.get(chave) or {}
                ed_base = dict(registro.get("edicao_padrao") or {})
                ed_base.update(edicoes.get(chave) or {})
                prop = self._proporcao_efetiva(caminho, ed_base)
                saida.append({
                    "caminho": caminho,
                    "proporcao": prop,
                    "data": dados.get("data"),
                    "destaque": bool(registro.get("destaque")),
                    "rosto": dados.get("rosto"),
                    "assinatura_cenario": dados.get("assinatura_cenario"),
                    "pasta": proj.pasta_da_foto(caminho),
                    "nome_pasta": proj.nome_pasta_da_foto(caminho),
                    "edicao": ed_base,
                })
        return saida

    def _lamina(self, indice=None):
        indice = self.atual if indice is None else indice
        if isinstance(indice, int) and 0 <= indice < len(self.projeto["laminas"]):
            return self.projeto["laminas"][indice]
        return None

    def _moldura_sombra_manuais(self, espacos):
        """A primeira moldura e a primeira sombra escolhidas à mão na lâmina (as do automático não contam)."""
        moldura_ref = sombra_ref = None
        for esp in espacos or []:
            ed = esp.get("edicao") or {}
            m_e = ed.get("moldura")
            if (moldura_ref is None and isinstance(m_e, dict) and m_e.get("estilo") and m_e.get("estilo") != "nenhuma"
                    and not diagramar.eh_automatico(ed, "moldura")):
                moldura_ref = dict(m_e)
            s_e = ed.get("sombra")
            if (sombra_ref is None and isinstance(s_e, dict) and s_e.get("ativa", True)
                    and not diagramar.eh_automatico(ed, "sombra")):
                sombra_ref = dict(s_e)
        return moldura_ref, sombra_ref

    def _cfg_acabamento(self, extra=None):
        cfg = dict(self._cfg())
        cfg["pasta_fundos"] = self._pasta_fundos()
        cfg["_arquivo_fundos"] = str(ARQUIVO_FUNDOS)
        cfg["_lista_fundos_pasta"] = list(getattr(self, "_fundos_lista", None) or [])
        cfg.update(extra or {})
        return cfg

    def _acabamento(self, laminas, forcar=(), cfg_extra=None):
        """
        Refaz fundo, degradê e molduras das lâminas depois de uma edição (diagramar.acabamento_lamina),
        com o produto real e os dados das fotos (grupo, pasta, estrela, rosto) para os motores decidirem juntos.
        """
        alvo = []
        for lamina in laminas or []:
            if isinstance(lamina, dict) and all(lamina is not l for l in alvo):
                alvo.append(lamina)
        if not alvo:
            return
        cfg = self._cfg_acabamento(cfg_extra)
        meta = {normalizar_caminho(f["caminho"]): f for f in proj.fotos_para_diagramar(self.projeto, self.info)}
        todas = self.projeto.get("laminas", [])
        for lamina in alvo:
            indice = next((i for i, l in enumerate(todas) if l is lamina), 0)
            fotos = []
            for esp in lamina.get("espacos", []):
                if esp.get("foto"):
                    dados = meta.get(normalizar_caminho(esp["foto"]))
                    fotos.append(dict(dados, caminho=esp["foto"]) if dados else {"caminho": esp["foto"]})
            diagramar.acabamento_lamina(lamina, fotos, self.produto, cfg, indice_lamina=indice, forcar=forcar)

    def _sincronizar_molduras_sombra_lamina(self, lamina, moldura_ref=None, sombra_ref=None):
        """
        Depois de mudar o layout: a moldura/sombra escolhida à mão vale para as fotos na Safe Zone,
        fotos sangradas ou com degradê ficam sem moldura, e o resto é o acabamento automático.
        """
        if not lamina or not lamina.get("espacos"):
            return
        achada_m, achada_s = self._moldura_sombra_manuais(lamina.get("espacos"))
        moldura_ref = moldura_ref or achada_m
        sombra_ref = sombra_ref or achada_s

        for esp in lamina.get("espacos", []):
            if not esp.get("foto"):
                continue
            ed = esp.setdefault("edicao", {})
            ex = float(esp.get("x", 0.0))
            ey = float(esp.get("y", 0.0))
            ew = float(esp.get("w", 0.0))
            eh = float(esp.get("h", 0.0))
            sangrada_borda = (
                ex <= 0.005
                or ey <= 0.005
                or (ex + ew) >= 0.995
                or (ey + eh) >= 0.995
            )
            tem_deg = bool((ed.get("degrade") or {}).get("ativo"))
            if sangrada_borda or tem_deg:
                for chave in ("moldura", "sombra", "_auto_moldura", "_auto_sombra"):
                    ed.pop(chave, None)
            else:
                if moldura_ref and (not ed.get("moldura") or diagramar.eh_automatico(ed, "moldura")):
                    ed["moldura"] = dict(moldura_ref)
                    ed.pop("_auto_moldura", None)
                if sombra_ref and (not ed.get("sombra") or diagramar.eh_automatico(ed, "sombra")):
                    ed["sombra"] = dict(sombra_ref)
                    ed.pop("_auto_sombra", None)

        self._acabamento([lamina])

    def _aplicar_opcao(self, lamina, opcao):
        """Troca o layout, mantendo giro e ajustes de cada foto (o corte é refeito) e refazendo o acabamento da lâmina."""
        antigas = {}
        moldura_ref, sombra_ref = self._moldura_sombra_manuais(lamina.get("espacos"))
        for espaco in lamina.get("espacos", []):
            if espaco.get("foto"):
                ed = dict(espaco.get("edicao") or {})
                ed.pop("corte", None)
                antigas[normalizar_caminho(espaco["foto"])] = ed
        novos_espacos = []
        mapa_registros = proj.mapa_registros(self.projeto)
        for e in opcao["espacos"]:
            if e.get("foto"):
                ch_f = normalizar_caminho(e["foto"])
                reg_f = mapa_registros.get(ch_f) or {}
                ed_f = dict(reg_f.get("edicao_padrao") or {})
                ed_f.update(antigas.get(ch_f, {}))
                ed_f.pop("corte", None)
                novos_espacos.append(dict(e, edicao=ed_f))
            else:
                novos_espacos.append(dict(e, edicao={}))
        lamina["espacos"] = novos_espacos
        lamina["sobreposicoes"] = list(opcao.get("sobreposicoes") or [])
        lamina["modelo"] = opcao.get("modelo")
        if opcao.get("tipo_layout"):
            lamina["tipo_layout"] = opcao["tipo_layout"]
        else:
            lamina.pop("tipo_layout", None)
        ch_l, nm_l, _dist = proj.pasta_da_lamina(self.projeto, lamina)
        if ch_l:
            lamina["pasta"] = ch_l
            lamina["nome_pasta"] = nm_l
        self._sincronizar_molduras_sombra_lamina(lamina, moldura_ref, sombra_ref)

    def relayout(self, indice, fotos=None, marcar=True):
        lamina = self._lamina(indice)
        if lamina is None:
            return
        if fotos is None:
            fotos = [e["foto"] for e in lamina["espacos"] if e.get("foto")]
        if marcar:
            self.marcar_historico()
        opcoes = diagramar.opcoes_lamina(self._fotos_info(fotos, lamina), self.produto, self._cfg(), maximo=12)
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
        produto, cfg = self.produto, self._cfg()

        def trabalho(t):
            saida = []
            for n, fotos in enumerate(infos, start=1):
                if t.parar():
                    return None
                saida.append(diagramar.opcoes_lamina(fotos, produto, cfg, maximo=1)[0])
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
                    self._acabamento([lamina])
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
        mapa_registros = proj.mapa_registros(self.projeto)
        if not misturadas:
            # Verifica se há mais de uma pasta carregada e oferece rediagramar mantendo cada pasta em seu bloco
            pastas = proj.listar_pastas_projeto(self.projeto)
            for lam in laminas_atuais:
                ch_l, nm_l, _dist = proj.pasta_da_lamina(self.projeto, lam, mapa_registros)
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
                reg = mapa_registros.get(normalizar_caminho(cam))
                ch_p = normalizar_caminho(proj.pasta_da_foto(reg if reg else cam))
                nm_p = proj.nome_pasta_da_foto(reg if reg else cam)
                por_pasta.setdefault(ch_p, (nm_p, []))[1].append(cam)
            for ch_p, (nm_p, cams_p) in por_pasta.items():
                infos_p = self._fotos_info(cams_p, lam)
                if not infos_p:
                    continue
                melhor = diagramar.opcoes_lamina(infos_p, self.produto, cfg, maximo=1)[0]
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
        reg_foto = proj.registro_foto(self.projeto, caminho) or {}
        ed_padrao = dict(reg_foto.get("edicao_padrao") or {})
        ed_padrao.pop("corte", None)
        espaco["edicao"] = ed_padrao
        ch_l, nm_l, _dist = proj.pasta_da_lamina(self.projeto, lamina)
        if ch_l:
            lamina["pasta"] = ch_l
            lamina["nome_pasta"] = nm_l
        if not self.editar_layout.get():
            diagramar.ajustar_espacos_lamina(lamina, self._mapa_proporcoes(lamina), self.produto, self._cfg())
        self._acabamento([lamina])
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
        self._acabamento([la, lb])
        self.atual, self.espaco_sel = lj, b
        self.atualizar_tudo()
        if antes is not None:
            self._animar_transicao_layout(antes, lb.get("espacos", []))

    def transferir_grupo_entre_laminas(self, li_orig, indices_orig, li_dest, modo="jogar", indices_dest=None):
        """
        Joga (move) ou troca de lugar um grupo de fotos da lâmina `li_orig` para a lâmina `li_dest`
        (acionado pelo botão do Scroll do mouse ou arraste de múltiplos espaços), reajustando o layout
        de ambas as lâminas com animação suave.
        """
        la = self._lamina(li_orig)
        lb = self._lamina(li_dest)
        if la is None or lb is None:
            return
        validos_orig = sorted({i for i in (indices_orig or []) if 0 <= i < len(la.get("espacos", []))})
        if not validos_orig:
            return
        if li_orig == li_dest:
            if indices_dest and len(indices_dest) == 1 and len(validos_orig) == 1 and validos_orig[0] != indices_dest[0]:
                self.trocar_espacos(li_orig, validos_orig[0], li_dest, indices_dest[0])
            self._grupo_scroll_troca = None
            return

        antes_dest = [dict(e) for e in lb.get("espacos", [])]
        self.marcar_historico()

        if modo == "trocar":
            if indices_dest:
                validos_dest = sorted({i for i in indices_dest if 0 <= i < len(lb.get("espacos", []))})
            elif self.atual == li_dest and self._indices_espacos_selecionados():
                validos_dest = sorted(self._indices_espacos_selecionados())
            else:
                validos_dest = list(range(len(lb.get("espacos", []))))

            if len(validos_orig) == len(validos_dest) and len(validos_orig) > 0:
                for i_o, i_d in zip(validos_orig, validos_dest):
                    ea, eb = la["espacos"][i_o], lb["espacos"][i_d]
                    ea["foto"], eb["foto"] = eb.get("foto"), ea.get("foto")
                    eda, edb = dict(ea.get("edicao") or {}), dict(eb.get("edicao") or {})
                    eda.pop("corte", None)
                    edb.pop("corte", None)
                    ea["edicao"], eb["edicao"] = edb, eda
                diagramar.ajustar_espacos_lamina(la, self._mapa_proporcoes(la), self.produto, self._cfg())
                diagramar.ajustar_espacos_lamina(lb, self._mapa_proporcoes(lb), self.produto, self._cfg())
                self._acabamento([la, lb])
                self._grupo_scroll_troca = None
                self.atual = li_dest
                self._definir_selecao_espacos(set(validos_dest), validos_dest[0])
                self.atualizar_tudo()
                self._animar_transicao_layout(antes_dest, lb.get("espacos", []))
                self.registrar("🖱 Grupo de %d foto(s) trocado de lugar entre a Lâmina %d e a Lâmina %d." % (
                    len(validos_orig), li_orig + 1, li_dest + 1
                ))
                return

            fotos_orig_mov = [la["espacos"][i].get("foto") for i in validos_orig if la["espacos"][i].get("foto")]
            fotos_dest_mov = [lb["espacos"][i].get("foto") for i in validos_dest if lb["espacos"][i].get("foto")]
            set_o, set_d = set(validos_orig), set(validos_dest)
            fotos_orig_ficam = [e.get("foto") for idx, e in enumerate(la["espacos"]) if idx not in set_o and e.get("foto")]
            fotos_dest_ficam = [e.get("foto") for idx, e in enumerate(lb["espacos"]) if idx not in set_d and e.get("foto")]

            self.relayout(li_orig, fotos_orig_ficam + fotos_dest_mov, marcar=False)
            self.relayout(li_dest, fotos_dest_ficam + fotos_orig_mov, marcar=False)
            self._grupo_scroll_troca = None
            self.atual = li_dest
            novos_sel = {idx for idx, e in enumerate(lb.get("espacos", [])) if e.get("foto") in set(fotos_orig_mov)}
            self._definir_selecao_espacos(novos_sel, min(novos_sel) if novos_sel else None)
            self.atualizar_tudo()
            self._animar_transicao_layout(antes_dest, lb.get("espacos", []))
            self.registrar("🖱 Grupo de %d foto(s) da Lâmina %d trocado com %d foto(s) da Lâmina %d." % (
                len(fotos_orig_mov), li_orig + 1, len(fotos_dest_mov), li_dest + 1
            ))
            return

        # Modo "jogar": move todas as fotos selecionadas da lâmina origem para a lâmina destino
        fotos_movidas = [la["espacos"][i].get("foto") for i in validos_orig if la["espacos"][i].get("foto")]
        if not fotos_movidas:
            self._grupo_scroll_troca = None
            return
        set_o = set(validos_orig)
        fotos_orig_restantes = [e.get("foto") for idx, e in enumerate(la["espacos"]) if idx not in set_o and e.get("foto")]
        fotos_dest_atuais = [e.get("foto") for e in lb["espacos"] if e.get("foto")]

        if fotos_orig_restantes:
            self.relayout(li_orig, fotos_orig_restantes, marcar=False)
        else:
            la["espacos"] = []
            la["modelo"] = None
        self.relayout(li_dest, fotos_dest_atuais + fotos_movidas, marcar=False)
        self._grupo_scroll_troca = None
        self.atual = li_dest
        novos_sel = {idx for idx, e in enumerate(lb.get("espacos", [])) if e.get("foto") in set(fotos_movidas)}
        self._definir_selecao_espacos(novos_sel, min(novos_sel) if novos_sel else None)
        self.atualizar_tudo()
        self._animar_transicao_layout(antes_dest, lb.get("espacos", []))
        self.registrar("🖱 Grupo de %d foto(s) jogado da Lâmina %d para a Lâmina %d!" % (
            len(fotos_movidas), li_orig + 1, li_dest + 1
        ))

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
        self._acabamento([lamina])
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
        self._acabamento([lamina])
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
            lamina.pop("tipo_layout", None)
            self._sincronizar_molduras_sombra_lamina(lamina)
            self.espaco_sel = None
            self.atualizar_lamina_atual()
            self._animar_transicao_layout(antes, lamina.get("espacos", []))
            self.desenhar_modelos()

    def aplicar_modelo_selecionado(self):
        if "modelos" in self._paineis_pendentes:
            self._verificar_paineis_pendentes()
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
            lamina.pop("tipo_layout", None)
            self._sincronizar_molduras_sombra_lamina(lamina)
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
        lamina.pop("tipo_layout", None)
        self._sincronizar_molduras_sombra_lamina(lamina)
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
        self._atualizar_barra_opcoes()

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
        self._cache_laminas[id_lam] = [ass, (dw, dh), img, tk_img]
        self._cache_laminas.move_to_end(id_lam)
        self._podar_cache_laminas()
        return tk_img

    def _podar_cache_laminas(self):
        cache = self._cache_laminas
        while len(cache) > LIMITE_CACHE_LAMINAS:
            cache.popitem(last=False)
        while len(cache) > 1 and sum(e[2].width * e[2].height for e in cache.values()) > PIXELS_CACHE_LAMINAS:
            cache.popitem(last=False)
        for item_cache in list(cache.values())[:-3]:
            item_cache[3] = None

    def _imagem_na_tela(self, fonte, dw, dh, ox, oy):
        """PhotoImage só da parte visível da lâmina: no zoom não escala a lâmina inteira."""
        cw = max(1, self.canvas.winfo_width())
        ch = max(1, self.canvas.winfo_height())
        vx0, vy0 = max(0, int(math.floor(-ox))), max(0, int(math.floor(-oy)))
        vx1, vy1 = min(dw, int(math.ceil(cw - ox))), min(dh, int(math.ceil(ch - oy)))
        if vx1 <= vx0 or vy1 <= vy0:
            return None, ox, oy
        sw, sh = fonte.size
        if (vx0, vy0, vx1, vy1) == (0, 0, dw, dh):
            img = fonte if (sw, sh) == (dw, dh) else fonte.resize((dw, dh), Image.BILINEAR)
        else:
            fx, fy = sw / float(dw), sh / float(dh)
            img = fonte.resize((vx1 - vx0, vy1 - vy0), Image.BILINEAR, box=(vx0 * fx, vy0 * fy, vx1 * fx, vy1 * fy))
        return ImageTk.PhotoImage(img), ox + vx0, oy + vy0

    def _lamina_inteira_visivel(self, dw, dh, ox, oy):
        return ox >= 0 and oy >= 0 and ox + dw <= self.canvas.winfo_width() and oy + dh <= self.canvas.winfo_height()

    def _miniatura_timeline_de(self, item):
        if not (isinstance(item, int) and 0 <= item < len(self.projeto["laminas"])):
            return None
        pronta = self._tl_pil.get(self._chave_timeline(item))
        return pronta[1] if pronta else None

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
        pos_img = (ox, oy)
        precisa_worker = pedir_render
        fonte = None
        inteira = self._lamina_inteira_visivel(dw, dh, ox, oy)

        if entrada_cache is not None:
            ass_cache, tam_cache, img_cache, tk_cache = entrada_cache
            self._cache_laminas.move_to_end(id_lam)
            fonte = img_cache
            self._imagem_lamina = (self.atual, img_cache)
            if tam_cache == (dw, dh):
                if ass_cache == ass_atual:
                    precisa_worker = False
                if inteira and img_cache.size == (dw, dh):
                    if tk_cache is None:
                        tk_cache = entrada_cache[3] = ImageTk.PhotoImage(img_cache)
                    tk_para_exibir = tk_cache
                    self._tk_lamina = (tk_cache, (self.atual, dw, dh))
        elif self._tk_lamina is not None and self._tk_lamina[1] == (self.atual, dw, dh) and inteira:
            tk_para_exibir = self._tk_lamina[0]
        if tk_para_exibir is None and fonte is None and self._imagem_lamina is not None and self._imagem_lamina[0] == self.atual:
            fonte = self._imagem_lamina[1]
        esboco = False
        if tk_para_exibir is None and fonte is None:
            fonte = self._miniatura_timeline_de(self.atual)
            esboco = fonte is None
        if tk_para_exibir is None and fonte is not None:
            self._tk_lamina_zoom, x_img, y_img = self._imagem_na_tela(fonte, dw, dh, ox, oy)
            tk_para_exibir, pos_img = self._tk_lamina_zoom, (x_img, y_img)

        self.canvas.delete("all")
        for dx, dy, cor in ((10, 12, "#1a1a1a"), (7, 9, "#222222"), (4, 5, "#2a2a2a")):
            self.canvas.create_rectangle(ox + dx, oy + dy, ox + dw + dx, oy + dh + dy, fill=cor, outline="")
        if tk_para_exibir is not None:
            self.canvas.create_image(pos_img[0], pos_img[1], image=tk_para_exibir, anchor="nw", tags="img")
        else:
            fundo = (lamina or {}).get("fundo", "#ffffff")
            self.canvas.create_rectangle(ox, oy, ox + dw, oy + dh, fill=cor_hex(fundo), outline="", tags="img")
            if esboco and lamina:
                for e in lamina.get("espacos", []):
                    if e.get("foto"):
                        x0, y0 = ox + e["x"] * dw, oy + e["y"] * dh
                        self.canvas.create_rectangle(x0, y0, x0 + e["w"] * dw, y0 + e["h"] * dh,
                                                     fill="#d4d4d8", outline="", tags="img")
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
            self._botoes_vazio.append((bx0, by0, bx1, by1, lambda: self.abrir_diagramacao(sugerir_ideal=True)))
            return

        w_card = min(720, cw - 48)
        h_card = min(400, ch - 36)
        x0, y0 = cx - w_card // 2, cy - h_card // 2
        x1, y1 = x0 + w_card, y0 + h_card

        self.canvas.create_rectangle(x0, y0, x1, y1, fill=tema.PAINEL, outline=tema.BORDA, width=1)
        self.canvas.create_rectangle(x0 + 14, y0 + 14, x1 - 14, y1 - 14, outline=tema.BORDA, dash=(5, 5), width=1)

        self.canvas.create_text(
            cx, y0 + 46,
            text="Bem-vindo ao Diagramador Inteligente (DI)",
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
        jan.title("Guia Rápido — Como usar o Diagramador Inteligente")
        jan.configure(bg=tema.PAINEL)
        jan.transient(self.root)

        tema.criar_cabecalho_janela(
            jan,
            "Bem-vindo ao Diagramador Inteligente (DI)",
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

            # Selo indicador quando múltiplos espaços estão selecionados ou no Grupo do Botão Scroll
            grupo_scroll = getattr(self, "_grupo_scroll_troca", None)
            em_scroll = bool(
                grupo_scroll
                and grupo_scroll.get("lamina") == self.atual
                and i in (grupo_scroll.get("espacos") or [])
            )
            if em_scroll and (x1 - x0) > 48 and (y1 - y0) > 28:
                lista_sc = list(grupo_scroll.get("espacos") or [])
                pos_sc = lista_sc.index(i) + 1 if i in lista_sc else 1
                txt_sel = "🖱 %d/%d" % (pos_sc, len(lista_sc))
                tw = max(44, len(txt_sel) * 6 + 10)
                bx1, by0 = x1 - 5, y0 + 5
                bx0, by1 = bx1 - tw, by0 + 17
                c.create_rectangle(
                    bx0, by0, bx1, by1,
                    fill="#3b2606", outline="#f5a623", width=1,
                    tags=("espaco", "anel_brilho")
                )
                c.create_text(
                    (bx0 + bx1) / 2, (by0 + by1) / 2,
                    text=txt_sel, fill="#fde68a", font=tema.FONTE_PEQUENA,
                    tags=("espaco", "anel_brilho")
                )
            elif multi and (x1 - x0) > 48 and (y1 - y0) > 28:
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
        c.delete("espaco&&!guia")
        if self._geo is None:
            c.delete("guia")
            return
        lamina = self._lamina()
        if lamina is None:
            c.delete("guia")
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

        # Linhas guias contínuas ciano no estilo Adobe Photoshop (Safe Zone e Sangria).
        # Ficam no canvas entre um desenho e outro: recriar retângulos do tamanho da lâmina obriga o Tk a repintar tudo.
        margem_cfg = float(self._cfg()["margem_mm"]) if self.guias.get() else None
        chave_guias = (self._geo, self.produto["nome"], margem_cfg)
        if not self.guias.get():
            c.delete("guia")
        elif chave_guias != getattr(self, "_chave_guias", None) or not c.find_withtag("guia"):
            c.delete("guia")
            etiquetas = ("espaco", "guia")
            lw, lh = self._mm()
            b = float(self.produto["sangria_mm"])
            # Linha de Sangria (ciano discreto contínuo)
            sx0, sy0 = ox + (b / lw) * dw, oy + (b / lh) * dh
            sx1, sy1 = ox + ((lw - b) / lw) * dw, oy + ((lh - b) / lh) * dh
            c.create_rectangle(sx0, sy0, sx1, sy1, outline="#00afdb", width=1, tags=etiquetas)
            # Linhas imaginárias da Safe Zone (ciano vivo contínuo estilo Photoshop #00e5ff)
            for pag in range(self.produto["paginas_por_lamina"]):
                ux, uy, uw, uh = area_util_pagina_mm(self.produto, pag, margem_cfg, respeitar_seguranca=True)
                gx0, gy0 = ox + (ux / lw) * dw, oy + (uy / lh) * dh
                gx1, gy1 = ox + ((ux + uw) / lw) * dw, oy + ((uy + uh) / lh) * dh
                c.create_rectangle(gx0, gy0, gx1, gy1, outline="#00e5ff", width=1, tags=etiquetas)
            if self.produto["paginas_por_lamina"] > 1:
                xm = ox + dw / 2.0
                c.create_line(xm, oy, xm, oy + dh, fill="#00cfe8", width=1, tags=etiquetas)
        self._chave_guias = chave_guias

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
        assinatura_paineis = (
            id(self.projeto), self.atual, self._assinatura(self.atual) if isinstance(self.atual, int) else None,
            self.espaco_sel, tuple(sorted(selecionados)), self.molde_sel, editando, hash(frozenset(self.fotos_sel)),
        )
        if assinatura_paineis != getattr(self, "_assinatura_paineis", None):
            self._assinatura_paineis = assinatura_paineis
            self._atualizar_paineis_selecao()

    def _atualizar_paineis_selecao(self):
        """Painéis que mostram a seleção: só quando a seleção ou os dados da lâmina mudam (não a cada quadro de zoom/pan)."""
        self._atualizar_paineis(("editar", "Moldura", "Sombra", "Degradê"))

    def _paineis_registrados(self):
        """Nome -> (atributo do widget que indica se o painel está na tela, funções que o atualizam)."""
        return {
            "editar": ("canvas_enquadramento", (self._atualizar_numeros, self._atualizar_enquadramento_inline)),
            "Moldura": ("rotulo_espessura_moldura", (self._atualizar_moldura_ui,)),
            "Sombra": ("amostra_sombra", (self._atualizar_sombra_ui,)),
            "Degradê": ("rotulo_alvo_degrade", (self._atualizar_degrade_ui,)),
            "sugestoes": ("canvas_sugestoes", (self.atualizar_sugestoes,)),
            "modelos": ("canvas_modelos", (self.desenhar_modelos,)),
            "gerados": ("galeria_fundos_gerados", (self._atualizar_galeria_gerados,)),
            "fundos_pasta": ("galeria_fundos", (self._agendar_fundos_pasta,)),
        }

    def _widget_do_painel(self, atributo):
        widget = getattr(self, atributo, None)
        return getattr(widget, "canvas", widget)

    @staticmethod
    def _na_tela(widget):
        """winfo_ismapped continua 1 dentro de um painel escondido com pack_forget; viewable olha os pais também."""
        try:
            return bool(widget is not None and widget.winfo_exists() and widget.winfo_viewable())
        except tk.TclError:
            return False

    def _painel_na_tela(self, nome):
        visivel = self._na_tela(self._widget_do_painel(self._paineis_registrados()[nome][0]))
        if not visivel:
            self._paineis_pendentes.add(nome)
        return visivel

    def _atualizar_paineis(self, nomes):
        """Atualiza os painéis visíveis; os escondidos ficam pendentes até aparecerem (mexer neles custa layout do Tk)."""
        paineis = self._paineis_registrados()
        for nome in nomes:
            if self._painel_na_tela(nome):
                self._paineis_pendentes.discard(nome)
                for funcao in paineis[nome][1]:
                    funcao()

    def _algo_apareceu(self, _evento=None):
        self.agendar("paineis_apareceram", 30, self._verificar_paineis_pendentes)

    def _verificar_paineis_pendentes(self):
        paineis = self._paineis_registrados()
        for nome in list(self._paineis_pendentes):
            if nome not in paineis:
                self._paineis_pendentes.discard(nome)
            elif self._na_tela(self._widget_do_painel(paineis[nome][0])):
                self._paineis_pendentes.discard(nome)
                for funcao in paineis[nome][1]:
                    funcao()
        for atributo in ("galeria_fundos_gerados", "galeria_fundos"):
            galeria = getattr(self, atributo, None)
            if galeria is not None and galeria.pendente and self._na_tela(galeria.canvas):
                galeria.desenhar()

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
        item = self.atual
        if isinstance(item, int) and not (0 <= item < len(self.projeto.get("laminas", []))):
            return
        mini = self._copia_para_render()
        produto = self.produto
        guias = self.guias.get()
        info = self.info

        largura = min(dw, LARGURA_MAX_RENDER)

        def trabalho():
            if versao != self._versao_render:
                return
            img = exportar.renderizar_item(mini, produto, info, item, largura=largura, guias=guias, rapido=True)
            self.fila.put(("render", versao, item, dw, dh, img))

        self.pintor.pedir("principal", trabalho)

    def _receber_render(self, versao, item, dw, dh, img):
        if versao != self._versao_render:
            return
        self._guardar_cache_lamina(item, dw, dh, img)
        self._imagem_lamina = (item, img)
        if self._geo is None or (item, dw, dh) != (self.atual, self._geo[0], self._geo[1]):
            return
        if self._anim_layout_id is not None:
            return
        _dw, _dh, ox, oy = self._geo
        if self._lamina_inteira_visivel(dw, dh, ox, oy) and img.size == (dw, dh):
            tk_img, x_img, y_img = ImageTk.PhotoImage(img), ox, oy
            item_cache = self._cache_laminas.get((self._lamina(item) or {}).get("id") or str(item))
            if item_cache is not None and item_cache[2] is img:
                item_cache[3] = tk_img
            self._tk_lamina = (tk_img, (item, dw, dh))
        else:
            tk_img, x_img, y_img = self._imagem_na_tela(img, dw, dh, ox, oy)
            self._tk_lamina_zoom = tk_img
        self._remendo = None
        self.canvas.delete("img")
        self.canvas.delete("remendo")
        if tk_img is not None:
            self.canvas.create_image(x_img, y_img, image=tk_img, anchor="nw", tags="img")
        self.canvas.tag_raise("espaco")
        self.canvas.tag_raise("arrasto")
        if abs(self.zoom - 1.0) < 0.001:
            self.agendar("prefetch_laminas", 450, self._prefetch_vizinhas)

    def _prefetch_vizinhas(self):
        """Com a janela parada, renderiza a lâmina anterior e a próxima para a troca ser instantânea."""
        if self._geo is None or abs(self.zoom - 1.0) > 0.001 or not isinstance(self.atual, int):
            return
        dw, dh = self._geo[:2]
        produto, info, guias = self.produto, self.info, self.guias.get()
        for item in (self.atual + 1, self.atual - 1):
            if not (0 <= item < len(self.projeto["laminas"])):
                continue
            lamina = self.projeto["laminas"][item]
            id_lam = lamina.get("id") or str(item)
            ass = self._assinatura_lamina_render(item, dw, dh)
            item_cache = self._cache_laminas.get(id_lam)
            if item_cache is not None and item_cache[0] == ass:
                continue
            mini = {"capa": None, "laminas": {item: copy.deepcopy(lamina)}}

            def trabalho(mini=mini, item=item, id_lam=id_lam, ass=ass):
                img = exportar.renderizar_item(mini, produto, info, item, largura=min(dw, LARGURA_MAX_RENDER), guias=guias, rapido=True)
                self.fila.put(("chamar", lambda: self._receber_prefetch(id_lam, ass, dw, dh, img)))

            self.pintor.pedir("pre:" + id_lam, trabalho)

    def _receber_prefetch(self, id_lam, ass, dw, dh, img):
        for item, lamina in enumerate(self.projeto["laminas"]):
            if (lamina.get("id") or str(item)) == id_lam:
                if item != self.atual and self._assinatura_lamina_render(item, dw, dh) == ass:
                    self._guardar_cache_lamina(item, dw, dh, img)
                    self._cache_laminas.move_to_end(id_lam, last=False)
                return

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
                self._atualizar_barra_opcoes()

        self.agendar("zoom_animacao", 1, animar)

    def _lamina_roda(self, evento):
        if evento.delta:
            self.mudar_zoom(1.12 ** (evento.delta / 120.0), (evento.x, evento.y))
        return "break"

    def _pan_inicio(self, evento):
        self._pan_ref = (evento.x, evento.y, list(self.pan))

    def _pan_mover(self, evento):
        x0, y0, pan = self._pan_ref
        self._deslocar_lamina([pan[0] + evento.x - x0, pan[1] + evento.y - y0])
        self.agendar("pan", 120, self.desenhar_lamina)

    def _deslocar_lamina(self, novo_pan):
        """Pan sem redesenhar: move o que já está no canvas e acerta a geometria."""
        dx, dy = novo_pan[0] - self.pan[0], novo_pan[1] - self.pan[1]
        self.pan = list(novo_pan)
        if not (dx or dy):
            return
        self.canvas.move("all", dx, dy)
        if self._geo is not None:
            dw, dh, ox, oy = self._geo
            self._geo = (dw, dh, ox + dx, oy + dy)
            if not self._lamina_inteira_visivel(dw, dh, ox + dx, oy + dy):
                self.agendar("pan_redesenho", 90, lambda: self.desenhar_lamina(pedir_render=False))

    def _scroll_click_inicio(self, evento):
        """
        Clique com o botão do Scroll (meio do mouse, <ButtonPress-2>):
        - Sobre uma foto na lâmina: seleciona/adiciona ao grupo de troca/transferência entre lâminas
          (ou se já houver um grupo vindo de outra lâmina, troca/joga o grupo aqui).
        - Também permite arrastar o grupo com o botão do Scroll pressionado até outra lâmina na linha do tempo.
        - Se clicar fora de qualquer foto e sem grupo pendente de outra lâmina, faz pan da mesa.
        """
        indice = self._espaco_em(evento.x, evento.y)
        grupo_atual = getattr(self, "_grupo_scroll_troca", None)
        com_shift = bool(getattr(evento, "state", 0) & SHIFT)

        if grupo_atual and isinstance(self.atual, int) and grupo_atual.get("lamina") != self.atual:
            if indice is not None:
                sel_atuais = self._indices_espacos_selecionados()
                alvos_dest = sorted(sel_atuais) if (len(sel_atuais) > 1 and indice in sel_atuais) else [indice]
                self.transferir_grupo_entre_laminas(
                    grupo_atual["lamina"],
                    grupo_atual["espacos"],
                    self.atual,
                    modo="jogar" if com_shift else "trocar",
                    indices_dest=alvos_dest,
                )
                return
            elif self._dentro_da_lamina(evento.x, evento.y):
                self.transferir_grupo_entre_laminas(
                    grupo_atual["lamina"],
                    grupo_atual["espacos"],
                    self.atual,
                    modo="trocar" if com_shift else "jogar",
                )
                return

        if indice is not None and isinstance(self.atual, int):
            sel_atuais = self._indices_espacos_selecionados()
            if not grupo_atual or grupo_atual.get("lamina") != self.atual:
                if len(sel_atuais) > 1 and indice in sel_atuais:
                    novos = sorted(sel_atuais)
                else:
                    novos = [indice]
            else:
                atuais_sc = list(grupo_atual.get("espacos") or [])
                if indice in atuais_sc:
                    atuais_sc.remove(indice)
                else:
                    atuais_sc.append(indice)
                novos = sorted(set(atuais_sc))

            if not novos:
                self._grupo_scroll_troca = None
                self._definir_selecao_espacos(set())
                self._desenhar_espacos()
                self.registrar("Seleção de grupo do botão Scroll cancelada.")
                return

            self._grupo_scroll_troca = {"lamina": self.atual, "espacos": novos}
            self._definir_selecao_espacos(set(novos), indice if indice in novos else novos[0])
            self._desenhar_espacos()
            self._arrasto = {
                "tipo": "espaco",
                "lamina": self.atual,
                "espaco": novos[0],
                "espacos_grupo": list(novos),
                "x": evento.x_root,
                "y": evento.y_root,
                "ativo": False,
            }
            self.registrar(
                "🖱 Scroll: %d foto(s) selecionada(s) na Lâmina %d · Clique com o Scroll em mais fotos ou clique com o Scroll em outra lâmina para jogar/trocar o grupo!"
                % (len(novos), self.atual + 1)
            )
            return

        self._pan_inicio(evento)

    def _scroll_mover(self, evento):
        if getattr(self, "_arrasto", None) is not None:
            self._mover_arrasto(evento)
        elif hasattr(self, "_pan_ref"):
            self._pan_mover(evento)

    def _scroll_soltar(self, evento):
        if getattr(self, "_arrasto", None) is not None:
            self._soltar_arrasto(evento)
        if hasattr(self, "_pan_ref"):
            del self._pan_ref

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
        self._deslocar_lamina([pan[0] + evento.x - x0, pan[1] + evento.y - y0])

    def _pan_direito_soltar(self, evento):
        estado = getattr(self, "_pan_ref_direito", None)
        self.canvas.configure(cursor="")
        if estado is None:
            return
        del self._pan_ref_direito
        if estado[3]:
            self.cancelar_agendado("pan_redesenho")
            self.desenhar_lamina()
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
            definir_cursor(self.canvas, "hand2" if acao_v is not None else "")
            return
        if self._editando_lamina():
            alca = self._alca_em(evento.x, evento.y)
            if alca:
                definir_cursor(self.canvas, CURSOR_ALCA[alca])
                return
            sobre = self._espaco_em(evento.x, evento.y) is not None or self._molde_em(evento.x, evento.y) is not None
            definir_cursor(self.canvas, "fleur" if sobre else "")
            return
        indice = self._espaco_em(evento.x, evento.y)
        definir_cursor(self.canvas, "hand2" if indice is not None else "")

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
                sel_grupo = sorted(self._indices_espacos_selecionados())
                self._arrasto = {"tipo": "espaco", "lamina": self.atual, "espaco": indice,
                                 "espacos_grupo": sel_grupo if (len(sel_grupo) > 1 and indice in sel_grupo) else [indice],
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
            return
        lamina = self._lamina()
        if lamina and lamina.get("espacos"):
            idx_escolhido = 0
            for idx_e, esp_e in enumerate(lamina["espacos"]):
                if esp_e.get("foto"):
                    idx_escolhido = idx_e
                    break
            self._definir_selecao_espacos({idx_escolhido}, idx_escolhido)
            self.abrir_editor(("lamina", self.atual, idx_escolhido))
            return
        self.abrir_editor(("lamina", self.atual if isinstance(self.atual, int) else 0, 0))

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
                menu_1c_esp = tk.Menu(menu, tearoff=0)
                fotos_sel_esp = [
                    self._lamina()["espacos"][i_e].get("foto")
                    for i_e in self._indices_espacos_selecionados()
                    if 0 <= i_e < len(self._lamina()["espacos"]) and self._lamina()["espacos"][i_e].get("foto")
                ] or [foto]
                menu_1c_esp.add_command(
                    label="⚡ 1-Clique LEVE nesta(s) foto(s)",
                    command=lambda fs=list(fotos_sel_esp): self.aplicar_lote_inteligente(fotos_alvo=fs, escopo="selecionadas", nivel="leve"),
                )
                menu_1c_esp.add_command(
                    label="✦ 1-Clique MÉDIO nesta(s) foto(s)",
                    command=lambda fs=list(fotos_sel_esp): self.aplicar_lote_inteligente(fotos_alvo=fs, escopo="selecionadas", nivel="medio"),
                )
                menu_1c_esp.add_command(
                    label="🔥 1-Clique PESADO (Recuperar Foto Ruim)",
                    command=lambda fs=list(fotos_sel_esp): self.aplicar_lote_inteligente(fotos_alvo=fs, escopo="selecionadas", nivel="pesado"),
                )
                menu_1c_esp.add_separator()
                menu_1c_esp.add_command(
                    label="✦ 1-Clique MÉDIO na Lâmina Inteira",
                    command=lambda: self.aplicar_lote_inteligente(escopo="lamina", nivel="medio"),
                )
                menu_1c_esp.add_command(
                    label="↺ Restaurar Luz/Cor Original",
                    command=lambda fs=list(fotos_sel_esp): self.restaurar_luz_cor_lote(fotos_alvo=fs, escopo="selecionadas"),
                )
                menu_1c_esp.add_separator()
                menu_1c_esp.add_command(
                    label="🎛 Abrir Edição em Lote Completa...",
                    command=lambda fs=list(fotos_sel_esp): self.abrir_tratamento(fs),
                )
                menu.add_cascade(label="✨ Melhoria 1-Clique / Lote (Luz e Cor)", menu=menu_1c_esp)
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
        if validos and getattr(self, "fotos_sel", None):
            self.fotos_sel.clear()
            if hasattr(self, "atualizar_fotos"):
                self.agendar("limpar_sel_tira", 20, self.atualizar_fotos)
        self._atualizar_paineis(("Moldura", "Sombra", "Degradê"))

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
        self._atualizar_barra_opcoes()

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
        a.pop("cache_previas_grupo", None)
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
        a.pop("cache_previas_grupo", None)

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
                    dobra_x = None
                    if item["x"] < 0.5 < item["x"] + item["w"] and item["w"] > 0.05:
                        dobra_x = (0.5 - item["x"]) / float(item["w"])
                    tk_img = self._imagem_previa(a.setdefault("cache_previas_grupo", {}), idx, previa, pw, ph, dobra_x)
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
            if self._painel_na_tela("editar"):
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
                dobra_x = None
                if item["x"] < 0.5 < item["x"] + item["w"] and item["w"] > 0.05:
                    dobra_x = (0.5 - item["x"]) / float(item["w"])
                a["tk"] = self._imagem_previa(a.setdefault("cache_previas_grupo", {}), None, previa, pw, ph, dobra_x)
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
        if self._painel_na_tela("editar"):
            self._atualizar_numeros()

    @staticmethod
    def _imagem_previa(cache, chave_item, previa, pw, ph, dobra_x):
        """Foto recortada para a prévia do arrasto; se só a posição mudou, reaproveita a anterior."""
        chave = (pw, ph, None if dobra_x is None else round(dobra_x, 3))
        guardada = cache.get(chave_item)
        if guardada is not None and guardada[0] == chave:
            return guardada[1]
        img = previa["img"]
        W, H = img.size
        aspecto = pw / float(ph)
        corte = recorte.corte_do_espaco(W, H, aspecto, previa["edicao"], previa["rosto_t"],
                                        rosto_ja_transformado=True, dobra_x=dobra_x)
        pronta = recorte.recortar(img, recorte.caixa_corte(W, H, aspecto, corte), (pw, ph), previa["fundo"], rapido=True)
        if previa.get("moldura"):
            aplicar_moldura(pronta, previa["moldura"], previa.get("px_mm"))
        tk_img = ImageTk.PhotoImage(pronta)
        cache[chave_item] = (chave, tk_img)
        return tk_img

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
            self._acabamento([l])

        self._mudar_espacos(mudar, "Foto girada em %s° com reajuste proporcional da página." % graus)

    def alinhar_espaco(self, acao):
        alvo = self._alvo_sel() if isinstance(self.atual, int) else None
        if alvo is None or (alvo[0] == "molde" and acao == "igualar"):
            self.registrar("Selecione um ou mais espaços na lâmina.")
            return
        lw, lh = self._mm()
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
        if final:
            self._marcar_modificado()
            self.cancelar_agendado("enq_inline")
            self._atualizar_enquadramento_inline()
            self.atualizar_lamina_atual(timeline=True)
            return
        if not self.modificado:
            self._marcar_modificado()
        espera = 0.016 - (time.perf_counter() - getattr(self, "_enq_inline_ultimo", 0.0))
        if espera <= 0:
            self._atualizar_enquadramento_inline_agora()
        else:
            self.agendar("enq_inline", max(1, int(espera * 1000)), self._atualizar_enquadramento_inline_agora)
        if self._geo:
            dw, dh = self._geo[:2]
            self.agendar("render_enq_inline", 16, lambda: self._pedir_render(dw, dh))

    def _atualizar_enquadramento_inline_agora(self):
        self._enq_inline_ultimo = time.perf_counter()
        self._atualizar_enquadramento_inline()

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
        idx, esp = self._espaco_enquadramento_ativo()
        if esp is None or not esp.get("foto"):
            return "break"
        agora = time.perf_counter()
        gesto = (self.atual, idx)
        if gesto != getattr(self, "_gesto_roda_enq", None) or agora - getattr(self, "_ultimo_roda_enq", 0.0) > 0.8:
            self.marcar_historico()
        self._gesto_roda_enq, self._ultimo_roda_enq = gesto, agora
        corte, _img_t, _asp = self._corte_atual_espaco(esp)
        fator = 1.08 if evento.delta > 0 else (1.0 / 1.08)
        novo_zoom = max(1.0, min(3.0, float(corte.get("zoom", 1.0)) * fator))
        self._aplicar_corte_inline(corte.get("cx", 0.5), corte.get("cy", 0.5), novo_zoom, final=False)
        self.agendar("enq_roda_final", 150, lambda: self.atualizar_lamina_atual(timeline=True))
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
            qtd_esp_g = len(a.get("espacos_grupo") or [1])
            texto = {"foto": "%s foto(s)" % len(a.get("fotos", [])),
                     "espaco": ("mover grupo (%d fotos)" % qtd_esp_g) if qtd_esp_g > 1 else "mover foto",
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
                grupo_esp = a.get("espacos_grupo") or [a["espaco"]]
                if a["lamina"] != self.atual and len(grupo_esp) > 1:
                    self.transferir_grupo_entre_laminas(a["lamina"], grupo_esp, self.atual, modo="trocar", indices_dest=[indice])
                else:
                    self.trocar_espacos(a["lamina"], a["espaco"], self.atual, indice)
        elif alvo is self.canvas_timeline:
            item = self._timeline_em(self._eixo_tl_root(evento.x_root, evento.y_root))
            if a["tipo"] == "foto" and isinstance(item, int):
                self.adicionar_na_lamina(a["fotos"], item)
            elif a["tipo"] == "espaco" and isinstance(item, int) and item != a["lamina"]:
                grupo_esp = a.get("espacos_grupo") or [a["espaco"]]
                com_shift = bool(getattr(evento, "state", 0) & SHIFT)
                self.transferir_grupo_entre_laminas(
                    a["lamina"],
                    grupo_esp,
                    item,
                    modo="trocar" if com_shift else "jogar",
                )
        elif alvo is self.canvas_fotos and a["tipo"] == "espaco":
            self.tirar_foto(a["lamina"], a["espaco"])

    # ---------------- painel de fotos (com divisões por pastas) ----------------

    def _atualizar_combo_pastas(self, pastas=None):
        if not hasattr(self, "combo_pasta"):
            return
        if pastas is None:
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

    def _fotos_filtradas(self, contagem=None):
        if contagem is None:
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

    def _construir_itens_painel_fotos(self, pastas_projeto=None):
        """
        Monta a sequência visual da tira de fotos separando cada pasta com um divisor clicável
        (para nunca misturar pastas carregadas). Suporta layout horizontal (embaixo) e vertical (à direita/esquerda).
        """
        cw, ch, _lado = self._celula
        vertical = self._fotos_vertical()
        if pastas_projeto is None:
            pastas_projeto = proj.listar_pastas_projeto(self.projeto)
        mostrar_divisores = len(pastas_projeto) >= 1 and self.pasta_filtro.get() == "todas"

        grupos = OrderedDict()
        for idx_f, (foto, usos) in enumerate(self._lista_fotos):
            ch_p = proj.pasta_da_foto(foto)
            nome_p = proj.nome_pasta_da_foto(foto)
            grupos.setdefault(ch_p, (nome_p, []))[1].append((idx_f, foto, usos))

        itens = []
        if vertical:
            c = self.canvas_fotos
            larg_painel = c.winfo_width() if c.winfo_ismapped() and c.winfo_width() > 40 else max(180, int(self.arranjo.get("largura_fotos", 290)))
            colunas = max(1, (larg_painel - 8) // max(1, cw))
            alt_div = 30
            y_cursor = 4
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
                        "x0": 4,
                        "x1": max(cw + 4, larg_painel - 4),
                        "y0": y_cursor,
                        "y1": y_cursor + alt_div,
                    })
                    y_cursor += alt_div + 4
                if not recolhida:
                    col = 0
                    for idx_f, foto, usos in fotos_p:
                        ix0 = 4 + col * cw
                        iy0 = y_cursor
                        itens.append({
                            "tipo": "foto",
                            "indice": idx_f,
                            "foto": foto,
                            "usos": usos,
                            "pasta": ch_p,
                            "nome_pasta": nome_p,
                            "x0": ix0,
                            "x1": ix0 + cw,
                            "y0": iy0,
                            "y1": iy0 + ch,
                        })
                        col += 1
                        if col >= colunas:
                            col = 0
                            y_cursor += ch
                    if col > 0:
                        y_cursor += ch
            self._itens_painel_fotos = itens
            self._xs_painel_fotos = []
            self._largura_total_fotos = max(1, larg_painel)
            self._altura_total_fotos = max(1, y_cursor + 8)
            return

        larg_div = 54
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
                    "y0": 0,
                    "y1": ch,
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
                        "y0": 0,
                        "y1": ch,
                    })
                    x_cursor += cw
        self._itens_painel_fotos = itens
        self._xs_painel_fotos = [it["x0"] for it in itens]
        self._largura_total_fotos = max(1, x_cursor)
        self._altura_total_fotos = max(1, ch)

    def atualizar_fotos(self):
        pastas = proj.listar_pastas_projeto(self.projeto)
        contagem = proj.usos(self.projeto)
        self._atualizar_combo_pastas(pastas)
        self._lista_fotos = self._fotos_filtradas(contagem)
        total = len(self.projeto["fotos"])
        usadas = sum(1 for _f, u in self._fotos_filtradas_todas(contagem) if u)
        selecionadas = len(self._fotos_escolhidas())
        n_pastas = len(pastas)
        texto = "Fotos  %s/%s no álbum" % (usadas, total)
        if n_pastas > 1:
            texto += " · %d pastas" % n_pastas
        if selecionadas:
            texto += " · %s sel." % selecionadas
        self.rotulo_fotos.configure(text=texto)
        self._celula = self._medidas_fotos()
        self._construir_itens_painel_fotos(pastas)
        self._area_fotos()
        self._desenhar_fotos_visiveis()

    def _medidas_fotos(self):
        """(largura da célula, altura, lado da miniatura): adapta-se ao painel embaixo ou à direita."""
        c = self.canvas_fotos
        if self._fotos_vertical():
            largura = c.winfo_width() if c.winfo_ismapped() and c.winfo_width() > 40 else max(180, int(self.arranjo.get("largura_fotos", 290)))
            colunas = 2 if largura >= 210 else 1
            cel_w = max(86, (largura - 12) // colunas)
            lado = max(56, min(140, cel_w - 14))
            cel_h = lado + 24
            return cel_w, cel_h, lado
        altura = c.winfo_height() if c.winfo_ismapped() and c.winfo_height() > 10 else int(c.cget("height"))
        lado = max(40, min(LADO_FOTO_MAXIMO, altura - 12))
        return lado + 16, altura, lado

    def _area_fotos(self):
        if self._fotos_vertical():
            largura = getattr(self, "_largura_total_fotos", 240)
            altura = getattr(self, "_altura_total_fotos", 240)
            area = "0 0 %d %d" % (largura, altura)
        else:
            largura = getattr(self, "_largura_total_fotos", max(1, len(getattr(self, "_lista_fotos", [])) * self._celula[0]))
            area = "0 0 %d %d" % (largura, self._celula[1])
        if self.canvas_fotos.cget("scrollregion") != area:
            self.canvas_fotos.configure(scrollregion=area)

    def _mudou_tamanho_fotos(self, evento):
        if self._fotos_vertical():
            self._celula = self._medidas_fotos()
            self._construir_itens_painel_fotos()
            self._area_fotos()
        elif evento.height != self._celula[1]:
            self._celula = self._medidas_fotos()
            self._construir_itens_painel_fotos()
            self._area_fotos()
        self.agendar("fotos", 30, self._desenhar_fotos_visiveis)

    def _fotos_filtradas_todas(self, contagem=None):
        if contagem is None:
            contagem = proj.usos(self.projeto)
        return [(f, contagem.get(normalizar_caminho(f["caminho"]), 0)) for f in self.projeto["fotos"]]

    def _rolar_fotos(self, *args):
        if self._fotos_vertical():
            self.canvas_fotos.yview(*args)
        else:
            self.canvas_fotos.xview(*args)

    def _rolar_fotos_h(self, *args):
        self.canvas_fotos.xview(*args)
        self.agendar("fotos", 16, self._desenhar_fotos_visiveis)

    def _rolar_fotos_v(self, *args):
        self.canvas_fotos.yview(*args)
        self.agendar("fotos", 16, self._desenhar_fotos_visiveis)

    def _fotos_roda(self, evento):
        if not evento.delta:
            return "break"
        passo = -int(evento.delta / 40)
        if self._fotos_vertical():
            self.canvas_fotos.yview_scroll(passo, "units")
        else:
            self.canvas_fotos.xview_scroll(passo, "units")
        self.agendar("fotos", 16, self._desenhar_fotos_visiveis)
        return "break"

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
                while len(self._tk_miniaturas) > 600:
                    self._tk_miniaturas.popitem(last=False)
                return foto
            except Exception:
                return None
        self._pedir_miniatura_foto(caminho, chave, lado, bool(usada))
        return None

    def _pedir_miniatura_foto(self, caminho, chave, lado, usada):
        agora = time.perf_counter()
        pedido_em = self._miniaturas_pendentes.get(chave)
        if pedido_em is not None and agora - pedido_em < 2.0:
            return
        self._miniaturas_pendentes[chave] = agora
        chave_pintor = "ft:%s:%d" % (chave[0], int(usada))
        anterior = self._pedidos_miniatura.get(chave_pintor)
        if anterior is not None and anterior != chave:
            self._miniaturas_pendentes.pop(anterior, None)
        self._pedidos_miniatura[chave_pintor] = chave

        def trabalho():
            try:
                img = self._miniatura_pil(caminho, lado, usada)
                self.fila.put(("miniatura_foto", chave, img))
            except Exception:
                self.fila.put(("miniatura_foto", chave, None))

        self.pintor.pedir(chave_pintor, trabalho)

    def _pedido_descartado(self, chave_pintor):
        """O Pintor jogou fora um pedido antigo: libera a chave para a miniatura poder ser pedida de novo."""
        if chave_pintor.startswith("ft:"):
            chave = self._pedidos_miniatura.pop(chave_pintor, None)
            if chave is not None:
                self._miniaturas_pendentes.pop(chave, None)
                self.agendar("fotos_descartadas", 300, self._desenhar_fotos_visiveis)
        elif chave_pintor.startswith("tl:"):
            self.agendar("timeline_descartada", 300, self.desenhar_timeline)

    def _receber_miniatura_foto(self, chave, img):
        self._miniaturas_pendentes.pop(chave, None)
        chave_pintor = "ft:%s:%d" % (chave[0], int(chave[3]))
        if self._pedidos_miniatura.get(chave_pintor) == chave:
            del self._pedidos_miniatura[chave_pintor]
        if img is not None:
            try:
                self._tk_miniaturas[chave] = ImageTk.PhotoImage(img)
                while len(self._tk_miniaturas) > 600:
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
        vertical = self._fotos_vertical()
        if not itens:
            c.create_text(12, max(24, ch // 2), anchor="w", fill=tema.TEXTO_FRACO,
                          text="Sem fotos aqui. Arraste fotos ou pastas do Explorer.",
                          width=max(160, c.winfo_width() - 24) if vertical else 0)
            return
        letras = max(10, (cw - 6) // 6)
        if vertical:
            y0_vis = c.canvasy(0)
            alt_vis = max(200, c.winfo_height())
            y1_vis = y0_vis + alt_vis
            margem_overscan = max(ch * 2, 160)
            indices_iter = range(len(itens))
        else:
            x0 = c.canvasx(0)
            larg_visivel = max(200, c.winfo_width())
            x1 = x0 + larg_visivel
            margem_overscan = max(cw * 3, 180)
            idx_ini = max(0, bisect.bisect_right(xs, x0 - margem_overscan) - 1) if xs else 0
            idx_fim = min(len(itens), bisect.bisect_right(xs, x1 + margem_overscan) + 1) if xs else len(itens)
            indices_iter = range(idx_ini, idx_fim)

        for idx_it in indices_iter:
            item = itens[idx_it]
            iy0 = item.get("y0", 0)
            iy1 = item.get("y1", ch)
            if vertical:
                if iy1 < y0_vis - margem_overscan or iy0 > y1_vis + margem_overscan:
                    continue
            else:
                if item["x1"] < x0 - margem_overscan or item["x0"] > x1 + margem_overscan:
                    continue
            if item["tipo"] == "pasta":
                ix0, ix1 = item["x0"], item["x1"]
                recolhida = item["recolhida"]
                seta = "▶" if recolhida else "▼"
                usadas_p = item.get("usadas", 0)
                if vertical:
                    c.create_rectangle(ix0, iy0, ix1, iy1,
                                       fill=tema.PAINEL_2 if not recolhida else tema.DESTAQUE_ESCURO,
                                       outline=tema.DESTAQUE if not recolhida else tema.BORDA, width=1)
                    ym = (iy0 + iy1) // 2
                    c.create_text(ix0 + 8, ym, anchor="w", text="📁 %s %s" % (seta, item["nome"]),
                                  fill=tema.TEXTO, font=tema.FONTE_FORTE)
                    c.create_text(ix1 - 8, ym, anchor="e", text="%d/%d" % (usadas_p, item["qtd"]),
                                  fill=tema.VERDE if usadas_p == item["qtd"] else tema.TEXTO_FRACO, font=tema.FONTE_PEQUENA)
                else:
                    cx = (ix0 + ix1) // 2
                    c.create_rectangle(ix0 + 3, 4, ix1 - 3, ch - 4,
                                       fill=tema.PAINEL_2 if not recolhida else tema.DESTAQUE_ESCURO,
                                       outline=tema.DESTAQUE if not recolhida else tema.BORDA, width=1)
                    c.create_text(cx, 15, text="📁 %s" % seta, fill=tema.ALERTA, font=tema.FONTE_FORTE)
                    nome_p = item["nome"]
                    curto_p = nome_p if len(nome_p) <= 12 else nome_p[:10] + "…"
                    c.create_text(cx, ch // 2 - 2, text=curto_p, fill=tema.TEXTO, font=tema.FONTE_PEQUENA, width=ix1 - ix0 - 8)
                    c.create_text(cx, ch - 14, text="%d/%d" % (usadas_p, item["qtd"]), fill=tema.VERDE if usadas_p == item["qtd"] else tema.TEXTO_FRACO, font=tema.FONTE_PEQUENA)
                continue

            foto, usos = item["foto"], item["usos"]
            cx = (item["x0"] + item["x1"]) // 2
            cy = iy0 + ch // 2 - 4
            selecionada = normalizar_caminho(foto["caminho"]) in self.fotos_sel
            if selecionada:
                c.create_rectangle(cx - cw // 2 + 3, iy0 + 2, cx + cw // 2 - 3, iy1 - 2, fill=tema.DESTAQUE_ESCURO, outline="")
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
                c.create_oval(direita - 16, iy0 + 4, direita + 2, iy0 + 22, fill=tema.VERDE, outline="")
                c.create_text(direita - 7, iy0 + 13, text=str(usos), fill="#0b2a1c", font=tema.FONTE_FORTE)
            if foto.get("destaque"):
                c.create_text(cx - lado // 2 + 6, iy0 + 12, text="★", fill=tema.ALERTA, font=("Segoe UI", 12))
            if foto.get("grupo_ordem"):
                num_g = max(1, int(foto.get("grupo_ordem") or 1))
                gx0 = cx - lado // 2 + 2
                gy0 = iy0 + 4
                gx1 = gx0 + (22 if num_g < 10 else 28)
                gy1 = iy0 + 18
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
                by1 = iy1 - 18
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
            c.create_text(cx, iy1 - 9, text=nome if len(nome) <= letras else nome[:letras - 2] + "…",
                          fill=tema.TEXTO if selecionada else tema.TEXTO_FRACO, font=tema.FONTE_PEQUENA)

    def _item_painel_fotos_em(self, evento):
        x = self.canvas_fotos.canvasx(evento.x)
        y = self.canvas_fotos.canvasy(evento.y)
        itens = getattr(self, "_itens_painel_fotos", [])
        xs = getattr(self, "_xs_painel_fotos", [])
        if not itens:
            return None
        if self._fotos_vertical():
            for item in itens:
                if item["x0"] <= x < item["x1"] and item.get("y0", 0) <= y < item.get("y1", 999999):
                    return item
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
        menu_1c_fotos = tk.Menu(menu, tearoff=0)
        menu_1c_fotos.add_command(
            label="⚡ 1-Clique LEVE em %s foto(s)" % quantidade,
            command=lambda fs=list(fotos): self.aplicar_lote_inteligente(fotos_alvo=fs, escopo="selecionadas", nivel="leve"),
        )
        menu_1c_fotos.add_command(
            label="✦ 1-Clique MÉDIO em %s foto(s)" % quantidade,
            command=lambda fs=list(fotos): self.aplicar_lote_inteligente(fotos_alvo=fs, escopo="selecionadas", nivel="medio"),
        )
        menu_1c_fotos.add_command(
            label="🔥 1-Clique PESADO (Recuperar Foto Ruim) em %s foto(s)" % quantidade,
            command=lambda fs=list(fotos): self.aplicar_lote_inteligente(fotos_alvo=fs, escopo="selecionadas", nivel="pesado"),
        )
        menu_1c_fotos.add_separator()
        menu_1c_fotos.add_command(
            label="↺ Restaurar Luz/Cor Original em %s foto(s)" % quantidade,
            command=lambda fs=list(fotos): self.restaurar_luz_cor_lote(fotos_alvo=fs, escopo="selecionadas"),
        )
        menu_1c_fotos.add_command(
            label="🎛 Abrir Edição em Lote (Luz, Cor e Saturação)...",
            command=lambda fs=list(fotos): self.abrir_tratamento(fs),
        )
        menu.add_cascade(label="✨ Melhoria 1-Clique / Lote (%s foto(s))" % quantidade, menu=menu_1c_fotos)
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
        self.contagem_desenhos["timeline"] += 1
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
        texto_area = "%d %d %d %d" % area_tl
        if c.cget("scrollregion") != texto_area:
            c.configure(scrollregion=texto_area)

        # Virtualização da linha do tempo: renderiza e pede miniaturas apenas para a janela visível (+ overscan de 3 lâminas)
        overscan = 3
        pos_ini = max(0, int(v0 // passo) - overscan)
        pos_fim = min(len(itens), int(v1 // passo) + 1 + overscan)
        mapa_fotos = proj.mapa_registros(self.projeto)
        pastas_lam = {}

        def pasta_de(it):
            if it not in pastas_lam:
                pastas_lam[it] = proj.pasta_da_lamina(self.projeto, self.projeto["laminas"][it], mapa_fotos)
            return pastas_lam[it]

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
            ch_pasta_lam, nome_pasta_lam, distintas_lam = pasta_de(item)
            pasta_ant = ""
            if posicao > 0:
                pasta_ant, _nm_ant, _d_ant = pasta_de(itens[posicao - 1])
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
        return "break"

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

    def _timeline_scroll_pressionou(self, evento):
        """
        Clique com o botão do Scroll (meio do mouse, <ButtonPress-2>) em uma miniatura da linha do tempo:
        - Se houver fotos selecionadas no Grupo do Scroll (ou espaços selecionados na lâmina atual),
          joga (move) o grupo inteiro para a lâmina clicada (ou troca de lugar se segurar Shift).
        - Caso contrário, captura todas as fotos da lâmina clicada no Grupo do Scroll.
        """
        item = self._timeline_em(self._eixo_tl(evento.x, evento.y))
        if not isinstance(item, int):
            return
        com_shift = bool(getattr(evento, "state", 0) & SHIFT)
        grupo_atual = getattr(self, "_grupo_scroll_troca", None)
        if grupo_atual and grupo_atual.get("lamina") != item and grupo_atual.get("espacos"):
            self.transferir_grupo_entre_laminas(
                grupo_atual["lamina"],
                grupo_atual["espacos"],
                item,
                modo="trocar" if com_shift else "jogar",
            )
            return
        if isinstance(self.atual, int) and self.atual != item and self._indices_espacos_selecionados():
            self.transferir_grupo_entre_laminas(
                self.atual,
                sorted(self._indices_espacos_selecionados()),
                item,
                modo="trocar" if com_shift else "jogar",
            )
            return
        lam = self._lamina(item)
        if lam and lam.get("espacos"):
            todos = list(range(len(lam["espacos"])))
            self._grupo_scroll_troca = {"lamina": item, "espacos": todos}
            self.selecionar_lamina(item, manter_selecao=False)
            self._definir_selecao_espacos(set(todos), 0)
            self._desenhar_espacos()
            self.registrar(
                "🖱 Scroll: Todas as %d foto(s) da Lâmina %d capturadas · Clique com o Scroll em outra lâmina para jogar ou trocar o grupo!"
                % (len(todos), item + 1)
            )

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
            self._sugestoes_calculando = False
            self._versao_sugestoes = getattr(self, "_versao_sugestoes", 0) + 1
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
                self.versao_fotos,
            )
            if getattr(self, "_chave_cache_sugestoes", None) != chave_sug:
                self._sugestoes = []
                self._versao_sugestoes = getattr(self, "_versao_sugestoes", 0) + 1
                self._sugestoes_calculando = False
                if self._painel_na_tela("sugestoes"):
                    self._chave_cache_sugestoes = chave_sug
                    fotos = self._fotos_info([e["foto"] for e in lamina["espacos"] if e.get("foto")], lamina)
                    if fotos:
                        self._calcular_sugestoes_fora(self._versao_sugestoes, fotos, self.produto, cfg_atual)
                else:
                    self._chave_cache_sugestoes = None
        self.desenhar_sugestoes()
        self.desenhar_modelos()
        self._atualizar_fundo_ui()

    def _calcular_sugestoes_fora(self, versao, fotos, produto, cfg_atual):
        self._sugestoes_calculando = True

        def trabalho():
            opcoes = []
            try:
                opcoes = diagramar.opcoes_lamina(fotos, produto, cfg_atual, maximo=12)
            finally:
                self.fila.put(("chamar", lambda: self._receber_sugestoes(versao, opcoes)))

        threading.Thread(target=trabalho, daemon=True).start()

    def _receber_sugestoes(self, versao, opcoes):
        if versao != getattr(self, "_versao_sugestoes", 0):
            return
        self._sugestoes_calculando = False
        self._sugestoes = opcoes
        self.desenhar_sugestoes()

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
            if getattr(self, "_sugestoes_calculando", False):
                texto = "Calculando sugestões..."
            else:
                texto = "Selecione uma lâmina com fotos." if isinstance(self.atual, int) else "A capa não tem sugestões."
            c.create_text(10, 20, anchor="w", fill=tema.TEXTO_FRACO, text=texto)
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
        if not self._painel_na_tela("modelos"):
            return
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
        infos_saem = {}
        for info_f in self._fotos_info([e["foto"] for e in saem if e.get("foto")], lamina):
            infos_saem.setdefault(info_f["caminho"], info_f)
        for e in saem:
            if e.get("foto"):
                info_f = infos_saem.get(e["foto"])
                if info_f:
                    fotos_pag.append(dict(info_f, edicao=e.get("edicao") or {}))
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

    def aplicar_fundo_gerado_em(self, dados_gerado, item=None, alvo=None):
        """
        Aplica um dicionário de fundo gerado proceduralmente (Degradê ou Abstrato) na lâmina/página,
        garantindo que esteja salvo como arquivo .png real na pasta temporária para edição livre.
        """
        item = self.atual if item is None else item
        dono = self._lamina(item)
        if dono is None or not isinstance(dados_gerado, dict):
            self.registrar("Escolha uma lâmina.")
            return False
        ch_cor, ch_img = self._chaves_fundo_alvo(alvo)
        self.marcar_historico()
        copia_dados = proj.copia(dados_gerado)
        espec = copia_dados.get("gerado") or {}
        if espec:
            cam_real = materializar_fundo_gerado_em_arquivo(espec, caminho_destino=copia_dados.get("caminho"))
            copia_dados["caminho"] = cam_real
        dono[ch_img] = copia_dados
        cores = espec.get("cores") or []
        if cores:
            dono[ch_cor] = str(cores[0])
        self._atualizar_rotulo_temp_fundos()
        self._atualizar_fundo_ui()
        self.atualizar_lamina_atual()
        nome_alvo = {
            "fundo_imagem": "lâmina %s" % (item + 1),
            "fundo_imagem_esquerda": "lâmina %s (pág. esquerda)" % (item + 1),
            "fundo_imagem_direita": "lâmina %s (pág. direita)" % (item + 1),
        }[ch_img]
        self.registrar("Fundo gerado salvo em Temp e aplicado na %s: %s" % (nome_alvo, copia_dados.get("nome_exibicao") or "Fundo Gerado"))
        return True

    def aplicar_fundo_em(self, caminho, item=None, alvo=None):
        """
        Põe a imagem como fundo do alvo selecionado (lâmina inteira, página esquerda ou página direita)
        com ajustes zerados exclusivos para este fundo (não herda ajustes de outros fundos).
        """
        if isinstance(caminho, dict) and eh_fundo_gerado(caminho):
            return self.aplicar_fundo_gerado_em(caminho, item=item, alvo=alvo)
        if isinstance(caminho, str) and hasattr(self, "galeria_fundos_gerados"):
            dados_g = self.galeria_fundos_gerados.obter_dados_gerado(caminho)
            if dados_g:
                return self.aplicar_fundo_gerado_em(dados_g, item=item, alvo=alvo)
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
        fundos.registrar_uso_fundo(ARQUIVO_FUNDOS, caminho)
        self._fundos_avisados.discard(normalizar_caminho(caminho))
        self._atualizar_fundo_ui()
        self.atualizar_lamina_atual()
        nome_alvo = {"fundo_imagem": "lâmina %s" % (item + 1),
                     "fundo_imagem_esquerda": "lâmina %s (pág. esquerda)" % (item + 1),
                     "fundo_imagem_direita": "lâmina %s (pág. direita)" % (item + 1)}[ch_img]
        self.registrar("Fundo da %s: %s" % (nome_alvo, Path(caminho).name))
        return True

    @staticmethod
    def _materializar_gerado(dados_g):
        copia_dados = proj.copia(dados_g)
        espec = copia_dados.get("gerado") or {}
        if espec:
            copia_dados["caminho"] = materializar_fundo_gerado_em_arquivo(espec, caminho_destino=copia_dados.get("caminho"))
        return copia_dados

    def aplicar_fundo_em_selecionadas(self, caminho):
        alvos = self._indices_laminas_selecionadas()
        if not alvos:
            return
        self.marcar_historico()
        _ch_cor, ch_img = self._chaves_fundo_alvo()
        dados_g = None
        if isinstance(caminho, dict) and eh_fundo_gerado(caminho):
            dados_g = caminho
        elif isinstance(caminho, str) and eh_fundo_gerado(caminho) and hasattr(self, "galeria_fundos_gerados"):
            dados_g = self.galeria_fundos_gerados.obter_dados_gerado(caminho)
        if dados_g:
            dados_g = self._materializar_gerado(dados_g)
        for idx in alvos:
            lamina = self.projeto["laminas"][idx]
            if dados_g:
                lamina[ch_img] = proj.copia(dados_g)
            else:
                dados = fundo_imagem_padrao()
                dados["caminho"] = str(Path(caminho))
                lamina[ch_img] = dados
        if not dados_g and isinstance(caminho, str):
            fundos.registrar_uso_fundo(ARQUIVO_FUNDOS, caminho)
        self.atualizar_tudo()
        nome_f = (dados_g.get("nome_exibicao") if dados_g else Path(caminho).name) or "Fundo"
        self.registrar("Fundo %s aplicado em %s lâmina(s) selecionada(s)." % (nome_f, len(alvos)))

    def aplicar_fundo_em_todas(self, caminho):
        if not self.projeto["laminas"]:
            return
        self.marcar_historico()
        _ch_cor, ch_img = self._chaves_fundo_alvo()
        dados_g = None
        if isinstance(caminho, dict) and eh_fundo_gerado(caminho):
            dados_g = caminho
        elif isinstance(caminho, str) and eh_fundo_gerado(caminho) and hasattr(self, "galeria_fundos_gerados"):
            dados_g = self.galeria_fundos_gerados.obter_dados_gerado(caminho)
        if dados_g:
            dados_g = self._materializar_gerado(dados_g)
        for lamina in self.projeto["laminas"]:
            if dados_g:
                lamina[ch_img] = proj.copia(dados_g)
            else:
                dados = fundo_imagem_padrao()
                dados["caminho"] = str(Path(caminho))
                lamina[ch_img] = dados
        if not dados_g and isinstance(caminho, str):
            fundos.registrar_uso_fundo(ARQUIVO_FUNDOS, caminho)
        self.atualizar_tudo()
        nome_f = (dados_g.get("nome_exibicao") if dados_g else Path(caminho).name) or "Fundo"
        self.registrar("Fundo %s em todas as %s lâminas." % (nome_f, len(self.projeto["laminas"])))

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

    def _fotos_alvo_fundo_atual(self):
        """Retorna os caminhos das fotos do alvo atual (lâmina inteira, página esquerda ou página direita)."""
        lamina = self._lamina()
        if not lamina:
            return []
        alvo = self.fundo_alvo.get() if hasattr(self, "fundo_alvo") else "lamina"
        espacos = lamina.get("espacos") or []
        if alvo == "esquerda":
            fotos = [
                e["foto"] for e in espacos
                if e.get("foto") and (float(e.get("x", 0.0)) + float(e.get("w", 0.0)) * 0.5) <= 0.52
            ]
            if fotos:
                return fotos
        elif alvo == "direita":
            fotos = [
                e["foto"] for e in espacos
                if e.get("foto") and (float(e.get("x", 0.0)) + float(e.get("w", 0.0)) * 0.5) >= 0.48
            ]
            if fotos:
                return fotos
        return [e["foto"] for e in espacos if e.get("foto")]

    def _atualizar_rotulo_temp_fundos(self):
        if not hasattr(self, "rotulo_temp_fundos"):
            return
        arqs = listar_fundos_temporarios()
        self.rotulo_temp_fundos.configure(
            text="Pasta Temp: %d fundo(s) editável(is)" % len(arqs)
        )

    def abrir_pasta_fundos_temporarios(self):
        """Abre a pasta temporária onde os fundos gerados pelo programa estão salvos como arquivos .png."""
        pasta = pasta_fundos_temporarios()
        try:
            if hasattr(os, "startfile"):
                os.startfile(str(pasta))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(pasta)])
            else:
                subprocess.Popen(["xdg-open", str(pasta)])
        except Exception as e:
            self.registrar("Pasta temporária de fundos: %s (%s)" % (pasta, e))

    def limpar_fundos_temporarios_ui(self):
        """Limpa os arquivos de fundos gerados na pasta temporária sob comando do usuário."""
        removidos, bytes_lib = limpar_fundos_temporarios()
        # Regera na pasta temporária apenas os fundos atualmente aplicados nas lâminas do projeto aberto e na galeria visível
        for lam in self.projeto.get("laminas", []):
            for ch in ("fundo_imagem", "fundo_imagem_esquerda", "fundo_imagem_direita"):
                d_img = lam.get(ch)
                if isinstance(d_img, dict) and isinstance(d_img.get("gerado"), dict):
                    d_img["caminho"] = materializar_fundo_gerado_em_arquivo(d_img["gerado"])
        self._chave_cache_gerados = None
        self._atualizar_galeria_gerados(forcar=True)
        self._atualizar_rotulo_temp_fundos()
        mb = bytes_lib / (1024.0 * 1024.0)
        self.registrar("Pasta temporária de fundos limpa: %d arquivo(s) removido(s) (%.1f MB liberados)." % (removidos, mb))

    def sortear_fundos_gerados(self):
        """Gera novas variações aleatórias de fundos (Preto+Cores e Branco+Cores) baseados nas fotos da página/lâmina."""
        self._seed_fundos_gerados = int(getattr(self, "_seed_fundos_gerados", 1)) + random.randint(7, 97)
        self._gerar_galeria_gerados_agora()
        if getattr(self, "_lista_gerados_atuais", None):
            # Escolhe preferencialmente o primeiro (que segue o padrão detectado Preto+Cores ou Branco+Cores)
            escolhido = self._lista_gerados_atuais[0]
            self.aplicar_fundo_gerado_em(escolhido)

    def _atualizar_galeria_gerados(self, forcar=False):
        """Só com a galeria visível, um pouco depois da última mudança, e calculada fora da janela."""
        if not hasattr(self, "galeria_fundos_gerados"):
            return
        if forcar:
            self._chave_cache_gerados = None
        if self._painel_na_tela("gerados"):
            self.agendar("galeria_gerados", 300, self._calcular_galeria_gerados)

    def _parametros_galeria_gerados(self):
        alvo = self.fundo_alvo.get() if hasattr(self, "fundo_alvo") else "lamina"
        fotos_alvo = self._fotos_alvo_fundo_atual()
        estilo = self.estilo_gerador_fundo.get() if hasattr(self, "estilo_gerador_fundo") else "ambos"
        padrao_f = self.padrao_gerador_fundo.get() if hasattr(self, "padrao_gerador_fundo") else "ambos"
        seed_atual = int(getattr(self, "_seed_fundos_gerados", 1))
        chave = (self.atual, alvo, tuple(fotos_alvo), estilo, padrao_f, seed_atual)
        seed_base = seed_atual + (self.atual + 1 if isinstance(self.atual, int) else 0) * 19
        return chave, alvo, fotos_alvo, estilo, padrao_f, seed_base

    @staticmethod
    def _gerar_lista_gerados(fotos_alvo, info, estilo, padrao_f, seed_base):
        paleta = extrair_paleta_fotos(fotos_alvo, info)
        gerados = gerar_galeria_fundos_procedurais(
            paleta,
            estilo=estilo,
            seed_base=seed_base,
            quantidade=8,
            padrao_filtro=padrao_f,
            materializar=False,
        )
        return paleta, gerados

    def _calcular_galeria_gerados(self):
        chave, alvo, fotos_alvo, estilo, padrao_f, seed_base = self._parametros_galeria_gerados()
        if chave == getattr(self, "_chave_cache_gerados", None):
            self.galeria_fundos_gerados._agendar()
            self._atualizar_rotulo_temp_fundos()
            return
        self._chave_cache_gerados = chave
        self._versao_gerados = getattr(self, "_versao_gerados", 0) + 1
        versao, info = self._versao_gerados, self.info

        def trabalho():
            try:
                paleta, gerados = self._gerar_lista_gerados(fotos_alvo, info, estilo, padrao_f, seed_base)
            except Exception:
                self.fila.put(("chamar", lambda: setattr(self, "_chave_cache_gerados", None)))
                raise
            self.fila.put(("chamar", lambda: self._receber_galeria_gerados(versao, alvo, fotos_alvo, paleta, gerados)))

        threading.Thread(target=trabalho, daemon=True).start()

    def _gerar_galeria_gerados_agora(self):
        chave, alvo, fotos_alvo, estilo, padrao_f, seed_base = self._parametros_galeria_gerados()
        paleta, gerados = self._gerar_lista_gerados(fotos_alvo, self.info, estilo, padrao_f, seed_base)
        self._chave_cache_gerados = chave
        self._versao_gerados = getattr(self, "_versao_gerados", 0) + 1
        self._receber_galeria_gerados(self._versao_gerados, alvo, fotos_alvo, paleta, gerados)

    def _receber_galeria_gerados(self, versao, alvo, fotos_alvo, paleta, gerados):
        if versao != getattr(self, "_versao_gerados", 0) or not hasattr(self, "galeria_fundos_gerados"):
            return
        paleta_mudou = paleta != getattr(self, "_paleta_fundo_atual", None)
        self._paleta_fundo_atual = paleta
        if paleta_mudou and getattr(self, "filtro_sugeridos_pasta", None) and self.filtro_sugeridos_pasta.get():
            self._agendar_fundos_pasta()
        pad_det = "Preto + Cores" if paleta.get("padrao_sugerido") == "preto" else "Branco + Cores"
        if hasattr(self, "rotulo_escopo_gerador"):
            txt_alvo = {
                "lamina": "Lâmina (%d foto%s)" % (len(fotos_alvo), "s" if len(fotos_alvo) != 1 else ""),
                "esquerda": "Pág. Esq. (%d foto%s)" % (len(fotos_alvo), "s" if len(fotos_alvo) != 1 else ""),
                "direita": "Pág. Dir. (%d foto%s)" % (len(fotos_alvo), "s" if len(fotos_alvo) != 1 else ""),
            }.get(alvo, "Lâmina")
            self.rotulo_escopo_gerador.configure(text="%s · Tom detectado: %s" % (txt_alvo, pad_det))
        self._lista_gerados_atuais = gerados
        self.galeria_fundos_gerados.definir(gerados)
        self._atualizar_rotulo_temp_fundos()

    def _galeria_gerados_pressionou(self, caminho, evento):
        dados_g = self.galeria_fundos_gerados.obter_dados_gerado(caminho)
        if dados_g:
            self.aplicar_fundo_gerado_em(dados_g)
            self._arrasto = {"tipo": "fundo", "caminho": dados_g.get("caminho") or caminho, "x": evento.x_root, "y": evento.y_root, "ativo": False}

    def _galeria_gerados_menu(self, caminho, evento):
        dados_g = self.galeria_fundos_gerados.obter_dados_gerado(caminho)
        if not dados_g:
            return
        qtd_sel = len(self._indices_laminas_selecionadas())
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(
            label="Aplicar no alvo atual (%s)" % self.fundo_alvo.get(),
            command=lambda: self.aplicar_fundo_gerado_em(dados_g),
        )
        menu.add_command(
            label="Aplicar na Lâmina inteira",
            command=lambda: (self.fundo_alvo.set("lamina"), self.aplicar_fundo_gerado_em(dados_g, alvo="lamina")),
        )
        menu.add_command(
            label="Aplicar na Página Esquerda",
            command=lambda: (self.fundo_alvo.set("esquerda"), self.aplicar_fundo_gerado_em(dados_g, alvo="esquerda")),
        )
        menu.add_command(
            label="Aplicar na Página Direita",
            command=lambda: (self.fundo_alvo.set("direita"), self.aplicar_fundo_gerado_em(dados_g, alvo="direita")),
        )
        menu.add_separator()
        menu.add_command(
            label="Aplicar nas %d lâmina(s) selecionada(s)" % max(1, qtd_sel),
            command=lambda: self.aplicar_fundo_em_selecionadas(dados_g),
        )
        menu.add_command(
            label="Aplicar em todas as lâminas",
            command=lambda: self.aplicar_fundo_em_todas(dados_g),
        )
        menu.add_command(
            label="Ajustar cor / Tingir...",
            command=lambda: (self.aplicar_fundo_gerado_em(dados_g), self.abrir_editor_fundo()),
        )
        menu.add_separator()
        menu.add_command(
            label="Abrir pasta temporária de fundos",
            command=self.abrir_pasta_fundos_temporarios,
        )
        menu.add_command(
            label="Limpar fundos temporários...",
            command=self.limpar_fundos_temporarios_ui,
        )
        menu.tk_popup(evento.x_root, evento.y_root)

    def executar_auto_fundos(self, apenas_atual=False):
        """Executa a Função 1 (Motor de Fundos) de forma independente."""
        laminas = [self._lamina()] if apenas_atual and self._lamina() else self.projeto.get("laminas", [])
        if not laminas:
            return
        self.marcar_historico()
        extra = {
            "estilo_fundo_gerado": (
                self.estilo_gerador_fundo.get() if hasattr(self, "estilo_gerador_fundo")
                else self._cfg().get("estilo_fundo_gerado", "ambos")
            ),
            "_seed_auto_fundos": random.randint(1, 900000),
        }
        alvo = self.fundo_alvo.get() if hasattr(self, "fundo_alvo") else "lamina"
        if apenas_atual and alvo in ("esquerda", "direita"):
            extra["escopo_auto_fundos"] = "pagina"
        # Fundo novo -> degradê de fusão e cor das molduras se ajustam a ele (o que foi feito à mão fica)
        self._acabamento(laminas, forcar=("fundos",), cfg_extra=extra)
        self.atualizar_tudo()
        self.registrar(
            "Função 1 (Fundos Automáticos) aplicada %s."
            % ("nesta lâmina" if apenas_atual else "em todas as lâminas do álbum")
        )

    def executar_auto_degrade(self, apenas_atual=False):
        """Executa a Função 2 (Motor de Degradê Automático) de forma independente."""
        laminas = [self._lamina()] if apenas_atual and self._lamina() else self.projeto.get("laminas", [])
        if not laminas:
            return
        self.marcar_historico()
        self._acabamento(laminas, forcar=("degrade",))
        self._atualizar_degrade_ui()
        self.atualizar_tudo()
        self.registrar(
            "Função 2 (Degradê Automático) aplicada %s."
            % ("nesta lâmina" if apenas_atual else "em todas as lâminas do álbum")
        )

    def executar_auto_molduras(self, apenas_atual=False):
        """Executa a Função 3 (Motor de Molduras Automáticas) de forma independente."""
        laminas = [self._lamina()] if apenas_atual and self._lamina() else self.projeto.get("laminas", [])
        if not laminas:
            return
        self.marcar_historico()
        # Estilo e cor vêm da configuração do álbum (padrão: por evento); o painel só define a espessura.
        extra = {}
        if hasattr(self, "moldura_espessura"):
            try:
                extra["espessura_auto_molduras_mm"] = float(self.moldura_espessura.get() or 0.5)
            except (tk.TclError, ValueError):
                pass
        if hasattr(self, "moldura_sinc_sombra") and self.moldura_sinc_sombra.get():
            extra["auto_sombra_molduras"] = True
            try:
                sombra_painel = self._sombra_atual_dos_controles()
            except (AttributeError, tk.TclError, ValueError):
                sombra_painel = None
            # Sombra ligada no painel vale para todas; senão cada evento usa a sua
            if sombra_painel and sombra_painel.get("ativa"):
                extra["cfg_sombra_molduras"] = sombra_painel
        self._acabamento(laminas, forcar=("molduras",), cfg_extra=extra)
        self._atualizar_moldura_ui()
        self._atualizar_sombra_ui()
        self.atualizar_tudo()
        self.registrar(
            "Função 3 (Molduras Automáticas) aplicada %s."
            % ("nesta lâmina" if apenas_atual else "em todas as lâminas do álbum")
        )

    def atualizar_fundos(self, forcar=False):
        """Lê a pasta de fundos (a do álbum ou a padrão) e atualiza a galeria se algo mudou."""
        if not hasattr(self, "galeria_fundos"):
            return
        pasta = self._pasta_fundos()
        try:
            marca = Path(pasta).stat().st_mtime_ns if pasta else None
        except OSError:
            marca = None
        if not forcar and pasta == self._fundos_pasta and marca == getattr(self, "_fundos_marca", None):
            lista_bruta = list(self._fundos_lista)
        else:
            lista_bruta = fundos.listar_fundos(pasta)
        self._fundos_marca = marca
        self._fundos_pasta = pasta
        self._fundos_lista = lista_bruta
        lista_exibir = list(lista_bruta)
        if lista_exibir and getattr(self, "filtro_sugeridos_pasta", None) and self.filtro_sugeridos_pasta.get():
            paleta = getattr(self, "_paleta_fundo_atual", None) or extrair_paleta_fotos(self._fotos_alvo_fundo_atual(), self.info)
            sugeridos = fundos.sugerir_fundos_da_pasta(
                ARQUIVO_FUNDOS, lista_exibir, paleta_fotos=paleta, limite=len(lista_exibir)
            )
            if sugeridos:
                lista_exibir = sugeridos
        self.galeria_fundos.definir(lista_exibir)
        if not pasta:
            texto = "Nenhuma pasta escolhida."
        elif not Path(pasta).is_dir():
            texto = "Pasta não encontrada: %s" % pasta
        else:
            qtd_fav = sum(1 for c in lista_bruta if fundos.eh_favorito_fundo(ARQUIVO_FUNDOS, c))
            extra_fav = (" · ★ %d padrão" % qtd_fav) if qtd_fav else ""
            texto = "%s · %s imagem(ns)%s%s" % (
                Path(pasta).name,
                len(lista_bruta),
                extra_fav,
                " · deste álbum" if self.projeto.get("pasta_fundos") else "",
            )
        self.rotulo_pasta_fundos.configure(text=texto)

    def _fundo_atual_normalizado(self):
        dados = self._dados_fundo_imagem_ativo() or {}
        cam = dados.get("caminho")
        if not cam:
            return None
        if str(cam).startswith("__gerado__:"):
            return str(cam)
        return normalizar_caminho(cam)

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

    def _alternar_favorito_pasta(self, caminho):
        marcado = fundos.alternar_favorito_fundo(ARQUIVO_FUNDOS, caminho)
        self.atualizar_fundos(forcar=True)
        self.registrar(
            "%s %s como Fundo Padrão ★."
            % (Path(caminho).name, "marcado" if marcado else "removido")
        )

    def _galeria_menu(self, caminho, evento):
        qtd_sel = len(self._indices_laminas_selecionadas())
        eh_fav = fundos.eh_favorito_fundo(ARQUIVO_FUNDOS, caminho)
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(
            label="★ Remover de Fundo Padrão" if eh_fav else "★ Marcar como Fundo Padrão (Favorito)",
            command=lambda: self._alternar_favorito_pasta(caminho),
        )
        menu.add_separator()
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
                if eh_fundo_gerado(base):
                    if isinstance(base.get("gerado"), dict) and (not base.get("caminho") or str(base["caminho"]).startswith("__gerado__:") or not Path(base["caminho"]).is_file()):
                        base["caminho"] = materializar_fundo_gerado_em_arquivo(base["gerado"])
                        dados["caminho"] = base["caminho"]
                    nome_g = base.get("nome_exibicao") or (base.get("gerado") or {}).get("titulo") or "Fundo Gerado pelas Fotos"
                    arq_t = Path(str(base.get("caminho") or "")).name
                    self.rotulo_fundo_imagem.configure(text="✨ %s (%s)" % (nome_g, arq_t))
                else:
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
        self._atualizar_galeria_gerados(forcar=False)
        self._agendar_fundos_pasta()
        if self.editor_fundo is not None:
            self.editor_fundo.mostrar()

    def _agendar_fundos_pasta(self):
        """A galeria da pasta só é relida/reordenada quando aparece na tela."""
        if not hasattr(self, "galeria_fundos") or not self._painel_na_tela("fundos_pasta"):
            return
        if getattr(self, "filtro_sugeridos_pasta", None) and self.filtro_sugeridos_pasta.get():
            self.agendar("fundos_pasta", 300, lambda: self.atualizar_fundos(forcar=False))
        else:
            self.galeria_fundos._agendar()

    def _avisar_fundo_faltando(self):
        dono = self._dono_do_fundo()
        if not dono:
            return
        for ch_img in ("fundo_imagem", "fundo_imagem_esquerda", "fundo_imagem_direita"):
            dados = dono.get(ch_img)
            if not dados or not dados.get("caminho") or eh_fundo_gerado(dados):
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
    # Melhoria Inteligente em 1 Clique e Edição em Lote (Luz, Cor e Saturação)
    # ==================================================================

    def _copiar_chaves_luz_cor(self, origem, destino):
        """Copia apenas os parâmetros de luz, cor, saturação, nitidez e P&B para `destino`, preservando corte/giro/máscara."""
        chaves_luz_cor = set(recorte.PADRAO_EDICAO.keys()) - {"giro", "espelhar", "ignorar_rosto"}
        for ch in chaves_luz_cor:
            val_padrao = recorte.PADRAO_EDICAO[ch]
            val = origem.get(ch, val_padrao)
            if isinstance(val_padrao, bool):
                if bool(val) != val_padrao:
                    destino[ch] = bool(val)
                else:
                    destino.pop(ch, None)
            else:
                if abs(float(val) - float(val_padrao)) > 1e-4:
                    destino[ch] = round(float(val), 2)
                else:
                    destino.pop(ch, None)
        if destino.get("ia_lut_forca") and origem.get("ia_lut"):
            destino["ia_lut"] = [float(p) for p in origem["ia_lut"][:3]]
        else:
            destino.pop("ia_lut", None)
        return destino

    def salvar_edicao_padrao_foto(self, caminho, edicao):
        """Salva os ajustes de luz/cor como padrão da foto no projeto para que qualquer uso reflita a melhoria."""
        reg = proj.registro_foto(self.projeto, caminho)
        if reg is not None:
            ed_p = dict(reg.get("edicao_padrao") or {})
            self._copiar_chaves_luz_cor(edicao or {}, ed_p)
            if ed_p:
                reg["edicao_padrao"] = ed_p
            else:
                reg.pop("edicao_padrao", None)

    def obter_edicao_foto(self, caminho):
        """Retorna um dicionário com a edição atual de uma foto (priorizando a lâmina atual, ou a edição padrão do projeto)."""
        ch = normalizar_caminho(caminho)
        lam = self._lamina()
        if lam:
            for esp in lam.get("espacos", []):
                if esp.get("foto") and normalizar_caminho(esp["foto"]) == ch:
                    return dict(esp.get("edicao") or {})
        for l in self.projeto.get("laminas", []):
            for esp in l.get("espacos", []):
                if esp.get("foto") and normalizar_caminho(esp["foto"]) == ch:
                    return dict(esp.get("edicao") or {})
        reg = proj.registro_foto(self.projeto, caminho)
        if reg and reg.get("edicao_padrao"):
            return dict(reg.get("edicao_padrao") or {})
        return {}

    def zerar_ajustes_luz_cor_foto(self, caminho):
        """Remove ajustes não-destrutivos de luz/cor de todas as instâncias de `caminho` (mantendo corte/giro/máscara)."""
        ch = normalizar_caminho(caminho)
        chaves_limpar = (set(recorte.PADRAO_EDICAO.keys()) | {"ia_lut"}) - {"giro", "espelhar", "ignorar_rosto"}
        for lam in self.projeto.get("laminas", []):
            for esp in lam.get("espacos", []):
                if esp.get("foto") and normalizar_caminho(esp["foto"]) == ch:
                    ed = esp.get("edicao")
                    if isinstance(ed, dict):
                        for k in chaves_limpar:
                            ed.pop(k, None)
        reg = proj.registro_foto(self.projeto, caminho)
        if reg and isinstance(reg.get("edicao_padrao"), dict):
            for k in chaves_limpar:
                reg["edicao_padrao"].pop(k, None)
            if not reg["edicao_padrao"]:
                reg.pop("edicao_padrao", None)
        recorte.limpar_cache_espaco_rapido(caminho)

    def _resolver_fotos_lote(self, fotos_alvo=None, escopo="auto"):
        """Resolve a lista de caminhos de fotos para operações de 1-Clique e Lote conforme o escopo escolhido."""
        if fotos_alvo:
            return list(dict.fromkeys(str(c) for c in fotos_alvo if c))
        if escopo == "lamina":
            lam = self._lamina()
            if not lam:
                return []
            return list(dict.fromkeys(e["foto"] for e in lam.get("espacos", []) if e.get("foto")))
        if escopo == "album":
            usadas = [
                e["foto"]
                for l in self.projeto.get("laminas", [])
                for e in l.get("espacos", [])
                if e.get("foto")
            ]
            todas = [f["caminho"] for f in self.projeto.get("fotos", []) if f.get("caminho")]
            return list(dict.fromkeys(usadas + todas))
        # escopo "auto" ou "selecionadas"
        lam = self._lamina()
        if lam:
            sel_esp = [
                lam["espacos"][i].get("foto")
                for i in self._indices_espacos_selecionados()
                if 0 <= i < len(lam.get("espacos", [])) and lam["espacos"][i].get("foto")
            ]
            if sel_esp:
                return list(dict.fromkeys(sel_esp))
        if self.fotos_sel:
            sel_tira = [
                f["caminho"]
                for f in self.projeto.get("fotos", [])
                if normalizar_caminho(f["caminho"]) in self.fotos_sel
            ]
            if sel_tira:
                return list(dict.fromkeys(sel_tira))
        if lam:
            fotos_lam = [e["foto"] for e in lam.get("espacos", []) if e.get("foto")]
            if fotos_lam:
                return list(dict.fromkeys(fotos_lam))
        return list(dict.fromkeys(f["caminho"] for f in self.projeto.get("fotos", []) if f.get("caminho")))

    def aplicar_ajustes_manuais_em_lote(self, edicao_origem, escopo="lamina", janela_pai=None):
        """Copia os ajustes de luz, cor e saturação de `edicao_origem` para todas as fotos da lâmina ou do álbum."""
        fotos = self._resolver_fotos_lote(escopo=escopo)
        if not fotos:
            messagebox.showinfo(
                "Sem Fotos",
                "Nenhuma foto encontrada no escopo selecionado.",
                parent=janela_pai or self.root,
            )
            return
        self.marcar_historico()
        chaves_alvo = {normalizar_caminho(c) for c in fotos}
        if escopo == "lamina":
            lam = self._lamina()
            laminas_alvo = [lam] if lam else []
        else:
            laminas_alvo = self.projeto.get("laminas", [])
        for lam in laminas_alvo:
            for esp in lam.get("espacos", []):
                cam = esp.get("foto")
                if cam and normalizar_caminho(cam) in chaves_alvo:
                    ed_dest = esp.setdefault("edicao", {})
                    self._copiar_chaves_luz_cor(edicao_origem, ed_dest)
        for cam in fotos:
            self.salvar_edicao_padrao_foto(cam, edicao_origem)
            recorte.limpar_cache_espaco_rapido(cam)
        self._cache_laminas.clear()
        self._marcar_modificado()
        self.atualizar_tudo()
        nome_escopo = "na lâmina atual" if escopo == "lamina" else "no álbum inteiro"
        self.registrar("Ajustes de luz, cor e saturação aplicados em %d foto(s) %s (Ctrl+Z desfaz)." % (len(fotos), nome_escopo))
        return len(fotos)

    def restaurar_luz_cor_lote(self, fotos_alvo=None, escopo="auto", janela_pai=None):
        """Restaura luz, cor e saturação originais nas fotos do escopo escolhido com suporte a Ctrl+Z."""
        fotos = self._resolver_fotos_lote(fotos_alvo=fotos_alvo, escopo=escopo)
        if not fotos:
            self.registrar("Nenhuma foto selecionada para restaurar.")
            return 0
        self.marcar_historico()
        for cam in fotos:
            self.zerar_ajustes_luz_cor_foto(cam)
        self._cache_laminas.clear()
        self._marcar_modificado()
        self.atualizar_tudo()
        self.registrar("Luz, cor e saturação originais restauradas em %d foto(s) (Ctrl+Z desfaz)." % len(fotos))
        return len(fotos)

    def aplicar_lote_inteligente(
        self,
        fotos_alvo=None,
        escopo="auto",
        nivel="medio",
        extras=None,
        janela_pai=None,
        silencioso=False,
        usar_ia=False,
    ):
        """
        Analisa fotometricamente cada foto do escopo e aplica melhoria inteligente 1-Clique
        (Leve, Médio, Pesado ou Manual) + ajustes finos extras de luz, cor e saturação,
        com suporte total a Ctrl+Z. usar_ia=True = Auto IA (3D LUT + cena), sempre numa thread.
        """
        if usar_ia and nivel in recorte.FORCA_IA_NIVEL:
            return self._aplicar_lote_ia(fotos_alvo, escopo, nivel, extras, janela_pai, silencioso)
        fotos = self._resolver_fotos_lote(fotos_alvo=fotos_alvo, escopo=escopo)
        if not fotos:
            if not silencioso:
                messagebox.showinfo(
                    "Sem Fotos",
                    "Selecione uma foto, lâmina ou carregue fotos no projeto primeiro.",
                    parent=janela_pai or self.root,
                )
            return 0
        extras = dict(extras or {})
        nomes_nivel = {
            "leve": "1-Clique LEVE",
            "medio": "1-Clique MÉDIO",
            "pesado": "1-Clique PESADO (Recuperar Foto Ruim)",
            "manual": "Ajuste Manual em Lote",
            "original": "Restaurar Original",
        }

        if nivel == "original":
            return self.restaurar_luz_cor_lote(fotos_alvo=fotos, escopo=escopo, janela_pai=janela_pai)

        def _aplicar_resultado_edicoes(mapa_edicoes):
            self.marcar_historico()
            somente_lamina = (escopo == "lamina")
            laminas_iter = [self._lamina()] if somente_lamina and self._lamina() else self.projeto.get("laminas", [])
            for lam in laminas_iter:
                if not lam:
                    continue
                for esp in lam.get("espacos", []):
                    cam = esp.get("foto")
                    if not cam:
                        continue
                    ch = normalizar_caminho(cam)
                    if ch in mapa_edicoes:
                        ed_dest = esp.setdefault("edicao", {})
                        self._copiar_chaves_luz_cor(mapa_edicoes[ch], ed_dest)
            for cam in fotos:
                ch = normalizar_caminho(cam)
                if ch in mapa_edicoes:
                    self.salvar_edicao_padrao_foto(cam, mapa_edicoes[ch])
                    recorte.limpar_cache_espaco_rapido(cam)
            self._cache_laminas.clear()
            self._marcar_modificado()
            self.atualizar_tudo()
            rotulo_n = nomes_nivel.get(nivel, str(nivel).capitalize())
            self.registrar(
                "✨ %s aplicado em %d foto(s)! Use Ctrl+Z se quiser desfazer."
                % (rotulo_n, len(mapa_edicoes))
            )

        # Para até 8 fotos, executa instantaneamente na thread principal (com cache fotométrico)
        if len(fotos) <= 8:
            mapa = {}
            for cam in fotos:
                ch = normalizar_caminho(cam)
                base_ed = self.obter_edicao_foto(cam)
                ed_calc = recorte.calcular_ajuste_inteligente(
                    cam,
                    nivel=nivel,
                    extras=extras,
                    base_edicao=base_ed,
                )
                if "pb" in extras:
                    ed_calc["pb"] = bool(extras["pb"])
                mapa[ch] = ed_calc
            _aplicar_resultado_edicoes(mapa)
            return len(fotos)

        # Para lotes maiores, executa com barra de progresso
        bases_iniciais = {normalizar_caminho(c): self.obter_edicao_foto(c) for c in fotos}

        def trabalho(t):
            mapa = {}
            total = len(fotos)
            for idx, cam in enumerate(fotos, 1):
                if t.parar():
                    break
                ch = normalizar_caminho(cam)
                t.aviso("Analisando luz e cor (%d/%d): %s" % (idx, total, Path(cam).name))
                ed_calc = recorte.calcular_ajuste_inteligente(
                    cam,
                    nivel=nivel,
                    extras=extras,
                    base_edicao=bases_iniciais.get(ch, {}),
                )
                if "pb" in extras:
                    ed_calc["pb"] = bool(extras["pb"])
                mapa[ch] = ed_calc
                t.progresso(idx, total)
            return mapa

        def fim(resultado, erro):
            if erro or not resultado:
                return
            _aplicar_resultado_edicoes(resultado)

        self.executar("Aplicando %s em %d fotos" % (nomes_nivel.get(nivel, "Melhoria"), len(fotos)), trabalho, fim)
        return len(fotos)

    def calcular_edicoes_ia(self, fotos, nivel, extras, bases, t=None):
        """{caminho normalizado: edição} do Auto IA (roda numa thread). Sem modelo, a foto fica com o 1-Clique por regras."""
        cfg = ia_modelos.config()
        mapa, sem_ia = {}, 0
        total = len(fotos)
        for idx, cam in enumerate(fotos, 1):
            if t is not None and t.parar():
                break
            ch = normalizar_caminho(cam)
            if t is not None:
                t.aviso("Auto IA (%d/%d): %s" % (idx, total, Path(cam).name))
            ia = None
            try:
                dados = analise.analisar_ia(cam, cena=bool(cfg.get("reconhecer_cenas")), lut=True)
                self.fila.put(("ia_pronta", cam, dados))
                if dados.get("ia_lut"):
                    ia = {"pesos": dados["ia_lut"], "rosto": dados.get("rosto")}
                    if cfg.get("reconhecer_cenas"):
                        ia["cena"] = dados.get("cena")
            except Exception:
                registrar_erro("Auto IA: %s" % cam, eco=False)
            if ia is None:
                sem_ia += 1
            ed_calc = recorte.calcular_ajuste_inteligente(cam, nivel=nivel, extras=extras, base_edicao=bases.get(ch, {}), ia=ia)
            if "pb" in (extras or {}):
                ed_calc["pb"] = bool(extras["pb"])
            mapa[ch] = ed_calc
            if t is not None:
                t.progresso(idx, total)
        analise.gravar_cache()
        return mapa, sem_ia

    def _aplicar_lote_ia(self, fotos_alvo, escopo, nivel, extras, janela_pai, silencioso):
        fotos = self._resolver_fotos_lote(fotos_alvo=fotos_alvo, escopo=escopo)
        if not fotos:
            if not silencioso:
                messagebox.showinfo("Sem Fotos", "Selecione uma foto, lâmina ou carregue fotos no projeto primeiro.",
                                    parent=janela_pai or self.root)
            return 0
        if not ia_modelos.config().get("edicao_ia"):
            messagebox.showinfo("Auto IA", "A edição automática por IA está desligada em Configurações.", parent=janela_pai or self.root)
            return 0
        extras = dict(extras or {})
        bases = {normalizar_caminho(c): self.obter_edicao_foto(c) for c in fotos}
        somente_lamina = (escopo == "lamina")

        def trabalho(t):
            return self.calcular_edicoes_ia(fotos, nivel, extras, bases, t)

        def fim(resultado, erro):
            if erro or not resultado:
                return
            mapa, sem_ia = resultado
            self.marcar_historico()
            laminas_iter = [self._lamina()] if somente_lamina and self._lamina() else self.projeto.get("laminas", [])
            for lam in laminas_iter:
                for esp in (lam or {}).get("espacos", []):
                    cam = esp.get("foto")
                    if cam and normalizar_caminho(cam) in mapa:
                        self._copiar_chaves_luz_cor(mapa[normalizar_caminho(cam)], esp.setdefault("edicao", {}))
            for cam in fotos:
                ch = normalizar_caminho(cam)
                if ch in mapa:
                    self.salvar_edicao_padrao_foto(cam, mapa[ch])
                    recorte.limpar_cache_espaco_rapido(cam)
            self._cache_laminas.clear()
            self._marcar_modificado()
            self.atualizar_tudo()
            nome_n = {"leve": "Leve", "medio": "Médio", "pesado": "Pesado"}.get(nivel, nivel)
            texto = "🤖 Auto IA %s aplicado em %d foto(s)! Use Ctrl+Z se quiser desfazer." % (nome_n, len(mapa))
            if sem_ia:
                texto += " (%d sem modelo de IA ficaram com o 1-Clique por regras)" % sem_ia
            self.registrar(texto)
            if self.editor is not None and self.editor.espaco:
                self.editor.recarregar()

        if not self.executar("Auto IA %s em %d foto(s)" % (nivel, len(fotos)), trabalho, fim, janela=janela_pai):
            return 0
        return len(fotos)

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
    # IA local (cena e edição automática) em segundo plano
    # ==================================================================

    def _ia_iniciar(self):
        self._ia_pendentes = OrderedDict()
        self._ia_trava = threading.Lock()
        self._ia_trabalhadores = 0
        self._ia_em_andamento = set()
        self._aplicar_cfg_ia()
        threading.Thread(target=self._ia_aquecer, daemon=True, name="ia_estado").start()

    @staticmethod
    def _ia_aquecer():
        try:
            ia_modelos.estado_modelos()
        except Exception:
            registrar_erro("estado dos modelos de IA", eco=False)

    def ia_cfg(self):
        return ia_modelos.config()

    def _aplicar_cfg_ia(self):
        cfg = ia_modelos.config()
        diagramar.USAR_CENA_TEMA = bool(cfg.get("reconhecer_cenas") and cfg.get("cena_no_tema"))

    def _agendar_ia_fundo(self, caminhos, primeiro=False):
        """Fila de baixa prioridade: cena e pesos da IA de cada foto, depois da análise normal, 1–2 threads."""
        cfg = ia_modelos.config()
        if cfg.get("modo_leve") or not (cfg.get("reconhecer_cenas") or cfg.get("edicao_ia")) or not caminhos:
            return
        with self._ia_trava:
            for c in caminhos:
                ch = normalizar_caminho(c)
                self._ia_pendentes[ch] = str(c)
                if primeiro:
                    self._ia_pendentes.move_to_end(ch, last=False)
            limite = min(2, max(1, (os.cpu_count() or 2) // 4))
            novos = max(0, limite - self._ia_trabalhadores)
            self._ia_trabalhadores += novos
        for _ in range(novos):
            threading.Thread(target=self._ia_trabalhador, daemon=True, name="ia_fundo").start()

    def _ia_trabalhador(self):
        _baixar_prioridade_thread()
        feitas = 0
        try:
            while True:
                with self._ia_trava:
                    if not self._ia_pendentes:
                        break
                    ch, caminho = self._ia_pendentes.popitem(last=False)
                while self.tarefa is not None:
                    time.sleep(0.4)
                cfg = ia_modelos.config()
                cena, lut = bool(cfg.get("reconhecer_cenas")), bool(cfg.get("edicao_ia"))
                if cfg.get("modo_leve") or not (cena or lut):
                    with self._ia_trava:
                        self._ia_pendentes.clear()
                    break
                try:
                    if not ia_modelos.disponivel("cena") and not ia_modelos.disponivel("edicao"):
                        with self._ia_trava:
                            self._ia_pendentes.clear()
                        break
                    anterior = self.info.get(ch)
                    if anterior is not None and not analise.falta_ia(anterior, cena, lut):
                        continue
                    if not Path(caminho).is_file():
                        continue
                    dados = analise.analisar_ia(caminho, cena=cena, lut=lut)
                    self.fila.put(("ia_pronta", caminho, dados))
                    feitas += 1
                    if feitas % 40 == 0:
                        analise.gravar_cache()
                except Exception:
                    registrar_erro("IA em segundo plano: %s" % caminho, eco=False)
        finally:
            with self._ia_trava:
                self._ia_trabalhadores -= 1
            if feitas:
                analise.gravar_cache()

    def pedir_ia_foto(self, caminho, ao_terminar=None):
        """Cena e pesos da IA de uma foto agora (numa thread); ao_terminar(dados) volta na janela."""
        cfg = ia_modelos.config()

        def trabalho():
            dados = None
            try:
                dados = analise.analisar_ia(caminho, cena=bool(cfg.get("reconhecer_cenas")), lut=True)
                if dados and dados.get("ia_lut"):
                    import ia_edicao
                    ia_edicao.lut_combinada(dados["ia_lut"])
            except Exception:
                registrar_erro("IA da foto %s" % caminho, eco=False)
            finally:
                self._ia_em_andamento.discard(normalizar_caminho(caminho))
            self.fila.put(("ia_pronta", caminho, dados))
            if ao_terminar is not None:
                self.fila.put(("chamar", lambda: ao_terminar(dados)))

        self._ia_em_andamento.add(normalizar_caminho(caminho))
        threading.Thread(target=trabalho, daemon=True, name="ia_foto").start()

    def pedir_ia_se_preciso(self, caminho):
        """Foto aberta no editor sem a análise de IA: calcula só ela (no Modo PC leve é o único momento)."""
        cfg = ia_modelos.config()
        if not caminho or not (cfg.get("reconhecer_cenas") or cfg.get("edicao_ia")):
            return
        ch = normalizar_caminho(caminho)
        dados = self.info.get(ch) or {}
        tem = (dados.get("cena") or not cfg.get("reconhecer_cenas")) and (dados.get("ia_lut") or not cfg.get("edicao_ia"))
        if tem or ch in self._ia_em_andamento:
            return
        if cfg.get("modo_leve"):
            self._ia_em_andamento.add(ch)
            threading.Thread(target=self._ia_uma_leve, args=(caminho, cfg), daemon=True, name="ia_leve").start()
        else:
            self._agendar_ia_fundo([caminho], primeiro=True)

    def _ia_uma_leve(self, caminho, cfg):
        _baixar_prioridade_thread()
        dados = None
        try:
            dados = analise.analisar_ia(caminho, cena=bool(cfg.get("reconhecer_cenas")), lut=bool(cfg.get("edicao_ia")))
        except Exception:
            registrar_erro("IA da foto %s" % caminho, eco=False)
        finally:
            self._ia_em_andamento.discard(normalizar_caminho(caminho))
        if dados is not None:
            self.fila.put(("ia_pronta", caminho, dados))

    def _receber_ia(self, caminho, dados):
        """Resultado da IA chegou: só atualiza os dados, a dica e o diagnóstico (sem redesenhar a lâmina)."""
        if not isinstance(dados, dict):
            return
        ch = normalizar_caminho(caminho)
        if ch in self.info or proj.registro_foto(self.projeto, caminho) is not None:
            self.info[ch] = dados
        if self.editor is not None:
            try:
                self.editor.ia_atualizada(caminho)
            except tk.TclError:
                pass
        dica = getattr(self, "_dica_foto_painel", None)
        if dica is not None and dica.get("chave") == ch:
            self._mostrar_dica_foto_painel(dica["item"], dica["x_root"], dica["y_root"], forcar=True)

    def _texto_dica_foto(self, caminho):
        if not ia_modelos.config().get("reconhecer_cenas"):
            return ""
        dados = self.info.get(normalizar_caminho(caminho)) or {}
        cena = dados.get("cena")
        if not isinstance(cena, dict) or not cena.get("rotulo"):
            return ""
        linhas = [Path(caminho).name, "Cena (IA): %s" % ia_cena.texto_curto(cena)]
        top = ", ".join(str(c[0]) for c in (cena.get("top5") or [])[:3])
        if top:
            linhas.append("Places365: %s" % top)
        linhas.append("Ambiente: %s" % ("externo" if cena.get("io") == "externa" else "interno"))
        return "\n".join(linhas)

    def _fotos_passou(self, evento):
        if not ia_modelos.config().get("reconhecer_cenas"):
            return
        if evento.state & 0x0100 or getattr(self, "_arrasto", None):
            self._esconder_dica_foto_painel()
            return
        item = self._item_painel_fotos_em(evento)
        if not item or item.get("tipo") != "foto":
            self._esconder_dica_foto_painel()
            return
        ch = normalizar_caminho(item["foto"]["caminho"])
        atual = getattr(self, "_dica_foto_painel", None)
        if atual is not None and atual.get("chave") == ch:
            return
        self._esconder_dica_foto_painel()
        self._dica_foto_painel = {"chave": ch, "item": item, "x_root": evento.x_root, "y_root": evento.y_root, "janela": None}
        self.agendar("dica_foto_painel", 450, lambda: self._mostrar_dica_foto_painel(item, evento.x_root, evento.y_root))

    def _mostrar_dica_foto_painel(self, item, x_root, y_root, forcar=False):
        dica = getattr(self, "_dica_foto_painel", None)
        if dica is None or dica.get("item") is not item:
            return
        texto = self._texto_dica_foto(item["foto"]["caminho"])
        if dica.get("janela") is not None:
            if not forcar:
                return
            try:
                dica["janela"].destroy()
            except tk.TclError:
                pass
            dica["janela"] = None
        if not texto:
            return
        jan = tk.Toplevel(self.root)
        jan.overrideredirect(True)
        jan.attributes("-topmost", True)
        borda = tk.Frame(jan, bg=tema.BORDA_CLARA, padx=1, pady=1)
        borda.pack()
        tk.Label(borda, text=texto, bg=tema.PAINEL, fg=tema.TEXTO, padx=8, pady=3, font=tema.FONTE, justify="left").pack()
        jan.geometry("+%d+%d" % (x_root + 14, max(0, y_root - 70)))
        dica["janela"] = jan

    def _esconder_dica_foto_painel(self, _evento=None):
        self.cancelar_agendado("dica_foto_painel")
        dica = getattr(self, "_dica_foto_painel", None)
        self._dica_foto_painel = None
        if dica is not None and dica.get("janela") is not None:
            try:
                dica["janela"].destroy()
            except tk.TclError:
                pass

    def importar_modelos_ia(self, janela_pai=None, ao_terminar=None):
        arquivo = filedialog.askopenfilename(
            parent=janela_pai or self.root,
            title="Importar modelos de IA (.zip)",
            filetypes=[("Pacote de modelos (*.zip)", "*.zip"), ("Todos os arquivos", "*.*")],
        )
        if not arquivo:
            return

        def trabalho():
            try:
                ok, mensagem = ia_modelos.importar_zip(arquivo)
            except Exception as e:
                ok, mensagem = False, str(e)
                registrar_erro("importar modelos de IA", eco=False)

            def fim():
                (messagebox.showinfo if ok else messagebox.showerror)("Modelos de IA", mensagem, parent=janela_pai or self.root)
                self.registrar(mensagem)
                if ok:
                    self._agendar_ia_fundo([f["caminho"] for f in self.projeto.get("fotos", [])])
                if ao_terminar is not None:
                    ao_terminar()

            self.fila.put(("chamar", fim))

        threading.Thread(target=trabalho, daemon=True, name="ia_importar").start()

    # ==================================================================
    # Janelas de tratamento e exportação, programa de capas
    # ==================================================================

    def dica(self, widget, texto):
        return tema.dica(widget, texto)

    def tratar_no_lightroom(self, fotos=None):
        return self.abrir_tratamento(fotos)

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
            messagebox.showerror("Erro ao iniciar Diagramador Inteligente", traceback.format_exc())
        except Exception:
            pass
        raise


if __name__ == "__main__":
    main()
