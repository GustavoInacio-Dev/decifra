"""
Testes da API Generic. Rodam sem rede, sem browser e sem carregar o modelo.

    python -m unittest api.test_api -v

Escopo desta API é leitura: imagem e áudio, bytes -> texto. Nada de detecção,
roteamento ou passagem por proteção de fornecedor — ver o docstring do módulo
`app.py`.
"""
from __future__ import annotations

import base64
import io
import unittest
import wave
from pathlib import Path

from fastapi.testclient import TestClient

from . import app as api


class TesteEscopoDaApi(unittest.TestCase):
    """
    A API expõe só leitura, e nada mais. Este é o contrato do projeto.
    """

    def setUp(self):
        self.cliente = TestClient(api.app)

    def test_rotas_expostas_sao_so_de_leitura(self):
        rotas = {r.path for r in api.app.routes if hasattr(r, "methods")}
        esperadas = {"/saude", "/health", "/ler", "/ler/imagem",
                     "/ler/imagem/arquivo", "/ler/audio"}
        # o FastAPI acrescenta /docs, /redoc, /openapi.json sozinho
        proprias = {p for p in rotas
                    if not p.startswith(("/docs", "/redoc", "/openapi"))}
        self.assertEqual(proprias, esperadas, f"rotas inesperadas: {proprias}")

    def test_a_api_nao_dirige_browser(self):
        """
        Guarda de fonte: a API só LÊ bytes. Se alguém colar aqui código que
        dirige um browser ou fala com um widget de fornecedor, isto quebra.
        """
        fonte = (Path(__file__).resolve().parent / "app.py").read_text(encoding="utf-8")
        for proibido in ("webdriver", "selenium", "execute_script"):
            self.assertNotIn(proibido, fonte,
                             f"api/app.py passou a mexer em browser: {proibido}")

    def test_saude_diz_o_escopo_e_o_que_nao_faz(self):
        d = self.cliente.get("/saude").json()
        self.assertIn("imagem", d["escopo"])
        self.assertIn("udio", d["escopo"])          # áudio, sem depender de acento
        self.assertTrue(d["nao_faz"], "/saude tem de dizer o que a API não faz")


class TesteSaude(unittest.TestCase):
    def setUp(self):
        self.cliente = TestClient(api.app)

    def test_reporta_cada_capacidade(self):
        r = self.cliente.get("/saude")
        self.assertEqual(r.status_code, 200)
        d = r.json()
        for chave in ("ok", "escopo", "autenticacao", "ler_imagem", "ler_audio",
                      "nao_faz"):
            self.assertIn(chave, d)

    def test_health_e_apelido_de_saude(self):
        self.assertEqual(self.cliente.get("/health").status_code, 200)

    def test_audio_reporta_disponibilidade_por_lingua(self):
        """
        Token do Wit é por app e app tem língua fixa: dizer só "disponível"
        escondia que o pt-BR funciona e outra língua não. Medido, o app inglês
        devolve 'Quattro' para "quatro".
        """
        a = self.cliente.get("/saude").json()["ler_audio"]
        self.assertIn("por_lingua", a)
        for idioma in ("pt-BR", "en-US"):
            self.assertIn(idioma, a["por_lingua"])
            self.assertIn("disponivel", a["por_lingua"][idioma])

    def test_saude_nao_carrega_o_modelo(self):
        """1,34 GB não podem entrar em memória só para responder /saude."""
        self.cliente.get("/saude")
        self.assertFalse(api._ocr_carregado())


class TesteFronteiraDeEntrada(unittest.TestCase):
    """Limite e validação são fronteira de confiança: nunca 500, sempre motivo."""

    def setUp(self):
        self.cliente = TestClient(api.app)

    def test_entrada_ruim_da_422_com_motivo(self):
        casos = [
            ("/ler/imagem", {"imagem_b64": "!!!nao-e-base64!!!"}),
            ("/ler/imagem", {"imagem_b64": ""}),
            ("/ler/imagem", {"imagem_b64": base64.b64encode(b"x" * 40).decode(),
                             "tamanho": 0}),
            ("/ler/imagem", {"imagem_b64": base64.b64encode(b"x" * 40).decode(),
                             "tamanho": 999}),
            ("/ler/audio", {"audio_b64": ""}),
            ("/ler/audio", {"audio_b64": "@@@"}),
        ]
        for rota, corpo in casos:
            r = self.cliente.post(rota, json=corpo)
            self.assertEqual(r.status_code, 422, (rota, corpo))
            self.assertTrue(r.json().get("detail"),
                            "422 sem motivo não ajuda ninguém")

    def test_corpo_acima_do_limite_da_413(self):
        grande = base64.b64encode(b"\x00" * (api.MAX_IMAGEM_BYTES + 10)).decode()
        r = self.cliente.post("/ler/imagem", json={"imagem_b64": grande})
        self.assertEqual(r.status_code, 413)

        grande_audio = base64.b64encode(b"\x00" * (api.MAX_AUDIO_BYTES + 10)).decode()
        r = self.cliente.post("/ler/audio", json={"audio_b64": grande_audio})
        self.assertEqual(r.status_code, 413)

    def test_ler_sem_nada_da_422(self):
        r = self.cliente.post("/ler", json={"tamanho": 4})
        self.assertEqual(r.status_code, 422)
        self.assertIn("imagem_b64", str(r.json().get("detail")))

    def test_arquivo_vazio_da_422(self):
        r = self.cliente.post("/ler/imagem/arquivo",
                              files={"arquivo": ("v.png", b"", "image/png")})
        self.assertEqual(r.status_code, 422)

    def test_data_uri_e_aceito_direto(self):
        """
        O captcha costuma vir como data URI no HTML. Aceitar a string inteira
        poupa o cliente de fatiar.
        """
        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR4nGMAAQAABQAB"
            "oIJXOQAAAABJRU5ErkJggg==")
        dados = api._decodificar(
            "data:image/png;base64," + base64.b64encode(png).decode(),
            api.MAX_IMAGEM_BYTES, "imagem_b64")
        self.assertEqual(dados, png)


class TesteAutenticacao(unittest.TestCase):
    def setUp(self):
        self.cliente = TestClient(api.app)

    def test_token_exigido_quando_configurado(self):
        api.TOKEN_ESPERADO = "segredo"
        try:
            self.assertEqual(self.cliente.get("/saude").status_code, 401)
            self.assertEqual(
                self.cliente.get("/saude",
                                 headers={"X-Decifra-Token": "errado"}).status_code, 401)
            self.assertEqual(
                self.cliente.get("/saude",
                                 headers={"X-Decifra-Token": "segredo"}).status_code, 200)
        finally:
            api.TOKEN_ESPERADO = ""

    def test_todas_as_rotas_exigem_o_token(self):
        """Uma rota esquecida sem `_autorizar` é OCR de graça para quem achar."""
        api.TOKEN_ESPERADO = "segredo"
        try:
            payloads = {
                "/ler/imagem": {"imagem_b64": "AAAA"},
                "/ler/audio": {"audio_b64": "AAAA"},
                "/ler": {"imagem_b64": "AAAA"},
            }
            for rota, corpo in payloads.items():
                self.assertEqual(self.cliente.post(rota, json=corpo).status_code, 401,
                                 f"{rota} não exigiu token")
            self.assertEqual(
                self.cliente.post("/ler/imagem/arquivo",
                                  files={"arquivo": ("a.png", b"x", "image/png")}
                                  ).status_code, 401)
        finally:
            api.TOKEN_ESPERADO = ""


class TesteNormalizacaoDeFala(unittest.TestCase):
    """Nome de letra em pt-BR -> caractere. Casos medidos em áudio real."""

    def test_casos_medidos(self):
        casos = {
            ("6 4 5 5", 4): "6455",
            ("dois que b a", 4): "2QBA",
            ("9 G 9 G", 4): "9G9G",
            ("0 L 0 l", 4): "0L0L",
            ("w 6 wv", 4): "W6WV",          # token colado: expande caractere a caractere
            ("seis, quatro, cinco, cinco", 4): "6455",
            ("zero. L zero. L", 4): "0L0L",
        }
        for (cru, tam), esperado in casos.items():
            self.assertEqual(api._normalizar_fala(cru, tam), esperado, cru)

    def test_incompleto_devolve_none_em_vez_de_palpite(self):
        for cru in ("f r l", "88 a", "psl l l", "M. P", "", None):
            self.assertIsNone(api._normalizar_fala(cru, 4), repr(cru))

    def test_tamanho_e_respeitado(self):
        self.assertEqual(api._normalizar_fala("a be ce de e efe", 6), "ABCDEF")
        self.assertIsNone(api._normalizar_fala("a be ce de e efe", 4))


class TesteRotaLer(unittest.TestCase):
    """
    O `/ler` combina as duas vias. Aqui os leitores são trocados por falsos:
    o que se testa é a ORDEM e o critério de concordância, que são a lógica
    medida — não o OCR nem o transcritor.
    """

    def setUp(self):
        self.cliente = TestClient(api.app)
        self._img, self._aud = api.ler_imagem, api.ler_audio

    def tearDown(self):
        api.ler_imagem, api.ler_audio = self._img, self._aud

    @staticmethod
    def _b64(n=40):
        return base64.b64encode(b"x" * n).decode()

    def test_imagem_vem_primeiro_e_encerra(self):
        chamadas = []
        api.ler_imagem = lambda d, t: (chamadas.append("img"),
                                       {"texto": "ABCD", "bruto": "ABCD", "ms": 1})[1]
        api.ler_audio = lambda d, t, i: self.fail("áudio não devia ser chamado")
        d = self.cliente.post("/ler", json={"imagem_b64": self._b64(),
                                            "audio_b64": None}).json()
        self.assertTrue(d["resolvido"])
        self.assertEqual(d["texto"], "ABCD")
        self.assertEqual(d["via"], "ocr_imagem")
        self.assertEqual(chamadas, ["img"])

    def test_audio_entra_quando_a_imagem_nao_fecha(self):
        api.ler_imagem = lambda d, t: {"texto": None, "bruto": "o/AB", "ms": 1}
        api.ler_audio = lambda d, t, i: {"texto": "WXYZ", "bruto": "w x y z",
                                         "motor": "wit", "ms": 2}
        d = self.cliente.post("/ler", json={"imagem_b64": self._b64(),
                                            "audio_b64": self._b64()}).json()
        self.assertEqual(d["texto"], "WXYZ")
        self.assertEqual(d["via"], "audio")
        self.assertEqual(len(d["tentativas"]), 2)

    def test_concordancia_marca_confianca_alta(self):
        api.ler_imagem = lambda d, t: {"texto": "6455", "bruto": "6455", "ms": 1}
        api.ler_audio = lambda d, t, i: {"texto": "6455", "bruto": "seis quatro",
                                         "motor": "wit", "ms": 2}
        d = self.cliente.post("/ler", json={"imagem_b64": self._b64(),
                                            "audio_b64": self._b64()}).json()
        self.assertTrue(d["concordancia"])
        self.assertEqual(d["confianca"], "alta")

    def test_divergencia_nao_marca_confianca_alta(self):
        api.ler_imagem = lambda d, t: {"texto": "6455", "bruto": "6455", "ms": 1}
        api.ler_audio = lambda d, t, i: {"texto": "6456", "bruto": "x", "motor": "wit",
                                         "ms": 2}
        d = self.cliente.post("/ler", json={"imagem_b64": self._b64(),
                                            "audio_b64": self._b64()}).json()
        self.assertFalse(d["concordancia"])
        self.assertEqual(d["confianca"], "normal")

    def test_nenhuma_via_fecha_devolve_nao_resolvido_com_motivo(self):
        api.ler_imagem = lambda d, t: {"texto": None, "bruto": "o/", "ms": 1}
        api.ler_audio = lambda d, t, i: {"texto": None, "bruto": "seis",
                                         "motor": None, "ms": 2}
        d = self.cliente.post("/ler", json={"imagem_b64": self._b64(),
                                            "audio_b64": self._b64()}).json()
        self.assertFalse(d["resolvido"])
        self.assertIsNone(d["texto"])
        self.assertIn("recarregue", d["detalhe"])
        self.assertIsNone(d["confianca"])


class TesteImagemMalformada(unittest.TestCase):
    """
    Bytes que não são imagem são ENTRADA do cliente, não falha nossa: 422 com
    motivo, nunca 500.

    Achado auditando o README contra o código, em 11/09: um PNG truncado subia
    `UnidentifiedImageError` do Pillow sem tratamento nas três rotas de leitura
    de imagem. Os 23 testes anteriores não pegaram porque nenhum deles chegava
    ao OCR — chegar ao OCR carregaria 1,34 GB de modelo. Por isso aqui o `_ocr`
    é trocado por um falso que levanta o mesmo erro: cobre a guarda sem carregar
    modelo nenhum.
    """

    def setUp(self):
        self.cliente = TestClient(api.app)
        self._ocr_real, self._img, self._aud = api._ocr, api.ler_imagem, api.ler_audio

        class OcrQueNaoDecodifica:
            ULTIMO_DIAGNOSTICO: dict = {}

            @staticmethod
            def ler(dados, tamanho):
                raise OSError("cannot identify image file")

        api._ocr = lambda: OcrQueNaoDecodifica()

    def tearDown(self):
        api._ocr, api.ler_imagem, api.ler_audio = self._ocr_real, self._img, self._aud

    @staticmethod
    def _b64(dados=bytes.fromhex("89504e470d0a1a0a") + bytes(40)):
        return base64.b64encode(dados).decode()

    def test_ler_imagem_devolve_422_e_nao_500(self):
        r = self.cliente.post("/ler/imagem", json={"imagem_b64": self._b64(),
                                                   "tamanho": 4})
        self.assertEqual(r.status_code, 422)
        self.assertIn("ilegível", r.json()["detail"])

    def test_ler_imagem_arquivo_devolve_422_e_nao_500(self):
        r = self.cliente.post(
            "/ler/imagem/arquivo",
            files={"arquivo": ("c.png", io.BytesIO(b"nao sou imagem"), "image/png")},
            data={"tamanho": 4})
        self.assertEqual(r.status_code, 422)
        self.assertIn("ilegível", r.json()["detail"])

    def test_imagem_ruim_nao_mata_a_via_de_audio(self):
        """O motivo de o `/ler` existir é ter DUAS vias independentes."""
        api.ler_audio = lambda d, t, i: {"texto": "WXYZ", "bruto": "w x y z",
                                         "motor": "wit", "ms": 2}
        d = self.cliente.post("/ler", json={"imagem_b64": self._b64(),
                                            "audio_b64": self._b64(b"x" * 40)}).json()
        self.assertTrue(d["resolvido"])
        self.assertEqual(d["texto"], "WXYZ")
        self.assertEqual(d["via"], "audio")
        self.assertEqual(len(d["tentativas"]), 2)
        self.assertIn("ilegível", d["tentativas"][0]["erro"])

    def test_sem_audio_para_degradar_o_erro_sobe(self):
        r = self.cliente.post("/ler", json={"imagem_b64": self._b64()})
        self.assertEqual(r.status_code, 422)


class TesteAudioMalformado(unittest.TestCase):
    """
    Mesmo defeito da imagem, na outra fronteira de entrada: WAV inválido subia
    `wave.Error` (que NÃO herda de OSError) sem tratamento.

    Achado ao testar a degradação da imagem no `/ler` com um WAV de mentira —
    ou seja, o teste de um bug encontrou o irmão dele.
    """

    def setUp(self):
        self.cliente = TestClient(api.app)
        self._casc, self._img = api._cascata_audio, api.ler_imagem

        class CascataQueNaoDecodifica:
            ULTIMO_DIAGNOSTICO: dict = {}

            @staticmethod
            def ler_bytes(dados, tamanho):
                raise wave.Error("not a WAVE file")

        api._cascata_audio = lambda: CascataQueNaoDecodifica()

    def tearDown(self):
        api._cascata_audio, api.ler_imagem = self._casc, self._img

    @staticmethod
    def _b64(dados=b"nao sou wav"):
        return base64.b64encode(dados).decode()

    def test_ler_audio_devolve_422_e_nao_500(self):
        r = self.cliente.post("/ler/audio", json={"audio_b64": self._b64(),
                                                  "tamanho": 4, "idioma": "pt-BR"})
        self.assertEqual(r.status_code, 422)
        self.assertIn("ilegível", r.json()["detail"])

    def test_audio_ruim_nao_apaga_a_resposta_da_imagem(self):
        api.ler_imagem = lambda d, t: {"texto": "ABCD", "bruto": "ABCD", "ms": 1}
        d = self.cliente.post("/ler", json={"imagem_b64": self._b64(b"x" * 40),
                                            "audio_b64": self._b64()}).json()
        self.assertTrue(d["resolvido"])
        self.assertEqual(d["texto"], "ABCD")
        self.assertIn("ilegível", d["tentativas"][1]["erro"])

    def test_as_duas_vias_ilegiveis_devolvem_422(self):
        """Nenhuma via rodou: o problema é a entrada, não o captcha."""
        # Aqui o `_ocr` é trocado, não o `ler_imagem`: é a guarda REAL do
        # `ler_imagem` que precisa rodar, senão o teste mede o stub.
        class OcrQueNaoDecodifica:
            ULTIMO_DIAGNOSTICO: dict = {}

            @staticmethod
            def ler(dados, tamanho):
                raise OSError("cannot identify image file")

        ocr_real, api._ocr = api._ocr, lambda: OcrQueNaoDecodifica()
        self.addCleanup(lambda: setattr(api, "_ocr", ocr_real))
        r = self.cliente.post("/ler", json={"imagem_b64": self._b64(),
                                            "audio_b64": self._b64()})
        self.assertEqual(r.status_code, 422)
        self.assertIn("imagem ilegível", r.json()["detail"])
        self.assertIn("áudio ilegível", r.json()["detail"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
