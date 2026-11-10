# -*- coding: utf-8 -*-
import os
import random
import sys
import tempfile
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.dont_write_bytecode = True

# Cache de análise, fundos gerados e drive.json vão para %LOCALAPPDATA%\smart_album;
# precisa ser trocado antes de importar os módulos do programa.
os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="smart_album_testes_")

from PIL import Image  # noqa: E402

import analise  # noqa: E402
import diagramar  # noqa: E402
import projeto as proj  # noqa: E402
from produtos import carregar_produto  # noqa: E402

TAMANHOS_FOTO = [(1500, 1000), (1000, 1500), (1200, 1200), (2400, 1000), (1000, 2000)]


def criar_fotos(pasta, quantidade, semente=7):
    sorteio = random.Random(semente)
    pasta.mkdir(parents=True, exist_ok=True)
    for i in range(quantidade):
        w, h = sorteio.choice(TAMANHOS_FOTO)
        cor = tuple(sorteio.randrange(256) for _ in range(3))
        Image.new("RGB", (w, h), cor).save(pasta / ("foto_%03d.jpg" % i), quality=80)
    return analise.listar_imagens(pasta)


def montar_album(pasta, nome_produto, quantidade, semente=7):
    produto = carregar_produto(nome_produto)
    caminhos = criar_fotos(pasta, quantidade, semente)
    projeto = proj.novo(produto["nome"], pasta, caminhos)
    info, erros = analise.analisar_fotos(caminhos)
    assert not erros
    proj.ordenar_fotos(projeto, info, "nome")
    cfg = diagramar.configuracao(produto, projeto.get("config"))
    fotos = proj.fotos_para_diagramar(projeto, info)
    laminas, sobras = diagramar.diagramar_album(fotos, produto, cfg, ())
    projeto["laminas"] = laminas
    return {
        "produto": produto,
        "projeto": projeto,
        "info": info,
        "cfg": cfg,
        "fotos": fotos,
        "laminas": laminas,
        "sobras": sobras,
    }


@pytest.fixture(scope="session", params=[("20X20", 18), ("30X22", 40)], ids=lambda p: "%s-%dfotos" % p)
def album(request, tmp_path_factory):
    nome_produto, quantidade = request.param
    return montar_album(tmp_path_factory.mktemp("fotos_" + nome_produto), nome_produto, quantidade)
