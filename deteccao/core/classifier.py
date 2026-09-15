"""
Classificação fina: resolve sobreposição entre adapters.

Casos reais de conflito:
  * hCaptcha em modo compatibilidade cria `g-recaptcha-response` — dois adapters
    reivindicam o mesmo campo.
  * reCAPTCHA v2 e v3 dividem o mesmo script; separa `render=<sitekey>`.
  * Enterprise usa o mesmo container do v2; separa o script enterprise.js.
  * GenericAdapter casa por morfologia e casaria em cima de todos.
"""

from __future__ import annotations

from .base import Ctx
from .models import DetectionResult, Signal, Vendor

#: Peso de cada sinal na confiança da atribuição. Assinatura própria vale mais
#: que morfologia: script e global são difíceis de confundir, classe não.
_PESO = {
    Signal.SCRIPT_PRESENT: 5,
    Signal.GLOBAL_OBJECT: 5,
    Signal.CUSTOM_ELEMENT: 5,
    Signal.SITEKEY_ATTR: 3,
    Signal.IFRAME_PRESENT: 3,
    Signal.CONTAINER: 2,
    Signal.RESPONSE_FIELD_PRESENT: 2,
    Signal.SHADOW_ROOT_OPEN: 1,
    Signal.CALLBACK_REGISTERED: 1,
}

#: Quando os dois aparecem, o primeiro vence e o segundo é descartado.
_PRECEDENCIA: tuple[tuple[Vendor, Vendor], ...] = (
    (Vendor.RECAPTCHA_ENTERPRISE, Vendor.RECAPTCHA_V2),
    (Vendor.RECAPTCHA_ENTERPRISE, Vendor.RECAPTCHA_V3),
    (Vendor.RECAPTCHA_V3, Vendor.RECAPTCHA_V2),
    (Vendor.HCAPTCHA, Vendor.RECAPTCHA_V2),      # modo compatibilidade
    (Vendor.GEETEST_V4, Vendor.GEETEST_V3),
)


def confianca(res: DetectionResult) -> int:
    return sum(_PESO.get(s, 0) for s in res.sinais)


def classificar(brutos: list[DetectionResult], ctx: Ctx) -> list[DetectionResult]:
    """
    Recebe o que cada adapter viu e devolve a lista final, sem duplicata.
    Preserva múltiplos fornecedores quando são de fato distintos (acontece:
    portal com reCAPTCHA no login e captcha caseiro na consulta).
    """
    if not brutos:
        return []

    por_vendor = {r.vendor: r for r in sorted(brutos, key=confianca)}

    # 1. Precedência declarada.
    for vencedor, perdedor in _PRECEDENCIA:
        if vencedor in por_vendor and perdedor in por_vendor:
            # Só derruba se o vencedor tiver assinatura forte; senão pode ser
            # página com os dois de verdade.
            if confianca(por_vendor[vencedor]) >= confianca(por_vendor[perdedor]):
                por_vendor.pop(perdedor, None)

    # 2. Genérico só sobrevive se nenhum fornecedor conhecido apareceu — ou se
    #    ele achou coisa que nenhum outro achou (imagem de captcha caseiro).
    if Vendor.GENERIC in por_vendor and len(por_vendor) > 1:
        generico = por_vendor[Vendor.GENERIC]
        if confianca(generico) <= 4:
            por_vendor.pop(Vendor.GENERIC)

    # 3. Descarta atribuição sem nenhum sinal forte: vira ruído no log.
    finais = [r for r in por_vendor.values() if confianca(r) >= 2]
    finais.sort(key=confianca, reverse=True)
    for r in finais:
        r.evidencias.sort(key=lambda e: (not e.positiva, e.signal.value))
    return finais
