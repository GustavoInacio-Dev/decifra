"""
Leitor do canal de ÁUDIO do captcha — a reserva de quando o OCR de imagem erra.

Muitos captchas oferecem um botão de áudio (acessibilidade): uma narração dos
mesmos caracteres da imagem. Este módulo baixa esse áudio, o limpa, e o
transcreve. É o mesmo recurso que o site oferece a quem não enxerga.

NÃO é o caminho principal. O OCR de imagem (`imagem.py`) acerta 64% por leitura e
resolve ~98% dos itens em 4 tentativas; esta reserva existe para o resto. Ela
entra apenas na ÚLTIMA tentativa de `imagem.resolver()`, em ~5% dos itens.

Por que vale a pena mesmo pouco usada: as duas vias falham em casos DIFERENTES.
Medido em 50 amostras — o áudio acerta em 9 casos onde o OCR erra, e o OCR
acerta em 10 onde nenhum áudio funciona. A união cobre 43 de 50.

    OCR de imagem                    68%
    áudio (Wit, blocos 0.3s)         50%
    áudio (Google SR)                42%
    cascata áudio -> OCR             82%   (n=50, dev split: NÃO é prova)

O achado central está em `remontar` e na constante `GAP_S`: recompactar o
espaçamento entre os caracteres falados leva o reconhecedor de 4% para 50%. É a
parte mais interessante deste repositório — ver `docs/COMO_FUNCIONA.md`.

DEPENDÊNCIA opcional de propósito: usa o transcritor de `_motores.py` por import
tardio. Sem ele (ou sem `numpy`), `disponivel()` devolve False e o
`imagem.resolver()` segue só com OCR. Nada quebra.

OS SELETORES DO BOTÃO DE ÁUDIO SÃO SEUS: configure `SELETORES_AUDIO` com os
elementos da sua página.
"""
from __future__ import annotations

import base64
import io
import re
import struct
import unicodedata
import wave

#: Onde estão o botão e a fonte do áudio na SUA página. Ajuste os ids/seletores.
#:   botao -> id do botão que dispara a narração
#:   fonte -> id do <audio>/<source> cujo .src aparece depois do clique
SELETORES_AUDIO = {
    "botao": "btnAudioCaptcha",
    "fonte": "srcAudioCaptcha",
}

#: Clica no botão de áudio e devolve a URL que a própria página montou. Usar a
#: URL da página (com o cache-buster dela) em vez de montar à mão evita pegar o
#: áudio de outra rodada.
def _js_clicar() -> str:
    return (
        f"var b=document.getElementById('{SELETORES_AUDIO['botao']}');"
        "if(!b)return null;"
        "b.click();"
        f"var s=document.getElementById('{SELETORES_AUDIO['fonte']}');"
        "return s?s.src:null;"
    )

#: Baixa pela sessão do BROWSER. É o cookie de sessão que amarra o áudio à imagem
#: na tela — baixar por fora pegaria outro captcha.
_JS_BAIXAR = """
var cb = arguments[arguments.length - 1];
fetch(arguments[0], {credentials: 'include'})
  .then(function (r) { return r.arrayBuffer(); })
  .then(function (b) {
     var u = new Uint8Array(b), s = '';
     for (var i = 0; i < u.length; i++) s += String.fromCharCode(u[i]);
     cb(btoa(s));
  })
  .catch(function () { cb(null); });
"""

#: Intervalo entre os blocos de fala ao remontar o áudio, em segundos.
#:
#: Medido, variando SÓ isto, em 20 amostras:
#:   0.05s -> 30%   0.15s -> 35%   0.30s -> 50%   0.40s -> 25%
#:   0.55s -> 10%   0.70s -> 0%
#:
#: A curva tem pico definido em 0.30 e cai depois, e a explicação é endpointing:
#: o reconhecedor encerra a fala no primeiro silêncio, e a narração tem ~1.1s
#: entre cada caractere. Com o áudio cru ele devolvia 4% — não errado, CORTADO
#: ('seis quatro' para 6455). Gap curto demais faz o oposto: funde letras
#: vizinhas ('PM' para 'pM').
GAP_S = 0.30

#: Silêncio nas pontas. Sem isso o motor come o primeiro/último caractere.
BORDA_S = 0.25

#: Diagnóstico da última chamada.
ULTIMO_DIAGNOSTICO: dict[str, object] = {}


def disponivel() -> tuple[bool, str]:
    """(dá para usar?, motivo). Não levanta: a reserva nunca derruba o leitor."""
    try:
        import numpy  # noqa: F401
    except ImportError:
        return False, "numpy ausente"
    try:
        from . import _motores as audio
    except Exception as exc:
        return False, f"transcritor indisponivel ({type(exc).__name__})"
    try:
        # pt-BR explicito: o token do Wit e por app, e app tem lingua fixa.
        # Perguntar sem lingua daria "disponivel" pelo app ingles, que nao serve
        # para narracao em portugues (medido: devolve 'Quattro' para "quatro").
        ok, motivo = audio.transcritor_disponivel("pt-BR")
        return bool(ok), motivo
    except Exception as exc:
        return False, f"transcritor com problema ({type(exc).__name__})"


# --------------------------------------------------------------------------- #
# WAV
# --------------------------------------------------------------------------- #

def consertar_riff(dados: bytes) -> bytes:
    """
    Alguns servidores mandam o WAV com o campo RIFF size ERRADO.

    Medido: arquivo de 126484 B declara RIFF size 2260 em vez de 126476. O chunk
    'data' está correto (5.73s), mas o módulo `wave` do Python confia no RIFF
    size e devolve 1112 amostras = 0.10s. Quem processar esse áudio sem consertar
    isto analisa um clique, não o áudio. Foi o bug que mais me custou tempo.
    """
    if len(dados) < 12 or dados[:4] != b"RIFF":
        return dados
    return dados[:4] + struct.pack("<I", len(dados) - 8) + dados[8:]


def _ler_wav(dados: bytes):
    import numpy as np
    with wave.open(io.BytesIO(consertar_riff(dados))) as w:
        canais, taxa, n = w.getnchannels(), w.getframerate(), w.getnframes()
        x = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float32)
    if canais == 2:
        x = x.reshape(-1, 2).mean(axis=1)
    return x, taxa


def _gravar_wav(x, taxa: int) -> bytes:
    import numpy as np
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(taxa)
        w.writeframes(np.clip(x, -32768, 32767).astype(np.int16).tobytes())
    return buf.getvalue()


def _blocos_de_fala(x, taxa: int):
    """
    Onde estão os caracteres falados. Devolve (lista de (ini, fim), janela).

    Com o RIFF consertado o áudio é 4 blocos limpos sobre silêncio quase total
    (medido: percentil 20 e 50 do envelope ficam em 2). Antes de achar o bug do
    header eu concluí que havia "ruído contínuo" — estava analisando 0.1s.
    """
    import numpy as np
    jan = max(1, taxa // 50)                       # janelas de 20 ms
    env = np.abs(x[:len(x) // jan * jan]).reshape(-1, jan).mean(axis=1)
    p20, p95 = np.percentile(env, 20), np.percentile(env, 95)
    limite = p20 + (p95 - p20) * 0.20
    blocos, dentro, silencio = [], False, 0
    for i, ativo in enumerate(env > limite):
        if ativo:
            if not dentro:
                blocos.append([i, i])
                dentro = True
            else:
                blocos[-1][1] = i
                silencio = 0
        elif dentro:
            silencio += 1
            if silencio >= 8:                      # 160 ms fecha o bloco
                dentro = False
                silencio = 0
    return [b for b in blocos if b[1] - b[0] >= 1], jan


def remontar(dados: bytes, tamanho: int = 4, gap: float = GAP_S) -> bytes:
    """
    Junta os blocos de fala com intervalo curto e uniforme.

    Se sobrarem mais blocos que `tamanho` (uma letra parte em dois), funde os
    dois blocos mais próximos até fechar a conta.
    """
    import numpy as np
    x, taxa = _ler_wav(dados)
    blocos, jan = _blocos_de_fala(x, taxa)
    if not blocos:
        return _gravar_wav(x, taxa)
    while len(blocos) > tamanho:
        vaos = [(blocos[i + 1][0] - blocos[i][1], i) for i in range(len(blocos) - 1)]
        _, i = min(vaos)
        blocos[i] = [blocos[i][0], blocos[i + 1][1]]
        del blocos[i + 1]

    borda = np.zeros(int(taxa * BORDA_S), np.float32)
    vao = np.zeros(int(taxa * gap), np.float32)
    partes = [borda]
    for a, b in blocos:
        partes += [x[max(0, (a - 2) * jan):min(len(x), (b + 3) * jan)], vao]
    partes.append(borda)
    return _gravar_wav(np.concatenate(partes), taxa)


# --------------------------------------------------------------------------- #
# Fala -> caracteres
# --------------------------------------------------------------------------- #

_DIGITOS = {"zero": "0", "um": "1", "uma": "1", "dois": "2", "duas": "2",
            "tres": "3", "quatro": "4", "cinco": "5", "seis": "6", "meia": "6",
            "sete": "7", "oito": "8", "nove": "9"}

#: Nome da letra em pt-BR, nas grafias que o motor de fala devolve na prática.
_LETRAS = {"a": "A", "be": "B", "ce": "C", "se": "C", "de": "D", "e": "E",
           "efe": "F", "ge": "G", "je": "G", "aga": "H", "aca": "H", "i": "I",
           "jota": "J", "ka": "K", "ca": "K", "que": "Q", "qui": "Q",
           "ele": "L", "eme": "M", "ene": "N", "o": "O", "pe": "P",
           "erre": "R", "esse": "S", "es": "S", "te": "T", "u": "U",
           "ve": "V", "dablio": "W", "xis": "X", "ipsilon": "Y", "ze": "Z"}


def normalizar(bruto: str | None, tamanho: int = 4) -> str | None:
    """
    Transcrição -> resposta do captcha, ou None.

    Devolve None quando não fecha `tamanho`: nesse caso o motor cortou
    caractere, e palpite parcial só gasta uma tentativa. Medido: quando fecha,
    o acerto por caractere é 97-99%.
    """
    if not bruto:
        return None
    sem_acento = "".join(c for c in unicodedata.normalize("NFD", bruto)
                         if unicodedata.category(c) != "Mn").lower()
    saida: list[str] = []
    for tok in re.sub(r"[^a-z0-9\s]", " ", sem_acento).split():
        if tok in _DIGITOS:
            saida.append(_DIGITOS[tok])
        elif tok in _LETRAS:
            saida.append(_LETRAS[tok])
        elif tok.isdigit():
            saida.extend(tok)
        elif tok.isalnum():
            # O motor às vezes cola caracteres ('w 6 wv' é W,6,W,V). Expandir é
            # seguro porque o filtro final exige EXATAMENTE `tamanho`: se a
            # expansão inventar caractere, a conta não fecha e vai fora.
            saida.extend(tok.upper())
        else:
            return None
    return "".join(saida) if len(saida) == tamanho else None


# --------------------------------------------------------------------------- #
# Entrada pública
# --------------------------------------------------------------------------- #

def baixar(driver) -> bytes | None:
    """Clica no botão de áudio e devolve o WAV, pela sessão do browser."""
    try:
        src = driver.execute_script(_js_clicar())
        if not src:
            ULTIMO_DIAGNOSTICO["estado"] = "sem_botao_de_audio"
            return None
        b64 = driver.execute_async_script(_JS_BAIXAR, src)
    except Exception as exc:
        ULTIMO_DIAGNOSTICO["estado"] = f"falha_ao_baixar:{type(exc).__name__}"
        return None
    if not b64:
        ULTIMO_DIAGNOSTICO["estado"] = "fetch_vazio"
        return None
    try:
        return base64.b64decode(b64)
    except Exception:
        ULTIMO_DIAGNOSTICO["estado"] = "base64_invalido"
        return None


def com_silencio(dados: bytes, seg: float = 0.6) -> bytes:
    """
    Silêncio nas pontas, sem remontar nada. É o pré-processamento do GOOGLE SR.

    Medido: subiu o Google SR de 30% para 36% no banco (e 42% depois do
    normalizador consertado). Compactar os blocos, que é o que o Wit precisa,
    PIORA o Google SR — 20%. Por isso cada motor tem o seu.
    """
    import numpy as np
    x, taxa = _ler_wav(dados)
    quieto = np.zeros(int(taxa * seg), np.float32)
    return _gravar_wav(np.concatenate([quieto, x, quieto]), taxa)


def ler_bytes(wav: bytes, tamanho: int = 4) -> str | None:
    """
    WAV -> resposta, ou None. A cascata medida, sem browser.

    Cada motor recebe o pré-processamento que É O DELE:

      1. Wit.ai com os blocos REMONTADOS a 0.3 s   -> medido 50%
      2. Google SR com silêncio nas pontas          -> medido 42%

    E cada um só é aceito se a transcrição fechar `tamanho` caracteres. Sem esse
    critério o Wit trunca ('seis' para "6455"), a resposta curta não é vazia, e
    ela BLOQUEIA o Google SR que teria acertado — medido 0/12 antes do conserto.

    Os dois somam de verdade: em 50 amostras o Wit acerta 25 e o Google 21, mas
    a união é 33 (interseção 13). Erram em casos diferentes.
    """
    from . import _motores as motor

    serve = lambda t: normalizar(t, tamanho) is not None   # noqa: E731

    bruto = motor.transcrever_wit(remontar(wav, tamanho), "audio/wav", "pt-BR",
                                  aceitar=serve)
    via = "wit"
    if not bruto:
        bruto = motor.transcrever_google(com_silencio(wav), "audio/wav", "pt-BR",
                                         aceitar=serve)
        via = "google"
    texto = normalizar(bruto, tamanho)
    ULTIMO_DIAGNOSTICO.update(estado="lido" if texto else "nao_fechou",
                              bruto=bruto, resposta=texto, via=via if texto else None,
                              bytes_audio=len(wav))
    return texto


def ler(driver, tamanho: int = 4) -> str | None:
    """
    Lê o captcha pelo canal de áudio. None em qualquer falha — NUNCA levanta.

    É reserva: se ela explodir, o item que já ia falhar continua falhando, mas
    não pode derrubar a RPA junto.
    """
    ULTIMO_DIAGNOSTICO.clear()
    ok, motivo = disponivel()
    if not ok:
        ULTIMO_DIAGNOSTICO.update(estado="indisponivel", motivo=motivo)
        return None
    try:
        wav = baixar(driver)
        if wav is None:
            return None
        return ler_bytes(wav, tamanho)
    except Exception as exc:
        ULTIMO_DIAGNOSTICO.update(estado=f"erro:{type(exc).__name__}",
                                  detalhe=str(exc)[:150])
        return None


# --------------------------------------------------------------------------- #
# Selfcheck: sem browser, sem rede
# --------------------------------------------------------------------------- #

def _selfcheck():
    # normalização, com transcrições reais medidas
    medidas = {
        ("6 4 5 5", 4): "6455",
        ("seis, quatro, cinco, cinco", 4): "6455",
        ("dois que b a", 4): "2QBA",
        ("zero. L zero. L", 4): "0L0L",
        ("w 6 wv", 4): "W6WV",          # token colado: expande caractere a caractere
        ("9 G 9 G", 4): "9G9G",
        ("A XM, L,", 4): "AXML",
        ("a be ce de e efe", 6): "ABCDEF",
    }
    for (cru, tam), esperado in medidas.items():
        assert normalizar(cru, tam) == esperado, (cru, normalizar(cru, tam), esperado)

    # incompleto não vira palpite
    for cru in ("f r l", "88 a", "psl l l", "M. P", "seis quatro", "", None):
        assert normalizar(cru, 4) is None, cru

    # o conserto do RIFF
    corpo = b"\x00" * 100
    ruim = b"RIFF" + struct.pack("<I", 12) + b"WAVE" + corpo
    bom = consertar_riff(ruim)
    assert struct.unpack("<I", bom[4:8])[0] == len(ruim) - 8
    assert bom[8:] == ruim[8:], "so o campo de tamanho pode mudar"
    assert consertar_riff(b"nao e riff") == b"nao e riff"

    # o gap veio de medição, e o pico medido foi 0.30
    assert GAP_S == 0.30

    # remontagem, com áudio sintético: 4 rajadas separadas por silêncio longo
    try:
        import numpy as np
    except ImportError:
        print("selfcheck ok (sem numpy: remontagem nao verificada)")
        return
    taxa = 11025
    silencio = np.zeros(int(taxa * 1.1), np.float32)
    rajada = (np.random.RandomState(7).randn(int(taxa * 0.2)) * 8000).astype(np.float32)
    x = np.concatenate([silencio, rajada, silencio, rajada,
                        silencio, rajada, silencio, rajada, silencio])
    original = _gravar_wav(x, taxa)
    blocos, _ = _blocos_de_fala(*_ler_wav(original))
    assert len(blocos) == 4, f"esperava 4 blocos, achei {len(blocos)}"

    remontado = remontar(original, 4)
    dur_antes = len(x) / taxa
    dur_depois = len(_ler_wav(remontado)[0]) / taxa
    assert dur_depois < dur_antes, "remontar tem de ENCURTAR (é o ponto)"
    assert 1.5 < dur_depois < 3.5, dur_depois

    # blocos demais são fundidos até fechar o tamanho pedido
    partido = np.concatenate([silencio, rajada, np.zeros(int(taxa * 0.2), np.float32),
                              rajada, silencio, rajada, silencio, rajada,
                              silencio, rajada, silencio])
    b2, _ = _blocos_de_fala(*_ler_wav(_gravar_wav(partido, taxa)))
    assert len(b2) > 4, f"o teste precisa de mais de 4 blocos, achei {len(b2)}"
    remontar(_gravar_wav(partido, taxa), 4)   # não pode levantar

    # disponivel() nunca levanta, mesmo sem nada instalado
    ok, motivo = disponivel()
    assert isinstance(ok, bool) and isinstance(motivo, str)

    # ler() com driver que explode devolve None e registra, sem propagar
    class DriverRuim:
        def execute_script(self, *a):
            raise RuntimeError("browser morreu")

        def execute_async_script(self, *a):
            raise RuntimeError("browser morreu")

    assert baixar(DriverRuim()) is None
    assert "falha_ao_baixar" in str(ULTIMO_DIAGNOSTICO.get("estado"))

    print("selfcheck ok")


if __name__ == "__main__":
    _selfcheck()
