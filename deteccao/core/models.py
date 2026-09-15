"""
Tipos do orquestrador. Sem dependência de browser: dá pra testar tudo aqui com
dicionários vindos de fixture, sem subir Chrome.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Vendor(str, Enum):
    """Fornecedor do desafio."""

    RECAPTCHA_V2 = "recaptcha_v2"
    RECAPTCHA_V3 = "recaptcha_v3"
    RECAPTCHA_ENTERPRISE = "recaptcha_enterprise"
    HCAPTCHA = "hcaptcha"
    AWS_WAF = "aws_waf"
    GEETEST_V3 = "geetest_v3"
    GEETEST_V4 = "geetest_v4"
    ARKOSE = "arkose"
    FRIENDLY_CAPTCHA = "friendly_captcha"
    ALTCHA = "altcha"
    GENERIC = "generic"
    UNKNOWN = "unknown"


#: Fornecedores reconhecidos mas fora do escopo de leitura (nenhum, por ora).
OUT_OF_SCOPE: frozenset[Vendor] = frozenset()


class Variant(str, Enum):
    """Como o desafio se apresenta. Muda o tratamento, não o fornecedor."""

    CHECKBOX = "checkbox"
    VISUAL_CHALLENGE = "visual_challenge"      # seleção de imagem, clique em ícone
    INVISIBLE = "invisible"
    PASSIVE = "passive"
    EXPLICIT_RENDER = "explicit_render"
    AUTO_RENDER = "auto_render"
    SCORE_BASED = "score_based"                # v3/Enterprise: sem UI
    PAGINA_INTEIRA = "pagina_inteira"
    POPUP = "popup"
    FLOATING = "floating"
    BIND = "bind"
    EMBEDDED = "embedded"
    PROOF_OF_WORK = "proof_of_work"
    SLIDER = "slider"
    TEXT_IMAGE = "text_image"
    MATH = "math"
    AUDIO = "audio"
    QUESTION_ANSWER = "question_answer"
    COORDINATE_CLICK = "coordinate_click"
    UNKNOWN = "unknown"


class Modality(str, Enum):
    """
    Distingue desafio visual de proof-of-work. Critério de aceite explícito:
    PoW e score resolvem sozinhos (só esperar); visual só conclui com a resposta
    certa, que está na imagem.
    """

    VISUAL = "visual"
    PROOF_OF_WORK = "proof_of_work"
    INVISIBLE_SCORE = "invisible_score"
    UNKNOWN = "unknown"


class State(str, Enum):
    """Estados da FSM. Ver state_machine.TRANSICOES para o grafo."""

    NOT_PRESENT = "NOT_PRESENT"
    DETECTED = "DETECTED"
    SCRIPT_LOADING = "SCRIPT_LOADING"
    WAITING_RENDER = "WAITING_RENDER"
    RENDERED = "RENDERED"
    PENDING = "PENDING"
    PUZZLE_REQUIRED = "PUZZLE_REQUIRED"
    TOKEN_GENERATED = "TOKEN_GENERATED"
    CALLBACK_EXECUTED = "CALLBACK_EXECUTED"
    VALIDATED = "VALIDATED"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"
    NETWORK_ERROR = "NETWORK_ERROR"
    CSP_BLOCKED = "CSP_BLOCKED"
    FRAME_BLOCKED = "FRAME_BLOCKED"
    TIMEOUT = "TIMEOUT"
    FAILED = "FAILED"


#: Estados a partir dos quais não há mais transição.
#:
#: PUZZLE_REQUIRED é terminal porque não há operador: se o desafio virou imagem
#: ou slider, ninguém neste processo vai resolvê-lo. Esperar mais não muda nada,
#: e devolver isso como 'aberto' faria o solver girar até o timeout.
TERMINAIS: frozenset[State] = frozenset(
    {State.VALIDATED, State.REJECTED, State.TIMEOUT, State.FAILED, State.NOT_PRESENT,
     State.PUZZLE_REQUIRED}
)

#: Estados que significam "tem desafio e ele não está resolvido".
ABERTOS: frozenset[State] = frozenset(
    {
        State.DETECTED,
        State.SCRIPT_LOADING,
        State.WAITING_RENDER,
        State.RENDERED,
        State.PENDING,
        State.EXPIRED,
    }
)


class Signal(str, Enum):
    """
    Sinal individual observado na página. O detector correlaciona vários; nenhum
    isolado decide, e a AUSÊNCIA de qualquer um deles nunca decide.
    """

    SCRIPT_PRESENT = "script_present"
    SCRIPT_FAILED = "script_failed"
    GLOBAL_OBJECT = "global_object"
    CONTAINER = "container"
    SITEKEY_ATTR = "sitekey_attr"
    RESPONSE_FIELD_PRESENT = "response_field_present"
    RESPONSE_FIELD_EMPTY = "response_field_empty"
    RESPONSE_FIELD_FILLED = "response_field_filled"
    IFRAME_PRESENT = "iframe_present"
    IFRAME_ZERO_SIZE = "iframe_zero_size"
    IFRAME_HIDDEN = "iframe_hidden"
    SHADOW_ROOT_OPEN = "shadow_root_open"
    SHADOW_ROOT_CLOSED_SUSPECT = "shadow_root_closed_suspect"
    CUSTOM_ELEMENT = "custom_element"
    CALLBACK_REGISTERED = "callback_registered"
    CALLBACK_FIRED = "callback_fired"
    INIT_REQUEST_PENDING = "init_request_pending"
    INIT_REQUEST_FAILED = "init_request_failed"
    TOKEN_COOKIE = "token_cookie"
    TOKEN_STORAGE = "token_storage"
    CONSOLE_ERROR = "console_error"
    CSP_VIOLATION = "csp_violation"
    POPUP_OPENED = "popup_opened"
    WIDGET_HIDDEN_PENDING_ACTION = "widget_hidden_pending_action"
    TOKEN_EXPIRED = "token_expired"
    POW_RUNNING = "pow_running"


def campo_preenchido(campo: dict[str, Any]) -> bool:
    """
    True só quando o retrato PROVA que o campo de resposta tem valor.

    A chave ausente nunca prova preenchimento. O probe do browser sempre manda
    `vazio`/`len` (ver browser_observer), mas um retrato montado à mão — fixture,
    exemplo, replay de log antigo — costuma trazer só `valor`, e ler "sem a chave
    `vazio`" como "preenchido" invertia o invariante do detector: concluía
    TOKEN_GENERATED com o campo vazio, e `validate_completion` devolvia sucesso
    sem token nenhum. Falso positivo é o erro caro aqui — na dúvida, pendente.
    """
    if "vazio" in campo:
        return not campo["vazio"]
    if campo.get("len") is not None:
        return int(campo["len"] or 0) > 0
    if "valor" in campo:
        return bool(campo["valor"])
    return False


def tamanho_do_campo(campo: dict[str, Any]) -> int:
    """Tamanho do valor, do `len` do probe ou medido no `valor` de fixture."""
    if campo.get("len") is not None:
        return int(campo["len"] or 0)
    return len(str(campo.get("valor") or ""))


def prefixo_do_campo(campo: dict[str, Any]) -> str:
    """
    Os 3 primeiros caracteres — só para reconhecer formato ('03A' do reCAPTCHA,
    'P1_' do hCaptcha). Nunca o token inteiro.
    """
    prefixo = campo.get("prefixo")
    if prefixo is None:
        prefixo = str(campo.get("valor") or "")[:3]
    return str(prefixo)


@dataclass(frozen=True)
class Evidence:
    """
    Uma evidência com procedência. `positiva` marca as que podem sustentar
    conclusão de sucesso — as demais só sustentam "pendente" ou diagnóstico.
    """

    signal: Signal
    detail: str = ""
    positiva: bool = False
    origem: str = "dom"  # dom | network | token | console | cdp | operator

    def __str__(self) -> str:  # pragma: no cover - conveniência de log
        return f"{self.signal.value}({self.origem})"


@dataclass
class DetectionResult:
    """Saída do detector para UM widget."""

    presente: bool
    vendor: Vendor = Vendor.UNKNOWN
    variant: Variant = Variant.UNKNOWN
    modality: Modality = Modality.UNKNOWN
    state: State = State.NOT_PRESENT
    sitekey: str | None = None
    container_selector: str | None = None
    response_field: str | None = None
    frame_url: str | None = None
    evidencias: list[Evidence] = field(default_factory=list)
    diagnostico: str | None = None
    fora_de_escopo: bool = False

    @property
    def pendente(self) -> bool:
        return self.state in ABERTOS

    @property
    def sinais(self) -> set[Signal]:
        return {e.signal for e in self.evidencias}

    def com(self, *evidencias: Evidence) -> DetectionResult:
        self.evidencias.extend(evidencias)
        return self


@dataclass
class CaptchaSnapshot:
    """Estado observado num instante. É o que a FSM consome."""

    state: State
    detections: list[DetectionResult] = field(default_factory=list)
    token_presente: bool = False
    token_idade_s: float | None = None
    callbacks_disparados: list[str] = field(default_factory=list)
    erros_rede: list[str] = field(default_factory=list)
    erros_console: list[str] = field(default_factory=list)
    url: str | None = None

    @property
    def abertos(self) -> list[DetectionResult]:
        return [d for d in self.detections if d.pendente and not d.fora_de_escopo]


@dataclass
class CaptchaResult:
    """Resultado final. `evidencias_positivas` é o que autoriza sucesso."""

    sucesso: bool
    state: State
    vendor: Vendor = Vendor.UNKNOWN
    duracao_s: float = 0.0
    #: automatico_pow | automatico_score | automatico_confianca |
    #: automatico_reconhecimento | portal_aceitou | nao_resolvido
    resolvido_por: str = "nao_resolvido"
    evidencias_positivas: list[Evidence] = field(default_factory=list)
    motivo: str | None = None
    #: True quando o solver chegou até o desafio visual e parou ali. Não é erro
    #: de ambiente: o item precisa de outro caminho (reenfileirar, outro portal,
    #: reconhecimento habilitado). O RPA trata diferente de uma falha de rede.
    puzzle_required: bool = False
    extra: dict[str, Any] = field(default_factory=dict)
