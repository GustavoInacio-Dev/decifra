"""
Arkose Labs (FunCaptcha).

Strings conferidas em developer.arkoselabs.com/docs/standard-setup:
  script    : https://client-api.arkoselabs.com/v2/<PUBLIC_KEY>/api.js
              (data-callback aponta a função que recebe o enforcement)
  config    : enforcement.setConfig({selector, mode, onCompleted, onReady,
              onShown, onShow, onSuppress, onError, onFailed, onResize})
  token     : response.token, normalmente gravado em input[name=fc-token]
  chave     : aparece no path do script e/ou em div[data-pkey]

MatchKey/enforcement carregam sob demanda: é comum o widget só existir depois
de uma ação na página. Widget ausente aqui NÃO é prova de ausência de desafio.
"""

from __future__ import annotations

from ...core.models import Modality, Variant, Vendor
from ...core.base import BaseAdapter, Ctx, Signature


class ArkoseAdapter(BaseAdapter):
    SIGNATURE = Signature(
        vendor=Vendor.ARKOSE,
        script_hosts=("client-api.arkoselabs.com", "arkoselabs.com", "funcaptcha.com",
                      "arkose-labs.com"),
        globals_=("ArkoseEnforcement", "arkose", "fcsettings", "ArkoseLabs"),
        container_selectors=("[data-pkey]", "#arkose", "#funcaptcha", ".arkose",
                             "#enforcement-trigger", "[id*='arkose']"),
        response_fields=("fc-token", "verification-token", "arkose-token"),
        frame_hosts=("arkoselabs.com", "funcaptcha.com"),
        token_keys=("arkose", "fc-token"),
    )
    MODALIDADE_PADRAO = Modality.VISUAL

    def sitekey(self, ctx: Ctx) -> str | None:
        """A public key do Arkose vem no path do script, não em query string."""
        chave = super().sitekey(ctx)
        if chave:
            return chave
        for s in ctx.scripts(self):
            src = s.get("src") or ""
            if "/v2/" in src and src.endswith("/api.js"):
                meio = src.split("/v2/", 1)[1].rsplit("/api.js", 1)[0]
                if meio and "/" not in meio:
                    return meio
        return None

    def variante(self, ctx: Ctx) -> Variant:
        if ctx.retrato.get("janelas_novas") or ctx.eventos.get("popups"):
            return Variant.POPUP
        frames = ctx.frames(self)
        if any(f.get("visivel") for f in frames):
            return Variant.VISUAL_CHALLENGE
        if frames:
            return Variant.EMBEDDED
        # Script presente e nada renderizado: enforcement sob demanda.
        if ctx.scripts(self) or ctx.globais(self):
            return Variant.INVISIBLE
        return Variant.UNKNOWN

    def modalidade(self, ctx: Ctx) -> Modality:
        # Modo transparente/enforcement não mostra UI: o token sai por decisão do
        # fornecedor, como score. Só o game embutido/popup é visual.
        if self.variante(ctx) is Variant.INVISIBLE:
            return Modality.INVISIBLE_SCORE
        return Modality.VISUAL

    def motivo_bloqueio(self, res) -> str:
        return ("Arkose/FunCaptcha: o desafio interativo (girar imagem, contar objetos) "
                "costuma ter várias rodadas e o token só sai no fim. Passa sozinho apenas "
                "no modo transparente, em que nenhuma UI aparece.")
