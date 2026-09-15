"""
decifra — API de leitura de captcha: imagem e áudio, bytes -> texto.

    python -m uvicorn api.app:app --host 0.0.0.0 --port 8010

O QUE ELA FAZ, E O QUE NÃO FAZ
------------------------------
Faz uma coisa só, e sem estado: recebe os bytes de um captcha de imagem ou de
áudio e devolve o texto. Manda os bytes, recebe o texto, o seu programa digita.

Ela NÃO passa por proteção de fornecedor (Cloudflare, reCAPTCHA e afins) e não
fabrica token de nenhum. Esse tipo de token nasce amarrado à sessão do browser
que o pediu — cookie, IP, estado do DOM — e não pode ser gerado do lado de fora.
Este projeto trata do que É sem estado: ler o texto de uma imagem, transcrever
uma narração. Ler não depende de sessão nenhuma, e por isso mora bem numa API.

Por que uma API, e não uma biblioteca importada: o modelo de OCR ocupa 1,34 GB
em memória. Uma instância serve várias automações, o modelo carrega uma vez, e a
correção fica num lugar só.

AUTENTICAÇÃO
------------
Se `DECIFRA_TOKEN` estiver definido no ambiente, todo endpoint exige o header
`X-Decifra-Token` com esse valor. Sem a variável, a API fica aberta — conveniente
para rodar local, perigoso exposto na rede.
"""
from __future__ import annotations

import base64
import binascii
import os
import threading
import time
import wave
from typing import Any

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from pydantic import BaseModel, Field

# --------------------------------------------------------------------------- #
# Limites de entrada. São fronteira de confiança: mesmo que o cliente seja seu,
# a API escuta numa porta, e corpo sem teto é negação de serviço de graça.
# --------------------------------------------------------------------------- #

#: Captcha de imagem real tem ~12 KB (medido: 4-15 KB). 2 MB é folga larga.
MAX_IMAGEM_BYTES = 2 * 1024 * 1024

#: Áudio de narração curto tem ~120 KB. 8 MB cobre WAV longo sem abrir a porta.
MAX_AUDIO_BYTES = 8 * 1024 * 1024

#: Comprimentos plausíveis de resposta de captcha.
TAMANHO_MIN, TAMANHO_MAX = 1, 16

TOKEN_ESPERADO = (os.getenv("DECIFRA_TOKEN") or "").strip()

app = FastAPI(
    title="decifra",
    description="Leitura de captcha de imagem e de áudio: bytes -> texto. "
                "NÃO passa por proteção de fornecedor nem emite token — só lê.",
    version="1.0.0",
)

# Inferência de OCR é CPU-bound. Duas requisições ao mesmo tempo em CPU não
# ficam mais rápidas, ficam mais lentas as duas — então serializa. Os endpoints
# são `def` (não `async def`) de propósito: o FastAPI os roda em threadpool, e
# assim uma requisição pesada não trava o event loop.
_trava_ocr = threading.Lock()


def _autorizar(token: str | None) -> None:
    if not TOKEN_ESPERADO:
        return
    if not token or token != TOKEN_ESPERADO:
        raise HTTPException(status_code=401, detail="X-Decifra-Token ausente ou inválido")


def _decodificar(b64: str, limite: int, rotulo: str) -> bytes:
    if not b64:
        raise HTTPException(status_code=422, detail=f"{rotulo}: vazio")
    # data URI é o formato em que o captcha costuma vir no HTML; aceitar direto
    # poupa o cliente de fatiar a string.
    if "base64," in b64:
        b64 = b64.split("base64,", 1)[1]
    try:
        dados = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=422, detail=f"{rotulo}: base64 inválido")
    if not dados:
        raise HTTPException(status_code=422, detail=f"{rotulo}: vazio depois de decodificar")
    if len(dados) > limite:
        raise HTTPException(status_code=413,
                            detail=f"{rotulo}: {len(dados)} bytes, limite {limite}")
    return dados


def _validar_tamanho(tamanho: int) -> int:
    if not TAMANHO_MIN <= tamanho <= TAMANHO_MAX:
        raise HTTPException(
            status_code=422,
            detail=f"tamanho fora da faixa [{TAMANHO_MIN}, {TAMANHO_MAX}]: {tamanho}")
    return tamanho


# --------------------------------------------------------------------------- #
# Leitores, carregados sob demanda
# --------------------------------------------------------------------------- #

def _ocr():
    """O leitor de imagem. Import tardio: carrega torch, ~20 s e 1.3 GB."""
    from leitor import imagem
    return imagem


def _transcritor():
    from leitor import _motores
    return _motores


def _cascata_audio():
    """A cascata de áudio, ou None se as dependências faltarem."""
    try:
        from leitor import _cascata
        ok, _ = _cascata.disponivel()
        return _cascata if ok else None
    except Exception:
        return None


def _ocr_carregado() -> bool:
    """True se o modelo já está em memória (sem carregá-lo para descobrir)."""
    import importlib.util as u
    if not u.find_spec("transformers"):
        return False
    import sys
    mod = sys.modules.get("leitor.imagem")
    return bool(mod and getattr(mod, "_modelo", None) is not None)


#: Erros que significam "os bytes que o cliente mandou não são o formato que ele
#: disse". São ENTRADA inválida, não falha da API: viram 422 com motivo, nunca
#: 500. `wave.Error` não herda de OSError, por isso é listado à parte. Falha de
#: rede (Wit indisponível) NÃO entra aqui de propósito: aquilo é problema nosso,
#: e esconder como 422 mandaria o cliente desistir de um item que deve repetir.
_ERROS_DE_ENTRADA = (OSError, ValueError, wave.Error)


def _audio_ilegivel(erro: Exception) -> HTTPException:
    return HTTPException(status_code=422,
                         detail="áudio ilegível: os bytes não decodificam como WAV")


def ler_imagem(dados: bytes, tamanho: int) -> dict[str, Any]:
    ocr = _ocr()
    t0 = time.monotonic()
    try:
        with _trava_ocr:
            texto = ocr.ler(dados, tamanho)
    except _ERROS_DE_ENTRADA as erro:
        # Bytes que não são imagem (PNG truncado, arquivo de outro tipo) sobem
        # `UnidentifiedImageError`/`OSError` do Pillow. Isso é ENTRADA do cliente,
        # não falha nossa: 422 com motivo, nunca 500. A guarda mora aqui, na
        # função que as três rotas de leitura compartilham, e não em cada rota.
        raise HTTPException(
            status_code=422,
            detail="imagem ilegível: os bytes não decodificam como imagem",
        ) from erro
    return {"texto": texto,
            "bruto": ocr.ULTIMO_DIAGNOSTICO.get("saida_crua"),
            "ms": int((time.monotonic() - t0) * 1000)}


def ler_audio(dados: bytes, tamanho: int, idioma: str) -> dict[str, Any]:
    """
    Áudio -> texto, pela cascata medida.

    NÃO usa `transcrever()` cru, e o motivo é medido: uma narração de caractere
    por caractere tem ~1.1 s de silêncio entre cada um, o Wit encerra a fala no
    primeiro silêncio (endpointing) e devolve 'seis' para "6455". Resposta curta
    não é vazia, então bloqueava o Google SR que teria acertado — 0/12 no banco.

    A cascata dá a cada motor o pré-processamento QUE É O DELE (blocos remontados
    para o Wit, silêncio nas pontas para o Google SR) e só aceita transcrição que
    feche `tamanho` caracteres. Vive em `leitor/_cascata.py`.

    `bruto` é o que o motor disse; `texto` é o palpite normalizado, ou null.
    """
    t0 = time.monotonic()
    cascata = _cascata_audio()
    if cascata is not None and (idioma or "").lower().startswith("pt"):
        try:
            texto = cascata.ler_bytes(dados, tamanho)
        except _ERROS_DE_ENTRADA as erro:
            raise _audio_ilegivel(erro) from erro
        diag = dict(cascata.ULTIMO_DIAGNOSTICO)
        return {"texto": texto, "bruto": diag.get("bruto"),
                "motor": diag.get("via"),
                "ms": int((time.monotonic() - t0) * 1000)}

    # Fora do pt-BR não há cascata medida: transcrição direta, e o cliente
    # decide o que fazer com o texto cru.
    aud = _transcritor()
    try:
        bruto = aud.transcrever(dados, "audio/wav", idioma)
    except _ERROS_DE_ENTRADA as erro:
        raise _audio_ilegivel(erro) from erro
    return {"texto": _normalizar_fala(bruto, tamanho), "bruto": bruto,
            "motor": "direto", "ms": int((time.monotonic() - t0) * 1000)}


def _normalizar_fala(bruto: str | None, tamanho: int) -> str | None:
    """
    Transcrição de fala -> caracteres. Mapeia nome de letra e número escrito em
    pt-BR ('dois que b a' -> '2QBA'). Devolve None se não fechar `tamanho`,
    porque palpite parcial só gasta uma tentativa do lado do cliente.
    """
    import re
    import unicodedata

    if not bruto:
        return None
    dig = {"zero": "0", "um": "1", "uma": "1", "dois": "2", "duas": "2",
           "tres": "3", "quatro": "4", "cinco": "5", "seis": "6", "meia": "6",
           "sete": "7", "oito": "8", "nove": "9"}
    let = {"a": "A", "be": "B", "ce": "C", "se": "C", "de": "D", "e": "E",
           "efe": "F", "ge": "G", "je": "G", "aga": "H", "aca": "H", "i": "I",
           "jota": "J", "ka": "K", "ca": "K", "que": "Q", "qui": "Q",
           "ele": "L", "eme": "M", "ene": "N", "o": "O", "pe": "P",
           "erre": "R", "esse": "S", "es": "S", "te": "T", "u": "U",
           "ve": "V", "dablio": "W", "xis": "X", "ipsilon": "Y", "ze": "Z"}
    sem_acento = "".join(c for c in unicodedata.normalize("NFD", bruto)
                         if unicodedata.category(c) != "Mn").lower()
    saida: list[str] = []
    for tok in re.sub(r"[^a-z0-9\s]", " ", sem_acento).split():
        if tok in dig:
            saida.append(dig[tok])
        elif tok in let:
            saida.append(let[tok])
        elif tok.isdigit():
            saida.extend(tok)
        elif tok.isalnum():
            saida.extend(tok.upper())
        else:
            return None
    return "".join(saida) if len(saida) == tamanho else None


# --------------------------------------------------------------------------- #
# Corpos das requisições
# --------------------------------------------------------------------------- #

class CorpoImagem(BaseModel):
    imagem_b64: str = Field(..., description="PNG/JPEG em base64, ou data URI")
    tamanho: int = Field(4, description="quantos caracteres a resposta tem")


class CorpoAudio(BaseModel):
    audio_b64: str = Field(..., description="WAV em base64, ou data URI")
    tamanho: int = Field(4, description="quantos caracteres a resposta tem")
    idioma: str = Field("pt-BR", description="idioma da narração")


class CorpoLer(BaseModel):
    imagem_b64: str | None = None
    audio_b64: str | None = None
    tamanho: int = 4
    idioma: str = "pt-BR"


# --------------------------------------------------------------------------- #
# Rotas
# --------------------------------------------------------------------------- #

@app.get("/saude")
@app.get("/health")
def saude(x_decifra_token: str | None = Header(None)) -> dict[str, Any]:
    """O que esta instância consegue fazer AGORA. Não carrega nada."""
    _autorizar(x_decifra_token)
    import importlib.util as u

    tem_ocr = all(u.find_spec(m) for m in ("torch", "transformers", "PIL"))
    aud = _transcritor()
    # Por língua, porque o token do Wit é por conta e a conta tem língua fixa:
    # dizer só "disponível" esconderia que o pt-BR funciona e o japonês não.
    por_lingua = {}
    for idioma in ("pt-BR", "en-US"):
        ok_l, motivo_l = aud.transcritor_disponivel(idioma)
        por_lingua[idioma] = {"disponivel": bool(ok_l), "motivo": motivo_l}
    disponivel, motivo = aud.transcritor_disponivel("pt-BR")
    return {
        "ok": True,
        "escopo": "captcha de imagem e de áudio (bytes -> texto)",
        "autenticacao": "token exigido" if TOKEN_ESPERADO else "aberta",
        "ler_imagem": {"disponivel": tem_ocr,
                       "modelo_carregado": _ocr_carregado(),
                       "motivo": None if tem_ocr else "instale transformers, torch e pillow"},
        "ler_audio": {"disponivel": bool(disponivel), "motivo": motivo,
                      "wit_por_ambiente": bool(aud._tokens_wit()),
                      "por_lingua": por_lingua,
                      "cascata": _cascata_audio() is not None},
        "nao_faz": "passar por proteção de fornecedor nem emitir token: isso "
                   "depende da sessão do browser e está fora do escopo.",
    }


@app.post("/ler/imagem")
def rota_ler_imagem(corpo: CorpoImagem,
                    x_decifra_token: str | None = Header(None)) -> dict[str, Any]:
    """Captcha de imagem -> texto. OCR local, sem chamada paga."""
    _autorizar(x_decifra_token)
    dados = _decodificar(corpo.imagem_b64, MAX_IMAGEM_BYTES, "imagem_b64")
    r = ler_imagem(dados, _validar_tamanho(corpo.tamanho))
    return {"resolvido": r["texto"] is not None, "via": "ocr_imagem", **r}


@app.post("/ler/imagem/arquivo")
def rota_ler_imagem_arquivo(arquivo: UploadFile = File(...),
                            tamanho: int = Form(4),
                            x_decifra_token: str | None = Header(None)) -> dict[str, Any]:
    """Igual ao /ler/imagem, mas multipart — para quem prefere subir o arquivo."""
    _autorizar(x_decifra_token)
    dados = arquivo.file.read(MAX_IMAGEM_BYTES + 1)
    if len(dados) > MAX_IMAGEM_BYTES:
        raise HTTPException(status_code=413, detail=f"imagem acima de {MAX_IMAGEM_BYTES} bytes")
    if not dados:
        raise HTTPException(status_code=422, detail="arquivo vazio")
    r = ler_imagem(dados, _validar_tamanho(tamanho))
    return {"resolvido": r["texto"] is not None, "via": "ocr_imagem", **r}


@app.post("/ler/audio")
def rota_ler_audio(corpo: CorpoAudio,
                   x_decifra_token: str | None = Header(None)) -> dict[str, Any]:
    """Áudio de captcha -> texto. Wit.ai primeiro, Google SR de reserva."""
    _autorizar(x_decifra_token)
    dados = _decodificar(corpo.audio_b64, MAX_AUDIO_BYTES, "audio_b64")
    r = ler_audio(dados, _validar_tamanho(corpo.tamanho), corpo.idioma)
    return {"resolvido": r["texto"] is not None, "via": "audio", **r}


@app.post("/ler")
def rota_ler(corpo: CorpoLer,
             x_decifra_token: str | None = Header(None)) -> dict[str, Any]:
    """
    Imagem e/ou áudio numa chamada só, com o sinal de concordância.

    Ordem deliberada, vinda de medição:

      1. IMAGEM, se houver. Mais certeira (64% por leitura) e mais barata
         (local, ~1.4 s, sem rede).
      2. ÁUDIO, se houver e a imagem não fechou. Menos frequente mas mais
         preciso quando responde (86-88%).

    Se vierem os dois e responderem IGUAL, `confianca` vem "alta": medido,
    quando duas vias independentes concordam o acerto foi 100% em 26 de 50
    amostras. **Concordância é sinal, não garantia** — 26 casos é pouco para
    prometer.

    Existe em vez de o cliente chamar os dois endpoints porque a ordem e o
    critério de concordância são lógica medida, e reimplementá-los no cliente é
    onde eles divergem.
    """
    _autorizar(x_decifra_token)
    if not corpo.imagem_b64 and not corpo.audio_b64:
        raise HTTPException(status_code=422,
                            detail="mande pelo menos um de: imagem_b64, audio_b64")
    tamanho = _validar_tamanho(corpo.tamanho)
    t0 = time.monotonic()
    tentativas: list[dict[str, Any]] = []

    por_imagem = por_audio = None
    if corpo.imagem_b64:
        dados = _decodificar(corpo.imagem_b64, MAX_IMAGEM_BYTES, "imagem_b64")
        try:
            r = ler_imagem(dados, tamanho)
        except HTTPException:
            # Imagem ilegível não pode matar a via de áudio: as duas vias existem
            # justamente para serem independentes, e derrubar a segunda por causa
            # da primeira desfaz o motivo. Sem áudio para tentar, o erro sobe.
            if not corpo.audio_b64:
                raise
            r = {"texto": None, "bruto": None, "ms": 0,
                 "erro": "imagem ilegível: os bytes não decodificam como imagem"}
        por_imagem = r["texto"]
        tentativas.append({"via": "ocr_imagem", **r})
    if corpo.audio_b64:
        dados = _decodificar(corpo.audio_b64, MAX_AUDIO_BYTES, "audio_b64")
        try:
            r = ler_audio(dados, tamanho, corpo.idioma)
        except HTTPException:
            # Simétrico à imagem: se a outra via já respondeu, um áudio ilegível
            # não pode apagar essa resposta. Sem nada mais tentado, o erro sobe.
            if not tentativas:
                raise
            r = {"texto": None, "bruto": None, "motor": None, "ms": 0,
                 "erro": "áudio ilegível: os bytes não decodificam como WAV"}
        por_audio = r["texto"]
        tentativas.append({"via": "audio", **r})

    # Se NENHUMA via chegou a rodar — todas recusaram os bytes na entrada —, o
    # problema é do que o cliente mandou, não do que a API leu. Devolver 200
    # "não resolvido" aqui faria o cliente gastar tentativa achando que o captcha
    # é que estava difícil.
    if tentativas and all("erro" in t for t in tentativas):
        raise HTTPException(status_code=422,
                            detail="; ".join(t["erro"] for t in tentativas))

    texto = por_imagem or por_audio
    concordam = bool(por_imagem and por_audio
                     and por_imagem.upper() == por_audio.upper())
    return {"resolvido": texto is not None,
            "texto": texto,
            "via": ("ocr_imagem" if por_imagem else "audio") if texto else None,
            "confianca": ("alta" if concordam else "normal") if texto else None,
            "concordancia": concordam if (por_imagem and por_audio) else None,
            "detalhe": None if texto else ("nenhuma via fechou o número de "
                                           "caracteres pedido; recarregue o "
                                           "captcha e tente outro"),
            "tentativas": tentativas,
            "ms": int((time.monotonic() - t0) * 1000)}
