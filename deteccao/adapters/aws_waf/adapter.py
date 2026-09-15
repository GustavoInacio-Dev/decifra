"""
AWS WAF CAPTCHA / Challenge.

Strings conferidas na doc da AWS (waf-js-captcha-api-specification e
waf-js-challenge-api-specification):
  render      : AwsWafCaptcha.renderCaptcha(container, {apiKey, onSuccess,
                onError, onLoad, onPuzzleTimeout, onPuzzleCorrect,
                onPuzzleIncorrect, defaultLocale, disableLanguageSelector,
                dynamicWidth, skipTitle})
  integração  : AwsWafIntegration.getToken() / .fetch() / .hasToken()
  token       : cookie `aws-waf-token`
  erros       : CaptchaError.kind = internal_error | network_error |
                token_error | client_error   (+ statusCode opcional)

Duas formas de aparecer:
  1) desafio de página inteira da própria AWS ("Verifique se é humano")
  2) puzzle embutido via renderCaptcha, sem tela de título
"""

from __future__ import annotations

from ...core.models import Evidence, Modality, State, Variant, Vendor
from ...core.base import BaseAdapter, Ctx, Signature


class AwsWafAdapter(BaseAdapter):
    SIGNATURE = Signature(
        vendor=Vendor.AWS_WAF,
        script_hosts=("captcha.awswaf.com", "challenge.awswaf.com", "token.awswaf.com", ".awswaf.com"),
        globals_=("AwsWafCaptcha", "AwsWafCaptcha.renderCaptcha", "AwsWafIntegration",
                  "AwsWafIntegration.getToken", "AwsWafIntegration.hasToken"),
        container_selectors=("#captcha-container", "[id*='captcha-container']", ".captcha-container",
                             # A UI do desafio da AWS, com prefixo próprio dela.
                             # Independe de idioma: medido em produção, o título fica
                             # em inglês ("Human Verification") e o corpo em
                             # pt-BR, mas estes ids/classes não mudam.
                             "#amzn-captcha-verify-button", ".amzn-captcha-state-container",
                             ".amzn-captcha-verify-button", "#verification-status"),
        response_fields=("aws-waf-token",),
        frame_hosts=(".awswaf.com",),
        # O cookie `aws-waf-token` NÃO entra como token_keys de propósito.
        #
        # Medido em produção: a página inteira "Human Verification"
        # tem o cookie desde o primeiro segundo, e continua pedindo a verificação
        # 30 s depois. Tratar a presença dele como evidência positiva devolvia
        # sucesso numa página que nunca liberou — o RPA extrairia a tela do WAF
        # achando que era a consulta. Aqui, sucesso é a UI do desafio NÃO estar
        # na tela.
        token_keys=(),
        test_keys=(),
    )

    #: Marcadores da UI do desafio. Se algum está no DOM, há desafio ABERTO.
    MARCAS_UI = ("amzn-captcha", "verification-status", "captcha-container")
    MODALIDADE_PADRAO = Modality.VISUAL

    #: Marcadores que são SÓ da AWS. `captcha-container` NÃO está aqui de
    #: propósito: é nome de classe comuníssimo, o adapter `generic` também o usa,
    #: e sozinho ele não identifica fornecedor nenhum.
    MARCAS_PROPRIAS = ("amzn-captcha", "verification-status")

    # Challenge silencioso: `AwsWafIntegration.getToken()` roda o fingerprint da
    # própria AWS e grava o cookie `aws-waf-token`. É a API documentada para o
    # integrador chamar; não decide nada no lugar da AWS. Não toca no
    # renderCaptcha (puzzle), que é justamente o caminho que exige imagem.
    ACIONAR_JS = r"""
    try {
      const I = window.AwsWafIntegration;
      if (!I) return 0;
      if (typeof I.hasToken === 'function' && I.hasToken()) return 0;
      if (typeof I.getToken === 'function') { I.getToken(); return 1; }
      if (typeof I.fetch === 'function') { I.fetch(location.href); return 1; }
    } catch (e) {}
    return 0;
    """

    def variante(self, ctx: Ctx) -> Variant:
        # O desafio de página inteira da AWS não tem container do integrador.
        if not ctx.containers(self) and (ctx.globais(self) or ctx.scripts(self)):
            return Variant.PAGINA_INTEIRA
        if any(f.get("visivel") for f in ctx.frames(self)):
            return Variant.VISUAL_CHALLENGE
        return Variant.EMBEDDED

    def modalidade(self, ctx: Ctx) -> Modality:
        # Challenge silencioso resolve sozinho; o CAPTCHA interativo é puzzle.
        if self.variante(ctx) == Variant.PAGINA_INTEIRA and not ctx.frames(self):
            return Modality.INVISIBLE_SCORE
        return Modality.VISUAL

    def _evidencia_propria(self, ctx: Ctx) -> bool:
        """
        Há algo que identifique a AWS nesta página?

        Sem isto, `.captcha-container` bastava para o adapter se declarar
        presente — e um captcha caseiro de tribunal com essa classe virava
        `aws_waf/visual_challenge`, ou seja "sem trilha, desista". Medido na
        fixture `generico_tribunal.html`, que é justamente um captcha de imagem
        que a API sabe LER.

        Não é sucesso falso, é instrução falsa: manda a RPA desistir de um
        captcha resolvível.
        """
        if ctx.globais(self) or ctx.scripts(self) or ctx.frames(self):
            return True
        if ctx.campos(self):                       # o campo aws-waf-token
            return True
        for c in ctx.containers(self):
            alvo = " ".join([
                c.get("seletor") or "",
                (c.get("attrs", {}).get("id") or ""),
                (c.get("attrs", {}).get("class") or ""),
                c.get("motivo") or "",
            ])
            if any(m in alvo for m in self.MARCAS_PROPRIAS):
                return True
        return False

    def presente(self, ctx: Ctx) -> bool:
        return super().presente(ctx) and self._evidencia_propria(ctx)

    def ui_do_desafio(self, ctx: Ctx) -> str | None:
        """Seletor da UI do desafio da AWS, se ela está na tela."""
        for c in ctx.containers(self):
            alvo = " ".join([
                c.get("seletor") or "",
                (c.get("attrs", {}).get("id") or ""),
                (c.get("attrs", {}).get("class") or ""),
                c.get("motivo") or "",
            ])
            for marca in self.MARCAS_UI:
                if marca in alvo:
                    return c.get("seletor") or marca
        return None

    def detect(self, ctx: Ctx):
        res = super().detect(ctx)
        if not res.presente:
            return res
        ui = self.ui_do_desafio(ctx)
        if ui and res.state not in (State.CSP_BLOCKED, State.FRAME_BLOCKED,
                                    State.NETWORK_ERROR):
            # A UI está na tela: há desafio aberto, e nenhuma evidência de token
            # vale contra isso. Medido em produção: cookie aws-waf-token presente e a
            # página pedindo verificação por mais de 30 s.
            res.state = State.PENDING
            res.variant = Variant.VISUAL_CHALLENGE
            res.evidencias = [
                Evidence(e.signal, e.detail + " (UI do desafio na tela: não conclui)",
                         positiva=False, origem=e.origem) if e.positiva else e
                for e in res.evidencias
            ]
            res.diagnostico = (
                f"UI do desafio da AWS WAF na tela ({ui}). O cookie aws-waf-token "
                "existe desde o carregamento e NÃO significa desafio resolvido — "
                "aqui só a ausência desta UI conta como passagem.")
        return res

    def _diagnosticar(self, ctx: Ctx, res):
        """
        Acrescenta o caso específico da AWS: o SDK exige contexto seguro e,
        sem HTTPS, `getToken` falha com token_error sem nada aparecer na tela.
        """
        estado = super()._diagnosticar(ctx, res)
        if estado:
            return estado
        for e in ctx.eventos.get("erros", []):
            msg = (e.get("msg") or "").lower()
            if "awswaf" in msg or "aws-waf" in msg:
                for kind in ("internal_error", "network_error", "token_error", "client_error"):
                    if kind in msg:
                        res.diagnostico = f"AwsWafCaptcha CaptchaError.kind={kind}"
                        break
        return None

    def motivo_bloqueio(self, res) -> str:
        if res.variant == Variant.PAGINA_INTEIRA:
            return ("AWS WAF challenge silencioso não emitiu o cookie aws-waf-token: "
                    "ver contexto seguro (HTTPS), rede e CaptchaError no diagnóstico.")
        return ("AWS WAF abriu o CAPTCHA interativo (quebra-cabeça): o token só sai "
                "depois da resposta certa na imagem, que não está no DOM.")
