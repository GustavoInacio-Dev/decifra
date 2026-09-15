"""
Observação de token: campo oculto, cookie, storage e callback.

Nunca lê o valor do token para fora do browser. O que sai daqui é presença,
tamanho, impressão digital (sha256 truncado) e idade — o suficiente para
detectar expiração e para saber que o token mudou, sem nunca copiar o segredo.

Não existe função de escrita neste módulo. Injeção e replay são proibidos por
política, e a ausência de API para isso é a garantia.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .config import CONFIG, Config
from .telemetry import impressao_digital

#: Vira o token do fornecedor em cookie. Cloudflare de fora, de propósito.
_COOKIES_TOKEN = ("aws-waf-token", "arkose", "geetest", "altcha", "frc-")

_JS_CALLBACKS = r"""
// Marca quais callbacks já dispararam, sem alterar o comportamento deles:
// embrulha uma vez e delega. Só observação.
const nomes = arg || [];
window.__co_cb = window.__co_cb || {};
for (const n of nomes) {
  if (!n || typeof window[n] !== 'function' || window[n].__co_wrapped) continue;
  const original = window[n];
  const envolto = function (...a) {
    window.__co_cb[n] = (window.__co_cb[n] || 0) + 1;
    return original.apply(this, a);
  };
  envolto.__co_wrapped = true;
  window[n] = envolto;
}
return window.__co_cb;
"""


@dataclass
class EstadoToken:
    presente: bool = False
    origem: str = ""            # campo | cookie | storage
    tamanho: int = 0
    fp: str = ""
    idade_s: float | None = None
    expirado: bool = False
    callbacks: dict[str, int] = field(default_factory=dict)


class TokenObserver:
    def __init__(self, bridge, config: Config | None = None):
        self.bridge = bridge
        self.config = config or CONFIG
        #: fp -> primeiro instante em que foi visto (para calcular idade).
        self._visto_em: dict[str, float] = {}

    # ------------------------------------------------------------------ #

    def observar(self, retrato: dict[str, Any]) -> EstadoToken:
        estado = EstadoToken()

        for f in retrato.get("responseFields", []):
            if not f.get("vazio"):
                estado.presente = True
                estado.origem = "campo"
                estado.tamanho = int(f.get("len") or 0)
                # fp derivado do que temos sem o valor: nome + tamanho + prefixo.
                estado.fp = impressao_digital(
                    f"{f.get('nome')}|{f.get('len')}|{f.get('prefixo')}"
                )
                break

        if not estado.presente:
            for nome in retrato.get("cookiesNomes", []):
                if any(c in nome for c in _COOKIES_TOKEN):
                    estado.presente = True
                    estado.origem = "cookie"
                    estado.fp = impressao_digital(f"cookie|{nome}")
                    break

        if not estado.presente:
            for item in retrato.get("storage", []):
                if item.get("len", 0) > 0:
                    estado.presente = True
                    estado.origem = "storage"
                    estado.tamanho = int(item.get("len") or 0)
                    estado.fp = impressao_digital(f"{item.get('onde')}|{item.get('chave')}")
                    break

        if estado.presente and estado.fp:
            agora = time.monotonic()
            primeiro = self._visto_em.setdefault(estado.fp, agora)
            estado.idade_s = round(agora - primeiro, 2)
            estado.expirado = self._expirou(estado, retrato)

        return estado

    def _expirou(self, estado: EstadoToken, retrato: dict[str, Any]) -> bool:
        """
        Expiração por idade observada. reCAPTCHA e hCaptcha documentam ~2 min;
        aplicamos margem para não submeter token morrendo no caminho.
        """
        if estado.idade_s is None:
            return False
        nomes = " ".join(f.get("nome", "") for f in retrato.get("responseFields", []))
        if "g-recaptcha-response" in nomes:
            ttl = self.config.tokens.recaptcha_ttl_s
        elif "h-captcha-response" in nomes:
            ttl = self.config.tokens.hcaptcha_ttl_s
        else:
            return False
        return estado.idade_s > max(0.0, ttl - self.config.tokens.margem_s)

    def marcar_callbacks(self, nomes: list[str]) -> dict[str, int]:
        """
        Instrumenta callbacks declarados em data-callback para saber se
        dispararam. Idempotente; preserva o comportamento original.
        """
        try:
            return self.bridge.eval_js(_JS_CALLBACKS, nomes) or {}
        except Exception:
            return {}

    def esquecer(self) -> None:
        """Zera a linha do tempo de idade. Chamar após navegação."""
        self._visto_em.clear()
