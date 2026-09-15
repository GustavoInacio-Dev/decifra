"""
GeeTest v3 e v4.

v4, conferido em docs.geetest.com/BehaviorVerification/deploy/client/web:
  script    : https://static.geetest.com/v4/gt4.js
  init      : initGeetest4({captchaId, product, protocol, language, riskType}, cb)
  product   : bind | popup | float | custom
  métodos   : appendTo, showCaptcha, destroy, reset, getValidate, onReady,
              onSuccess, onError, onClose, onFail
  validate  : {lot_number, captcha_output, pass_token, gen_time}
  riskType  : slide | icon | ai | word | nine

v3 usa initGeetest({gt, challenge, offline, new_captcha}) e devolve
{geetest_challenge, geetest_validate, geetest_seccode}.

Nenhum slider é arrastado automaticamente: a posição de encaixe está na
imagem, e sem lê-la o arrasto não gera token. Slider devolve puzzle_required.
"""

from __future__ import annotations

from ...core.models import Modality, Variant, Vendor
from ...core.base import BaseAdapter, Ctx, Signature

_RISK_PARA_VARIANTE = {
    "slide": Variant.SLIDER,
    "icon": Variant.COORDINATE_CLICK,
    "word": Variant.COORDINATE_CLICK,
    "nine": Variant.VISUAL_CHALLENGE,
    "ai": Variant.INVISIBLE,
}


class GeeTestV4Adapter(BaseAdapter):
    SIGNATURE = Signature(
        vendor=Vendor.GEETEST_V4,
        script_hosts=("static.geetest.com/v4", "gcaptcha4.geetest.com", "static.geetest.com/gt4"),
        globals_=("initGeetest4",),
        # '#captcha' saiu de propósito: é o id do exemplo da doc, genérico
        # demais, e casava em página de outro fornecedor.
        container_selectors=(".geetest_captcha", ".geetest_holder", "[data-captcha-id]",
                             ".geetest_wrap", ".geetest_btn"),
        # v4 não cria campo oculto por conta própria: o site guarda o
        # getValidate() onde quiser. Cobrimos os nomes mais usados.
        response_fields=("lot_number", "captcha_output", "pass_token", "gen_time",
                         "geetest_validate", "geetest_seccode", "geetest_challenge"),
        frame_hosts=("geetest.com",),
        token_keys=("geetest",),
    )
    MODALIDADE_PADRAO = Modality.VISUAL

    def variante(self, ctx: Ctx) -> Variant:
        for c in ctx.containers(self):
            attrs = c.get("attrs", {})
            produto = (attrs.get("data-product") or "").lower()
            if produto in ("popup", "float", "bind", "custom"):
                return {"popup": Variant.POPUP, "float": Variant.FLOATING,
                        "bind": Variant.BIND, "custom": Variant.EMBEDDED}[produto]
            risco = (attrs.get("data-risk-type") or attrs.get("data-risktype") or "").lower()
            if risco in _RISK_PARA_VARIANTE:
                return _RISK_PARA_VARIANTE[risco]
        if ctx.retrato.get("janelas_novas"):
            return Variant.POPUP
        classes = " ".join((c.get("attrs", {}).get("class") or "") for c in ctx.containers(self))
        if "slide" in classes:
            return Variant.SLIDER
        return Variant.EMBEDDED if ctx.containers(self) else Variant.BIND

    def modalidade(self, ctx: Ctx) -> Modality:
        return Modality.INVISIBLE_SCORE if self.variante(ctx) == Variant.INVISIBLE else Modality.VISUAL

    def motivo_bloqueio(self, res) -> str:
        base = "GeeTest: o token (validate/seccode) só sai após a interação correta. "
        if res.variant == Variant.SLIDER:
            return base + "É slider: a posição de encaixe está na imagem."
        if res.variant == Variant.COORDINATE_CLICK:
            return base + "Exige clique em ícones/caracteres identificados na imagem."
        if res.variant == Variant.POPUP:
            return base + "O desafio abriu em outra janela."
        return base + "Passa sozinho apenas no modo passivo (radar), sem UI."


class GeeTestV3Adapter(GeeTestV4Adapter):
    SIGNATURE = Signature(
        vendor=Vendor.GEETEST_V3,
        script_hosts=("static.geetest.com/static/js/gt", "api.geetest.com",
                      "static.geetest.com/static/js/geetest"),
        globals_=("initGeetest", "Geetest"),
        container_selectors=(".geetest_holder", ".geetest_wrap", ".geetest_widget"),
        response_fields=("geetest_challenge", "geetest_validate", "geetest_seccode"),
        frame_hosts=("geetest.com",),
        token_keys=("geetest",),
    )
