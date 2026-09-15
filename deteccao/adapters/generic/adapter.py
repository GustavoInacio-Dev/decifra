"""
CAPTCHA caseiro: tribunais e sistemas próprios.

Não há assinatura de fornecedor aqui, então a detecção é morfológica: imagem
cujo src/alt/id fala 'captcha' ao lado de um input de texto, pergunta
matemática, áudio, slider proprietário, seleção de imagens.

Política deste adapter, sem exceção em produção de terceiros:
resolução humana. O módulo experimental abaixo existe para fixture sintética e
ambiente com autorização expressa, e mesmo lá ele NÃO faz reconhecimento de
imagem nem automatiza slider — só lê enunciado textual (ex.: "quanto é 3+4?").
Reconhecimento de imagem e arraste de slider estão fora por decisão de projeto,
não por falta de tempo.
"""

from __future__ import annotations

import re
from typing import Any

from ...core.config import CONFIG
from ...core.exceptions import ForbiddenOperation
from ...core.models import CaptchaResult, Evidence, Modality, Signal, State, Variant, Vendor
from ...core.base import BaseAdapter, Ctx, Signature

_PALAVRAS = re.compile(r"captcha|verificacao|verificação|codigo|código|imagem|desafio", re.I)
_MATH = re.compile(r"(\d+)\s*([+\-*x×])\s*(\d+)")

#: Tamanho mínimo para uma imagem contar como O DESAFIO, e não como ícone ao
#: lado dele.
#:
#: Medido: numa página real o captcha era um PNG de 137x45, e o botão de
#: acessibilidade ao lado era um ícone de 24x24 cujo id continha "captcha" E
#: "audio". Sem este piso de tamanho, o ícone virava a evidência e a variante
#: saía `audio` em vez de `text_image`.
IMG_MIN_W, IMG_MIN_H = 40, 15


class GenericAdapter(BaseAdapter):
    SIGNATURE = Signature(
        vendor=Vendor.GENERIC,
        script_hosts=(),
        globals_=(),
        container_selectors=(
            "[id*='captcha']", "[class*='captcha']", "[name*='captcha']",
            "#imagemCaptcha", "#captchaImg", ".captcha-container", "[id*='Captcha']",
            # A imagem do desafio muitas vezes não tem id nem classe própria —
            # ela é só um <img> dentro do bloco do captcha. Sem estes seletores,
            # a única imagem que o probe trazia era o ícone do botão de áudio.
            "[id*='captcha'] img", "[id*='Captcha'] img", "[class*='captcha'] img",
        ),
        response_fields=("captcha", "captchaText", "txtCaptcha", "codigoCaptcha",
                         "captcha_text", "vlCaptcha", "captchaResposta"),
        frame_hosts=(),
        token_keys=(),
    )
    MODALIDADE_PADRAO = Modality.VISUAL

    # ------------------------------------------------------------------ #
    # Detecção morfológica
    # ------------------------------------------------------------------ #

    def _imagens_suspeitas(self, ctx: Ctx) -> list[dict[str, Any]]:
        achados = []
        for c in ctx.retrato.get("containers", []):
            attrs = c.get("attrs", {})
            # `motivo` entra no texto, e não é detalhe: a imagem do desafio
            # muitas vezes não tem id nem classe, e aí o probe gera
            # `seletor: "img"` com `attrs: {}` — nada para casar. O que sobra é
            # o motivo, que grava QUAL seletor a achou
            # (`seletor:[id*='Captcha'] img`). Sem isto a imagem do desafio era
            # coletada e ignorada, e a variante saía pelo ícone de áudio.
            texto = " ".join([
                " ".join(str(v) for v in attrs.values()),
                c.get("seletor") or "",
                c.get("motivo") or "",
            ])
            if c.get("tag") == "img" and _PALAVRAS.search(texto):
                achados.append(c)
        return achados

    def _imagem_do_desafio(self, ctx: Ctx) -> list[dict[str, Any]]:
        """
        Imagens grandes o bastante para SER o desafio.

        Separar isto de `_imagens_suspeitas` é o que impede o ícone de 24x24 do
        botão de áudio de ser lido como o captcha. Ver IMG_MIN_W/IMG_MIN_H.
        """
        saida = []
        for c in self._imagens_suspeitas(ctx):
            caixa = c.get("caixa") or {}
            if (caixa.get("w") or 0) >= IMG_MIN_W and (caixa.get("h") or 0) >= IMG_MIN_H:
                saida.append(c)
        return saida

    def presente(self, ctx: Ctx) -> bool:
        if super().presente(ctx):
            return True
        return bool(self._imagens_suspeitas(ctx))

    def variante(self, ctx: Ctx) -> Variant:
        alvos = ctx.containers(self) + self._imagens_suspeitas(ctx)
        blob = " ".join(
            " ".join(str(v) for v in c.get("attrs", {}).values()) + " " + (c.get("seletor") or "")
            for c in alvos
        ).lower()
        imagens = self._imagem_do_desafio(ctx)

        if "slider" in blob or "arraste" in blob or "deslize" in blob:
            return Variant.SLIDER
        if _MATH.search(blob):
            return Variant.MATH
        # Imagem de desafio ganha do sinal de áudio, e a ordem é o conserto:
        # o áudio costuma ser o canal de ACESSIBILIDADE ao lado do captcha, não a
        # natureza dele. A palavra "audio" chegava no blob pelo id do botão de
        # áudio e roubava a variante.
        if imagens:
            return Variant.TEXT_IMAGE
        if "audio" in blob or "som" in blob:
            return Variant.AUDIO
        if any(c.get("tag") == "img" for c in alvos):
            return Variant.TEXT_IMAGE
        if "pergunta" in blob or "question" in blob:
            return Variant.QUESTION_ANSWER
        return Variant.UNKNOWN

    def detect(self, ctx: Ctx):
        res = super().detect(ctx)
        if not res.presente:
            return res
        if res.state == State.WAITING_RENDER:
            res.state = State.PENDING

        # Aqui o campo preenchido NÃO é evidência positiva.
        #
        # Em fornecedor de verdade, o campo guarda um token assinado por ele: se
        # tem conteúdo, o desafio foi resolvido. Em captcha caseiro o campo é
        # texto que alguém digitou — pode estar errado, e o portal só diz isso
        # depois do submit. Tratar 'digitado' como 'resolvido' faria o RPA seguir
        # com uma resposta errada e registrar a falha como sucesso.
        res.evidencias = [
            Evidence(e.signal, e.detail + " (digitado, não validado)",
                     positiva=False, origem=e.origem)
            if e.signal is Signal.RESPONSE_FIELD_FILLED else e
            for e in res.evidencias
        ]
        res.diagnostico = res.diagnostico or (
            "CAPTCHA proprietário: conclusão só é confirmável pela resposta do "
            "formulário, não por campo preenchido."
        )
        return res

    def validate_completion(self, ctx: Ctx):
        """
        Só aceita quando o desafio SAIU da página — o que só acontece se o
        portal tiver aceitado o formulário. Enquanto a imagem e o campo
        continuarem lá, o que existe é texto digitado, não conclusão.
        """
        res = self.detect(ctx)
        if not res.presente:
            return CaptchaResult(
                sucesso=True, state=State.VALIDATED, vendor=Vendor.GENERIC,
                resolvido_por="portal_aceitou",
                evidencias_positivas=[Evidence(Signal.RESPONSE_FIELD_FILLED,
                                               "desafio saiu da página: formulário aceito",
                                               positiva=True, origem="dom")],
            )
        preenchido = any(
            e.signal is Signal.RESPONSE_FIELD_FILLED for e in res.evidencias
        )
        return CaptchaResult(
            sucesso=False, state=res.state, vendor=Vendor.GENERIC,
            motivo=("campo preenchido, mas o desafio continua na página: "
                    "submeter o formulário para o portal confirmar"
                    if preenchido else
                    "campo de resposta vazio: desafio ainda pendente"),
        )

    def motivo_bloqueio(self, res) -> str:
        mapa = {
            Variant.TEXT_IMAGE: "caracteres estão numa imagem: exige reconhecimento.",
            Variant.MATH: "enunciado é aritmético em texto: resolvível sem imagem.",
            Variant.AUDIO: "resposta está no áudio: exige transcrição.",
            Variant.SLIDER: "exige arrastar um controle até a posição da imagem.",
            Variant.QUESTION_ANSWER: "pergunta em texto livre.",
        }
        return ("CAPTCHA próprio do portal — " + mapa.get(
            res.variant, "formato não classificado.")
            + " Não há token de fornecedor: quem valida é o submit do formulário.")

    # ------------------------------------------------------------------ #
    # Módulo experimental — travado por padrão
    # ------------------------------------------------------------------ #

    def resolver_experimental(self, ctx: Ctx, enunciado: str) -> str:
        """
        Só para fixture sintética / ambiente autorizado. Resolve exclusivamente
        enunciado ARITMÉTICO em texto. Não faz OCR, não toca em imagem, não
        automatiza slider.

        Levanta ForbiddenOperation em qualquer outro caso — inclusive quando a
        flag está ligada mas o domínio não está na allowlist.
        """
        from urllib.parse import urlparse

        host = urlparse(ctx.url).hostname
        if not CONFIG.experimental_liberado(host):
            raise ForbiddenOperation(
                f"reconhecimento experimental não autorizado para {host!r}. "
                "Exige flag CAPTCHA_RECONHECIMENTO_EXPERIMENTAL e domínio na allowlist."
            )
        m = _MATH.search(enunciado or "")
        if not m:
            raise ForbiddenOperation(
                "experimental cobre apenas enunciado aritmético em texto. "
                "Desafio visual é resolução humana, por decisão de projeto."
            )
        a, op, b = int(m.group(1)), m.group(2), int(m.group(3))
        return str({"+": a + b, "-": a - b, "*": a * b, "x": a * b, "×": a * b}[op])
