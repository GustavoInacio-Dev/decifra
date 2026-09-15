"""
Cliente da API decifra, para colar dentro de um programa em OUTRA máquina.

Um arquivo, sem dependência além de `requests`. Não carrega modelo nenhum:
quem faz o trabalho pesado é a instância da API, do outro lado da rede.

    from cliente import Decifra

    api = Decifra("http://10.0.0.12:8010", token="o-token-que-te-passaram")

    r = api.ler(imagem=src_do_img, audio=bytes_do_wav, tamanho=6)
    if r.resolvido:
        campo.send_keys(r.texto)
    elif r.reenfileirar:
        ...   # não insista: o item volta para a fila

`imagem` aceita o `src` do `<img>` inteiro (data URI), bytes, ou caminho de
arquivo — não é preciso fatiar string.

POR QUE `reenfileirar` E `resolvido` SÃO COISAS DIFERENTES
----------------------------------------------------------
`resolvido=False` com `reenfileirar=False` significa "a leitura não fechou":
recarregue o captcha e tente de novo, é barato. `reenfileirar=True` significa
que insistir não adianta — a API está fora, o token está errado, ou os bytes
enviados não eram imagem/áudio. Tratar os dois igual faz a RPA queimar
tentativas contra um problema que não é o captcha.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

TIMEOUT_PADRAO_S = 180.0   # a 1a leitura de imagem paga a carga do modelo (~60 s)


@dataclass
class Leitura:
    """O que a API respondeu, já traduzido para decisão de RPA."""

    resolvido: bool
    texto: str | None = None
    via: str | None = None
    confianca: str | None = None
    concordancia: bool | None = None
    #: True quando insistir não adianta: reenfileire o item em vez de repetir.
    reenfileirar: bool = False
    motivo: str | None = None
    bruto: dict[str, Any] = field(default_factory=dict)


def _para_b64(origem: str | bytes | Path | None) -> str | None:
    """Aceita data URI, base64 puro, bytes ou caminho — devolve o que a API quer."""
    if origem is None:
        return None
    if isinstance(origem, (bytes, bytearray)):
        return base64.b64encode(bytes(origem)).decode()
    texto = str(origem)
    if texto.startswith("data:"):
        return texto                      # a API fatia o data URI sozinha
    if len(texto) < 260 and Path(texto).exists():
        return base64.b64encode(Path(texto).read_bytes()).decode()
    return texto                          # já é base64


class Decifra:
    """Cliente da API Generic. Uma instância por processo basta."""

    def __init__(self, url: str, token: str | None = None,
                 timeout: float = TIMEOUT_PADRAO_S):
        self.url = url.rstrip("/")
        self.token = token if token is not None else os.environ.get("DECIFRA_TOKEN")
        self.timeout = timeout

    # ---------------------------------------------------------------- interno
    @property
    def _cabecalhos(self) -> dict[str, str]:
        return {"X-Decifra-Token": self.token} if self.token else {}

    def _post(self, rota: str, corpo: dict[str, Any]) -> Leitura:
        try:
            r = requests.post(f"{self.url}{rota}", headers=self._cabecalhos,
                              json=corpo, timeout=self.timeout)
        except requests.RequestException as erro:
            # A API não respondeu. Não é o captcha que está difícil.
            return Leitura(resolvido=False, reenfileirar=True,
                           motivo=f"API inacessível: {type(erro).__name__}")

        if r.status_code in (401, 403):
            return Leitura(resolvido=False, reenfileirar=True,
                           motivo="token inválido ou ausente (X-Decifra-Token)")
        if r.status_code in (413, 422):
            # Entrada malformada ou grande demais: mandar de novo dá o mesmo.
            return Leitura(resolvido=False, reenfileirar=True,
                           motivo=f"entrada recusada: {_detalhe(r)}")
        if r.status_code >= 500:
            return Leitura(resolvido=False, reenfileirar=True,
                           motivo=f"erro na API (HTTP {r.status_code})")
        if r.status_code != 200:
            return Leitura(resolvido=False, reenfileirar=True,
                           motivo=f"resposta inesperada (HTTP {r.status_code})")

        d = r.json()
        return Leitura(resolvido=bool(d.get("resolvido")),
                       texto=d.get("texto"),
                       via=d.get("via"),
                       confianca=d.get("confianca"),
                       concordancia=d.get("concordancia"),
                       reenfileirar=False,
                       motivo=d.get("detalhe"),
                       bruto=d)

    # ---------------------------------------------------------------- público
    def ler(self, imagem=None, audio=None, tamanho: int = 4,
            idioma: str = "pt-BR") -> Leitura:
        """
        As duas vias numa chamada. Mande as duas quando o site oferecer as duas:
        quando imagem e áudio concordam, `confianca` vem `"alta"`.
        """
        corpo: dict[str, Any] = {"tamanho": tamanho, "idioma": idioma}
        if imagem is not None:
            corpo["imagem_b64"] = _para_b64(imagem)
        if audio is not None:
            corpo["audio_b64"] = _para_b64(audio)
        if "imagem_b64" not in corpo and "audio_b64" not in corpo:
            raise ValueError("mande ao menos um de: imagem, audio")
        return self._post("/ler", corpo)

    def ler_imagem(self, imagem, tamanho: int = 4) -> Leitura:
        return self._post("/ler/imagem",
                          {"imagem_b64": _para_b64(imagem), "tamanho": tamanho})

    def ler_audio(self, audio, tamanho: int = 4, idioma: str = "pt-BR") -> Leitura:
        return self._post("/ler/audio", {"audio_b64": _para_b64(audio),
                                         "tamanho": tamanho, "idioma": idioma})

    def saude(self) -> dict[str, Any]:
        """Checagem de fumaça: chame no boot da RPA e falhe cedo."""
        r = requests.get(f"{self.url}/saude", headers=self._cabecalhos, timeout=30)
        r.raise_for_status()
        return r.json()

    def aquecer(self) -> bool:
        """
        Paga a carga do modelo (~14-60 s) UMA vez, no boot, em vez de na primeira
        consulta do dia. Devolve True se a instância respondeu.
        """
        try:
            png = base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42mP8"
                "z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
            self.ler_imagem(png, tamanho=4)
            return True
        except Exception:
            return False


def _detalhe(r: requests.Response) -> str:
    try:
        return str(r.json().get("detail"))
    except Exception:
        return r.text[:120]


def _selfcheck() -> None:
    """Roda sem rede e sem API no ar: `python cliente.py`."""
    assert _para_b64(b"abc") == base64.b64encode(b"abc").decode()
    assert _para_b64("data:image/png;base64,AAAA") == "data:image/png;base64,AAAA"
    assert _para_b64(None) is None
    assert _para_b64("QUJD") == "QUJD"

    api = Decifra("http://127.0.0.1:9", token="x", timeout=0.5)
    r = api.ler(imagem=b"abc", tamanho=4)
    assert r.reenfileirar is True and r.resolvido is False, r
    assert "inacess" in (r.motivo or ""), r.motivo

    try:
        api.ler(tamanho=4)
    except ValueError:
        pass
    else:
        raise AssertionError("ler() sem imagem nem audio tinha de recusar")

    print("selfcheck ok")


if __name__ == "__main__":
    _selfcheck()
