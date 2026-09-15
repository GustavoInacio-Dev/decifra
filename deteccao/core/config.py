"""
Configuração. Tudo com default utilizável; nada aqui é obrigatório para rodar.

Valores de tempo vêm de medição, não de chute — ver README, seção "Timings".
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

RAIZ = Path(__file__).resolve().parent


def criar_dir_de_artefato(caminho: Path) -> Path:
    """
    Cria o diretório e deixa DENTRO dele um `.gitignore` que o ignora.

    Por que não confiar só no .gitignore da raiz: estes diretórios são relativos
    ao MÓDULO (`Path(__file__)`), então acompanham o módulo quando a estrutura
    muda. Um `.gitignore` na raiz deixa de casar se os módulos se movem; um que
    viaja junto do diretório não tem esse problema, em
    qualquer estrutura futura. Falhar aqui nunca derruba quem chamou: não poder
    escrever o ignore é irrelevante para o trabalho em curso.
    """
    caminho.mkdir(parents=True, exist_ok=True)
    marca = caminho / ".gitignore"
    if not marca.exists():
        try:
            marca.write_text(
                "# Artefato de runtime: nunca versionar.\n*\n!.gitignore\n",
                encoding="utf-8")
        except OSError:
            pass
    return caminho


def _env_float(nome: str, default: float) -> float:
    try:
        return float(os.environ[nome])
    except (KeyError, ValueError):
        return default


def _env_bool(nome: str, default: bool) -> bool:
    v = os.environ.get(nome)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "sim")


@dataclass
class Timeouts:
    #: Janela para o script do fornecedor carregar antes de virar NETWORK_ERROR.
    script_load_s: float = 15.0
    #: Do script carregado até o widget renderizar (iframe/shadow/campo).
    render_s: float = 20.0
    #: Proof-of-work resolve sozinho; janela generosa porque depende de CPU.
    proof_of_work_s: float = 90.0
    #: Após conclusão alegada, prazo para reunir evidência positiva.
    validacao_s: float = 20.0
    #: Intervalo do poll reativo. Não é sleep de transição: é teto de latência
    #: entre eventos do MutationObserver serem drenados.
    poll_s: float = 0.4


@dataclass
class TokenPolicy:
    """
    Validade temporal. Só usada para DETECTAR expiração — nunca para reusar
    token: reuso entre sessões/máquinas/IPs é proibido por política.
    """

    #: reCAPTCHA documenta 2 minutos de validade do token de resposta.
    recaptcha_ttl_s: float = 120.0
    #: hCaptcha também expira; o próprio widget chama expired-callback.
    hcaptcha_ttl_s: float = 120.0
    #: Margem de segurança: token com menos que isso de sobra é tratado como
    #: expirado, para não submeter formulário com token morrendo no caminho.
    margem_s: float = 15.0


@dataclass
class Config:
    timeouts: Timeouts = field(default_factory=Timeouts)
    tokens: TokenPolicy = field(default_factory=TokenPolicy)

    #: Log estruturado JSON (uma linha por evento).
    log_path: Path = field(default_factory=lambda: Path(os.environ.get("CAPTCHA_LOG", RAIZ / "_logs" / "captcha.jsonl")))
    #: Ecoar os eventos no stderr além do arquivo.
    log_stderr: bool = field(default_factory=lambda: _env_bool("CAPTCHA_LOG_STDERR", False))

    #: Se True, permite o módulo de reconhecimento (leitura) do captcha caseiro.
    #: Só liga com domínio na allowlist abaixo. Ver generic.GenericAdapter.
    permitir_reconhecimento_experimental: bool = field(
        default_factory=lambda: _env_bool("CAPTCHA_RECONHECIMENTO_EXPERIMENTAL", False)
    )
    #: Domínios onde o experimental pode rodar. Vazio = nenhum.
    dominios_autorizados: tuple[str, ...] = ()

    def experimental_liberado(self, hostname: str | None) -> bool:
        """Só libera com flag ligada E domínio na allowlist explícita."""
        if not self.permitir_reconhecimento_experimental:
            return False
        if not hostname:
            return False
        return any(hostname == d or hostname.endswith("." + d) for d in self.dominios_autorizados)


#: Config padrão do processo. Trocar por instância própria em teste.
CONFIG = Config()
