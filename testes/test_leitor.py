"""
Testes do leitor, sem rede, sem browser e sem carregar o modelo.

    python -m unittest testes.test_leitor -v

A lógica pura de cada módulo já tem um selfcheck rico (rode
`python leitor/imagem.py`, etc.); aqui esses selfchecks entram na suíte de
unittest, mais alguns casos de fronteira em cima das funções públicas.
"""
from __future__ import annotations

import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from leitor import _cascata, _motores, imagem   # noqa: E402


class TesteSelfchecks(unittest.TestCase):
    """Os selfchecks dos módulos não podem quebrar em silêncio."""

    def test_imagem(self):
        imagem._selfcheck()

    def test_cascata(self):
        _cascata._selfcheck()

    def test_motores(self):
        _motores._selfcheck()


class TestePosProcessamentoDeImagem(unittest.TestCase):
    """O 'últimos N' é o que leva o OCR de 16% a 64%."""

    def test_pega_os_ultimos_n(self):
        self.assertEqual(imagem._normalizar("o/EdTc", 4), "EDTC")
        self.assertEqual(imagem._normalizar("29JRQ", 4), "9JRQ")

    def test_tamanho_e_parametro(self):
        self.assertEqual(imagem._normalizar("o/AB12CD", 6), "AB12CD")
        self.assertEqual(imagem._normalizar("ABCDEF", 1), "F")

    def test_curto_demais_vira_none(self):
        self.assertIsNone(imagem._normalizar("ab", 4))
        self.assertIsNone(imagem._normalizar("", 4))
        self.assertIsNone(imagem._normalizar(None, 4))


class TesteConsertoDoRiff(unittest.TestCase):
    """
    Um WAV com o campo RIFF size errado engana o módulo `wave` do Python, que
    devolve 0.1s de um arquivo de vários segundos. O conserto reescreve só o
    campo de tamanho.
    """

    def test_corrige_so_o_tamanho(self):
        corpo = b"\x00" * 100
        ruim = b"RIFF" + struct.pack("<I", 12) + b"WAVE" + corpo
        bom = _cascata.consertar_riff(ruim)
        self.assertEqual(struct.unpack("<I", bom[4:8])[0], len(ruim) - 8)
        self.assertEqual(bom[8:], ruim[8:], "só o campo de tamanho pode mudar")

    def test_nao_mexe_no_que_nao_e_riff(self):
        self.assertEqual(_cascata.consertar_riff(b"nao e riff"), b"nao e riff")


class TesteTranscricaoDeFala(unittest.TestCase):
    def test_nome_de_letra_vira_caractere(self):
        self.assertEqual(_cascata.normalizar("dois que b a", 4), "2QBA")
        self.assertEqual(_cascata.normalizar("seis quatro cinco cinco", 4), "6455")

    def test_incompleto_vira_none(self):
        self.assertIsNone(_cascata.normalizar("seis quatro", 4))
        self.assertIsNone(_cascata.normalizar("", 4))


if __name__ == "__main__":
    unittest.main(verbosity=2)
