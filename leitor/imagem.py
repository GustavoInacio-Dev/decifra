"""
Leitor de captcha de imagem: lê o texto de uma imagem distorcida, localmente.

Este módulo NÃO passa por proteção de fornecedor e não fabrica token nenhum. Ele
faz uma coisa só: recebe os bytes de uma imagem de captcha e devolve o texto que
está escrito nela, usando um modelo de OCR que roda na sua máquina. Nenhum
serviço externo, nenhuma chamada paga.

Como funciona: um TrOCR (`anuashok/ocr-captcha-v3`, um fine-tune do
`microsoft/trocr-base-printed`) lê a imagem. O truque que faz a taxa saltar não
está no modelo — está no pós-processamento, explicado em `_normalizar`.

Medido num captcha de 4 caracteres (fonte serifada, sem distorção geométrica,
banco de 150 imagens rotuladas):

    leitura correta por tentativa    64% (cru: 16%)
    com retentativa                 ~85% em 2, ~94% em 3, ~98% em 4
    tempo por leitura                1.3-1.9 s em CPU
    modelo em disco                  1.34 GB (baixa uma vez, fica em cache)

Retentar costuma ser barato num captcha de imagem: errar recarrega e vem outro.
Por isso a estratégia é "responder e retentar", não "acertar de primeira".

O COMPRIMENTO DA RESPOSTA É PARÂMETRO, não constante: `ler`, `ler_da_pagina`,
`preencher` e `resolver` aceitam `tamanho=` (default 4). É o comprimento que
decide onde o pós-processamento corta a saída do modelo, então é o primeiro
lugar a ajustar ao apontar isto para um captcha de outro tamanho:

    preencher(driver, tamanho=6)

OS SELETORES SÃO SEUS: este módulo não sabe como é a sua página. Configure
`SELETORES` (abaixo) com os elementos do seu formulário antes de usar as funções
que tocam o DOM. As funções que só leem bytes (`ler`, `_normalizar`) não
dependem disso.
"""
from __future__ import annotations

import base64
import re
import time

#: Repositório do modelo no Hugging Face.
MODELO = "anuashok/ocr-captcha-v3"

#: Quantos caracteres a resposta tem, quando ninguém informa.
#:
#: É DEFAULT, não verdade absoluta: todas as funções aceitam `tamanho=`. O
#: comprimento sustenta o pós-processamento — decide quantos caracteres cortar —,
#: então passar o valor errado degrada a leitura sem dar erro.
TAMANHO_PADRAO = 4

#: Onde estão os elementos na SUA página. Ajuste para o seu formulário.
#:
#:   imagem  -> o <img> do captcha. Se a imagem for um data URI embutido no HTML
#:              (comum), não há download extra nem risco de pegar outra rodada.
#:   campo   -> onde a resposta é digitada.
#:   flag    -> opcional: campo escondido que a página marca ao responder. Deixe
#:              None se sua página não tiver.
SELETORES = {
    "imagem": "img.captcha",
    "campo": "input#captcha",
    "flag": None,
}

#: Palavras que, no texto de um alerta de erro, indicam que o captcha foi
#: recusado. Ajuste para a mensagem da sua página. Um alerta do servidor é um
#: oráculo melhor que qualquer heurística de DOM.
MARCAS_RECUSA = ("captcha", "imagem", "incorret")

#: Diagnóstico da última chamada.
ULTIMO_DIAGNOSTICO: dict[str, object] = {}

_modelo = None
_processador = None


# --------------------------------------------------------------------------- #
# Leitura da imagem
# --------------------------------------------------------------------------- #

def carregar():
    """
    Carrega modelo e processador (uma vez só). Chame no boot se não quiser pagar
    ~20 s de carga na primeira leitura.
    """
    global _modelo, _processador
    if _modelo is None:
        from transformers import TrOCRProcessor, VisionEncoderDecoderModel
        _processador = TrOCRProcessor.from_pretrained(MODELO)
        _modelo = VisionEncoderDecoderModel.from_pretrained(MODELO)
        _modelo.eval()
    return _processador, _modelo


def _normalizar(bruto, tamanho: int = TAMANHO_PADRAO):
    """
    Saída do modelo -> resposta do captcha, com EXATAMENTE `tamanho` caracteres.

    ISTO É O QUE FAZ O MÉTODO FUNCIONAR, e não é detalhe: o modelo lê a poluição
    do fundo como caractere e devolve lixo nas pontas. Medido, com tamanho=4:

        '/Cnyx'   -> CNYX      'o/EdTc' -> EDTC
        '29JRQ'   -> 9JRQ      '202754' -> 2754
        'Sc4dLL'  -> 4DLL      'XY3FF'  -> Y3FF

    Cru, o modelo acerta 16%. Descartando não-alfanuméricos e pegando os ÚLTIMOS
    `tamanho`, acerta 64%. Pegar os PRIMEIROS dá 30% — o lixo é mais frequente
    no início, porque a varredura começa na margem esquerda da imagem.

    Devolve None quando sobra MENOS que `tamanho`: aí faltou caractere de
    verdade, e completar com palpite só gastaria uma tentativa à toa.

    Maiúsculas porque a maioria dos captchas é insensível a caixa; se o seu não
    for, remova o `.upper()`.
    """
    if tamanho < 1:
        raise ValueError(f"tamanho tem de ser >= 1, recebi {tamanho!r}")
    limpo = re.sub(r"[^0-9A-Za-z]", "", bruto or "")
    if len(limpo) < tamanho:
        return None
    return limpo[-tamanho:].upper()


def ler(png, tamanho: int = TAMANHO_PADRAO):
    """
    Bytes PNG do captcha -> `tamanho` caracteres em maiúsculas, ou None.

    O pré-processamento (RGBA sobre fundo branco) é o do próprio model card.
    Ampliar a imagem NÃO ajuda: medido 64% em 1x, 63% em 2x, 63% em 3x — e
    ampliar custa o mesmo tempo. Fica em 1x.
    """
    from io import BytesIO

    import torch
    from PIL import Image

    proc, mod = carregar()
    im = Image.open(BytesIO(png)).convert("RGBA")
    fundo = Image.new("RGBA", im.size, (255, 255, 255))
    im = Image.alpha_composite(fundo, im).convert("RGB")
    with torch.no_grad():
        pv = proc(im, return_tensors="pt").pixel_values
        # Folga sobre `tamanho`, não número fixo: o modelo emite lixo além da
        # resposta, e é justamente esse excesso que `_normalizar` descarta. Com
        # teto curto o corte perderia caractere do fim, que é a parte que vale.
        ids = mod.generate(pv, max_new_tokens=max(12, tamanho * 3))
    bruto = proc.batch_decode(ids, skip_special_tokens=True)[0].strip()
    ULTIMO_DIAGNOSTICO["saida_crua"] = bruto
    return _normalizar(bruto, tamanho)


# --------------------------------------------------------------------------- #
# Página (usa SELETORES)
# --------------------------------------------------------------------------- #

def presente(driver) -> bool:
    """True se a página atual tem o campo de captcha configurado."""
    from selenium.webdriver.common.by import By
    try:
        return bool(driver.find_elements(By.CSS_SELECTOR, SELETORES["campo"]))
    except Exception:
        return False


def imagem(driver):
    """PNG do captcha da página atual, decodificado do data URI."""
    try:
        src = driver.execute_script(
            "var e=document.querySelector(arguments[0]);return e?e.src:null;",
            SELETORES["imagem"])
    except Exception:
        return None
    if not src or "base64," not in src:
        return None
    try:
        return base64.b64decode(src.split("base64,", 1)[1])
    except Exception:
        return None


def ler_da_pagina(driver, tamanho: int = TAMANHO_PADRAO):
    """Lê o captcha da página atual sem digitar nada."""
    png = imagem(driver)
    if png is None:
        ULTIMO_DIAGNOSTICO.update(estado="sem_captcha")
        return None
    return ler(png, tamanho)


def preencher(driver, tamanho: int = TAMANHO_PADRAO):
    """
    Lê o captcha e digita no campo. Devolve o que digitou, ou None.

    Digita com send_keys (evento real de teclado) em vez de setar `.value` por
    JS: muitas páginas escutam onkeypress no campo.
    """
    inicio = time.monotonic()
    resposta = ler_da_pagina(driver, tamanho)
    if resposta is None:
        return None
    digitar(driver, resposta)
    ULTIMO_DIAGNOSTICO.update(estado="preenchido", resposta=resposta,
                              duracao_s=round(time.monotonic() - inicio, 2))
    return resposta


def digitar(driver, resposta: str) -> None:
    """Digita uma resposta já conhecida no campo do captcha."""
    from selenium.webdriver.common.by import By

    campo = driver.find_element(By.CSS_SELECTOR, SELETORES["campo"])
    campo.clear()
    campo.send_keys(resposta)
    if SELETORES.get("flag"):
        try:
            driver.execute_script(
                "var e=document.querySelector(arguments[0]);if(e)e.value='1';",
                SELETORES["flag"])
        except Exception:
            pass


def captcha_recusado(driver):
    """
    Consome o alert da página e diz se ele recusou o captcha.

      True  -> a página disse que o código está errado: recarregue e tente outra
      False -> apareceu alert, mas de outro assunto (texto no diagnóstico)
      None  -> não apareceu alert nenhum

    CONSUMIR O ALERT NÃO É OPCIONAL: alert pendente trava a sessão do Selenium e
    toda chamada seguinte estoura UnexpectedAlertPresentException.
    """
    try:
        alerta = driver.switch_to.alert
        texto = alerta.text or ""
        alerta.accept()
    except Exception:
        ULTIMO_DIAGNOSTICO["alerta"] = None
        return None
    ULTIMO_DIAGNOSTICO["alerta"] = texto
    baixo = texto.lower()
    return any(m in baixo for m in MARCAS_RECUSA)


# --------------------------------------------------------------------------- #
# Laço com retentativa
# --------------------------------------------------------------------------- #

def resolver(driver, submeter, preparar=None, tentativas: int = 4,
             recarregar=None, tamanho: int = TAMANHO_PADRAO,
             reserva_audio: bool = True) -> bool:
    """
    Laço completo: preenche o captcha, submete, e retenta se a página recusar.

      submeter(driver)    -> obrigatório. Clica no botão de enviar.
      preparar(driver)    -> opcional, chamado ANTES de cada tentativa, para
                             repreencher os campos (o reload os limpa).
      recarregar(driver)  -> opcional, como buscar um captcha novo.
                             Padrão: driver.get(driver.current_url).
      tamanho             -> quantos caracteres a resposta tem (default 4).
      reserva_audio       -> na ÚLTIMA tentativa, tenta o canal de áudio antes
                             do OCR. Ver abaixo.

    Retorna True se a página aceitou o captcha. `tentativas=4` vem de medição: a
    ~64% por tentativa, 4 dá ~98%.

    A RESERVA DE ÁUDIO entra só na última tentativa, ou seja em ~5% dos itens, e
    é aí que ela vale: as duas vias falham em casos DIFERENTES — o áudio acerta
    em 9 de 50 casos onde o OCR erra. Ver `_cascata.py`.

    Ela degrada sozinha para o OCR quando não dá para usar: sem `numpy`, sem o
    transcritor, sem rede, ou sem botão de áudio na página. `reserva_audio=False`
    desliga de vez.

    Não decide nada sobre o RESULTADO do envio — só sobre o captcha.
    """
    ULTIMO_DIAGNOSTICO.clear()
    inicio = time.monotonic()

    for n in range(1, tentativas + 1):
        if preparar is not None:
            preparar(driver)

        por_audio = None
        if reserva_audio and n == tentativas and tentativas > 1:
            por_audio = _reserva_audio(driver, tamanho)

        if por_audio is not None:
            digitar(driver, por_audio)
            ULTIMO_DIAGNOSTICO.update(resposta=por_audio, via="audio_reserva")
        elif preencher(driver, tamanho) is None:
            # Sem captcha na página: nada a resolver, e isto NÃO é falha.
            ULTIMO_DIAGNOSTICO.update(estado="sem_captcha", tentativas=n,
                                      duracao_s=round(time.monotonic() - inicio, 2))
            return True
        else:
            ULTIMO_DIAGNOSTICO["via"] = "ocr_imagem"

        submeter(driver)
        if not captcha_recusado(driver):
            ULTIMO_DIAGNOSTICO.update(estado="aceito", tentativas=n,
                                      duracao_s=round(time.monotonic() - inicio, 2))
            return True

        if n < tentativas:
            if recarregar is not None:
                recarregar(driver)
            else:
                driver.get(driver.current_url)

    ULTIMO_DIAGNOSTICO.update(estado="recusado", tentativas=tentativas,
                              duracao_s=round(time.monotonic() - inicio, 2),
                              detalhe="a página recusou o código em todas as "
                                      "tentativas; reenfileirar o item")
    return False


def _reserva_audio(driver, tamanho: int):
    """
    Canal de áudio, se der. None em QUALQUER problema — nunca levanta.

    Import tardio e dentro de try porque a cascata depende de um transcritor:
    quem não o tem continua funcionando com OCR em vez de estourar.
    """
    try:
        from . import _cascata
    except Exception:
        try:
            import _cascata            # rodando como script nesta pasta
        except Exception:
            return None
    try:
        texto = _cascata.ler(driver, tamanho)
        ULTIMO_DIAGNOSTICO["audio"] = dict(_cascata.ULTIMO_DIAGNOSTICO)
        return texto
    except Exception as exc:
        ULTIMO_DIAGNOSTICO["audio"] = {"estado": f"erro:{type(exc).__name__}"}
        return None


# --------------------------------------------------------------------------- #
# Selfcheck: roda sem browser e sem modelo
# --------------------------------------------------------------------------- #

def _selfcheck():
    """
    Checa a lógica pura. O pós-processamento é testado com as saídas CRUAS que o
    modelo devolveu em imagens reais — se alguém trocar 'últimos N' por outra
    coisa, isto quebra.
    """
    medidas = {
        "/Cnyx": "CNYX", "o/EdTc": "EDTC", "29JRQ": "9JRQ", "202754": "2754",
        "Sc4dLL": "4DLL", "XY3FF": "Y3FF", "/2xUU": "2XUU", "33vUT": "3VUT",
        "PTRR": "PTRR", "9JBA": "9JBA", "o/Ab8A": "AB8A",
    }
    for bruto, esperado in medidas.items():
        assert _normalizar(bruto) == esperado, (bruto, _normalizar(bruto), esperado)

    # curto demais não vira palpite
    assert _normalizar("ab") is None
    assert _normalizar("") is None
    assert _normalizar(None) is None
    # 'primeiros 4' foi medido PIOR (30% contra 64%); guarda contra a troca
    assert _normalizar("29JRQ") != "29JR"

    # ---- o tamanho é parâmetro, não constante ----
    assert _normalizar("o/AB12CD", 6) == "AB12CD"
    assert _normalizar("xyzAB12CD", 8) == "YZAB12CD"
    assert _normalizar("/9JRQ", 4) == "9JRQ"
    assert _normalizar("/9JRQ", 5) is None, "4 caracteres não podem virar 5"
    assert _normalizar("ABCDEF", 1) == "F"
    assert _normalizar("ABCDEF", 6) == "ABCDEF"
    for ruim in (0, -1):
        try:
            _normalizar("ABCD", ruim)
        except ValueError:
            pass
        else:
            raise AssertionError(f"tamanho={ruim} devia levantar ValueError")

    # ---- laço de retentativa, com driver falso ----
    class FakeAlerta:
        def __init__(self, texto):
            self.text = texto

        def accept(self):
            pass

    class FakeSwitch:
        def __init__(self, d):
            self._d = d

        @property
        def alert(self):
            if self._d.alertas:
                return FakeAlerta(self._d.alertas.pop(0))
            raise Exception("sem alerta")

    class FakeCampo:
        def clear(self):
            pass

        def send_keys(self, v):
            pass

    class FakeDriver:
        current_url = "https://exemplo.test/consulta"

        def __init__(self, alertas, tem_captcha=True):
            self.alertas = list(alertas)
            self.tem_captcha = tem_captcha
            self.gets, self.submissoes, self.preparos = [], 0, 0
            self.switch_to = FakeSwitch(self)

        def find_elements(self, by, sel):
            return [FakeCampo()] if self.tem_captcha else []

        def find_element(self, by, sel):
            return FakeCampo()

        def execute_script(self, js, *a):
            if "e.src" in js:
                return "data:image/png;base64,AAAA" if self.tem_captcha else None
            return None

        def get(self, url):
            self.gets.append(url)

    global ler
    real_ler = ler

    def ler_falso(png, tamanho=TAMANHO_PADRAO):
        return "ABCDEFGH"[:tamanho]

    ler = ler_falso

    def submeter(dr):
        dr.submissoes += 1

    try:
        # aceita na 1a: nenhum alerta
        d = FakeDriver([])
        assert resolver(d, submeter) is True
        assert ULTIMO_DIAGNOSTICO["estado"] == "aceito"
        assert ULTIMO_DIAGNOSTICO["tentativas"] == 1
        assert d.gets == [], "não pode recarregar quando foi aceito"

        # recusa duas vezes, aceita na 3a -> 2 reloads
        d = FakeDriver(["Código da imagem (captcha) incorreto.",
                        "Código da imagem (captcha) incorreto."])
        assert resolver(d, submeter) is True
        assert ULTIMO_DIAGNOSTICO["tentativas"] == 3
        assert len(d.gets) == 2, d.gets

        # recusa sempre -> False, e recarrega tentativas-1 vezes
        d = FakeDriver(["captcha incorreto."] * 4)
        assert resolver(d, submeter, tentativas=4) is False
        assert ULTIMO_DIAGNOSTICO["estado"] == "recusado"
        assert len(d.gets) == 3, d.gets
        assert "reenfileirar" in ULTIMO_DIAGNOSTICO["detalhe"]

        # alerta de OUTRO assunto não conta como recusa de captcha
        d = FakeDriver(["Informe o numero do documento."])
        assert resolver(d, submeter) is True
        assert ULTIMO_DIAGNOSTICO["estado"] == "aceito"

        # página sem captcha: sucesso, e nada é submetido às cegas
        d = FakeDriver([], tem_captcha=False)
        assert resolver(d, submeter) is True
        assert ULTIMO_DIAGNOSTICO["estado"] == "sem_captcha"
        assert d.submissoes == 0

        # preparar() é chamado antes de CADA tentativa (o reload limpa o form)
        d = FakeDriver(["captcha incorreto.", "captcha incorreto."])

        def preparar(dr):
            dr.preparos += 1

        assert resolver(d, submeter, preparar=preparar) is True
        assert d.preparos == 3, d.preparos

        # `tamanho` tem de atravessar resolver -> preencher -> ler_da_pagina -> ler
        d = FakeDriver([])
        assert resolver(d, submeter, tamanho=6) is True
        assert ULTIMO_DIAGNOSTICO["resposta"] == "ABCDEF", ULTIMO_DIAGNOSTICO
        d = FakeDriver([])
        assert resolver(d, submeter) is True
        assert ULTIMO_DIAGNOSTICO["resposta"] == "ABCD", ULTIMO_DIAGNOSTICO

        # ---- reserva de áudio: só na ÚLTIMA tentativa ----
        global _reserva_audio
        real_reserva = _reserva_audio
        try:
            chamadas = []

            def reserva_falsa(dr, tam):
                chamadas.append(tam)
                return "AUDI"

            _reserva_audio = reserva_falsa

            d = FakeDriver(["captcha incorreto.", "captcha incorreto."])
            assert resolver(d, submeter, tentativas=3) is True
            assert chamadas == [4], f"áudio devia ser tentado 1x, foi {len(chamadas)}"
            assert ULTIMO_DIAGNOSTICO["via"] == "audio_reserva"
            assert ULTIMO_DIAGNOSTICO["resposta"] == "AUDI"

            chamadas.clear()
            d = FakeDriver([])
            assert resolver(d, submeter, tentativas=3) is True
            assert chamadas == [], "áudio não pode entrar quando o OCR resolveu"
            assert ULTIMO_DIAGNOSTICO["via"] == "ocr_imagem"

            chamadas.clear()
            d = FakeDriver(["captcha incorreto."] * 3)
            assert resolver(d, submeter, tentativas=3, reserva_audio=False) is False
            assert chamadas == [], "reserva_audio=False tem de desligar de vez"

            chamadas.clear()
            d = FakeDriver(["captcha incorreto."])
            assert resolver(d, submeter, tentativas=1) is False
            assert chamadas == [], "com 1 tentativa a reserva não faz sentido"

            chamadas.clear()
            d = FakeDriver(["captcha incorreto."])
            resolver(d, submeter, tentativas=2, tamanho=6)
            assert chamadas == [6], chamadas
        finally:
            _reserva_audio = real_reserva

        d = FakeDriver([])
        assert _reserva_audio(d, 4) is None
    finally:
        ler = real_ler

    print("selfcheck ok")


if __name__ == "__main__":
    _selfcheck()
