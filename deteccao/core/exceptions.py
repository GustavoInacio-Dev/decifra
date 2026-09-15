"""Exceções do orquestrador."""


class CaptchaOrchestratorError(Exception):
    """Base de tudo que este pacote levanta."""


class BridgeError(CaptchaOrchestratorError):
    """Falha ao falar com o browser (driver morto, sessão inválida, JS quebrado)."""


class ProbeError(BridgeError):
    """O probe JS não retornou estrutura utilizável."""


class OutOfScopeError(CaptchaOrchestratorError):
    """
    Tentativa de tratar um fornecedor marcado como fora de escopo de leitura.
    Existe para falhar alto em vez de silenciosamente sair do que o projeto faz.
    """


class ValidationFailed(CaptchaOrchestratorError):
    """Conclusão alegada, mas sem evidência positiva suficiente."""


class ForbiddenOperation(CaptchaOrchestratorError):
    """
    Operação vetada por política: forjar/injetar/reusar token, resolver desafio
    visual automaticamente, integrar serviço de quebra, ocultar automação.
    """
