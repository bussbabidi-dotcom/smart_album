# -*- coding: utf-8 -*-
"""
Baixa, converte e valida os modelos de IA do programa (feito uma vez, com internet, fora do programa).

Precisa de um Python com torch (CPU), onnx, numpy, pillow e opencv:
    py -3 -m venv %TEMP%\\sa_conv\\venv
    %TEMP%\\sa_conv\\venv\\Scripts\\python -m pip install torch --index-url https://download.pytorch.org/whl/cpu
    %TEMP%\\sa_conv\\venv\\Scripts\\python -m pip install onnx onnxscript numpy pillow opencv-python-headless
    %TEMP%\\sa_conv\\venv\\Scripts\\python ferramentas\\converter_modelos.py

Gera em recursos\\modelos_ia: places365_resnet18.onnx, places365_categorias.json, lut3d_classificador.onnx,
lut3d_base.npz, modelos_ia.json (SHA-256 de cada arquivo) e LICENCAS.txt. O programa nunca usa torch nem internet.
"""

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

PASTA_APP = Path(__file__).resolve().parent.parent
os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="sa_converter_")
sys.path.insert(0, str(PASTA_APP))
sys.dont_write_bytecode = True

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import onnx  # noqa: E402
import torch  # noqa: E402
import torch.nn as nn  # noqa: E402
import torch.nn.functional as F  # noqa: E402
from onnx import helper, numpy_helper  # noqa: E402
from PIL import Image, ImageDraw, ImageFilter  # noqa: E402

import ia_cena  # noqa: E402
import ia_edicao  # noqa: E402

URL_PLACES = "http://places2.csail.mit.edu/models_places365/resnet18_places365.pth.tar"
URL_PLACES_RAW = "https://raw.githubusercontent.com/CSAILVision/places365/master/"
URL_LUT_RAW = "https://raw.githubusercontent.com/HuiZeng/Image-Adaptive-3DLUT/master/"
FONTES = {
    "resnet18_places365.pth.tar": URL_PLACES,
    "categories_places365.txt": URL_PLACES_RAW + "categories_places365.txt",
    "IO_places365.txt": URL_PLACES_RAW + "IO_places365.txt",
    "places365_LICENSE.txt": URL_PLACES_RAW + "LICENSE",
    "cam_example.jpg": URL_PLACES_RAW + "cam_example.jpg",
    "mountains.jpg": URL_PLACES_RAW + "docker/images/mountains.jpg",
    "LUTs.pth": URL_LUT_RAW + "pretrained_models/sRGB/LUTs.pth",
    "classifier.pth": URL_LUT_RAW + "pretrained_models/sRGB/classifier.pth",
    "lut3d_LICENSE.txt": URL_LUT_RAW + "LICENSE",
    "a1629.jpg": URL_LUT_RAW + "demo_images/sRGB/a1629.jpg",
}
VERSAO_CENA = "resnet18-places365-2017/onnx-%s-1"
VERSAO_EDICAO = "ia3dlut-fivek-srgb-2020/onnx-1"


def sha256(caminho):
    h = hashlib.sha256()
    with open(caminho, "rb") as arq:
        for bloco in iter(lambda: arq.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def baixar(cache):
    cache.mkdir(parents=True, exist_ok=True)
    for nome, url in FONTES.items():
        destino = cache / nome
        if destino.is_file() and destino.stat().st_size > 0:
            continue
        print("baixando", url)
        urllib.request.urlretrieve(url, str(destino))
    return {nome: {"url": url, "sha256": sha256(cache / nome), "bytes": (cache / nome).stat().st_size}
            for nome, url in FONTES.items()}


def carregar_torch(caminho):
    try:
        return torch.load(str(caminho), map_location="cpu", weights_only=True)
    except Exception:
        return torch.load(str(caminho), map_location="cpu", weights_only=False)


# ------------------------------------------------------------
# Places365 / ResNet18 (mesma estrutura e nomes do torchvision)
# ------------------------------------------------------------

class Bloco(nn.Module):
    def __init__(self, entrada, saida, passo):
        super().__init__()
        self.conv1 = nn.Conv2d(entrada, saida, 3, passo, 1, bias=False)
        self.bn1 = nn.BatchNorm2d(saida)
        self.relu = nn.ReLU(inplace=True)
        self.conv2 = nn.Conv2d(saida, saida, 3, 1, 1, bias=False)
        self.bn2 = nn.BatchNorm2d(saida)
        self.downsample = None
        if passo != 1 or entrada != saida:
            self.downsample = nn.Sequential(nn.Conv2d(entrada, saida, 1, passo, bias=False), nn.BatchNorm2d(saida))

    def forward(self, x):
        y = self.relu(self.bn1(self.conv1(x)))
        y = self.bn2(self.conv2(y))
        return self.relu(y + (self.downsample(x) if self.downsample is not None else x))


class ResNet18(nn.Module):
    def __init__(self, classes=365):
        super().__init__()
        self.conv1 = nn.Conv2d(3, 64, 7, 2, 3, bias=False)
        self.bn1 = nn.BatchNorm2d(64)
        self.relu = nn.ReLU(inplace=True)
        self.maxpool = nn.MaxPool2d(3, 2, 1)
        self.layer1 = nn.Sequential(Bloco(64, 64, 1), Bloco(64, 64, 1))
        self.layer2 = nn.Sequential(Bloco(64, 128, 2), Bloco(128, 128, 1))
        self.layer3 = nn.Sequential(Bloco(128, 256, 2), Bloco(256, 256, 1))
        self.layer4 = nn.Sequential(Bloco(256, 512, 2), Bloco(512, 512, 1))
        self.avgpool = nn.AdaptiveAvgPool2d(1)
        self.fc = nn.Linear(512, classes)

    def forward(self, x):
        x = self.maxpool(self.relu(self.bn1(self.conv1(x))))
        x = self.layer4(self.layer3(self.layer2(self.layer1(x))))
        return self.fc(torch.flatten(self.avgpool(x), 1))


# ------------------------------------------------------------
# Image-Adaptive 3D LUT (models.py do repositório original)
# ------------------------------------------------------------

def bloco_discriminador(entrada, saida, normalizacao=False):
    camadas = [nn.Conv2d(entrada, saida, 3, stride=2, padding=1), nn.LeakyReLU(0.2)]
    if normalizacao:
        camadas.append(nn.InstanceNorm2d(saida, affine=True))
    return camadas


class Classificador(nn.Module):
    def __init__(self):
        super().__init__()
        self.model = nn.Sequential(
            nn.Upsample(size=(256, 256), mode="bilinear"),
            nn.Conv2d(3, 16, 3, stride=2, padding=1),
            nn.LeakyReLU(0.2),
            nn.InstanceNorm2d(16, affine=True),
            *bloco_discriminador(16, 32, True),
            *bloco_discriminador(32, 64, True),
            *bloco_discriminador(64, 128, True),
            *bloco_discriminador(128, 128),
            nn.Dropout(p=0.5),
            nn.Conv2d(128, 3, 8, padding=0),
        )

    def forward(self, x):
        return self.model(x)


class ClassificadorSemUpsample(nn.Module):
    """O mesmo classificador para entrada já em 256x256 (o programa redimensiona com cv2.INTER_LINEAR)."""

    def __init__(self, original):
        super().__init__()
        self.model = nn.Sequential(*list(original.model.children())[1:])

    def forward(self, x):
        return self.model(x)


def trilinear_referencia(lut, img, binsize):
    """Porta em torch do TriLinearForwardCpu (trilinear_c/src/trilinear.c): lut [c][b][g][r], img (3, H, W)."""
    dim = lut.shape[-1]
    r, g, b = img[0], img[1], img[2]
    r_id, g_id, b_id = (torch.floor(v / binsize).long() for v in (r, g, b))
    r_d, g_d, b_d = (torch.fmod(v, binsize) / binsize for v in (r, g, b))
    plana = lut.reshape(3, -1)

    def em(ri, gi, bi):
        return plana[:, ri + gi * dim + bi * dim * dim]

    return ((1 - r_d) * (1 - g_d) * (1 - b_d) * em(r_id, g_id, b_id)
            + r_d * (1 - g_d) * (1 - b_d) * em(r_id + 1, g_id, b_id)
            + (1 - r_d) * g_d * (1 - b_d) * em(r_id, g_id + 1, b_id)
            + r_d * g_d * (1 - b_d) * em(r_id + 1, g_id + 1, b_id)
            + (1 - r_d) * (1 - g_d) * b_d * em(r_id, g_id, b_id + 1)
            + r_d * (1 - g_d) * b_d * em(r_id + 1, g_id, b_id + 1)
            + (1 - r_d) * g_d * b_d * em(r_id, g_id + 1, b_id + 1)
            + r_d * g_d * b_d * em(r_id + 1, g_id + 1, b_id + 1))


# ------------------------------------------------------------
# ONNX
# ------------------------------------------------------------

def exportar_onnx(modelo, entrada, caminho):
    modelo.eval()
    kwargs = dict(input_names=["entrada"], output_names=["saida"], opset_version=17, do_constant_folding=True)
    try:
        torch.onnx.export(modelo, entrada, str(caminho), dynamo=False, **kwargs)
    except TypeError:
        torch.onnx.export(modelo, entrada, str(caminho), **kwargs)
    m = onnx.load(str(caminho))
    onnx.checker.check_model(m)
    return m


def converter_fp16(caminho_fp32, caminho_fp16):
    """Pesos em float16 no arquivo + Cast para float32 na carga: metade do tamanho, conta igual em float32."""
    m = onnx.load(str(caminho_fp32))
    novos, casts = [], []
    for ini in m.graph.initializer:
        arr = numpy_helper.to_array(ini)
        if arr.dtype == np.float32 and arr.size > 64:
            nome16 = ini.name + "__fp16"
            novos.append(numpy_helper.from_array(arr.astype(np.float16), nome16))
            casts.append(helper.make_node("Cast", [nome16], [ini.name], to=onnx.TensorProto.FLOAT))
        else:
            novos.append(ini)
    del m.graph.initializer[:]
    m.graph.initializer.extend(novos)
    nos = list(m.graph.node)
    del m.graph.node[:]
    m.graph.node.extend(casts + nos)
    onnx.checker.check_model(m)
    onnx.save(m, str(caminho_fp16))


def rede_cv(caminho):
    return cv2.dnn.readNetFromONNX(str(caminho))


def rodar_cv(net, blob):
    net.setInput(blob)
    return np.asarray(net.forward(), dtype=np.float64).reshape(-1)


# ------------------------------------------------------------
# Fotos de validação
# ------------------------------------------------------------

def fotos_geradas():
    rng = np.random.default_rng(7)
    fotos = []
    ceu = np.linspace([70, 130, 220], [225, 235, 245], 300)[:, None, :].repeat(600, 1)
    grama = (np.array([60, 120, 50]) + rng.normal(0, 18, (300, 600, 3))).clip(0, 255)
    externa = Image.fromarray(np.concatenate([ceu, grama]).astype(np.uint8))
    d = ImageDraw.Draw(externa)
    d.ellipse((260, 180, 340, 270), fill=(220, 180, 150))
    d.rectangle((250, 270, 350, 480), fill=(40, 40, 60))
    fotos.append(("gerada_externa", externa.filter(ImageFilter.GaussianBlur(1.2))))
    interna = (np.array([70, 45, 30]) + rng.normal(0, 10, (600, 600, 3))).clip(0, 255).astype(np.uint8)
    interna = Image.fromarray(interna)
    d = ImageDraw.Draw(interna)
    for x in range(40, 600, 110):
        d.ellipse((x, 40, x + 40, 80), fill=(255, 220, 140))
    d.rectangle((0, 420, 600, 600), fill=(110, 70, 40))
    fotos.append(("gerada_salao", interna.filter(ImageFilter.GaussianBlur(2))))
    estudio = Image.new("RGB", (600, 800), (128, 130, 135))
    d = ImageDraw.Draw(estudio)
    d.ellipse((240, 160, 360, 300), fill=(230, 190, 160))
    d.rectangle((200, 300, 400, 800), fill=(15, 15, 20))
    fotos.append(("gerada_estudio", estudio.filter(ImageFilter.GaussianBlur(1.5))))
    escura = (np.linspace(10, 90, 600)[None, :, None] + rng.normal(0, 6, (400, 600, 3))).clip(0, 255).astype(np.uint8)
    fotos.append(("gerada_subexposta", Image.fromarray(escura)))
    return fotos


def fotos_validacao(cache):
    fotos = [(n, Image.open(cache / n).convert("RGB")) for n in ("cam_example.jpg", "mountains.jpg", "a1629.jpg")]
    pasta_teste = Path(os.environ.get("TEMP", tempfile.gettempdir())) / "teste_smart_album" / "fotos 30X22"
    for arq in sorted(pasta_teste.glob("*.jpg"))[:8]:
        fotos.append((arq.name, Image.open(arq).convert("RGB")))
    return fotos + fotos_geradas()


def reduzir(img, lado=ia_edicao.LADO_REFERENCIA):
    if max(img.size) <= lado:
        return img
    escala = lado / float(max(img.size))
    return img.resize((max(1, round(img.width * escala)), max(1, round(img.height * escala))), Image.BILINEAR)


# ------------------------------------------------------------
# Principal
# ------------------------------------------------------------

def principal():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default=str(Path(tempfile.gettempdir()) / "sa_conv" / "fontes"))
    ap.add_argument("--saida", default=str(PASTA_APP / "recursos" / "modelos_ia"))
    ap.add_argument("--sem-fp16", action="store_true")
    args = ap.parse_args()
    cache, saida = Path(args.cache), Path(args.saida)
    saida.mkdir(parents=True, exist_ok=True)
    torch.set_num_threads(max(1, (os.cpu_count() or 2) // 2))
    print("torch", torch.__version__, "| onnx", onnx.__version__, "| cv2", cv2.__version__, "| numpy", np.__version__)
    fontes = baixar(cache)
    relatorio = {"fontes": fontes}

    # ---------------- Places365 ----------------
    ck = carregar_torch(cache / "resnet18_places365.pth.tar")
    estado = {k.replace("module.", ""): v for k, v in (ck.get("state_dict", ck)).items()}
    places = ResNet18()
    places.load_state_dict(estado)
    places.eval()
    onnx32 = Path(tempfile.gettempdir()) / "sa_conv" / "places365_resnet18_fp32.onnx"
    exportar_onnx(places, torch.zeros(1, 3, 224, 224), onnx32)
    linhas_cat = (cache / "categories_places365.txt").read_text(encoding="utf-8").split()
    nomes = [linhas_cat[i][3:] for i in range(0, len(linhas_cat), 2)]
    io_linhas = (cache / "IO_places365.txt").read_text(encoding="utf-8").split()
    io_mapa = {io_linhas[i][3:]: int(io_linhas[i + 1]) for i in range(0, len(io_linhas), 2)}
    externa = [1 if io_mapa.get(n) == 2 else 0 for n in nomes]
    assert len(nomes) == 365 and len(io_mapa) == 365

    fotos = fotos_validacao(cache)
    blobs = [(n, ia_cena.preprocessar(img)) for n, img in fotos]
    with torch.no_grad():
        ref = {n: F.softmax(places(torch.from_numpy(b)), 1).numpy().reshape(-1) for n, b in blobs}

    def validar_places(caminho):
        net = rede_cv(caminho)
        res = []
        for n, b in blobs:
            logits = rodar_cv(net, b)
            p = np.exp(logits - logits.max())
            p /= p.sum()
            res.append((n, int(np.argmax(ref[n])), int(np.argmax(p)), float(np.abs(p - ref[n]).max())))
        return res

    res32 = validar_places(onnx32)
    escolhido, res_escolhido, precisao = onnx32, res32, "fp32"
    if not args.sem_fp16:
        onnx16 = Path(tempfile.gettempdir()) / "sa_conv" / "places365_resnet18_fp16.onnx"
        try:
            converter_fp16(onnx32, onnx16)
            res16 = validar_places(onnx16)
            if all(a == b for _n, a, b, _d in res16) and max(d for *_x, d in res16) < 0.01:
                escolhido, res_escolhido, precisao = onnx16, res16, "fp16"
            else:
                print("fp16 recusado (diferença grande):", res16)
        except Exception as e:
            print("fp16 não carregou no cv2.dnn, fica fp32:", e)
    destino_places = saida / "places365_resnet18.onnx"
    destino_places.write_bytes(escolhido.read_bytes())
    (saida / "places365_categorias.json").write_text(
        json.dumps({"categorias": nomes, "externa": externa}, ensure_ascii=False), encoding="utf-8")
    print("\n== Places365 (ResNet18, %s) cv2.dnn x PyTorch ==" % precisao)
    for n, a, b, d in res_escolhido:
        print("  %-26s top1 torch=%-22s cv2=%-22s %s  max|dp|=%.2e" % (n[:26], nomes[a], nomes[b], "OK" if a == b else "DIFERENTE", d))
    relatorio["places"] = {"precisao": precisao, "top1_iguais": sum(1 for _n, a, b, _d in res_escolhido if a == b),
                           "total": len(res_escolhido), "max_dp": max(d for *_x, d in res_escolhido),
                           "max_dp_fp32": max(d for *_x, d in res32)}

    # ---------------- 3D LUT ----------------
    luts_ck = carregar_torch(cache / "LUTs.pth")
    luts = np.stack([np.asarray(luts_ck[str(i)]["LUT"].float().numpy(), dtype=np.float32) for i in range(3)])
    assert luts.shape == (3, 3, 33, 33, 33), luts.shape
    np.savez_compressed(str(saida / "lut3d_base.npz"), luts=luts)
    classif = Classificador()
    classif.load_state_dict(carregar_torch(cache / "classifier.pth"))
    classif.eval()
    destino_lut = saida / "lut3d_classificador.onnx"
    exportar_onnx(ClassificadorSemUpsample(classif), torch.zeros(1, 3, 256, 256), destino_lut)
    net_lut = rede_cv(destino_lut)
    binsize = 1.0001 / 32
    luts_t = torch.from_numpy(luts)
    print("\n== 3D LUT (classificador cv2.dnn x PyTorch; LUT numpy x trilinear.c em torch) ==")
    difs_w, difs_img, difs_img_mesmos = [], [], []
    for n, img in fotos:
        img480 = reduzir(img)
        tensor = torch.from_numpy(np.asarray(img480, dtype=np.float32) / 255.0).permute(2, 0, 1)
        with torch.no_grad():
            w_ref = classif(tensor[None]).reshape(-1)
            lut_ref = w_ref[0] * luts_t[0] + w_ref[1] * luts_t[1] + w_ref[2] * luts_t[2]
            out_ref = trilinear_referencia(lut_ref, tensor, binsize)
        img_ref = out_ref.mul(255).add(0.5).clamp(0, 255).permute(1, 2, 0).to(torch.uint8).numpy()
        w_cv = rodar_cv(net_lut, ia_edicao.preparar_entrada(img480))[:3]
        dw = float(np.abs(w_cv - w_ref.numpy()).max())
        arr = np.asarray(img480)
        lut_cv = w_cv[0] * luts[0] + w_cv[1] * luts[1] + w_cv[2] * luts[2]
        out_np = ia_edicao.aplicar_lut_array(arr, lut_cv.astype(np.float32), 100)
        out_mesmos = ia_edicao.aplicar_lut_array(arr, lut_ref.numpy().astype(np.float32), 100)
        d_img = np.abs(out_np.astype(np.int16) - img_ref.astype(np.int16))
        d_mesmos = np.abs(out_mesmos.astype(np.int16) - img_ref.astype(np.int16))
        difs_w.append(dw)
        difs_img.append((float(d_img.mean()), int(d_img.max())))
        difs_img_mesmos.append((float(d_mesmos.mean()), int(d_mesmos.max())))
        print("  %-26s pesos torch=%s cv2=%s max|dw|=%.2e | imagem: média|d|=%.4f máx=%d (mesmos pesos: %.4f / %d)" % (
            n[:26], np.round(w_ref.numpy(), 4), np.round(w_cv, 4), dw, d_img.mean(), d_img.max(), d_mesmos.mean(), d_mesmos.max()))
    relatorio["lut"] = {"max_dw": max(difs_w), "media_d_img": max(m for m, _x in difs_img), "max_d_img": max(x for _m, x in difs_img),
                        "media_d_img_mesmos_pesos": max(m for m, _x in difs_img_mesmos),
                        "max_d_img_mesmos_pesos": max(x for _m, x in difs_img_mesmos)}

    t = time.perf_counter()
    net_cena = rede_cv(destino_places)
    cv2.setNumThreads(1)
    for _n, b in blobs:
        rodar_cv(net_cena, b)
    t_cena = (time.perf_counter() - t) / len(blobs)
    t = time.perf_counter()
    for _n, img in fotos:
        rodar_cv(net_lut, ia_edicao.preparar_entrada(reduzir(img)))
    t_lut = (time.perf_counter() - t) / len(fotos)
    relatorio["tempo_ms_1thread"] = {"cena_mais_carga": round(t_cena * 1000, 1), "lut_pesos": round(t_lut * 1000, 1)}

    # ---------------- índice e licenças ----------------
    arquivos = {}
    for nome in ("places365_resnet18.onnx", "places365_categorias.json", "lut3d_classificador.onnx", "lut3d_base.npz"):
        caminho = saida / nome
        arquivos[nome] = {"sha256": sha256(caminho), "bytes": caminho.stat().st_size}
    indice = {
        "formato": 1,
        "gerado_em": time.strftime("%Y-%m-%d %H:%M:%S"),
        "gerado_com": {"torch": torch.__version__, "onnx": onnx.__version__, "opencv": cv2.__version__},
        "modelos": {
            "cena": {
                "nome": "ResNet18-Places365 (MIT CSAIL)",
                "versao": VERSAO_CENA % precisao,
                "arquivos": {"rede": "places365_resnet18.onnx", "categorias": "places365_categorias.json"},
                "fonte": URL_PLACES,
                "licenca": "Modelo: CC BY (atribuição) · Código: MIT · Zhou et al., Places: A 10 million Image Database for Scene Recognition, IEEE TPAMI 2017",
            },
            "edicao": {
                "nome": "Image-Adaptive 3D LUT (Zeng et al., FiveK sRGB)",
                "versao": VERSAO_EDICAO,
                "arquivos": {"rede": "lut3d_classificador.onnx", "luts": "lut3d_base.npz"},
                "fonte": URL_LUT_RAW + "pretrained_models/sRGB/",
                "licenca": "Apache-2.0 (repositório HuiZeng/Image-Adaptive-3DLUT) · treinado no MIT-Adobe FiveK (fotos com licença só para pesquisa)",
            },
        },
        "arquivos": arquivos,
        "validacao": relatorio,
    }
    (saida / "modelos_ia.json").write_text(json.dumps(indice, ensure_ascii=False, indent=1), encoding="utf-8")
    (saida / "LICENCAS.txt").write_text(TEXTO_LICENCAS, encoding="utf-8")

    print("\n== Resumo ==")
    print(json.dumps({k: v for k, v in relatorio.items() if k != "fontes"}, ensure_ascii=False, indent=1))
    for nome, info in arquivos.items():
        print("  %-28s %8.1f KB  %s" % (nome, info["bytes"] / 1024.0, info["sha256"][:16]))
    ok = (relatorio["places"]["top1_iguais"] == relatorio["places"]["total"] and relatorio["lut"]["max_dw"] < 1e-3
          and relatorio["lut"]["media_d_img"] < 0.5)
    print("VALIDAÇÃO:", "OK" if ok else "FALHOU")
    return 0 if ok else 1


TEXTO_LICENCAS = """Modelos de IA usados pelo Diagramador Inteligente (rodam só neste computador, sem internet)

1) Reconhecimento de cena: ResNet18-Places365
   Autores: Bolei Zhou, Agata Lapedriza, Aditya Khosla, Aude Oliva, Antonio Torralba (MIT CSAIL).
   "Places: A 10 million Image Database for Scene Recognition", IEEE TPAMI, 2017. http://places2.csail.mit.edu
   Pesos: http://places2.csail.mit.edu/models_places365/resnet18_places365.pth.tar
   Licença dos modelos pré-treinados: Creative Commons Atribuição (CC BY) - dar crédito aos autores.
   Código do repositório CSAILVision/places365: MIT License, Copyright (c) 2017 Bolei Zhou and MIT CSAIL Computer Vision.
   Convertido para ONNX (pesos em float16 quando a validação aprova) sem outras alterações.

2) Edição automática: Image-Adaptive 3D LUT (modelo pré-treinado sRGB / MIT-Adobe FiveK)
   Hui Zeng, Jianrui Cai, Lida Li, Zisheng Cao, Lei Zhang. "Learning Image-adaptive 3D Lookup Tables for
   High Performance Photo Enhancement in Real-time", IEEE TPAMI, 2020. https://github.com/HuiZeng/Image-Adaptive-3DLUT
   Licença do repositório (código e pesos publicados nele): Apache License 2.0.
   Classificador convertido para ONNX (sem a camada Upsample, feita antes com redimensionamento bilinear);
   as 3 LUTs base foram copiadas para lut3d_base.npz sem alteração.
   ATENÇÃO: o modelo foi treinado com as fotos do MIT-Adobe FiveK, cujas licenças (LicenseAdobe.txt e
   LicenseAdobeMIT.txt) permitem usar as FOTOS só para pesquisa, sem fins comerciais. As licenças não falam
   de modelos treinados com elas; para uso comercial, avalie esse ponto (ou troque por um modelo treinado
   com fotos próprias usando o mesmo formato).
"""


if __name__ == "__main__":
    sys.exit(principal())
