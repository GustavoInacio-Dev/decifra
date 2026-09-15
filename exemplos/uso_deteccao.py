"""
Detecção read-only: dado um retrato do DOM, diz qual captcha a página usa.

    python exemplos/uso_deteccao.py

Este exemplo usa um retrato sintético (montado à mão) para não depender de
navegador. Em uso real, o retrato vem de um BrowserObserver ligado ao Selenium —
ver deteccao/core/browser_observer.py.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deteccao.adapters import ADAPTERS, Ctx
from deteccao.core.classifier import classificar


def detectar(retrato: dict):
    brutos = [ad.detect(Ctx(retrato=retrato)) for ad in ADAPTERS]
    return classificar([b for b in brutos if b.presente], Ctx(retrato=retrato))


if __name__ == "__main__":
    # Um retrato de uma página com reCAPTCHA v2.
    retrato = {
        "url": "https://exemplo.test/login",
        "scripts": [{"src": "https://www.google.com/recaptcha/api.js"}],
        "globals": {"grecaptcha": True},
        "containers": [{"seletor": ".g-recaptcha", "tag": "div",
                        "attrs": {"class": "g-recaptcha", "data-sitekey": "..."}}],
        "responseFields": [{"nome": "g-recaptcha-response", "valor": ""}],
        "iframes": [], "customElements": [], "totalIframes": 0, "eventos": {},
    }

    for d in detectar(retrato):
        print(f"fornecedor : {d.vendor.value}")
        print(f"variante   : {d.variant.value}")
        print(f"estado     : {d.state.value}")
        print(f"sinais     : {sorted(s.value for s in d.sinais)}")
