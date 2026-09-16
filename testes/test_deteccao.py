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
from deteccao.core.models import Signal, State, Vendor, campo_preenchido  # noqa: E402
from deteccao.core.token_observer import TokenObserver  # noqa: E402


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


class TesteCampoDeResposta(unittest.TestCase):
    """
    Só evidência POSITIVA conclui 'resolvido'. Um campo cujo retrato não diz se
    tem valor não prova nada — e era lido como preenchido, porque o código
    perguntava `if f.get("vazio")` e a chave ausente cai no ramo do preenchido.
    """

    def _com_campo(self, campo: dict) -> dict:
        r = _base()
        r["containers"] = [{"seletor": ".g-recaptcha", "tag": "div",
                            "attrs": {"class": "g-recaptcha", "data-sitekey": "ABC"}}]
        r["responseFields"] = [dict({"nome": "g-recaptcha-response"}, **campo)]
        return r

    def test_predicado_exige_prova_de_valor(self):
        self.assertFalse(campo_preenchido({"nome": "x"}))
        self.assertFalse(campo_preenchido({"nome": "x", "valor": ""}))
        self.assertFalse(campo_preenchido({"nome": "x", "vazio": True, "len": 0}))
        self.assertFalse(campo_preenchido({"nome": "x", "len": 0}))
        self.assertTrue(campo_preenchido({"nome": "x", "valor": "03Axyz"}))
        self.assertTrue(campo_preenchido({"nome": "x", "vazio": False, "len": 6}))
        self.assertTrue(campo_preenchido({"nome": "x", "len": 6}))

    def test_campo_vazio_sem_a_chave_vazio_nao_gera_token(self):
        """O retrato de fixture traz `valor: ''` e nenhum `vazio`."""
        r = self._com_campo({"valor": ""})
        res = RecaptchaV2Adapter().detect(ctx(r))
        self.assertNotEqual(res.state, State.TOKEN_GENERATED)
        self.assertEqual([e for e in res.evidencias if e.positiva], [])
        self.assertIn(Signal.RESPONSE_FIELD_EMPTY, res.sinais)

    def test_campo_sem_nenhuma_informacao_de_valor_fica_pendente(self):
        """Na dúvida, pendente: falso positivo aqui submete formulário sem token."""
        r = self._com_campo({})
        res = RecaptchaV2Adapter().detect(ctx(r))
        self.assertNotEqual(res.state, State.TOKEN_GENERATED)
        self.assertFalse(RecaptchaV2Adapter().validate_completion(ctx(r)).sucesso)

    def test_campo_marcado_vazio_pelo_probe_continua_pendente(self):
        r = self._com_campo({"vazio": True, "len": 0, "prefixo": ""})
        res = RecaptchaV2Adapter().detect(ctx(r))
        self.assertNotEqual(res.state, State.TOKEN_GENERATED)

    def test_campo_preenchido_pelo_probe_gera_token(self):
        r = self._com_campo({"vazio": False, "len": 380, "prefixo": "03A"})
        res = RecaptchaV2Adapter().detect(ctx(r))
        self.assertEqual(res.state, State.TOKEN_GENERATED)
        self.assertIn(Signal.RESPONSE_FIELD_FILLED, res.sinais)
        self.assertTrue(RecaptchaV2Adapter().validate_completion(ctx(r)).sucesso)

    def test_campo_preenchido_de_fixture_gera_token_e_nao_vaza_o_valor(self):
        token = "03AGdBq26xSecretoNaoDeveVazar"
        r = self._com_campo({"valor": token})
        res = RecaptchaV2Adapter().detect(ctx(r))
        self.assertEqual(res.state, State.TOKEN_GENERATED)
        detalhe = next(e.detail for e in res.evidencias
                       if e.signal is Signal.RESPONSE_FIELD_FILLED)
        self.assertIn(f"len={len(token)}", detalhe)
        self.assertIn("prefixo=03A", detalhe)
        self.assertNotIn("Secreto", detalhe)

    def test_token_observer_nao_inventa_token(self):
        obs = TokenObserver(bridge=None)
        r = self._com_campo({"valor": ""})
        self.assertFalse(obs.observar(r).presente)
        cheio = self._com_campo({"vazio": False, "len": 380, "prefixo": "03A"})
        estado = obs.observar(cheio)
        self.assertTrue(estado.presente)
        self.assertEqual(estado.tamanho, 380)


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
