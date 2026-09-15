"""
Registro de adapters.

Ordem importa: os mais específicos primeiro. reCAPTCHA Enterprise antes de v2
(mesmo container, script diferente) e v3 antes de v2 (mesmo script, `render=`
diferente). GenericAdapter é sempre o último — ele casa por morfologia e
engoliria os outros se viesse antes.
"""

from __future__ import annotations

from .altcha.adapter import AltchaAdapter
from .arkose.adapter import ArkoseAdapter
from .aws_waf.adapter import AwsWafAdapter
from ..core.base import BaseAdapter, CaptchaAdapter, Ctx, Signature
from .friendly.adapter import FriendlyCaptchaAdapter
from .geetest.adapter import GeeTestV3Adapter, GeeTestV4Adapter
from .generic.adapter import GenericAdapter
from .hcaptcha.adapter import HCaptchaAdapter
from .recaptcha.adapter import RecaptchaEnterpriseAdapter, RecaptchaV2Adapter, RecaptchaV3Adapter

#: Instâncias são stateless: reusar é seguro e barato.
ADAPTERS: tuple[BaseAdapter, ...] = (
    RecaptchaEnterpriseAdapter(),
    RecaptchaV3Adapter(),
    RecaptchaV2Adapter(),
    HCaptchaAdapter(),
    AwsWafAdapter(),
    GeeTestV4Adapter(),
    GeeTestV3Adapter(),
    ArkoseAdapter(),
    FriendlyCaptchaAdapter(),
    AltchaAdapter(),
    GenericAdapter(),
)

__all__ = [
    "ADAPTERS",
    "BaseAdapter",
    "CaptchaAdapter",
    "Ctx",
    "Signature",
    "AltchaAdapter",
    "ArkoseAdapter",
    "AwsWafAdapter",
    "FriendlyCaptchaAdapter",
    "GeeTestV3Adapter",
    "GeeTestV4Adapter",
    "GenericAdapter",
    "HCaptchaAdapter",
    "RecaptchaEnterpriseAdapter",
    "RecaptchaV2Adapter",
    "RecaptchaV3Adapter",
]
