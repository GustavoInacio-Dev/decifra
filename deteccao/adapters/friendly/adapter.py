"""
Friendly Captcha (v1 e v2).

Conferido em developer.friendlycaptcha.com:
  widget   : div.frc-captcha com data-sitekey
  v2 campo : frc-captcha-response  (renomeável via data-form-field-name)
  v1 campo : frc-captcha-solution  (opção solutionFieldName do WidgetInstance)
  data-*   : data-sitekey, data-start (auto|focus|none), data-puzzle-endpoint,
             data-callback, data-lang, data-form-field-name
  JS       : friendlyChallenge.WidgetInstance (start, reset, destroy)

É proof-of-work: resolve sozinho, só consome CPU. Humano não tem o que fazer —
por isso AUTORRESOLVE=True e a espera é longa em vez de virar fila de operador.
Falha típica: Web Worker bloqueado por CSP (worker-src / child-src).
"""

from __future__ import annotations

from ...core.models import Modality, State, Variant, Vendor
from ...core.base import BaseAdapter, Ctx, Signature


class FriendlyCaptchaAdapter(BaseAdapter):
    SIGNATURE = Signature(
        vendor=Vendor.FRIENDLY_CAPTCHA,
        script_hosts=("friendlycaptcha.com", "friendly-challenge", "cdn.jsdelivr.net/npm/friendly-challenge"),
        globals_=("friendlyChallenge", "friendlyChallenge.WidgetInstance"),
        container_selectors=(".frc-captcha", "[data-sitekey].frc-captcha"),
        response_fields=("frc-captcha-response", "frc-captcha-solution"),
        frame_hosts=(),  # não usa iframe: mais um caso onde exigir iframe erra
        token_keys=("frc",),
    )
    MODALIDADE_PADRAO = Modality.PROOF_OF_WORK
    AUTORRESOLVE = True

    # data-start=focus/none espera um gesto que o RPA não faz. `start()` é a API
    # pública do WidgetInstance; o puzzle continua sendo resolvido pelo Worker.
    ACIONAR_JS = r"""
    let acionados = 0;
    try {
      const fc = window.friendlyChallenge;
      if (fc && fc.autoWidget && typeof fc.autoWidget.start === 'function') {
        fc.autoWidget.start(); acionados++;
      }
    } catch (e) {}
    for (const el of document.querySelectorAll('.frc-captcha')) {
      try {
        const btn = el.querySelector('button.frc-button, .frc-button');
        if (btn && !btn.disabled) { btn.click(); acionados++; }
      } catch (e) {}
    }
    return acionados;
    """

    def variante(self, ctx: Ctx) -> Variant:
        return Variant.PROOF_OF_WORK

    def modalidade(self, ctx: Ctx) -> Modality:
        return Modality.PROOF_OF_WORK

    def detect(self, ctx: Ctx):
        res = super().detect(ctx)
        if not res.presente:
            return res
        # Sem iframe é o NORMAL aqui. Se o super caiu em WAITING_RENDER só por
        # falta de frame, corrigimos para PENDING: o PoW pode já estar rodando.
        if res.state == State.WAITING_RENDER:
            for c in ctx.containers(self):
                if c.get("filhos", 0) > 0 or c.get("htmlLen", 0) > 8:
                    res.state = State.PENDING
                    break
        return res

    def _diagnosticar(self, ctx: Ctx, res):
        estado = super()._diagnosticar(ctx, res)
        if estado:
            return estado
        # Worker barrado por CSP é a falha clássica e não aparece como erro de
        # script: o widget simplesmente nunca termina.
        for v in ctx.eventos.get("csp", []):
            diretiva = (v.get("diretiva") or "").lower()
            if "worker" in diretiva or "child-src" in diretiva:
                res.diagnostico = (
                    "Web Worker do Friendly Captcha bloqueado por CSP "
                    f"({v.get('diretiva')}). O proof-of-work nunca conclui; "
                    "liberar worker-src na página."
                )
                return State.CSP_BLOCKED
        return None

    def motivo_bloqueio(self, res) -> str:
        return ("Friendly Captcha é proof-of-work: resolve sozinho, sem clique. Se não "
                "concluiu, houve falha de rede, CSP no Web Worker ou endpoint de puzzle "
                "indisponível — ver diagnóstico.")
