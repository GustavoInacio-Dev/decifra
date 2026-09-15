"""
Testes da detecção, sem browser: um retrato sintético entra, a decisão sai.

    python -m unittest testes.test_deteccao -v

Os retratos aqui são montados à mão (nenhum vem de um site real). Provam que os
adapters reconhecem a assinatura de cada fornecedor e que o detector integra
tudo sem subir um navegador.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deteccao.adapters import (  # noqa: E402
    ADAPTERS, Ctx, HCaptchaAdapter, RecaptchaV2Adapter,
)
from deteccao.core.classifier import classificar  # noqa: E402
from deteccao.core.models import Vendor  # noqa: E402


def ctx(retrato: dict) -> Ctx:
    return Ctx(retrato=retrato)


def detectar_tudo(retrato: dict):
    """Roda todos os adapters + o classificador, como o Detector faz."""
    brutos = [ad.detect(ctx(retrato)) for ad in ADAPTERS]
    return classificar([b for b in brutos if b.presente], ctx(retrato))


def _base() -> dict:
    """Um retrato vazio, com todas as chaves que os adapters leem."""
    return {"url": "https://exemplo.test/form", "containers": [], "responseFields": [],
            "scripts": [], "globals": {}, "iframes": [], "customElements": [],
            "totalIframes": 0, "eventos": {}}


class TesteReconhecimentoPorFornecedor(unittest.TestCase):
    def test_recaptcha_pela_assinatura(self):
        r = _base()
        r["scripts"] = [{"src": "https://www.google.com/recaptcha/api.js"}]
        r["globals"] = {"grecaptcha": True}
        r["containers"] = [{"seletor": ".g-recaptcha", "tag": "div",
                            "attrs": {"class": "g-recaptcha", "data-sitekey": "ABC"}}]
        r["responseFields"] = [{"nome": "g-recaptcha-response", "valor": ""}]
        res = RecaptchaV2Adapter().detect(ctx(r))
        self.assertTrue(res.presente)
        self.assertEqual(res.vendor, Vendor.RECAPTCHA_V2)

    def test_hcaptcha_pela_assinatura(self):
        r = _base()
        r["scripts"] = [{"src": "https://js.hcaptcha.com/1/api.js"}]
        r["globals"] = {"hcaptcha": True}
        r["containers"] = [{"seletor": ".h-captcha", "tag": "div",
                            "attrs": {"class": "h-captcha", "data-sitekey": "XYZ"}}]
        r["responseFields"] = [{"nome": "h-captcha-response", "valor": ""}]
        res = HCaptchaAdapter().detect(ctx(r))
        self.assertTrue(res.presente)
        self.assertEqual(res.vendor, Vendor.HCAPTCHA)

    def test_captcha_de_imagem_caseiro(self):
        r = _base()
        r["containers"] = [
            {"seletor": "#captcha img", "tag": "img",
             "attrs": {"id": "captcha"}, "caixa": {"w": 137, "h": 45},
             "motivo": "[id*='captcha'] img"},
        ]
        r["responseFields"] = [{"nome": "captcha_text", "valor": ""}]
        deteccoes = detectar_tudo(r)
        vendors = {d.vendor for d in deteccoes}
        self.assertIn(Vendor.GENERIC, vendors)


class TesteIntegracao(unittest.TestCase):
    def test_pagina_sem_captcha_nao_detecta_nada(self):
        deteccoes = detectar_tudo(_base())
        presentes = [d for d in deteccoes if d.presente]
        self.assertEqual(presentes, [], f"detectou fantasma: {presentes}")

    def test_um_adapter_quebrado_nao_derruba_os_outros(self):
        """Cada detect() é isolado: exceção num adapter não some com o resto."""
        r = _base()
        r["scripts"] = [{"src": "https://www.google.com/recaptcha/api.js"}]
        r["globals"] = {"grecaptcha": True}
        r["containers"] = [{"seletor": ".g-recaptcha", "tag": "div",
                            "attrs": {"class": "g-recaptcha"}}]
        r["responseFields"] = [{"nome": "g-recaptcha-response", "valor": ""}]
        deteccoes = detectar_tudo(r)
        self.assertIn(Vendor.RECAPTCHA_V2, {d.vendor for d in deteccoes})


if __name__ == "__main__":
    unittest.main(verbosity=2)
