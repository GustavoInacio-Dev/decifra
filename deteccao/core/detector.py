"""
Detecção multi-sinal.

Monta um ProbeSpec com a união das assinaturas de todos os adapters, tira UM
retrato do documento e pergunta a cada adapter o que ele vê. O classifier
resolve sobreposição depois.

Cada adapter é read-only: inspeciona o retrato e diz "isto é meu", com a
variante e os sinais que viu. Nada aqui resolve o desafio.
"""

from __future__ import annotations

from typing import Any

from ..adapters import ADAPTERS
from .base import Ctx
from .browser_observer import BrowserObserver, ProbeSpec
from .classifier import classificar
from .config import CONFIG, Config
from .models import (
    ABERTOS,
    CaptchaSnapshot,
    DetectionResult,
    Evidence,
    Signal,
    State,
    Variant,
    Vendor,
)
from .telemetry import Telemetry
from .token_observer import TokenObserver

def montar_spec() -> ProbeSpec:
    """União das assinaturas de todos os adapters."""
    spec = ProbeSpec()
    for ad in ADAPTERS:
        sig = ad.SIGNATURE
        spec.response_fields.extend(sig.response_fields)
        spec.container_selectors.extend(sig.container_selectors)
        spec.globals_.extend(sig.globals_)
        spec.script_hosts.extend(sig.script_hosts)
        spec.custom_elements.extend(sig.custom_elements)
        spec.frame_hosts.extend(sig.frame_hosts)
        spec.token_keys.extend(sig.token_keys)

    # dedup preservando ordem
    for campo in ("response_fields", "container_selectors", "globals_",
                  "script_hosts", "custom_elements", "frame_hosts", "token_keys"):
        setattr(spec, campo, list(dict.fromkeys(getattr(spec, campo))))
    return spec


class Detector:
    """Ponto de entrada da detecção."""

    def __init__(self, observer: BrowserObserver, config: Config | None = None,
                 telemetry: Telemetry | None = None):
        self.observer = observer
        self.config = config or CONFIG
        self.telemetry = telemetry or Telemetry(self.config)
        self.spec = montar_spec()
        self.tokens = TokenObserver(observer.bridge, self.config)

    # ------------------------------------------------------------------ #

    def retrato(self) -> dict[str, Any]:
        return self.observer.retrato(self.spec)

    def contexto(self) -> Ctx:
        return Ctx(retrato=self.retrato(), bridge=self.observer.bridge,
                   observer=self.observer, telemetry=self.telemetry, config=self.config)

    def detectar(self, ctx: Ctx | None = None) -> CaptchaSnapshot:
        ctx = ctx or self.contexto()
        retrato = ctx.retrato

        brutos: list[DetectionResult] = []
        for ad in ADAPTERS:
            try:
                res = ad.detect(ctx)
            except Exception as exc:  # adapter quebrado não derruba a detecção
                self.telemetry.diagnostico("adapter_falhou", adapter=type(ad).__name__, erro=str(exc))
                continue
            if res.presente:
                brutos.append(res)

        detections = classificar(brutos, ctx)

        estado_token = self.tokens.observar(retrato)
        snap = CaptchaSnapshot(
            state=self._estado_agregado(detections),
            detections=detections,
            token_presente=estado_token.presente,
            token_idade_s=estado_token.idade_s,
            erros_console=[e.get("msg", "") for e in (retrato.get("eventos") or {}).get("erros", [])],
            url=retrato.get("url"),
        )

        self.telemetry.deteccao(
            url=snap.url,
            estado=snap.state.value,
            achados=[
                {"vendor": d.vendor.value, "variante": d.variant.value,
                 "estado": d.state.value, "fora_de_escopo": d.fora_de_escopo,
                 "sinais": sorted(s.value for s in d.sinais)}
                for d in detections
            ],
        )
        return snap

    # ------------------------------------------------------------------ #

    @staticmethod
    def _estado_agregado(detections: list[DetectionResult]) -> State:
        """
        O pior estado manda: se qualquer desafio em escopo está aberto, a página
        está bloqueada. Falha de ambiente tem prioridade porque muda a ação.
        """
        em_escopo = [d for d in detections if not d.fora_de_escopo]
        if not em_escopo:
            return State.NOT_PRESENT
        for prioritario in (State.CSP_BLOCKED, State.FRAME_BLOCKED, State.NETWORK_ERROR,
                            State.EXPIRED, State.REJECTED):
            if any(d.state == prioritario for d in em_escopo):
                return prioritario
        abertos = [d for d in em_escopo if d.state in ABERTOS]
        if abertos:
            # PENDING é mais informativo que DETECTED para quem consome.
            for pref in (State.PENDING, State.WAITING_RENDER, State.SCRIPT_LOADING):
                if any(d.state == pref for d in abertos):
                    return pref
            return abertos[0].state
        if any(d.state in (State.TOKEN_GENERATED, State.CALLBACK_EXECUTED, State.VALIDATED)
               for d in em_escopo):
            return State.TOKEN_GENERATED
        return State.NOT_PRESENT
