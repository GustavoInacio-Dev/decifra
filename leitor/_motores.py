"""
Motores de transcrição de fala: áudio -> texto. É o back-end do `_cascata.py`.

Dois motores, cada um com o seu ponto forte, e o chamador escolhe:
  * Wit.ai        — um POST HTTP; melhor em narração de caractere por caractere
  * Google SR     — via `speech_recognition` + ffmpeg local

Nenhum token fica escrito neste arquivo. O Wit.ai é configurado por variável de
ambiente (`WIT_TOKEN`, ou `WIT_TOKEN_1..3`). Sem token e sem `speech_recognition`
instalado, `transcritor_disponivel()` devolve False com o motivo, em vez de
falhar silenciosamente.

A LÍNGUA IMPORTA: uma conta Wit.ai tem a língua fixada na criação. Uma conta em
inglês NÃO transcreve narração em português — medido, devolve 'Quattro' para
"quatro". Por isso a escolha é sempre por língua explícita, nunca "o primeiro
token que existir".
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile

WIT_API_URL = os.getenv("WIT_API_URL", "https://api.wit.ai/speech?v=20240205")
HTTP_TIMEOUT = float(os.getenv("WIT_HTTP_TIMEOUT", "30"))
AUDIO_LANG = os.getenv("AUDIO_LANG", "pt-BR")


def _tokens_wit() -> list[str]:
    """Tokens Wit.ai vindos do AMBIENTE, na ordem, sem duplicata."""
    out, vistos = [], set()
    for nome in ("WIT_TOKEN", "WIT_TOKEN_1", "WIT_TOKEN_2", "WIT_TOKEN_3"):
        v = (os.getenv(nome) or "").strip()
        if v and v not in vistos:
            vistos.add(v)
            out.append(v)
    return out


def _tem_google_sr() -> bool:
    import importlib.util as u
    return all(u.find_spec(m) for m in ("speech_recognition", "imageio_ffmpeg"))


def transcritor_disponivel(lang: str | None = None) -> tuple[bool, str]:
    """(disponível?, motivo). Verificado ANTES de baixar o áudio."""
    if _tokens_wit():
        return True, "Wit.ai configurado por ambiente"
    if _tem_google_sr():
        return True, "Google Speech Recognition (sem token Wit.ai)"
    return False, ("nenhum transcritor disponível: configure WIT_TOKEN ou instale "
                   "speech_recognition + imageio_ffmpeg")


def transcrever_wit(audio: bytes, content_type: str | None = None,
                    lang: str | None = None, aceitar=None) -> str | None:
    """
    Só o Wit.ai, sem cair para o Google SR.

    Existe separado porque o pré-processamento ÓTIMO é diferente por motor, e
    isso foi medido: o Wit quer os blocos de fala remontados (intervalo de
    0.3 s), o Google SR quer o áudio original com silêncio nas pontas. Uma
    chamada que tenta os dois no MESMO áudio desperdiça um deles.

    `aceitar(texto) -> bool` decide se a resposta serve. Ver a nota em `_cascata`.
    """
    if not audio:
        return None
    ct = _normalizar_content_type(content_type)
    serve = aceitar or (lambda t: bool(t))
    for token in _tokens_wit():
        texto = _wit(audio, ct, token)
        if texto and serve(texto):
            return texto
    return None


def transcrever_google(audio: bytes, content_type: str | None = None,
                       lang: str | None = None, aceitar=None) -> str | None:
    """Só o Google SR. Ver a nota em `transcrever_wit`."""
    if not audio:
        return None
    texto = _google_sr(audio, _normalizar_content_type(content_type),
                       lang or AUDIO_LANG)
    serve = aceitar or (lambda t: bool(t))
    return texto if (texto and serve(texto)) else None


def transcrever(audio: bytes, content_type: str | None = None,
                lang: str | None = None, aceitar=None) -> str | None:
    """
    Áudio → texto. Wit.ai primeiro, Google SR depois.

    `aceitar(texto) -> bool` decide se a resposta de um motor SERVE. Sem isso,
    qualquer resposta não vazia encerra a busca — e isso mediu 0/12 num áudio de
    caracteres espaçados: o Wit trunca a fala (devolve 'seis' para "6455") e a
    resposta curta, por não ser vazia, bloqueava o Google SR, que teria acertado.
    Quem sabe o formato esperado é o chamador, então é ele que passa o critério.
    """
    if not audio:
        return None
    ct = _normalizar_content_type(content_type)
    idioma = lang or AUDIO_LANG
    serve = aceitar or (lambda t: bool(t))
    for token in _tokens_wit():
        texto = _wit(audio, ct, token)
        if texto and serve(texto):
            return texto
    texto = _google_sr(audio, ct, idioma)
    return texto if (texto and serve(texto)) else None


def _normalizar_content_type(ct: str | None) -> str:
    ct = ct or "audio/wav"
    # Wit.ai recusa 'audio/mp3' com unsupported-content-type.
    return "audio/mpeg" if ct == "audio/mp3" else ct


def _wit(audio: bytes, content_type: str, token: str) -> str | None:
    """
    Um POST para o Wit.ai. Devolve o texto cru.

    O Wit.ai às vezes responde com VÁRIOS objetos JSON concatenados
    (transcrições parciais + a final). Vale o último `text` não vazio.
    """
    try:
        import requests
    except ImportError:
        return None
    try:
        r = requests.post(
            WIT_API_URL, data=audio, timeout=HTTP_TIMEOUT,
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": _normalizar_content_type(content_type)})
        if r.status_code >= 300:
            return None
        try:
            texto = (r.json().get("text") or "").strip()
        except ValueError:
            achados = re.findall(r'"text"\s*:\s*"([^"]*)"', r.text)
            texto = next((t for t in reversed(achados) if t.strip()), "").strip()
        return texto or None
    except Exception:
        return None


def _google_sr(audio: bytes, content_type: str, lang: str) -> str | None:
    """Converte para WAV 16 kHz mono via ffmpeg, e daí para o Google SR."""
    try:
        import imageio_ffmpeg
        import speech_recognition as sr
    except ImportError:
        return None

    sufixo = ".mp3" if ("mp3" in (content_type or "") or "mpeg" in (content_type or "")) else ".wav"
    entrada = wav = None
    try:
        with tempfile.NamedTemporaryFile(suffix=sufixo, delete=False) as f:
            f.write(audio)
            entrada = f.name
        wav = entrada.replace(sufixo, ".conv.wav")
        res = subprocess.run(
            [imageio_ffmpeg.get_ffmpeg_exe(), "-i", entrada, "-ar", "16000", "-ac", "1",
             wav, "-y"], capture_output=True, timeout=20)
        if res.returncode != 0:
            return None
        rec = sr.Recognizer()
        rec.energy_threshold = 300
        rec.dynamic_energy_threshold = True
        with sr.AudioFile(wav) as fonte:
            dados = rec.record(fonte)
        return (rec.recognize_google(dados, language=lang) or "").strip() or None
    except Exception:
        return None
    finally:
        for caminho in (entrada, wav):
            if caminho:
                try:
                    os.unlink(caminho)
                except OSError:
                    pass


def _selfcheck():
    # sem token e (talvez) sem SR, disponivel() nao levanta e diz o motivo
    ok, motivo = transcritor_disponivel("pt-BR")
    assert isinstance(ok, bool) and isinstance(motivo, str)

    # audio vazio nunca chama rede
    assert transcrever_wit(b"") is None
    assert transcrever_google(b"") is None
    assert transcrever(b"") is None

    # content-type: audio/mp3 vira audio/mpeg (o Wit recusa mp3)
    assert _normalizar_content_type("audio/mp3") == "audio/mpeg"
    assert _normalizar_content_type(None) == "audio/wav"

    # sem WIT_TOKEN no ambiente, a lista vem vazia
    for nome in ("WIT_TOKEN", "WIT_TOKEN_1", "WIT_TOKEN_2", "WIT_TOKEN_3"):
        os.environ.pop(nome, None)
    assert _tokens_wit() == []
    os.environ["WIT_TOKEN"] = "AAA"
    os.environ["WIT_TOKEN_1"] = "AAA"      # duplicata é descartada
    os.environ["WIT_TOKEN_2"] = "BBB"
    assert _tokens_wit() == ["AAA", "BBB"], _tokens_wit()
    for nome in ("WIT_TOKEN", "WIT_TOKEN_1", "WIT_TOKEN_2"):
        os.environ.pop(nome, None)

    print("selfcheck ok")


if __name__ == "__main__":
    _selfcheck()
