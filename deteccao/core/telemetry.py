"""
Log estruturado JSON, uma linha por evento.

Regra dura: token, cookie, senha e dado pessoal nunca entram no log. O que
entra é *metadado* — presença, tamanho, prefixo de hash — o suficiente para
depurar sem vazar credencial em arquivo de log que vai para ticket e print.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from .config import CONFIG, Config, criar_dir_de_artefato

#: Chaves cujo valor nunca é logado na íntegra.
SENSIVEIS = re.compile(
    r"token|cookie|senha|password|secret|authorization|pass_token|captcha_output"
    r"|solution|response|clearance|cpf|cnpj|email|pin|certificad",
    re.IGNORECASE,
)

#: Padrões de dado pessoal que podem aparecer solto em string livre.
_CPF = re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")
_CNPJ = re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b")
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")


def impressao_digital(valor: str) -> str:
    """
    Identidade estável de um segredo, sem o segredo: 12 hex do sha256. Serve
    para dizer "é o mesmo token de antes" sem nunca registrar o token.
    """
    return hashlib.sha256(valor.encode("utf-8", "replace")).hexdigest()[:12]


def redigir(valor: Any, chave: str = "") -> Any:
    """Redige recursivamente. Chave sensível vira metadado; string livre é limpa."""
    if isinstance(valor, dict):
        return {k: redigir(v, k) for k, v in valor.items()}
    if isinstance(valor, (list, tuple)):
        return [redigir(v, chave) for v in valor]

    if isinstance(valor, str):
        if chave and SENSIVEIS.search(chave):
            if not valor:
                return {"presente": False}
            return {"presente": True, "len": len(valor), "fp": impressao_digital(valor)}
        limpo = _CPF.sub("[cpf]", valor)
        limpo = _CNPJ.sub("[cnpj]", limpo)
        limpo = _EMAIL.sub("[email]", limpo)
        return limpo

    if chave and SENSIVEIS.search(chave) and valor is not None and not isinstance(valor, bool):
        return {"presente": True, "tipo": type(valor).__name__}
    return valor


class Telemetry:
    """
    Escreve JSONL. Um `correlation_id` por execução de RPA e um `captcha_id`
    por desafio, para juntar a linha do tempo depois.
    """

    def __init__(self, config: Config | None = None, correlation_id: str | None = None):
        self.config = config or CONFIG
        self.correlation_id = correlation_id or uuid.uuid4().hex[:12]
        self._path: Path = Path(self.config.log_path)
        self._inicio = time.monotonic()

    def evento(self, tipo: str, **campos: Any) -> dict[str, Any]:
        registro: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "t_rel_s": round(time.monotonic() - self._inicio, 3),
            "correlation_id": self.correlation_id,
            "evento": tipo,
        }
        registro.update(redigir(campos))
        linha = json.dumps(registro, ensure_ascii=False, default=str)

        try:
            criar_dir_de_artefato(self._path.parent)
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(linha + "\n")
        except OSError as exc:  # log nunca derruba o RPA
            print(f"[telemetry] falha ao gravar: {exc}", file=sys.stderr)

        if self.config.log_stderr:
            try:
                print(linha, file=sys.stderr)
            except UnicodeEncodeError:
                # Console do Windows em cp1252 não imprime CJK nem emoji, e o
                # retrato carrega texto da página. Medido com o título do
                # bilibili.com: o print levantava UnicodeEncodeError e derrubava
                # o solver inteiro — justamente o que este módulo promete não
                # fazer. O arquivo já é gravado em UTF-8 e não perde nada.
                print(linha.encode("ascii", "backslashreplace").decode("ascii"),
                      file=sys.stderr)
        return registro

    # Atalhos com nome de domínio, para o call site ficar legível.

    def deteccao(self, **kw: Any) -> dict[str, Any]:
        return self.evento("deteccao", **kw)

    def transicao(self, de: str, para: str, **kw: Any) -> dict[str, Any]:
        return self.evento("transicao", de=de, para=para, **kw)

    def operador(self, acao: str, **kw: Any) -> dict[str, Any]:
        return self.evento("operador", acao=acao, **kw)

    def diagnostico(self, causa: str, **kw: Any) -> dict[str, Any]:
        return self.evento("diagnostico", causa=causa, **kw)

    def resultado(self, sucesso: bool, **kw: Any) -> dict[str, Any]:
        return self.evento("resultado", sucesso=sucesso, **kw)
