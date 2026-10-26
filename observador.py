# -*- coding: utf-8 -*-
"""
Verifica de tempos em tempos se arquivos mudaram (sem biblioteca extra).
O arquivo só conta como pronto quando o tamanho e a data param de mudar,
para não ler uma foto que o Photoshop ainda está gravando.
"""

import os
import threading
import time

from comum import normalizar_caminho


def _assinatura(caminho):
    try:
        st = os.stat(caminho)
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def esperar_pronto(caminho, espera_maxima=120.0, intervalo=0.5, parar=lambda: False):
    """Espera o arquivo existir e ficar com o mesmo tamanho por duas leituras seguidas."""
    limite = time.time() + espera_maxima
    anterior = None
    while time.time() < limite and not parar():
        atual = _assinatura(caminho)
        if atual and atual[1] > 0 and atual == anterior:
            return True
        anterior = atual
        time.sleep(intervalo)
    return False


class Observador:
    """
    vigiar(caminho) e, quando o arquivo mudar e estabilizar, ao_mudar(caminho)
    é chamado (numa thread de fundo: a janela deve repassar com after()).
    """

    def __init__(self, ao_mudar, intervalo=1.0):
        self.ao_mudar = ao_mudar
        self.intervalo = intervalo
        self._vigiados = {}
        self._trava = threading.Lock()
        self._parar = threading.Event()
        self._thread = threading.Thread(target=self._rodar, daemon=True)
        self._thread.start()

    def vigiar(self, caminho):
        with self._trava:
            self._vigiados[normalizar_caminho(caminho)] = {
                "caminho": str(caminho), "visto": _assinatura(caminho), "mudando": None,
            }

    def esquecer(self, caminho):
        with self._trava:
            self._vigiados.pop(normalizar_caminho(caminho), None)

    def vigiados(self):
        with self._trava:
            return [v["caminho"] for v in self._vigiados.values()]

    def parar(self):
        self._parar.set()

    def _rodar(self):
        while not self._parar.wait(self.intervalo):
            with self._trava:
                itens = list(self._vigiados.values())
            for item in itens:
                atual = _assinatura(item["caminho"])
                if atual is None or atual == item["visto"]:
                    item["mudando"] = None
                    continue
                if item["mudando"] != atual:
                    item["mudando"] = atual
                    continue
                item["visto"] = atual
                item["mudando"] = None
                try:
                    self.ao_mudar(item["caminho"])
                except Exception:
                    pass
