# -*- coding: utf-8 -*-
"""
Diagramar e exportar sem abrir a janela.

  py -3 linha_comando.py --fotos "D:\\fotos\\casamento" --produto 30X22 --saida "D:\\saida"
  py -3 linha_comando.py --projeto album.album.json --saida "D:\\saida" --psd
"""

import argparse
import sys
from pathlib import Path

import analise
import diagramar
import exportar
import projeto as proj
from modelos_lamina import listar_modelos
from produtos import adivinhar_produto, carregar_produto


def main(argv=None):
    p = argparse.ArgumentParser(description="Diagrama e exporta um álbum.")
    p.add_argument("--fotos", help="Pasta com as fotos")
    p.add_argument("--projeto", help="Projeto .album.json já salvo")
    p.add_argument("--produto", help="Nome do produto (ex.: 30X22)")
    p.add_argument("--saida", help="Pasta de saída (padrão: 'saida' ao lado das fotos)")
    p.add_argument("--ordem", choices=("nome", "data"), default="nome")
    p.add_argument("--salvar", help="Grava o projeto diagramado neste arquivo")
    p.add_argument("--jpg", action="store_true", help="Exporta JPG (padrão se nada for escolhido)")
    p.add_argument("--psd", action="store_true", help="Exporta PSD pelo Photoshop")
    a = p.parse_args(argv)

    if a.projeto:
        projeto = proj.abrir(a.projeto)
        produto = carregar_produto(a.produto or projeto["produto"])
        caminhos = [f["caminho"] for f in projeto["fotos"]]
    elif a.fotos:
        pasta = Path(a.fotos)
        caminhos = analise.listar_imagens(pasta)
        nome = a.produto or adivinhar_produto([str(pasta)] + [c.name for c in caminhos[:20]])
        if not nome:
            p.error("Informe --produto (não deu para adivinhar pelo nome da pasta).")
        produto = carregar_produto(nome)
        projeto = proj.novo(produto["nome"], pasta, caminhos)
    else:
        p.error("Informe --fotos ou --projeto.")

    print("Produto: %s | %s fotos" % (produto["nome"], len(caminhos)))

    def progresso(n, total, caminho):
        if n % 10 == 0 or n == total:
            print("  analisadas %s de %s" % (n, total))

    info, erros = analise.analisar_fotos(caminhos, progresso)
    for caminho, erro in erros.items():
        print("  ERRO ao ler %s: %s" % (caminho, erro))

    if not a.projeto:
        proj.ordenar_fotos(projeto, info, a.ordem)
        cfg = diagramar.configuracao(produto, projeto.get("config"))
        laminas, sobras = diagramar.diagramar_album(
            proj.fotos_para_diagramar(projeto, info), produto, cfg, listar_modelos(),
        )
        projeto["laminas"] = laminas
        print("Diagramado: %s lâminas" % len(laminas))
        if sobras:
            print("  %s foto(s) não couberam no máximo de lâminas do produto." % len(sobras))

    if a.salvar:
        print("Projeto salvo em %s" % proj.salvar(projeto, a.salvar))

    saida = Path(a.saida) if a.saida else Path(projeto.get("pasta_fotos") or ".") / "saida"
    if a.jpg or not a.psd:
        exportar.exportar_jpg(projeto, produto, info, saida)
    if a.psd:
        import exportar_psd
        exportar_psd.exportar_psd(projeto, produto, info, saida)
    return 0


if __name__ == "__main__":
    sys.exit(main())
