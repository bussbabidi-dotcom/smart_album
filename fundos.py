# -*- coding: utf-8 -*-
"""
Pasta de fundos: a galeria de imagens de fundo da aba Fundo e a janela de
ajuste de cor do fundo (brilho, contraste, saturação, matiz e tingir com conta-gotas de tela).

Os ajustes nunca mexem no arquivo da imagem: ficam exclusivamente no fundo
daquela lâmina/página (fundo_imagem["ajustes"]) e não passam para outros fundos
ao trocar a imagem, a menos que o usuário clique em Aplicar nas Selecionadas / Todas.
"""

import math
import threading
import tkinter as tk
from collections import OrderedDict
from pathlib import Path
from tkinter import ttk

from PIL import ImageTk

import analise
import tema
from comum import cor_hex, cor_rgb, gravar_json, ler_json, normalizar_caminho
from composicao import (
    AJUSTES_FUNDO_PADRAO,
    eh_fundo_gerado,
    extrair_paleta_fotos,
    gerar_galeria_fundos_procedurais,
    renderizar_fundo_gerado,
)

LIMITE_RECENTES = 8
LARGURA_MINIMA_CELULA = 92
_CACHE_COR_FUNDO_PASTA = OrderedDict()
_TRAVA_COR_FUNDO = threading.Lock()


def ler_config(arquivo):
    dados = ler_json(arquivo, {})
    if not isinstance(dados, dict):
        dados = {}
    dados.setdefault("pasta", "")
    dados["recentes"] = [p for p in dados.get("recentes") or [] if isinstance(p, str)]
    dados["favoritos"] = [normalizar_caminho(p) for p in (dados.get("favoritos") or []) if isinstance(p, str) and p]
    usos_brutos = dados.get("usos") if isinstance(dados.get("usos"), dict) else {}
    dados["usos"] = {
        normalizar_caminho(k): max(0, int(v))
        for k, v in usos_brutos.items()
        if isinstance(k, str) and isinstance(v, (int, float))
    }
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


def registrar_uso_fundo(arquivo, caminho):
    """Incrementa o contador de uso de um fundo da pasta para aprender quais fundos o usuário usa por padrão."""
    if not arquivo or not caminho or eh_fundo_gerado(caminho):
        return
    ch = normalizar_caminho(caminho)
    dados = ler_config(arquivo)
    usos = dados.setdefault("usos", {})
    usos[ch] = int(usos.get(ch) or 0) + 1
    try:
        gravar_json(arquivo, dados)
    except OSError:
        pass


def eh_favorito_fundo(arquivo, caminho):
    if not arquivo or not caminho or eh_fundo_gerado(caminho):
        return False
    ch = normalizar_caminho(caminho)
    return ch in set(ler_config(arquivo).get("favoritos") or [])


def alternar_favorito_fundo(arquivo, caminho):
    """Marca ou desmarca um arquivo da pasta como '★ Fundo Padrão / Favorito' do usuário."""
    if not arquivo or not caminho or eh_fundo_gerado(caminho):
        return False
    ch = normalizar_caminho(caminho)
    dados = ler_config(arquivo)
    favs = list(dados.get("favoritos") or [])
    if ch in favs:
        favs = [f for f in favs if f != ch]
        ativo = False
    else:
        favs.insert(0, ch)
        ativo = True
    dados["favoritos"] = favs
    try:
        gravar_json(arquivo, dados)
    except OSError:
        pass
    return ativo


def _assinatura_cor_fundo_pasta(caminho):
    """Extrai de forma rápida (com cache em RAM) o matiz, saturação e brilho médios de um fundo da pasta."""
    import colorsys

    ch = normalizar_caminho(caminho)
    with _TRAVA_COR_FUNDO:
        guardado = _CACHE_COR_FUNDO_PASTA.get(ch)
        if guardado is not None:
            _CACHE_COR_FUNDO_PASTA.move_to_end(ch)
            return guardado
    try:
        mini = analise.miniatura(caminho, 96).convert("RGB").resize((24, 24))
        pxs = list(mini.getdata())
        r_m = sum(p[0] for p in pxs) / (255.0 * max(1, len(pxs)))
        g_m = sum(p[1] for p in pxs) / (255.0 * max(1, len(pxs)))
        b_m = sum(p[2] for p in pxs) / (255.0 * max(1, len(pxs)))
        h, s, v = colorsys.rgb_to_hsv(r_m, g_m, b_m)
        res = (h * 360.0, s, v)
    except Exception:
        res = (32.0, 0.12, 0.85)
    with _TRAVA_COR_FUNDO:
        _CACHE_COR_FUNDO_PASTA[ch] = res
        while len(_CACHE_COR_FUNDO_PASTA) > 500:
            _CACHE_COR_FUNDO_PASTA.popitem(last=False)
    return res


def sugerir_fundos_da_pasta(arquivo_cfg, caminhos_pasta, paleta_fotos, limite=8):
    """
    Sugere os melhores fundos da pasta do usuário combinando:
    1. O padrão de uso do usuário (fundos marcados como ★ Favorito/Padrão e frequência de uso no histórico)
    2. A harmonia cromática e de tom com as fotos analisadas (da página ou da lâmina inteira).
    Devolve lista ordenada dos caminhos sugeridos (sem aleatoriedade destrutiva).
    """
    lista = [str(c) for c in (caminhos_pasta or []) if c and not eh_fundo_gerado(c)]
    if not lista:
        return []
    cfg_f = ler_config(arquivo_cfg) if arquivo_cfg else {"favoritos": [], "usos": {}}
    favoritos = set(cfg_f.get("favoritos") or [])
    usos = cfg_f.get("usos") or {}

    pal = paleta_fotos or extrair_paleta_fotos([])
    matizes_alvo = pal.get("matizes") or [32.0, 205.0]
    lum_alvo = float(pal.get("luminosidade", 0.54))

    avaliados = []
    # Avalia até 160 arquivos da pasta para resposta instantânea na UI
    amostra_pasta = lista[:160]
    for idx_orig, cam in enumerate(amostra_pasta):
        ch = normalizar_caminho(cam)
        eh_fav = ch in favoritos
        qtd_uso = int(usos.get(ch) or 0)
        h_bg, s_bg, v_bg = _assinatura_cor_fundo_pasta(cam)

        # Harmonia de matiz (fundos neutros suaves também pontuam bem com qualquer foto)
        if s_bg < 0.08:
            harmonia_matiz = 0.82
        else:
            dist_min = min(min(abs(h_bg - m), 360.0 - abs(h_bg - m)) for m in matizes_alvo)
            # Máxima pontuação para tons análogos (0..45°) ou complementares suaves (150..180°)
            if dist_min <= 50.0:
                harmonia_matiz = 1.0 - (dist_min / 90.0)
            elif 145.0 <= dist_min <= 180.0:
                harmonia_matiz = 0.78
            else:
                harmonia_matiz = max(0.25, 1.0 - (dist_min / 180.0))

        # Harmonia de luminosidade (prefere fundos suaves elegantes)
        harmonia_lum = 1.0 - min(0.6, abs(v_bg - max(0.35, min(0.88, lum_alvo * 1.1))) * 0.7)
        score_cor = harmonia_matiz * 0.70 + harmonia_lum * 0.30

        # Peso forte para os fundos que o usuário já usa por padrão
        bonus_padrao = (0.48 if eh_fav else 0.0) + min(0.42, math.log1p(qtd_uso) * 0.16)
        score_total = score_cor * 0.55 + bonus_padrao
        avaliados.append((score_total, eh_fav, qtd_uso, -idx_orig, cam))

    avaliados.sort(reverse=True)
    return [item[4] for item in avaliados[: max(1, int(limite))]]


def listar_fundos(pasta):
    """Imagens da pasta e das subpastas, em ordem natural."""
    if not pasta or not Path(pasta).is_dir():
        return []
    return [str(f) for f in analise.listar_imagens(pasta)]


class Galeria:
    """
    Grade de miniaturas com rolagem. Suporta tanto caminhos de arquivos da Pasta de Fundos
    (exibindo estrela ★ nos fundos padrão/favoritos) quanto Fundos Gerados na Hora pelo
    programa (Degradês e Abstratos baseados nas cores das fotos).
    """

    def __init__(self, pai, atual, ao_pressionar, ao_duplo, ao_menu, altura_canvas=150, texto_vazio=None, eh_favorito=None):
        self.atual = atual
        self.ao_pressionar = ao_pressionar
        self.ao_duplo = ao_duplo
        self.ao_menu = ao_menu
        self.eh_favorito = eh_favorito
        self.texto_vazio = texto_vazio or "Escolha uma pasta com imagens de fundo (botão 'Pasta de fundos...')."
        self.caminhos = []
        self._mapa_gerados = {}
        self.quadro = ttk.Frame(pai)
        barra = ttk.Scrollbar(self.quadro, orient="vertical")
        barra.pack(side="right", fill="y")
        self.canvas = tk.Canvas(
            self.quadro,
            bg=tema.FUNDO,
            highlightthickness=1,
            highlightbackground=tema.BORDA,
            height=altura_canvas,
            yscrollincrement=20,
        )
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

    def definir(self, itens):
        """Aceita lista de caminhos (str) ou lista de dicts `fundo_imagem` gerados pelo programa."""
        novos_caminhos = []
        novo_mapa = {}
        for item in itens or []:
            if isinstance(item, dict) and item.get("caminho"):
                ch = str(item["caminho"])
                novos_caminhos.append(ch)
                novo_mapa[ch] = dict(item)
            elif isinstance(item, str) and item:
                novos_caminhos.append(item)
        self.caminhos = novos_caminhos
        self._mapa_gerados = novo_mapa
        with self._trava:
            self._pendentes.clear()
        self.canvas.yview_moveto(0)
        self.desenhar()

    def dados_gerado_de(self, caminho):
        if not caminho:
            return None
        s = str(caminho)
        if s in self._mapa_gerados:
            return self._mapa_gerados[s]
        ch_n = normalizar_caminho(s)
        for k, v in self._mapa_gerados.items():
            if normalizar_caminho(k) == ch_n:
                return v
        return None

    def obter_dados_gerado(self, caminho):
        return self.dados_gerado_de(caminho)

    def medidas(self):
        """(colunas, largura da célula, altura da célula, altura da miniatura)."""
        largura = max(LARGURA_MINIMA_CELULA * 2, self.canvas.winfo_width())
        colunas = max(2, min(4, largura // LARGURA_MINIMA_CELULA))
        cw = largura // colunas
        th = int(cw * 0.64)
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
            c.create_text(
                10,
                12,
                anchor="nw",
                fill=tema.TEXTO_FRACO,
                width=max(120, c.winfo_width() - 20),
                text=self.texto_vazio,
            )
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
                ch_comp = caminho if str(caminho).startswith("__gerado__:") else normalizar_caminho(caminho)
                atual_comp = atual if (not atual or str(atual).startswith("__gerado__:")) else normalizar_caminho(atual)
                eh_atual = bool(atual_comp and ch_comp == atual_comp)
                if eh_atual:
                    c.create_rectangle(
                        x + 1,
                        y + 1,
                        x + cw - 1,
                        y + ch - 1,
                        fill=tema.DESTAQUE_ESCURO,
                        outline=tema.DESTAQUE,
                        width=2,
                        tags="atual",
                    )
                imagem = self._tk.get((caminho, cw))
                cx, cy = x + cw // 2, y + 4 + th // 2
                if imagem is not None:
                    self._tk.move_to_end((caminho, cw))
                    c.create_image(cx, cy, image=imagem, tags="miniatura")
                else:
                    c.create_rectangle(x + 6, y + 4, x + cw - 6, y + 4 + th, fill=tema.PAINEL_2, outline=tema.BORDA)
                    c.create_text(cx, cy, text="...", fill=tema.TEXTO_FRACO)
                    self._pedir(caminho, cw - 10, th)

                # Estrela dourada quando for fundo padrão/favorito da pasta
                if self.eh_favorito and not eh_fundo_gerado(caminho) and self.eh_favorito(caminho):
                    c.create_rectangle(x + cw - 20, y + 5, x + cw - 6, y + 18, fill="#18181b", outline="#f59e0b")
                    c.create_text(x + cw - 13, y + 11, text="★", fill="#fbbf24", font=tema.FONTE_PEQUENA)

                dados_g = self._mapa_gerados.get(caminho)
                if dados_g:
                    padrao_c = str(dados_g.get("padrao_cor") or (dados_g.get("gerado") or {}).get("padrao_cor") or "").lower()
                    if padrao_c in ("preto", "branco"):
                        bg_tag = "#111318" if padrao_c == "preto" else "#f4f3ef"
                        fg_tag = "#e4e4e7" if padrao_c == "preto" else "#18181b"
                        bd_tag = "#52525b" if padrao_c == "preto" else "#a1a1aa"
                        txt_tag = "PRETO" if padrao_c == "preto" else "BRANCO"
                        c.create_rectangle(x + 7, y + 5, x + 48, y + 18, fill=bg_tag, outline=bd_tag)
                        c.create_text(x + 27, y + 11, text=txt_tag, fill=fg_tag, font=("Segoe UI", 7, "bold"))
                    nome = str(dados_g.get("nome_exibicao") or (dados_g.get("gerado") or {}).get("titulo") or "Gerado")
                else:
                    nome = Path(caminho).name
                c.create_text(
                    cx,
                    y + th + 13,
                    text=nome if len(nome) <= letras else nome[: letras - 1] + "…",
                    fill=tema.TEXTO if eh_atual else tema.TEXTO_FRACO,
                    font=tema.FONTE_PEQUENA,
                )

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
        if caminho and self.ao_pressionar:
            self.ao_pressionar(caminho, evento)

    def _duplo(self, evento):
        caminho = self.caminho_em(evento.x, evento.y)
        if caminho and self.ao_duplo:
            self.ao_duplo(caminho)

    def _menu(self, evento):
        caminho = self.caminho_em(evento.x, evento.y)
        if caminho and self.ao_menu:
            self.ao_menu(caminho, evento)

    def _pedir(self, caminho, largura, altura):
        dados_g = self._mapa_gerados.get(caminho)
        with self._trava:
            self._pendentes.pop(caminho, None)
            self._pendentes[caminho] = (largura, altura, dados_g)
            self._trava.notify()

    def _trabalhar(self):
        while True:
            with self._trava:
                while not self._pendentes:
                    self._trava.wait()
                caminho, (largura, altura, dados_g) = self._pendentes.popitem(last=True)
            try:
                if dados_g and dados_g.get("gerado"):
                    img = renderizar_fundo_gerado(dados_g["gerado"], (max(32, largura), max(24, altura)), rapido=True)
                else:
                    img = analise.miniatura(caminho).copy()
                    img.thumbnail((largura, altura))
            except Exception:
                continue
            with self._trava:
                self._prontas.append((caminho, largura + 10, img))

    def _receber(self):
        try:
            if not self.canvas.winfo_exists():
                return
        except tk.TclError:
            return
        with self._trava:
            prontas, self._prontas = self._prontas, []
        for caminho, cw, img in prontas:
            self._tk[(caminho, cw)] = ImageTk.PhotoImage(img)
            while len(self._tk) > 600:
                self._tk.popitem(last=False)
        if prontas:
            self._agendar()
        try:
            self.canvas.after(25, self._receber)
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

        # Rodapé fixo na base para estar sempre acessível mesmo em janelas redimensionadas menores
        rodape_fixo = tk.Frame(self, bg=tema.CABECALHO, highlightthickness=1, highlightbackground=tema.BORDA, padx=12, pady=8)
        rodape_fixo.pack(side="bottom", fill="x")

        quadro, _container_rol, _canvas_rol = tema.criar_corpo_rolavel(self, padding=12)

        self.vars = {}
        self.rotulos = {}
        self._controles = []

        # Seção 0: Redimensionamento, Giro 90°, Espelhar e Cortar vs Distorcer
        sec_transf = ttk.LabelFrame(quadro, text="Transformação e Redimensionamento", padding=8)
        sec_transf.pack(fill="x", pady=(0, 8))

        linha_modo_tf = ttk.Frame(sec_transf)
        linha_modo_tf.pack(fill="x", pady=(0, 5))
        ttk.Label(linha_modo_tf, text="Encaixe:", width=10).pack(side="left")
        self.var_modo_tf = tk.StringVar(value="preencher")
        rb_cortar = ttk.Radiobutton(
            linha_modo_tf,
            text="Cortar (Sem Distorcer)",
            value="preencher",
            variable=self.var_modo_tf,
            command=self._mudou_modo_tf,
        )
        rb_cortar.pack(side="left", padx=(0, 8))
        rb_distorcer = ttk.Radiobutton(
            linha_modo_tf,
            text="Distorcer (Esticar)",
            value="esticar",
            variable=self.var_modo_tf,
            command=self._mudou_modo_tf,
        )
        rb_distorcer.pack(side="left")
        self._controles.extend([rb_cortar, rb_distorcer])

        linha_bts_tf = ttk.Frame(sec_transf)
        linha_bts_tf.pack(fill="x", pady=(0, 5))
        bt_esp_h = ttk.Button(
            linha_bts_tf, text="⇄ Espelhar H", style="Compacto.TButton", command=lambda: self._alternar_espelho("espelhar_h")
        )
        bt_esp_h.pack(side="left", fill="x", expand=True, padx=(0, 4))
        bt_esp_v = ttk.Button(
            linha_bts_tf, text="⇅ Espelhar V", style="Compacto.TButton", command=lambda: self._alternar_espelho("espelhar_v")
        )
        bt_esp_v.pack(side="left", fill="x", expand=True, padx=(0, 4))
        bt_girar = ttk.Button(
            linha_bts_tf, text="↻ Girar 90°", style="Compacto.TButton", command=self._girar_90
        )
        bt_girar.pack(side="left", fill="x", expand=True)
        self.rotulo_giro = ttk.Label(linha_bts_tf, text="0°", style="Fraco.TLabel", width=5, anchor="e", font=tema.FONTE_MONO)
        self.rotulo_giro.pack(side="right", padx=(6, 0))
        self._controles.extend([bt_esp_h, bt_esp_v, bt_girar])

        self._escala(sec_transf, "escala_pct", "Zoom (%)", 100, 300)

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

        # Rodapé de ações fixo (Zerar, Aplicar nas selecionadas, Aplicar em todas, OK)
        botoes = ttk.Frame(rodape_fixo)
        botoes.pack(fill="x")

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

        ttk.Button(botoes, text="OK", style="PrimarioCompacto.TButton", command=self.fechar).pack(side="right", padx=(0, 10))

        self.mostrar()
        tema.configurar_janela_secundaria(
            self,
            pai=app.root,
            chave="editor_fundo",
            largura=460,
            altura=520,
            min_largura=370,
            min_altura=310,
            centralizar=False,
        )
        lateral = getattr(app, "quadro_lateral", None)
        if lateral is not None and lateral.winfo_ismapped() and lateral.winfo_rootx() > app.canvas.winfo_rootx():
            x = lateral.winfo_rootx() + lateral.winfo_width() - self.winfo_width()
            y = lateral.winfo_rooty() + 24
        else:
            x, y = app.canvas.winfo_rootx() + 20, app.canvas.winfo_rooty() + 20
        wx, wy, ww, wh = tema.area_util_tela(self)
        x = max(wx + 8, min(x, wx + ww - self.winfo_width() - 12))
        y = max(wy + 8, min(y, wy + wh - self.winfo_height() - 36))
        self.geometry("+%d+%d" % (x, y))

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

    def _mudou_modo_tf(self):
        if self._carregando:
            return
        dono, dados = self._dados()
        if not dados:
            return
        self.app.marcar_historico()
        dados["modo"] = self.var_modo_tf.get()
        if hasattr(self.app, "_atualizar_fundo_ui"):
            self.app._atualizar_fundo_ui()
        self.app.fundo_ajustado(final=True)

    def _alternar_espelho(self, chave):
        dono, dados = self._dados()
        if not dados:
            return
        self.app.marcar_historico()
        dados[chave] = not bool(dados.get(chave, False))
        if hasattr(self.app, "_atualizar_fundo_ui"):
            self.app._atualizar_fundo_ui()
        self.mostrar()
        self.app.fundo_ajustado(final=True)

    def _girar_90(self):
        dono, dados = self._dados()
        if not dados:
            return
        self.app.marcar_historico()
        dados["giro"] = (int(dados.get("giro") or 0) + 90) % 360
        if hasattr(self.app, "_atualizar_fundo_ui"):
            self.app._atualizar_fundo_ui()
        self.mostrar()
        self.app.fundo_ajustado(final=True)

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
            modo_atual = (dados or {}).get("modo", "preencher")
            self.var_modo_tf.set("esticar" if modo_atual == "esticar" else "preencher")
            giro_atual = int((dados or {}).get("giro") or 0) % 360
            self.rotulo_giro.configure(text="%d°" % giro_atual)
            for chave, var in self.vars.items():
                if chave == "escala_pct":
                    valor = int(round(float((dados or {}).get("escala", 1.0) or 1.0) * 100))
                elif chave in ("opacidade", "desfoque"):
                    valor = (dados or {}).get(chave, 100 if chave == "opacidade" else 0)
                else:
                    valor = ajustes.get(chave, 0)
                var.set(valor)
                self.rotulos[chave].configure(text="%d" % round(float(valor)))
            cor_t = cor_hex(ajustes["tingir_cor"])
            self.amostra.configure(bg=cor_t)
            self.rotulo_hex.configure(text=cor_t.upper())
            if dados and eh_fundo_gerado(dados):
                nome_g = dados.get("nome_exibicao") or (dados.get("gerado") or {}).get("titulo") or "Fundo Gerado pelas Fotos"
                arq_temp = Path(str(dados.get("caminho") or "")).name if dados.get("caminho") else "temp.png"
                self.rotulo_arquivo.configure(
                    text="Fundo temporário: %s (%s · livre para alterar)" % (nome_g, arq_temp)
                )
            elif dados and dados.get("caminho"):
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
        if chave == "escala_pct":
            nova_esc = max(1.0, min(3.0, valor / 100.0))
            if abs(float(dados.get("escala", 1.0) or 1.0) - nova_esc) < 0.005:
                return
            dados["escala"] = nova_esc
            if hasattr(self.app, "_atualizar_fundo_ui"):
                self.app._atualizar_fundo_ui()
        elif chave in ("opacidade", "desfoque"):
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
        if chave == "escala_pct":
            dados["escala"] = 1.0
        elif chave in ("opacidade", "desfoque"):
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
            dados["escala"] = 1.0
            dados["giro"] = 0
            dados["espelhar_h"] = False
            dados["espelhar_v"] = False
            dados["cx"] = 0.5
            dados["cy"] = 0.5
            if hasattr(self.app, "_atualizar_fundo_ui"):
                self.app._atualizar_fundo_ui()
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
