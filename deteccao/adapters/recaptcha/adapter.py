"""
Google reCAPTCHA v2, v3 e Enterprise.

Strings conferidas na doc oficial (developers.google.com/recaptcha):
  script v2/v3 : https://www.google.com/recaptcha/api.js
  script ent.  : https://www.google.com/recaptcha/enterprise.js
  container    : div.g-recaptcha  (data-sitekey, data-callback, data-size,
                 data-theme, data-expired-callback, data-error-callback)
  campo        : g-recaptcha-response
  global       : grecaptcha  (render, getResponse, reset, execute)
  ent. global  : grecaptcha.enterprise
  validade     : token de resposta vale 2 minutos
"""

from __future__ import annotations

from ...core.models import Modality, Variant, Vendor
from ...core.base import BaseAdapter, Ctx, Signature

# Casa por host E por caminho: portais que espelham/proxiam o api.js no próprio
# domínio são comuns, e sem o padrão de caminho o script ficava irreconhecível —
# junto com todo o diagnóstico de rede/CSP que depende de saber que é dele.
_HOSTS = ("google.com/recaptcha", "recaptcha.net/recaptcha", "gstatic.com/recaptcha",
          "/recaptcha/api.js", "/recaptcha/enterprise.js")


class RecaptchaV2Adapter(BaseAdapter):
    SIGNATURE = Signature(
        vendor=Vendor.RECAPTCHA_V2,
        script_hosts=_HOSTS,
        globals_=("grecaptcha", "grecaptcha.render", "grecaptcha.getResponse", "___grecaptcha_cfg"),
        container_selectors=(".g-recaptcha", "[data-sitekey].g-recaptcha"),
        response_fields=("g-recaptcha-response",),
        frame_hosts=("google.com/recaptcha", "recaptcha.net/recaptcha"),
        token_prefixos=("03A", "6Le"),
    )
    MODALIDADE_PADRAO = Modality.VISUAL

    def variante(self, ctx: Ctx) -> Variant:
        containers = ctx.containers(self)
        for c in containers:
            if (c.get("attrs", {}).get("data-size") or "").lower() == "invisible":
                return Variant.INVISIBLE
        frames = ctx.frames(self)
        # bframe = painel do desafio de imagem; anchor = só o checkbox.
        if any("bframe" in (f.get("src") or "") and f.get("visivel") for f in frames):
            return Variant.VISUAL_CHALLENGE
        if any("anchor" in (f.get("src") or "") for f in frames):
            return Variant.CHECKBOX
        if containers:
            return Variant.AUTO_RENDER
        if ctx.globais(self):
            return Variant.EXPLICIT_RENDER
        return Variant.UNKNOWN

    def modalidade(self, ctx: Ctx) -> Modality:
        if self.variante(ctx) == Variant.INVISIBLE:
            return Modality.INVISIBLE_SCORE
        return Modality.VISUAL

    def motivo_bloqueio(self, res) -> str:
        if res.variant == Variant.EXPLICIT_RENDER:
            # Medido em produção: a tela inicial carrega o api.js e expõe
            # `grecaptcha`, mas tem ZERO container, ZERO iframe e nenhum campo de
            # resposta. O widget só é montado depois que o portal chama
            # grecaptcha.render(), no passo do formulário. Não há o que clicar
            # ainda, e chamar isso de "desafio pendente" faz o RPA reenfileirar
            # item que estava bom.
            return ("script do reCAPTCHA carregado, mas nenhum widget montado: o portal "
                    "renderiza o captcha só depois de interação no formulário. Seguir o "
                    "fluxo e chamar resolver() de novo no passo que dispara o captcha.")
        if res.variant == Variant.VISUAL_CHALLENGE:
            return ("reCAPTCHA v2 abriu o desafio de imagem (bframe): o token só é emitido "
                    "após a seleção correta das imagens, que está fora do escopo de "
                    "leitura deste projeto.")
        return ("reCAPTCHA v2 checkbox não emitiu token: o clique não teve efeito ou o "
                "Google ainda está decidindo — ver diagnóstico.")


class RecaptchaV3Adapter(BaseAdapter):
    """
    v3 não tem UI. Roda invisível e devolve token por `action`, e não bloqueia a
    navegação: quem julga é o backend do portal, no submit. Se o score reprovar,
    isso vira REJECTED — não é desafio pendente e não há o que acionar.
    """

    SIGNATURE = Signature(
        vendor=Vendor.RECAPTCHA_V3,
        script_hosts=_HOSTS,
        globals_=("grecaptcha", "grecaptcha.execute", "___grecaptcha_cfg"),
        container_selectors=(".grecaptcha-badge",),
        response_fields=("g-recaptcha-response",),
        frame_hosts=("google.com/recaptcha",),
        token_prefixos=("03A",),
        test_keys=(),
    )
    MODALIDADE_PADRAO = Modality.INVISIBLE_SCORE
    AUTORRESOLVE = True
    # v3 não renderiza widget: a ausência de container/iframe/campo é o estado
    # NORMAL dele, não sinal de "script carregou sem instanciar nada".
    SEM_WIDGET_POR_DESIGN = True

    def variante(self, ctx: Ctx) -> Variant:
        return Variant.SCORE_BASED

    def presente(self, ctx: Ctx) -> bool:
        """
        v3 se distingue de v2 pelo `render=<sitekey>` no src do script (v2 usa
        render=explicit ou nada) ou pela badge flutuante.
        """
        for s in ctx.scripts(self):
            src = s.get("src") or ""
            if "render=" in src and "render=explicit" not in src:
                return True
        return bool(ctx.containers(self))

    def motivo_bloqueio(self, res) -> str:
        return ("reCAPTCHA v3 é invisível e por score: não bloqueia a navegação e não há "
                "nada para clicar. Quem julga é o backend do portal no submit; se o item "
                "falhar, é score baixo, não desafio pendente.")


class RecaptchaEnterpriseAdapter(RecaptchaV2Adapter):
    """
    Enterprise divide o container `.g-recaptcha` com o v2. Por isso exige sinal
    próprio (enterprise.js ou grecaptcha.enterprise): sem isso, reivindicava
    toda página de v2 puro e aparecia como segundo fornecedor no relatório.
    """

    def presente(self, ctx: Ctx) -> bool:
        return bool(ctx.scripts(self) or ctx.globais(self))

    SIGNATURE = Signature(
        vendor=Vendor.RECAPTCHA_ENTERPRISE,
        script_hosts=("google.com/recaptcha/enterprise.js", "recaptcha.net/recaptcha/enterprise.js"),
        globals_=("grecaptcha.enterprise", "grecaptcha.enterprise.execute",
                  "grecaptcha.enterprise.render", "grecaptcha.enterprise.getResponse"),
        container_selectors=(".g-recaptcha", ".grecaptcha-badge"),
        response_fields=("g-recaptcha-response",),
        frame_hosts=("google.com/recaptcha",),
        token_prefixos=("03A",),
    )
