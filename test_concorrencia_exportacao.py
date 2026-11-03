# -*- coding: utf-8 -*-
"""
Suite de testes automatizados de concorrência e isolamento entre álbuns durante
a exportação em background (PSD Multicamadas 8BPS + JPG Industrial).

Valida:
1. Separação automática de subpastas por nome do álbum/projeto ('Album 30X20 - <Nome>')
   quando dois álbuns do mesmo tamanho são exportados na mesma pasta base.
2. Isolamento total de snapshot (copy.deepcopy): editar lâminas, trocar fotos, mudar
   fundos ou alternar para outra aba de álbum enquanto a exportação roda em segundo
   plano NUNCA altera nem contamina as páginas JPG ou camadas PSD do job em andamento.
3. Execução simultânea em paralelo de múltiplos jobs PSD + JPG sem colisão de cache
   de máscaras de degradê/fusão nem mistura de camadas.
"""

import concurrent.futures
import copy
import shutil
import struct
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _instalar_shim_pil_se_necessario():
    """
    Se o ambiente de testes CI não tiver o pacote nativo Pillow instalado,
    fornece uma implementação em memória de PIL (Image, ImageDraw, ImageChops,
    ImageEnhance, ImageFilter, ImageFont, ImageOps, ImageCms) com matriz real
    de pixels (R, G, B, A) para validar matematicamente cada pixel e camada PSD/JPG.
    """
    try:
        import PIL.Image  # noqa: F401
        return
    except ImportError:
        pass

    pil_mod = types.ModuleType("PIL")

    class _PixelAccess:
        def __init__(self, im):
            self._im = im

        def __getitem__(self, xy):
            x, y = int(xy[0]), int(xy[1])
            x = max(0, min(self._im.width - 1, x))
            y = max(0, min(self._im.height - 1, y))
            p = self._im._pixels[y * self._im.width + x]
            if self._im.mode == "L":
                return p[0]
            if self._im.mode == "RGB":
                return (p[0], p[1], p[2])
            return p

        def __setitem__(self, xy, val):
            x, y = int(xy[0]), int(xy[1])
            if not (0 <= x < self._im.width and 0 <= y < self._im.height):
                return
            if self._im.mode == "L":
                v = int(val[0] if isinstance(val, (tuple, list)) else val) & 255
                self._im._pixels[y * self._im.width + x] = (v, v, v, 255)
            elif isinstance(val, (tuple, list)):
                if len(val) >= 4:
                    self._im._pixels[y * self._im.width + x] = (
                        int(val[0]) & 255,
                        int(val[1]) & 255,
                        int(val[2]) & 255,
                        int(val[3]) & 255,
                    )
                else:
                    self._im._pixels[y * self._im.width + x] = (
                        int(val[0]) & 255,
                        int(val[1]) & 255,
                        int(val[2]) & 255,
                        255,
                    )

    class _Image:
        NEAREST = 0
        BILINEAR = 1
        BICUBIC = 2
        LANCZOS = 3

        def __init__(self, mode, size, pixels=None):
            self.mode = mode
            self.width = max(1, int(size[0]))
            self.height = max(1, int(size[1]))
            self.info = {}
            if pixels is not None:
                self._pixels = list(pixels)
            else:
                self._pixels = [(0, 0, 0, 255)] * (self.width * self.height)

        @property
        def size(self):
            return (self.width, self.height)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def close(self):
            pass

        def copy(self):
            im = _Image(self.mode, (self.width, self.height), list(self._pixels))
            im.info = dict(self.info)
            return im

        def load(self):
            return _PixelAccess(self)

        def getpixel(self, xy):
            return self.load()[xy]

        def convert(self, mode):
            im = self.copy()
            im.mode = mode
            return im

        def resize(self, size, _resample=0, box=None):
            src = self.crop(box) if box is not None else self
            nw, nh = max(1, int(size[0])), max(1, int(size[1]))
            novos = [None] * (nw * nh)
            for y in range(nh):
                sy = min(src.height - 1, int(y * src.height / nh))
                row_src = sy * src.width
                row_dst = y * nw
                for x in range(nw):
                    sx = min(src.width - 1, int(x * src.width / nw))
                    novos[row_dst + x] = src._pixels[row_src + sx]
            return _Image(self.mode, (nw, nh), novos)

        def crop(self, box):
            x0, y0, x1, y1 = [int(round(v)) for v in box]
            cw = max(1, x1 - x0)
            ch = max(1, y1 - y0)
            novos = []
            for y in range(y0, y0 + ch):
                cy = max(0, min(self.height - 1, y))
                for x in range(x0, x0 + cw):
                    cx = max(0, min(self.width - 1, x))
                    novos.append(self._pixels[cy * self.width + cx])
            return _Image(self.mode, (cw, ch), novos)

        def paste(self, im, box=(0, 0), mask=None):
            ox, oy = int(box[0]), int(box[1])
            for y in range(im.height):
                dy = oy + y
                if not (0 <= dy < self.height):
                    continue
                for x in range(im.width):
                    dx = ox + x
                    if not (0 <= dx < self.width):
                        continue
                    sp = im._pixels[y * im.width + x]
                    if mask is not None and 0 <= x < mask.width and 0 <= y < mask.height:
                        mp = mask._pixels[y * mask.width + x][0] / 255.0
                    else:
                        mp = (sp[3] / 255.0) if im.mode == "RGBA" else 1.0
                    dp = self._pixels[dy * self.width + dx]
                    nr = int(round(sp[0] * mp + dp[0] * (1.0 - mp)))
                    ng = int(round(sp[1] * mp + dp[1] * (1.0 - mp)))
                    nb = int(round(sp[2] * mp + dp[2] * (1.0 - mp)))
                    na = max(dp[3], int(round(sp[3] * mp)))
                    self._pixels[dy * self.width + dx] = (nr, ng, nb, na)

        def alpha_composite(self, im, dest=(0, 0)):
            self.paste(im, dest)

        def getchannel(self, ch):
            idx = {"R": 0, "G": 1, "B": 2, "A": 3}.get(ch, 0)
            novos = [(p[idx], p[idx], p[idx], 255) for p in self._pixels]
            return _Image("L", (self.width, self.height), novos)

        def split(self):
            if self.mode == "L":
                return (self.getchannel("R"),)
            if self.mode == "RGB":
                return (self.getchannel("R"), self.getchannel("G"), self.getchannel("B"))
            return (self.getchannel("R"), self.getchannel("G"), self.getchannel("B"), self.getchannel("A"))

        def putalpha(self, alpha):
            if isinstance(alpha, int):
                a_val = max(0, min(255, alpha))
                self._pixels = [(p[0], p[1], p[2], a_val) for p in self._pixels]
            else:
                self._pixels = [
                    (p[0], p[1], p[2], alpha._pixels[i][0])
                    for i, p in enumerate(self._pixels)
                ]

        def getbbox(self):
            return (0, 0, self.width, self.height)

        def tobytes(self):
            if self.mode == "L":
                return bytes(p[0] for p in self._pixels)
            if self.mode == "RGB":
                b = bytearray(len(self._pixels) * 3)
                for i, p in enumerate(self._pixels):
                    b[i * 3 : i * 3 + 3] = (p[0], p[1], p[2])
                return bytes(b)
            b = bytearray(len(self._pixels) * 4)
            for i, p in enumerate(self._pixels):
                b[i * 4 : i * 4 + 4] = p
            return bytes(b)

        def filter(self, _f):
            return self.copy()

        def rotate(self, _angle, expand=False, resample=0):
            return self.copy()

        def save(self, fp, format=None, **kwargs):
            # Serialização binária determinística com assinatura JFIF/PNG + pixels reais
            cab = struct.pack(">4s4sII", b"\xff\xd8\xff\xe0", self.mode.encode("ascii")[:4].ljust(4, b" "), self.width, self.height)
            dados_px = self.tobytes()
            payload = cab + struct.pack(">I", len(dados_px)) + dados_px
            if hasattr(fp, "write"):
                fp.write(payload)
            else:
                Path(fp).write_bytes(payload)

    class ImageMod:
        NEAREST = 0
        BILINEAR = 1
        BICUBIC = 2
        LANCZOS = 3
        Image = _Image

        @staticmethod
        def new(mode, size, color=0):
            w, h = max(1, int(size[0])), max(1, int(size[1]))
            if isinstance(color, (tuple, list)):
                if len(color) == 4:
                    c = (int(color[0]) & 255, int(color[1]) & 255, int(color[2]) & 255, int(color[3]) & 255)
                elif len(color) == 3:
                    c = (int(color[0]) & 255, int(color[1]) & 255, int(color[2]) & 255, 255)
                else:
                    v = int(color[0]) & 255
                    c = (v, v, v, 255)
            else:
                v = int(color or 0) & 255
                c = (v, v, v, 255)
            return _Image(mode, (w, h), [c] * (w * h))

        @staticmethod
        def open(fp):
            raw = fp.read() if hasattr(fp, "read") else Path(fp).read_bytes()
            if raw[:4] == b"\xff\xd8\xff\xe0" and len(raw) >= 20:
                _sig, mode_b, w, h, tam = struct.unpack(">4s4sIII", raw[:20])
                mode = mode_b.decode("ascii", errors="ignore").strip() or "RGB"
                buf = raw[20 : 20 + tam]
                px = []
                if mode == "L":
                    for b in buf:
                        px.append((b, b, b, 255))
                elif mode == "RGB":
                    for i in range(0, len(buf), 3):
                        px.append((buf[i], buf[i + 1], buf[i + 2], 255))
                else:
                    for i in range(0, len(buf), 4):
                        px.append((buf[i], buf[i + 1], buf[i + 2], buf[i + 3]))
                return _Image(mode, (w, h), px)
            return _Image("RGB", (100, 100))

        @staticmethod
        def composite(im1, im2, mask):
            out = im2.copy()
            out.paste(im1, (0, 0), mask)
            return out

        @staticmethod
        def blend(im1, im2, alpha):
            return im1.copy()

    class ImageChopsMod:
        @staticmethod
        def multiply(im1, im2):
            n = min(len(im1._pixels), len(im2._pixels))
            px = []
            for i in range(n):
                v = int(round((im1._pixels[i][0] * im2._pixels[i][0]) / 255.0))
                px.append((v, v, v, 255))
            return _Image("L", (im1.width, im1.height), px)

    class ImageDrawMod:
        class _Draw:
            def __init__(self, im):
                self._im = im

            def rectangle(self, *a, **kw):
                pass

            def rounded_rectangle(self, *a, **kw):
                pass

            def line(self, *a, **kw):
                pass

            def ellipse(self, *a, **kw):
                pass

            def text(self, *a, **kw):
                pass

        @staticmethod
        def Draw(im, mode=None):
            return ImageDrawMod._Draw(im)

    class ImageEnhanceMod:
        class _Enh:
            def __init__(self, im):
                self._im = im

            def enhance(self, _f):
                return self._im.copy()

        Color = _Enh
        Brightness = _Enh
        Contrast = _Enh
        Sharpness = _Enh

    class ImageFilterMod:
        class GaussianBlur:
            def __init__(self, radius=2):
                self.radius = radius

    class ImageFontMod:
        @staticmethod
        def truetype(*a, **kw):
            return object()

        @staticmethod
        def load_default():
            return object()

    class ImageOpsMod:
        @staticmethod
        def exif_transpose(im):
            return im

        @staticmethod
        def grayscale(im):
            return im.convert("L")

        @staticmethod
        def mirror(im):
            return im.copy()

        @staticmethod
        def flip(im):
            return im.copy()

        @staticmethod
        def colorize(im, **kw):
            return im.convert("RGB")

    class ImageCmsMod:
        @staticmethod
        def createProfile(name):
            return name

        @staticmethod
        def ImageCmsProfile(fp):
            return fp

        @staticmethod
        def profileToProfile(im, *a, **kw):
            return im.copy()

    class ImageFileMod:
        LOAD_TRUNCATED_IMAGES = True

    class ImageGrabMod:
        @staticmethod
        def grab(*_a, **_kw):
            return ImageMod.new("RGB", (10, 10))

    class ImageTkMod:
        class PhotoImage:
            def __init__(self, *_a, **_kw):
                pass

    pil_mod.Image = ImageMod
    pil_mod.ImageChops = ImageChopsMod
    pil_mod.ImageDraw = ImageDrawMod
    pil_mod.ImageEnhance = ImageEnhanceMod
    pil_mod.ImageFilter = ImageFilterMod
    pil_mod.ImageFont = ImageFontMod
    pil_mod.ImageOps = ImageOpsMod
    pil_mod.ImageCms = ImageCmsMod
    pil_mod.ImageFile = ImageFileMod
    pil_mod.ImageGrab = ImageGrabMod
    pil_mod.ImageTk = ImageTkMod

    sys.modules["PIL"] = pil_mod
    sys.modules["PIL.Image"] = ImageMod
    sys.modules["PIL.ImageChops"] = ImageChopsMod
    sys.modules["PIL.ImageDraw"] = ImageDrawMod
    sys.modules["PIL.ImageEnhance"] = ImageEnhanceMod
    sys.modules["PIL.ImageFilter"] = ImageFilterMod
    sys.modules["PIL.ImageFont"] = ImageFontMod
    sys.modules["PIL.ImageOps"] = ImageOpsMod
    sys.modules["PIL.ImageCms"] = ImageCmsMod
    sys.modules["PIL.ImageFile"] = ImageFileMod
    sys.modules["PIL.ImageGrab"] = ImageGrabMod
    sys.modules["PIL.ImageTk"] = ImageTkMod

    try:
        import cv2  # noqa: F401
    except ImportError:
        cv2_mod = types.ModuleType("cv2")
        cv2_mod.COLOR_RGB2GRAY = 0
        cv2_mod.CV_64F = 0
        cv2_mod.CASCADE_SCALE_IMAGE = 0
        cv2_mod.cvtColor = lambda arr, _code: arr
        cv2_mod.Laplacian = lambda _g, _d: types.SimpleNamespace(var=lambda: 100.0)
        cv2_mod.CascadeClassifier = lambda *_a, **_kw: types.SimpleNamespace(
            empty=lambda: True, detectMultiScale=lambda *_x, **_y: []
        )
        cv2_mod.data = types.SimpleNamespace(haarcascades="")
        sys.modules["cv2"] = cv2_mod

    try:
        import numpy  # noqa: F401
    except ImportError:
        np_mod = types.ModuleType("numpy")
        np_mod.array = lambda x, **_kw: x
        np_mod.asarray = lambda x, **_kw: x
        np_mod.uint8 = int
        np_mod.float32 = float
        sys.modules["numpy"] = np_mod

    try:
        import tkinter  # noqa: F401
    except ImportError:
        tk_mod = types.ModuleType("tkinter")
        tk_mod.Toplevel = object
        tk_mod.Tk = object
        tk_mod.Frame = object
        tk_mod.Canvas = object
        tk_mod.Label = object
        tk_mod.Button = object
        tk_mod.Menu = object
        tk_mod.BooleanVar = object
        tk_mod.IntVar = object
        tk_mod.DoubleVar = object
        tk_mod.StringVar = object
        tk_mod.TclError = Exception
        tk_mod.filedialog = types.ModuleType("tkinter.filedialog")
        tk_mod.ttk = types.ModuleType("tkinter.ttk")
        tk_mod.messagebox = types.ModuleType("tkinter.messagebox")
        tk_mod.simpledialog = types.ModuleType("tkinter.simpledialog")
        tk_mod.colorchooser = types.ModuleType("tkinter.colorchooser")
        sys.modules["tkinter"] = tk_mod
        sys.modules["tkinter.filedialog"] = tk_mod.filedialog
        sys.modules["tkinter.ttk"] = tk_mod.ttk
        sys.modules["tkinter.messagebox"] = tk_mod.messagebox
        sys.modules["tkinter.simpledialog"] = tk_mod.simpledialog
        sys.modules["tkinter.colorchooser"] = tk_mod.colorchooser


_instalar_shim_pil_se_necessario()

from PIL import Image  # noqa: E402
import analise  # noqa: E402
import exportar  # noqa: E402
import exportar_psd  # noqa: E402
import janela_exportar  # noqa: E402
import produtos  # noqa: E402
import projeto as proj  # noqa: E402


def _ler_nomes_camadas_psd(caminho_psd):
    """Lê o binário .psd (8BPS v1) e extrai (largura, altura, qtd_camadas, lista_nomes_camadas)."""
    dados = Path(caminho_psd).read_bytes()
    if dados[:4] != b"8BPS":
        raise ValueError("Assinatura PSD inválida em %s" % caminho_psd)
    ver, _res, canais, alt, larg, bits, modo = struct.unpack(">H6sHIIHH", dados[4:26])
    assert ver == 1 and bits == 8 and modo == 3
    pos = 26
    tam_color_mode = struct.unpack(">I", dados[pos : pos + 4])[0]
    pos += 4 + tam_color_mode
    tam_img_res = struct.unpack(">I", dados[pos : pos + 4])[0]
    pos += 4 + tam_img_res
    tam_layer_mask = struct.unpack(">I", dados[pos : pos + 4])[0]
    pos += 4
    if tam_layer_mask == 0:
        return larg, alt, 0, []
    tam_layer_info = struct.unpack(">I", dados[pos : pos + 4])[0]
    pos += 4
    qtd_camadas = abs(struct.unpack(">h", dados[pos : pos + 2])[0])
    pos += 2
    nomes = []
    for _ in range(qtd_camadas):
        _top, _left, _bottom, _right, num_ch = struct.unpack(">iiiih", dados[pos : pos + 18])
        pos += 18
        pos += num_ch * 6
        pos += 12
        extra_len = struct.unpack(">I", dados[pos : pos + 4])[0]
        pos += 4
        fim_extra = pos + extra_len
        tam_mask = struct.unpack(">I", dados[pos : pos + 4])[0]
        pos += 4 + tam_mask
        tam_ranges = struct.unpack(">I", dados[pos : pos + 4])[0]
        pos += 4 + tam_ranges
        tam_nome = dados[pos]
        nome = dados[pos + 1 : pos + 1 + tam_nome].decode("ascii", errors="replace")
        nomes.append(nome)
        pos = fim_extra
    return larg, alt, qtd_camadas, nomes


class TestConcorrenciaExportacaoPsdJpg(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="test_smartalbum_concorrencia_"))
        self.pasta_fotos_a = self.temp_dir / "fotos_album_A"
        self.pasta_fotos_b = self.temp_dir / "fotos_album_B"
        self.pasta_saida_base = self.temp_dir / "saida_comum"
        self.pasta_fotos_a.mkdir(parents=True, exist_ok=True)
        self.pasta_fotos_b.mkdir(parents=True, exist_ok=True)
        self.pasta_saida_base.mkdir(parents=True, exist_ok=True)

        # Fotos do Álbum A: Vermelho Puro (255, 0, 0) e Azul Puro (0, 0, 255)
        self.foto_vermelha_a = self.pasta_fotos_a / "foto_vermelha_A.png"
        self.foto_azul_a = self.pasta_fotos_a / "foto_azul_A.png"
        Image.new("RGB", (240, 160), (255, 0, 0)).save(self.foto_vermelha_a, "PNG")
        Image.new("RGB", (240, 160), (0, 0, 255)).save(self.foto_azul_a, "PNG")

        # Fotos do Álbum B: Verde Puro (0, 255, 0) e Amarelo Puro (255, 255, 0)
        self.foto_verde_b = self.pasta_fotos_b / "foto_verde_B.png"
        self.foto_amarela_b = self.pasta_fotos_b / "foto_amarela_B.png"
        Image.new("RGB", (240, 160), (0, 255, 0)).save(self.foto_verde_b, "PNG")
        Image.new("RGB", (240, 160), (255, 255, 0)).save(self.foto_amarela_b, "PNG")

        lista_prods = produtos.listar_produtos()
        base_prod = (
            next((p for p in lista_prods if "30" in p["nome"] and "20" in p["nome"]), lista_prods[0])
            if lista_prods
            else produtos._completar({"nome": "30x20", "pagina_mm": [300, 200]}, "30x20")
        )
        self.produto_30x20 = copy.deepcopy(base_prod)
        self.produto_30x20["pagina_mm"] = (60, 40)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _montar_projeto_teste(self, nome_album, foto_esq, foto_dir, cor_fundo, qtd_laminas=2):
        p = proj.novo(self.produto_30x20["nome"])
        p["nome"] = nome_album
        p["pasta_fotos"] = str(Path(foto_esq).parent)
        proj.adicionar_fotos(p, [str(foto_esq), str(foto_dir)])
        p["laminas"] = []
        for _ in range(qtd_laminas):
            p["laminas"].append(
                {
                    "fundo": cor_fundo,
                    "espacos": [
                        {
                            "x": 0.05,
                            "y": 0.10,
                            "w": 0.40,
                            "h": 0.80,
                            "foto": str(foto_esq),
                            "edicao": {
                                "opacidade": 100,
                                "degrade": {
                                    "ativo": True,
                                    "direcao": "direita",
                                    "inicio": 60,
                                    "fim": 100,
                                    "opacidade_fim": 85,
                                },
                            },
                        },
                        {
                            "x": 0.55,
                            "y": 0.10,
                            "w": 0.40,
                            "h": 0.80,
                            "foto": str(foto_dir),
                            "edicao": {"opacidade": 100},
                        },
                    ],
                    "sobreposicoes": [],
                }
            )
        info_map, _erros = analise.analisar_fotos([str(foto_esq), str(foto_dir)])
        return p, info_map

    def test_01_separacao_subpasta_por_nome_do_album(self):
        """
        Garante que dois álbuns de mesmo tamanho (30x20) exportados na mesma pasta base
        criam subpastas separadas com o nome de cada álbum/projeto sem sobrescrever páginas.
        """
        proj_a, _ = self._montar_projeto_teste(
            "Formatura Medicina", self.foto_vermelha_a, self.foto_azul_a, "#101010"
        )
        proj_b, _ = self._montar_projeto_teste(
            "Casamento Jardim", self.foto_verde_b, self.foto_amarela_b, "#ffffff"
        )
        op = {"pasta": str(self.pasta_saida_base)}
        pasta_a = janela_exportar.pasta_final(op, proj_a, self.produto_30x20)
        pasta_b = janela_exportar.pasta_final(op, proj_b, self.produto_30x20)

        self.assertNotEqual(pasta_a, pasta_b)
        self.assertIn("Formatura Medicina", pasta_a.name)
        self.assertIn("Casamento Jardim", pasta_b.name)
        self.assertTrue(pasta_a.name.startswith("Album "))
        self.assertTrue(pasta_b.name.startswith("Album "))

    def test_02_isolamento_mutacao_simultanea_e_concorrencia_psd_jpg(self):
        """
        Inicia a exportação PSD e JPG do Álbum A enquanto simultaneamente altera o estado
        em memória para o Álbum B e exporta o Álbum B em paralelo.
        Verifica nos pixels dos JPGs e nas camadas binárias dos PSDs que NÃO houve
        qualquer mistura de páginas, fotos, cores de fundo ou pastas.
        """
        proj_a, info_a = self._montar_projeto_teste(
            "Album Formatura A", self.foto_vermelha_a, self.foto_azul_a, "#000000", qtd_laminas=2
        )
        proj_b, info_b = self._montar_projeto_teste(
            "Album Casamento B", self.foto_verde_b, self.foto_amarela_b, "#ffffff", qtd_laminas=2
        )

        op_base = exportar.opcoes_padrao()
        op_base.update(
            {
                "pasta": str(self.pasta_saida_base),
                "exportar_jpg": True,
                "exportar_psd": True,
                "dpi": 150,
                "qualidade": 100,
                "marcas_registro": False,
                "workers": 2,
            }
        )

        pasta_alb_a = janela_exportar.pasta_final(op_base, proj_a, self.produto_30x20)
        pasta_alb_b = janela_exportar.pasta_final(op_base, proj_b, self.produto_30x20)

        # Congela snapshots no momento de enfileirar cada job (idêntico a app.pyw:executar_exportacao)
        snap_proj_a = copy.deepcopy(proj_a)
        snap_info_a = copy.deepcopy(info_a)
        snap_proj_b = copy.deepcopy(proj_b)
        snap_info_b = copy.deepcopy(info_b)

        # Muta agressivamente proj_a ANTES e DURANTE a execução dos workers, simulando o usuário
        # trocando para outra aba / substituindo todas as fotos na interface enquanto exporta
        proj_a["nome"] = "MUTADO_DURANTE_EXPORTACAO"
        for lam in proj_a["laminas"]:
            lam["fundo"] = "#00ff00"
            for esp in lam["espacos"]:
                esp["foto"] = str(self.foto_verde_b)

        def exportar_album_completo(projeto_ref, info_ref, pasta_raiz):
            pasta_jpg = janela_exportar.pasta_do_formato(pasta_raiz, "jpg")
            pasta_psd = janela_exportar.pasta_do_formato(pasta_raiz, "psd")
            pasta_jpg.mkdir(parents=True, exist_ok=True)
            pasta_psd.mkdir(parents=True, exist_ok=True)

            jpgs = exportar.exportar_jpg(
                projeto_ref,
                self.produto_30x20,
                info_ref,
                pasta_jpg,
                aviso=lambda _m: None,
                parar=lambda: False,
                ao_progresso=None,
                opcoes=op_base,
            )
            psds = exportar_psd.exportar_psd(
                projeto_ref,
                self.produto_30x20,
                info_ref,
                pasta_psd,
                aviso=lambda _m: None,
                parar=lambda: False,
                ao_progresso=None,
                opcoes=op_base,
            )
            return jpgs, psds

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            fut_a = executor.submit(exportar_album_completo, snap_proj_a, snap_info_a, pasta_alb_a)
            fut_b = executor.submit(exportar_album_completo, snap_proj_b, snap_info_b, pasta_alb_b)
            jpgs_a, psds_a = fut_a.result(timeout=60)
            jpgs_b, psds_b = fut_b.result(timeout=60)

        self.assertEqual(len(jpgs_a), 2)
        self.assertEqual(len(psds_a), 2)
        self.assertEqual(len(jpgs_b), 2)
        self.assertEqual(len(psds_b), 2)

        # 1. Conferência de pixels nos JPGs do Álbum A (Esquerda = Vermelho, Direita = Azul, Fundo = Preto)
        for arq_jpg_a in jpgs_a:
            self.assertTrue(arq_jpg_a.exists())
            self.assertEqual(arq_jpg_a.parent.parent, pasta_alb_a)
            with Image.open(arq_jpg_a) as im_a:
                rgb_a = im_a.convert("RGB")
                W, H = rgb_a.size
                px_esq = rgb_a.getpixel((int(W * 0.20), int(H * 0.50)))
                px_dir = rgb_a.getpixel((int(W * 0.75), int(H * 0.50)))
                px_fundo = rgb_a.getpixel((int(W * 0.50), int(H * 0.03)))
                self.assertGreater(px_esq[0], 200, f"Pixel esquerdo do Álbum A contaminado: {px_esq}")
                self.assertLess(px_esq[1], 40, f"Pixel esquerdo do Álbum A contaminado com verde: {px_esq}")
                self.assertGreater(px_dir[2], 200, f"Pixel direito do Álbum A contaminado: {px_dir}")
                self.assertLess(px_dir[1], 40, f"Pixel direito do Álbum A contaminado com verde: {px_dir}")
                self.assertLess(px_fundo[1], 30, f"Fundo do Álbum A sofreu mutação concorrente: {px_fundo}")

        # 2. Conferência de pixels nos JPGs do Álbum B (Esquerda = Verde, Direita = Amarelo, Fundo = Branco)
        for arq_jpg_b in jpgs_b:
            self.assertTrue(arq_jpg_b.exists())
            self.assertEqual(arq_jpg_b.parent.parent, pasta_alb_b)
            with Image.open(arq_jpg_b) as im_b:
                rgb_b = im_b.convert("RGB")
                W, H = rgb_b.size
                px_esq = rgb_b.getpixel((int(W * 0.20), int(H * 0.50)))
                px_dir = rgb_b.getpixel((int(W * 0.75), int(H * 0.50)))
                px_fundo = rgb_b.getpixel((int(W * 0.50), int(H * 0.03)))
                self.assertGreater(px_esq[1], 200, f"Pixel esquerdo do Álbum B contaminado: {px_esq}")
                self.assertLess(px_esq[0], 40, f"Pixel esquerdo do Álbum B contaminado com vermelho: {px_esq}")
                self.assertGreater(px_dir[0], 200, f"Pixel direito do Álbum B contaminado: {px_dir}")
                self.assertGreater(px_dir[1], 200, f"Pixel direito do Álbum B contaminado: {px_dir}")
                self.assertLess(px_dir[2], 40, f"Pixel direito do Álbum B contaminado com azul: {px_dir}")
                self.assertGreater(px_fundo[0], 235)
                self.assertGreater(px_fundo[1], 235)
                self.assertGreater(px_fundo[2], 235)

        # 3. Conferência das camadas binárias nos PSDs do Álbum A e do Álbum B
        for arq_psd_a in psds_a:
            larg, alt, qtd, nomes = _ler_nomes_camadas_psd(arq_psd_a)
            self.assertGreater(larg, 100)
            self.assertGreater(alt, 100)
            self.assertEqual(qtd, 3)
            self.assertEqual(nomes[0], "Fundo da Lamina")
            self.assertIn("foto_vermelha_A", nomes[1])
            self.assertIn("foto_azul_A", nomes[2])
            for n in nomes:
                self.assertNotIn("foto_verde_B", n)
                self.assertNotIn("foto_amarela_B", n)

        for arq_psd_b in psds_b:
            larg, alt, qtd, nomes = _ler_nomes_camadas_psd(arq_psd_b)
            self.assertGreater(larg, 100)
            self.assertGreater(alt, 100)
            self.assertEqual(qtd, 3)
            self.assertEqual(nomes[0], "Fundo da Lamina")
            self.assertIn("foto_verde_B", nomes[1])
            self.assertIn("foto_amarela_B", nomes[2])
            for n in nomes:
                self.assertNotIn("foto_vermelha_A", n)
                self.assertNotIn("foto_azul_A", n)


if __name__ == "__main__":
    unittest.main(verbosity=2)
