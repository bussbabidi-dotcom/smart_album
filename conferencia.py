"""
Conferência do álbum: lista, lâmina por lâmina, o que merece uma olhada antes de exportar.

Cada problema é um dicionário:
  {"nivel": "erro" | "aviso" | "info", "tipo": ..., "lamina": índice (0...) ou None,
   "espaco": índice do espaço ou None, "texto": mensagem para a pessoa}
Erro = vai sair errado na exportação (foto ou fundo sumiu do disco).
Aviso = pode sair feio (espaço vazio, foto repetida, resolução baixa, texto vazio).
Info = só para saber (fotos que não entraram em nenhuma lâmina).
"""

from pathlib import Path

from comum import normalizar_caminho
from produtos import lamina_px

DPI_AVISO = 150          # abaixo disso a foto começa a ficar mole no papel
DPI_ERRO = 100           # abaixo disso fica claramente pixelada
CAMPOS_FUNDO = ("fundo_imagem", "fundo_imagem_esquerda", "fundo_imagem_direita")


def dpi_efetivo(espaco, dados_foto, produto):
    """Resolução real da foto dentro do espaço (pontos por polegada), já contando o zoom do corte."""
    if not dados_foto or not dados_foto.get("w") or not dados_foto.get("h"):
        return None
    W, H = lamina_px(produto)
    larg_px, alt_px = espaco["w"] * W, espaco["h"] * H
    if larg_px <= 0 or alt_px <= 0:
        return None
    aspecto = larg_px / alt_px
    fw, fh = float(dados_foto["w"]), float(dados_foto["h"])
    corte_w = min(fw, fh * aspecto)           # maior corte da foto com a forma do espaço
    zoom = max(1.0, float(((espaco.get("edicao") or {}).get("corte") or {}).get("zoom", 1.0) or 1.0))
    return (corte_w / zoom) * float(produto["dpi"]) / larg_px


def conferir(projeto, produto, info, indices=None):
    """Lista de problemas do álbum (ou só das lâminas em `indices`)."""
    laminas = projeto.get("laminas", [])
    indices = list(range(len(laminas))) if indices is None else [i for i in indices if 0 <= i < len(laminas)]
    problemas = []

    def novo(nivel, tipo, lamina, espaco, texto):
        problemas.append({"nivel": nivel, "tipo": tipo, "lamina": lamina, "espaco": espaco, "texto": texto})

    onde = {}   # foto -> [(lâmina, espaço)] em todo o álbum (repetição vale entre lâminas)
    for li, lamina in enumerate(laminas):
        for ei, espaco in enumerate(lamina.get("espacos", [])):
            if espaco.get("foto"):
                onde.setdefault(normalizar_caminho(espaco["foto"]), []).append((li, ei))

    for li in indices:
        lamina = laminas[li]
        for ei, espaco in enumerate(lamina.get("espacos", [])):
            foto = espaco.get("foto")
            if not foto:
                novo("aviso", "sem_foto", li, ei, "Espaço %d sem foto (sai só o fundo)." % (ei + 1))
                continue
            nome = Path(foto).name
            if not Path(foto).is_file():
                novo("erro", "foto_sumiu", li, ei, "Foto não encontrada no disco: %s." % nome)
                continue
            lugares = onde.get(normalizar_caminho(foto), [])
            if len(lugares) > 1:
                outros = sorted({l + 1 for l, e in lugares if (l, e) != (li, ei)})
                if outros:
                    novo("aviso", "repetida", li, ei, "%s aparece também na(s) lâmina(s) %s." % (
                        nome, ", ".join(str(o) for o in outros[:4]) + ("..." if len(outros) > 4 else "")))
            dpi = dpi_efetivo(espaco, info.get(normalizar_caminho(foto)), produto)
            if dpi is not None and dpi < DPI_AVISO:
                novo("erro" if dpi < DPI_ERRO else "aviso", "resolucao", li, ei,
                     "%s com resolução baixa neste espaço: %d dpi (o ideal é %d ou mais)." % (nome, round(dpi), DPI_AVISO))
        for ti, texto in enumerate(lamina.get("textos", [])):
            if not str(texto.get("texto") or "").strip():
                novo("aviso", "texto_vazio", li, None, "Bloco de texto %d está vazio (não aparece na lâmina)." % (ti + 1))
        for campo in CAMPOS_FUNDO:
            dados = lamina.get(campo) or {}
            fundo = dados.get("caminho")
            if fundo and not str(fundo).startswith("__gerado__:") and not dados.get("gerado") and not Path(fundo).is_file():
                novo("erro", "fundo_sumiu", li, None, "Imagem de fundo não encontrada: %s (sai só a cor)." % Path(fundo).name)
        textura = ((lamina.get("fundo_imagem") or {}).get("gerado") or {}).get("textura_arquivo")
        if textura and not Path(textura).is_file():
            novo("aviso", "textura_sumiu", li, None, "A textura %s não está mais no disco (o fundo sai liso)." % Path(textura).name)

    if indices == list(range(len(laminas))):
        usadas = set(onde)
        sobras = [f for f in projeto.get("fotos", []) if normalizar_caminho(f["caminho"]) not in usadas]
        if sobras:
            novo("info", "sobras", None, None, "%d foto(s) do projeto não entraram em nenhuma lâmina." % len(sobras))
    ordem = {"erro": 0, "aviso": 1, "info": 2}
    problemas.sort(key=lambda p: (ordem[p["nivel"]], 10 ** 6 if p["lamina"] is None else p["lamina"], p["espaco"] or 0))
    return problemas


def resumo(problemas):
    """'2 erros · 5 avisos' ou 'Tudo certo.'"""
    erros = sum(1 for p in problemas if p["nivel"] == "erro")
    avisos = sum(1 for p in problemas if p["nivel"] == "aviso")
    if not erros and not avisos:
        return "Tudo certo: nenhum problema encontrado." if not problemas else "Sem erros nem avisos."
    partes = []
    if erros:
        partes.append("%d erro%s" % (erros, "" if erros == 1 else "s"))
    if avisos:
        partes.append("%d aviso%s" % (avisos, "" if avisos == 1 else "s"))
    return " · ".join(partes)
