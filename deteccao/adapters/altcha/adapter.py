"""
ALTCHA.

Conferido em altcha.org/docs/website-integration:
  elemento : <altcha-widget>  (Web Component, com shadow root)
  atributos: challengeurl | challengejson, auto (off|onfocus|onload|onsubmit),
             name (default 'altcha'), floating (auto|top|bottom), expire,
             delay, workers, hidefooter, hidelogo, debug, test, mockerror
  campo    : 'altcha' por padrão (renomeável via name)
  eventos  : load, statechange, verified, serververification
  estados  : unverified | verifying | verified | error

Custom element com shadow DOM é justamente o caso em que procurar iframe não
acha nada. Aqui a leitura do estado vem do atributo/propriedade do elemento.
"""

from __future__ import annotations

from ...core.models import Modality, State, Variant, Vendor
from ...core.base import BaseAdapter, Ctx, Signature

_ESTADO_ALTCHA = {
    "unverified": State.PENDING,
    "verifying": State.PENDING,
    "verified": State.TOKEN_GENERATED,
    "error": State.FAILED,
    "expired": State.EXPIRED,
    "code": State.PENDING,
}


class AltchaAdapter(BaseAdapter):
    SIGNATURE = Signature(
        vendor=Vendor.ALTCHA,
        script_hosts=("altcha", "cdn.jsdelivr.net/gh/altcha-org", "unpkg.com/altcha"),
        globals_=("altcha",),
        container_selectors=("altcha-widget", ".altcha"),
        response_fields=("altcha",),
        frame_hosts=(),
        custom_elements=("altcha-widget",),
        token_keys=("altcha",),
    )
    MODALIDADE_PADRAO = Modality.PROOF_OF_WORK
    AUTORRESOLVE = True

    # Dispara o PoW quando o site usa auto="onfocus"/"onsubmit" e o RPA nunca
    # foca nem submete. `verify()` é a API pública do elemento; o cálculo do
    # SHA-256 continua sendo feito pelo widget, não por nós.
    ACIONAR_JS = r"""
    let acionados = 0;
    for (const w of document.querySelectorAll('altcha-widget')) {
      try {
        const estado = (w.getAttribute('data-state') || '').toLowerCase();
        if (estado === 'verified' || estado === 'verifying') continue;
        if (typeof w.verify === 'function') { w.verify(); acionados++; }
        else if (typeof w.configure === 'function') { w.configure({auto: 'onload'}); acionados++; }
      } catch (e) {}
    }
    return acionados;
    """

    def variante(self, ctx: Ctx) -> Variant:
        for e in self._custom_elements(ctx):
            attrs = e.get("attrs", {})
            if attrs.get("floating") not in (None, "", "false"):
                return Variant.FLOATING
            if (attrs.get("auto") or "").lower() == "onsubmit":
                return Variant.INVISIBLE
        return Variant.PROOF_OF_WORK

    def modalidade(self, ctx: Ctx) -> Modality:
        return Modality.PROOF_OF_WORK

    def detect(self, ctx: Ctx):
        res = super().detect(ctx)
        if not res.presente:
            return res
        # O elemento publica o próprio estado: usar isso é mais confiável que
        # inferir do DOM, porque o widget vive dentro de shadow root.
        for e in self._custom_elements(ctx):
            estado = (e.get("estado") or "").lower()
            if estado in _ESTADO_ALTCHA:
                res.state = _ESTADO_ALTCHA[estado]
                res.diagnostico = res.diagnostico or f"altcha-widget data-state={estado}"
                break
            if not e.get("definido"):
                res.state = State.SCRIPT_LOADING
                res.diagnostico = "custom element altcha-widget ainda não definido"
        return res

    def motivo_bloqueio(self, res) -> str:
        return ("ALTCHA é proof-of-work e conclui sozinho: se não concluiu, o problema "
                "é rede, CSP ou o endpoint de challenge do portal — não há clique útil.")
