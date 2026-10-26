# -*- coding: utf-8 -*-
"""
Tela de edição da foto (janela própria, pode ficar aberta ao lado).

Mostra a foto grande com a moldura de corte na proporção do espaço.
Arrastar move o corte, a rodinha dá zoom. Inclui controles completos
estilo Lightroom (Temperatura, Matiz, Exposição, Brilho, Contraste,
Realces, Sombras, Vibração, Saturação, Nitidez, Vinheta e P&B).
Os ajustes ficam no projeto e só são aplicados no álbum; a foto original não é alterada.
"""

import math
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from PIL import Image, ImageTk

import analise
import recorte
import tema
from composicao import rosto_de

LADO_PREVIA_FINAL = 280
ZOOM_MAXIMO = recorte.ZOOM_MAXIMO

CONTROLES_LIGHTROOM = (
    # (chave, rótulo, mínimo, máximo, grupo, dica)
    ("exposicao", "Exposição", -100, 100, "Luz", "Ajuste geral de exposição fotográfica (EV)"),
    ("brilho", "Brilho", -100, 100, "Luz", "Clareia ou escurece os meios-tons"),
    ("contraste", "Contraste", -100, 100, "Luz", "Aumenta ou reduz a separação entre claros e escuros"),
    ("realces", "Realces", -100, 100, "Luz", "Recupera detalhes nas áreas claras ou intensifica os brilhos"),
    ("sombras", "Sombras", -100, 100, "Luz", "Clareia ou aprofunda as áreas escuras sem lavar o preto"),
    ("temperatura", "Temperatura", -100, 100, "Cor", "Balanço de branco: Azul frio (-) <-> Âmbar quente (+)"),
    ("matiz", "Matiz", -100, 100, "Cor", "Tinta do balanço de branco: Verde (-) <-> Magenta (+)"),
    ("vibracao", "Vibração", -100, 100, "Cor", "Saturação inteligente que protege tons de pele e cores já vivas"),
    ("saturacao", "Saturação", -100, 100, "Cor", "Intensidade uniforme de todas as cores da foto"),
    ("nitidez", "Nitidez", 0, 100, "Efeitos", "Realça a definição de bordas e detalhes finos"),
    ("vinheta", "Vinheta", -100, 100, "Efeitos", "Escurece (+) ou clareia (-) suavemente os cantos da foto"),
)


class EditorFoto(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.title("Editar foto · Lightroom & Enquadramento")
        self.configure(bg=tema.FUNDO)
        self.geometry("1220x820")
        self.minsize(920, 600)
        self.protocol("WM_DELETE_WINDOW", self.fechar)
        self.alvo = None
        self.espaco = None
        self.aspecto = 1.0
        self._fonte = None
        self._chave_fonte = None
        self._transformada = None
        self._chave_transformada = None
        self._tk_grande = None
        self._tk_previa = None
        self._escala = 1.0
        self._origem = (0, 0)
        self._arrasto = None
        self._ultima_marca = 0.0
        self._sincronizando = False
        self._agendado = None
        self._montar()
        self.bind("<Left>", lambda e: self.navegar(-1))
        self.bind("<Right>", lambda e: self.navegar(1))
        self.bind("<Escape>", lambda e: self.fechar())
        self.bind("<Control-z>", lambda e: self.app.desfazer())
        self.bind("<Control-y>", lambda e: self.app.refazer())

    # ------------------------------------------------------------
    # Montagem
    # ------------------------------------------------------------

    def _montar(self):
        corpo = ttk.Frame(self, style="Fundo.TFrame")
        corpo.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(corpo, bg=tema.FUNDO, highlightthickness=0, cursor="fleur")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda e: self._agendar_desenho())
        self.canvas.bind("<ButtonPress-1>", self._pressionou)
        self.canvas.bind("<B1-Motion>", self._moveu)
        self.canvas.bind("<ButtonRelease-1>", self._soltou)
        self.canvas.bind("<MouseWheel>", self._roda)

        # Painel lateral direito com rolagem suave para caber todos os controles Lightroom
        container_lado = ttk.Frame(corpo, width=348)
        container_lado.pack(side="right", fill="y")
        container_lado.pack_propagate(False)

        barra_rol = ttk.Scrollbar(container_lado, orient="vertical")
        barra_rol.pack(side="right", fill="y")
        self.canvas_lado = tk.Canvas(container_lado, bg=tema.PAINEL, highlightthickness=0, width=330,
                                     yscrollcommand=barra_rol.set)
        self.canvas_lado.pack(side="left", fill="both", expand=True)
        barra_rol.configure(command=self.canvas_lado.yview)

        lado = ttk.Frame(self.canvas_lado, padding=10)
        self._janela_lado = self.canvas_lado.create_window((0, 0), window=lado, anchor="nw")
        lado.bind("<Configure>", lambda e: self.canvas_lado.configure(scrollregion=self.canvas_lado.bbox("all")))
        self.canvas_lado.bind("<Configure>", lambda e: self.canvas_lado.itemconfigure(self._janela_lado, width=e.width))

        nav = ttk.Frame(lado)
        nav.pack(fill="x", pady=(0, 6))
        ttk.Button(nav, text="◀ Anterior", style="Compacto.TButton", command=lambda: self.navegar(-1)).pack(side="left")
        ttk.Button(nav, text="Fechar", style="Compacto.TButton", command=self.fechar).pack(side="left", expand=True, padx=4)
        ttk.Button(nav, text="Próxima ▶", style="Compacto.TButton", command=lambda: self.navegar(1)).pack(side="right")

        self.rotulo_nome = ttk.Label(lado, text="", style="Titulo.TLabel", wraplength=300)
        self.rotulo_nome.pack(anchor="w")
        self.rotulo_lugar = ttk.Label(lado, text="", style="Fraco.TLabel")
        self.rotulo_lugar.pack(anchor="w", pady=(0, 4))
        self.previa = tk.Canvas(lado, width=LADO_PREVIA_FINAL, height=160, bg=tema.PAINEL_2, highlightthickness=0)
        self.previa.pack(pady=(0, 6))

        corte = ttk.LabelFrame(lado, text="Corte e Enquadramento", padding=6)
        corte.pack(fill="x", pady=3)
        self.zoom = tk.DoubleVar(value=0)
        linha = ttk.Frame(corte)
        linha.pack(fill="x")
        ttk.Label(linha, text="Zoom", width=11).pack(side="left")
        self.escala_zoom = ttk.Scale(linha, from_=0, to=100, variable=self.zoom, command=self._mudou_zoom)
        self.escala_zoom.pack(side="left", fill="x", expand=True)
        self.escala_zoom.bind("<ButtonPress-1>", lambda e: self._marcar(True))
        botoes = ttk.Frame(corte)
        botoes.pack(fill="x", pady=(4, 0))
        ttk.Button(botoes, text="Centralizar no rosto", style="Compacto.TButton", command=self.centralizar_rosto).pack(side="left", fill="x", expand=True, padx=(0, 2))
        ttk.Button(botoes, text="Preencher", style="Compacto.TButton", command=self.preencher).pack(side="left", fill="x", expand=True, padx=2)
        ttk.Button(botoes, text="Encaixar inteira", style="Compacto.TButton", command=self.encaixar).pack(side="left", fill="x", expand=True, padx=(2, 0))

        giro = ttk.LabelFrame(lado, text="Girar e Endireitar", padding=6)
        giro.pack(fill="x", pady=3)
        botoes3 = ttk.Frame(giro)
        botoes3.pack(fill="x", pady=(0, 3))
        ttk.Button(botoes3, text="◀ 90°", style="Compacto.TButton", command=lambda: self.girar(-90)).pack(side="left", fill="x", expand=True, padx=(0, 2))
        ttk.Button(botoes3, text="90° ▶", style="Compacto.TButton", command=lambda: self.girar(90)).pack(side="left", fill="x", expand=True, padx=2)
        ttk.Button(botoes3, text="Espelhar", style="Compacto.TButton", command=self.espelhar).pack(side="left", fill="x", expand=True, padx=(2, 0))
        self.angulo = tk.DoubleVar(value=0)
        self._linha_escala(giro, "Endireitar", self.angulo, -15, 15, "angulo", "Gira suavemente em graus (duplo clique zera)")

        # Grupos estilo Lightroom: Luz, Cor e Efeitos
        quadros_lr = {}
        for nome_grupo, titulo_grupo in (
            ("Luz", "Luz (Lightroom)"),
            ("Cor", "Cor e Balanço de Branco"),
            ("Efeitos", "Nitidez e Vinheta"),
        ):
            qf = ttk.LabelFrame(lado, text=titulo_grupo, padding=6)
            qf.pack(fill="x", pady=3)
            quadros_lr[nome_grupo] = qf

        for chave, texto_rot, de, ate, grupo, dica_txt in CONTROLES_LIGHTROOM:
            var = tk.DoubleVar(value=0)
            setattr(self, chave, var)
            self._linha_escala(quadros_lr[grupo], texto_rot, var, de, ate, chave, dica_txt)

        self.pb = tk.BooleanVar(value=False)
        linha_pb = ttk.Frame(quadros_lr["Cor"])
        linha_pb.pack(fill="x", pady=(4, 0))
        ttk.Checkbutton(linha_pb, text="Preto e branco (P&B)", variable=self.pb, command=self._mudou_pb).pack(side="left")

        linha_zerar = ttk.Frame(lado)
        linha_zerar.pack(fill="x", pady=(4, 3))
        ttk.Button(linha_zerar, text="↺ Zerar todos os ajustes de luz e cor", style="Compacto.TButton",
                   command=self.zerar_ajustes).pack(fill="x")

        tratar = ttk.LabelFrame(lado, text="Tratar arquivo original externo", padding=6)
        tratar.pack(fill="x", pady=3)
        linha = ttk.Frame(tratar)
        linha.pack(fill="x")
        ttk.Button(linha, text="Photoshop", style="Compacto.TButton", command=self._photoshop).pack(side="left", fill="x", expand=True, padx=(0, 2))
        ttk.Button(linha, text="Lightroom", style="Compacto.TButton", command=self._lightroom).pack(side="left", fill="x", expand=True, padx=(2, 0))

    def _linha_escala(self, pai, texto, variavel, de, ate, chave, dica_txt=None):
        linha = ttk.Frame(pai)
        linha.pack(fill="x", pady=1)
        rot = ttk.Label(linha, text=texto, width=11)
        rot.pack(side="left")
        valor = ttk.Label(linha, text="0", width=4, anchor="e", style="Fraco.TLabel")
        valor.pack(side="right")
        escala = ttk.Scale(linha, from_=de, to=ate, variable=variavel,
                           command=lambda v, c=chave, r=valor: self._mudou_escala(c, v, r))
        escala.pack(side="left", fill="x", expand=True, padx=(2, 4))
        escala.bind("<ButtonPress-1>", lambda e: self._marcar(True))
        escala.bind("<Double-Button-1>", lambda e, c=chave, var=variavel: self._zerar_um(c, var))
        if dica_txt:
            tema.dica(rot, dica_txt + "\n(Duplo clique na barra para zerar)")
            tema.dica(escala, dica_txt + "\n(Duplo clique na barra para zerar)")
        setattr(self, "_rotulo_" + chave, valor)

    # ------------------------------------------------------------
    # Mostrar uma foto
    # ------------------------------------------------------------

    def mostrar(self, alvo):
        self.alvo = alvo
        self.espaco, self.aspecto, descricao = self.app.alvo_espaco(alvo)
        self._chave_transformada = None
        if not self.espaco or not self.espaco.get("foto"):
            self._fonte = None
            self.rotulo_nome.configure(text="Espaço vazio")
            self.rotulo_lugar.configure(text=descricao or "")
            self.canvas.delete("all")
            self.previa.delete("all")
            self.canvas.create_text(self.canvas.winfo_width() // 2, self.canvas.winfo_height() // 2,
                                    text="Arraste uma foto para este espaço", fill=tema.TEXTO_FRACO)
            return
        self.espaco.setdefault("edicao", {})
        self.rotulo_nome.configure(text=Path(self.espaco["foto"]).name)
        self.rotulo_lugar.configure(text=descricao or "")
        self.recarregar()
        self.deiconify()
        self.lift()

    def recarregar(self):
        """Lê a foto de novo (depois de tratada no Photoshop ou no Lightroom)."""
        if not self.espaco or not self.espaco.get("foto"):
            return
        try:
            self._fonte = analise.miniatura(self.espaco["foto"], analise.LADO_PREVIA)
        except OSError as e:
            self._fonte = None
            self.rotulo_lugar.configure(text="Erro ao abrir: %s" % e)
            return
        self._chave_transformada = None
        self._sincronizar_controles()
        self._desenhar()

    @property
    def ed(self):
        return self.espaco.setdefault("edicao", {})

    def _transformar(self):
        ed = self.ed
        chave = (id(self._fonte), ed.get("rot", 0), bool(ed.get("espelhar")), round(float(ed.get("angulo", 0) or 0), 2))
        if chave != self._chave_transformada:
            self._transformada = recorte.transformar(self._fonte, ed)
            self._chave_transformada = chave
        return self._transformada

    def _rosto(self):
        return rosto_de(self.app.info, self.espaco["foto"])

    def _corte(self):
        img = self._transformar()
        return recorte.corte_do_espaco(img.width, img.height, self.aspecto, self.ed, self._rosto())

    def _faixa_zoom(self):
        img = self._transformar()
        return recorte.zoom_encaixar(img.width, img.height, self.aspecto), ZOOM_MAXIMO

    def _zoom_para_escala(self, zoom):
        zmin, zmax = self._faixa_zoom()
        zoom = min(max(zoom, zmin), zmax)
        return 100.0 * math.log(zoom / zmin) / math.log(zmax / zmin)

    def _escala_para_zoom(self, valor):
        zmin, zmax = self._faixa_zoom()
        return zmin * (zmax / zmin) ** (float(valor) / 100.0)

    def _sincronizar_controles(self):
        if self._fonte is None:
            return
        self._sincronizando = True
        try:
            ed = recorte.edicao_padrao()
            ed.update(self.ed)
            self.zoom.set(self._zoom_para_escala(self._corte()["zoom"]))
            for chave in ("angulo",) + recorte.CHAVES_AJUSTES:
                val = float(ed.get(chave) or 0)
                getattr(self, chave).set(val)
                formato = "%.1f" % val if chave == "angulo" and abs(val - round(val)) > 0.05 else "%d" % round(val)
                getattr(self, "_rotulo_" + chave).configure(text=formato)
            self.pb.set(bool(ed["pb"]))
        finally:
            self._sincronizando = False

    # ------------------------------------------------------------
    # Desenho
    # ------------------------------------------------------------

    def _agendar_desenho(self):
        if self._agendado:
            self.after_cancel(self._agendado)
        self._agendado = self.after(24, self._desenhar)

    def _desenhar(self):
        self._agendado = None
        if self._fonte is None or not self.espaco:
            return
        img = self._transformar()
        cw = max(50, self.canvas.winfo_width())
        ch = max(50, self.canvas.winfo_height())
        s = min((cw - 60) / float(img.width), (ch - 60) / float(img.height))
        s = max(0.01, s)
        dw, dh = max(1, int(img.width * s)), max(1, int(img.height * s))
        ox, oy = (cw - dw) // 2, (ch - dh) // 2
        self._escala, self._origem = s, (ox, oy)
        exibida = recorte.aplicar_ajustes(img.resize((dw, dh), Image.BILINEAR), self.ed)
        self._tk_grande = ImageTk.PhotoImage(exibida)
        self.canvas.delete("all")
        self.canvas.create_image(ox, oy, image=self._tk_grande, anchor="nw")
        self._desenhar_moldura()
        self._desenhar_previa()

    def _desenhar_moldura(self):
        self.canvas.delete("moldura")
        img = self._transformada
        s = self._escala
        ox, oy = self._origem
        l, t, r, b = recorte.caixa_corte(img.width, img.height, self.aspecto, self._corte())
        x0, y0, x1, y1 = ox + l * s, oy + t * s, ox + r * s, oy + b * s
        ix0, iy0, ix1, iy1 = ox, oy, ox + img.width * s, oy + img.height * s
        for caixa in ((ix0, iy0, ix1, max(iy0, y0)), (ix0, min(iy1, y1), ix1, iy1),
                      (ix0, max(iy0, y0), max(ix0, x0), min(iy1, y1)), (min(ix1, x1), max(iy0, y0), ix1, min(iy1, y1))):
            if caixa[2] > caixa[0] and caixa[3] > caixa[1]:
                self.canvas.create_rectangle(*caixa, fill="#000000", stipple="gray50", outline="", tags="moldura")
        for i in (1, 2):
            gx = x0 + (x1 - x0) * i / 3.0
            gy = y0 + (y1 - y0) * i / 3.0
            self.canvas.create_line(gx, y0, gx, y1, fill="#ffffff", dash=(2, 4), tags="moldura")
            self.canvas.create_line(x0, gy, x1, gy, fill="#ffffff", dash=(2, 4), tags="moldura")
        self.canvas.create_rectangle(x0, y0, x1, y1, outline=tema.DESTAQUE, width=2, tags="moldura")
        rosto = recorte.rosto_transformado(self._rosto(), self.ed)
        if rosto:
            fx, fy = ox + rosto[0] * img.width * s, oy + rosto[1] * img.height * s
            self.canvas.create_oval(fx - 5, fy - 5, fx + 5, fy + 5, outline=tema.VERDE, width=2, tags="moldura")

    def _desenhar_previa(self):
        img = self._transformada
        if img is None:
            return
        largura = LADO_PREVIA_FINAL
        altura = int(round(largura / self.aspecto))
        if altura > 150:
            altura = 150
            largura = int(round(altura * self.aspecto))
        caixa = recorte.caixa_corte(img.width, img.height, self.aspecto, self._corte())
        fundo = self.app.fundo_do_alvo(self.alvo)
        pronta = recorte.aplicar_ajustes(recorte.recortar(img, caixa, (largura, altura), fundo, rapido=True), self.ed)
        self._tk_previa = ImageTk.PhotoImage(pronta)
        self.previa.configure(height=altura + 8)
        self.previa.delete("all")
        self.previa.create_image(LADO_PREVIA_FINAL // 2, altura // 2 + 4, image=self._tk_previa)

    # ------------------------------------------------------------
    # Mudanças
    # ------------------------------------------------------------

    def _marcar(self, forcar=False):
        agora = time.time()
        if forcar or agora - self._ultima_marca > 0.8:
            self.app.marcar_historico()
        self._ultima_marca = agora

    def _avisar(self, rapido=False):
        self.app.edicao_mudou(self.alvo, rapido=rapido)

    def _pressionou(self, evento):
        if self._fonte is None:
            return
        self._marcar(True)
        self._arrasto = (evento.x, evento.y, dict(self._corte()))

    def _moveu(self, evento):
        if not self._arrasto or self._fonte is None:
            return
        x0, y0, corte = self._arrasto
        img = self._transformada
        dx = (evento.x - x0) / self._escala / img.width
        dy = (evento.y - y0) / self._escala / img.height
        novo = {"cx": corte["cx"] + dx, "cy": corte["cy"] + dy, "zoom": corte["zoom"]}
        self.ed["corte"] = recorte.limitar_corte(img.width, img.height, self.aspecto, novo)
        self._desenhar_moldura()
        self._desenhar_previa()

    def _soltou(self, _evento):
        if self._arrasto:
            self._arrasto = None
            self._avisar()

    def _roda(self, evento):
        if self._fonte is None:
            return
        self._marcar()
        corte = dict(self._corte())
        corte["zoom"] *= 1.1 ** (evento.delta / 120.0)
        img = self._transformada
        self.ed["corte"] = recorte.limitar_corte(img.width, img.height, self.aspecto, corte)
        self._sincronizar_controles()
        self._desenhar_moldura()
        self._desenhar_previa()
        self._avisar(rapido=True)

    def _mudou_zoom(self, valor):
        if self._sincronizando or self._fonte is None:
            return
        corte = dict(self._corte())
        corte["zoom"] = self._escala_para_zoom(valor)
        img = self._transformada
        self.ed["corte"] = recorte.limitar_corte(img.width, img.height, self.aspecto, corte)
        self._desenhar_moldura()
        self._desenhar_previa()
        self._avisar(rapido=True)

    def _mudou_escala(self, chave, valor, rotulo):
        if self._sincronizando or self._fonte is None:
            return
        numero = round(float(valor), 1 if chave == "angulo" else 0)
        rotulo.configure(text="%g" % numero)
        self.ed[chave] = numero
        self._agendar_desenho()
        self._avisar(rapido=True)

    def _zerar_um(self, chave, variavel):
        self._marcar(True)
        variavel.set(0)
        self.ed[chave] = 0
        self._sincronizar_controles()
        self._agendar_desenho()
        self._avisar()

    def _mudou_pb(self):
        if self._sincronizando or self._fonte is None:
            return
        self._marcar(True)
        self.ed["pb"] = bool(self.pb.get())
        self._agendar_desenho()
        self._avisar()

    def centralizar_rosto(self):
        if self._fonte is None:
            return
        self._marcar(True)
        self.ed.pop("corte", None)
        self._sincronizar_controles()
        self._desenhar()
        self._avisar()

    def preencher(self):
        if self._fonte is None:
            return
        self._marcar(True)
        corte = dict(self._corte())
        corte["zoom"] = 1.0
        img = self._transformada
        self.ed["corte"] = recorte.limitar_corte(img.width, img.height, self.aspecto, corte)
        self._sincronizar_controles()
        self._desenhar()
        self._avisar()

    def encaixar(self):
        if self._fonte is None:
            return
        self._marcar(True)
        img = self._transformada
        self.ed["corte"] = {"cx": 0.5, "cy": 0.5, "zoom": recorte.zoom_encaixar(img.width, img.height, self.aspecto)}
        self._sincronizar_controles()
        self._desenhar()
        self._avisar()

    def girar(self, graus):
        if self._fonte is None:
            return
        self._marcar(True)
        self.ed["rot"] = (int(self.ed.get("rot", 0)) + graus) % 360
        self.ed.pop("corte", None)
        self._sincronizar_controles()
        self._desenhar()
        self._avisar()

    def espelhar(self):
        if self._fonte is None:
            return
        self._marcar(True)
        self.ed["espelhar"] = not self.ed.get("espelhar")
        corte = self.ed.get("corte")
        if corte:
            corte["cx"] = 1 - corte["cx"]
        self._desenhar()
        self._avisar()

    def zerar_ajustes(self):
        if self._fonte is None:
            return
        self._marcar(True)
        for chave in recorte.CHAVES_AJUSTES:
            self.ed[chave] = 0
        self.ed["pb"] = False
        self._sincronizar_controles()
        self._desenhar()
        self._avisar()

    def navegar(self, passo):
        novo = self.app.alvo_vizinho(self.alvo, passo)
        if novo:
            self.app.selecionar_alvo(novo)

    def _photoshop(self):
        if self.espaco and self.espaco.get("foto"):
            self.app.editar_no_photoshop([self.espaco["foto"]])

    def _lightroom(self):
        if self.espaco and self.espaco.get("foto"):
            self.app.tratar_no_lightroom([self.espaco["foto"]])

    def fechar(self):
        self.app.editor = None
        self.destroy()
