"""
hCaptcha.

Strings conferidas em docs.hcaptcha.com:
  script      : https://js.hcaptcha.com/1/api.js
  container   : div.h-captcha (data-sitekey, data-callback, data-size=invisible,
                data-theme, data-error-callback, data-expired-callback,
                data-chalexpired-callback, data-open-callback, data-close-callback)
  campo       : h-captcha-response  (e g-recaptcha-response por compatibilidade)
  global      : hcaptcha (render, execute, getResponse, reset, getRespKey)
"""

from __future__ import annotations

from ...core.models import Modality, State, Variant, Vendor
from ...core.base import BaseAdapter, Ctx, Signature


class HCaptchaAdapter(BaseAdapter):
    SIGNATURE = Signature(
        vendor=Vendor.HCAPTCHA,
        script_hosts=("js.hcaptcha.com",),
        globals_=("hcaptcha", "hcaptcha.render", "hcaptcha.execute", "hcaptcha.getResponse"),
        container_selectors=(".h-captcha", "[data-hcaptcha-widget-id]"),
        # hCaptcha cria também g-recaptcha-response em modo compatibilidade —
        # por isso o campo é lido, mas marcado como compartilhado: sozinho ele
        # não prova hCaptcha (senão reivindica página de reCAPTCHA puro).
        response_fields=("h-captcha-response", "g-recaptcha-response"),
        campos_compartilhados=("g-recaptcha-response",),
        frame_hosts=("hcaptcha.com",),
        token_prefixos=("P0_", "P1_", "E0_", "E1_"),
    )
    MODALIDADE_PADRAO = Modality.VISUAL

    # Acionar hCaptcha exige DUAS guardas, e as duas vieram de caso real.
    #
    # 1) Só widget invisible. Em checkbox visível, `execute()` ABRE o desafio de
    #    imagem — empurraria para o puzzle um caso que podia passar limpo.
    #
    # 2) NUNCA quando o elemento declara `data-callback`. Medido na Consulta
    #    Optantes da Receita: o `.h-captcha` está no próprio botão "Consultar"
    #    com `data-callback="onSubmit"`, então `execute()` dispara o callback do
    #    site e SUBMETE A CONSULTA. Submeter formulário do portal não é trabalho
    #    deste pacote; quem clica no botão é o RPA, no fluxo dele.
    ACIONAR_JS = r"""
    try {
      var invisivel = document.querySelector('.h-captcha[data-size="invisible"], '
        + '[data-hcaptcha-widget-id][data-size="invisible"]');
      if (!invisivel) return 0;
      if (invisivel.getAttribute('data-callback')) return 0;   // submeteria o form
      if (window.hcaptcha && typeof window.hcaptcha.execute === 'function') {
        window.hcaptcha.execute(); return 1;
      }
    } catch (e) {}
    return 0;
    """

    def variante(self, ctx: Ctx) -> Variant:
        """
        Quatro assinaturas de integração INVISÍVEL, todas medidas em produção na
        Receita Federal (31/08). Nenhuma delas tem widget para clicar, e tratar
        como `auto_render` fazia o solver esperar um render que nunca vem:

        1. `data-size="invisible"` no elemento — a forma clássica, por atributo;
        2. `.h-captcha` num BOTÃO/link — "bind to button": clicar o botão do site
           dispara o desafio (medido na Consulta Optantes, com
           `data-callback="onSubmit"`);
        3. `.h-captcha` com área ZERO e campo de resposta presente — o widget
           existe, só não ocupa espaço;
        4. NENHUM `.h-captcha` no DOM, mas global `hcaptcha` + campo de resposta
           + iframe do fornecedor: render programático via `hcaptcha.render()`,
           com a sitekey no bundle JS (medido na página de certidões, que é SPA).
        """
        containers = ctx.containers(self)
        campos = self.campos_proprios(ctx)

        for c in containers:
            attrs = c.get("attrs", {})
            if (attrs.get("data-size") or "").lower() == "invisible":
                return Variant.INVISIBLE
            if (c.get("tag") or "").lower() in ("button", "a", "input"):
                return Variant.INVISIBLE
            caixa = c.get("caixa") or {}
            if campos and not (caixa.get("w") or 0) and not (caixa.get("h") or 0):
                return Variant.INVISIBLE

        if not containers and ctx.globais(self) and (campos or ctx.frames(self)):
            return Variant.INVISIBLE

        for c in containers:
            if (c.get("attrs", {}).get("data-size") or "").lower() == "compact":
                return Variant.CHECKBOX
        if any("hcaptcha" in (f.get("src") or "") and f.get("visivel")
               and (f.get("caixa") or {}).get("h", 0) >= 200 for f in ctx.frames(self)):
            return Variant.VISUAL_CHALLENGE
        if any(f.get("visivel") for f in ctx.frames(self)):
            return Variant.CHECKBOX
        return Variant.AUTO_RENDER if containers else Variant.EXPLICIT_RENDER

    def modalidade(self, ctx: Ctx) -> Modality:
        v = self.variante(ctx)
        if v in (Variant.INVISIBLE, Variant.PASSIVE):
            return Modality.INVISIBLE_SCORE
        return Modality.VISUAL

    def detect(self, ctx: Ctx):
        res = super().detect(ctx)
        if not res.presente:
            return res
        if res.variant is Variant.INVISIBLE and res.state is State.WAITING_RENDER:
            # O widget ESTÁ montado — há iframe do fornecedor e campo de resposta.
            # Ele é invisível e espera o gesto do site (submit do formulário, ou
            # `hcaptcha.execute()` chamado pelo próprio portal). Dizer
            # WAITING_RENDER fazia o solver esperar a janela inteira por um render
            # que nunca vem: medido 20 s de timeout em duas páginas da Receita.
            res.state = State.PENDING
            res.diagnostico = res.diagnostico or (
                "hCaptcha invisível já montado, aguardando o gesto do próprio site "
                "(clique no botão de submit, ou hcaptcha.execute() chamado pelo "
                "portal). Não há widget para clicar: seguir o fluxo da página e "
                "chamar resolver() depois desse gesto, para esperar o token.")
        return res

    def motivo_bloqueio(self, res) -> str:
        if res.variant is Variant.INVISIBLE:
            return ("hCaptcha invisível: não há widget para clicar e o disparo pertence "
                    "ao site (submit do formulário ou hcaptcha.execute() do portal). "
                    "Se o token não veio, o gesto ainda não aconteceu — chamar de novo "
                    "depois dele, e não esperar aqui.")
        return ("hCaptcha exigiu desafio visual: o token só é emitido após a resposta "
                "certa nas imagens, que está fora do escopo de leitura deste projeto.")
