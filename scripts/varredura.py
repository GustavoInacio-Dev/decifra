"""
Trava de publicação: falha se algo que não deve ser público estiver no repo.

    python scripts/varredura.py

Roda no CI e antes de cada commit. Existe porque sanitizar "no olho" não
escala: são milhares de linhas, e basta UMA menção sobreviver para o repo
deixar de ser o que promete ser.

O que ele procura:

  1. nome de alvo real — o projeto nasceu medindo portais específicos, e
     nenhum deles deve aparecer aqui;
  2. credencial — token de serviço, chave de site, cookie de sessão;
  3. domínio de produção;
  4. vocabulário de bypass — este repo LÊ captcha, não passa por desafio de
     fornecedor, e o texto tem de refletir isso.

Cada achado vem com arquivo, linha e o trecho, para conserto direto.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

#: Extensões varridas. Binário não entra: não há como revisar, e o repo não
#: deve ter nenhum.
EXTENSOES = {".py", ".md", ".txt", ".toml", ".cfg", ".yml", ".yaml", ".json",
             ".html", ".svg", ".sh", ".bat", ".ps1", ""}

IGNORAR = {".git", "__pycache__", ".venv", "venv", "node_modules", ".pytest_cache"}

#: (rótulo, regex). Case-insensitive.
REGRAS: list[tuple[str, str]] = [
    # 1. alvos reais
    ("nome de alvo",
     r"\b(tjrs|tjpb|tjsc|tjba|tjal|tjsp|tjmg|tjms|jfrj|trf4|trf6|cnj|pdpj)\b"),
    ("sistema de alvo",
     r"\b(eproc|e-?saj|pje|infracaptcha|datajud)\b"),

    # 2. credenciais
    ("token de serviço (32 maiúsculas)", r"\b[A-Z0-9]{32}\b"),
    ("sitekey de fornecedor", r"0x[0-9A-Za-z]{20,}"),
    ("chave reCAPTCHA", r"\b6L[0-9A-Za-z_-]{30,}\b"),
    ("cookie de sessão", r"cf_clearance|_GRECAPTCHA|AUTH_SESSION_ID"),

    # 3. domínios
    ("domínio de produção", r"[a-z0-9.-]+\.(jus\.br|gov\.br)"),

    # 4. vocabulário que descreve outro projeto
    ("vocabulário de bypass",
     r"\b(bypass|burlar|contornar o desafio|passar_cloudflare|cf_pass|"
     r"turnstile|interstitial|challenge-platform)\b"),
]

#: Arquivos que podem conter um termo da lista sem serem violação: são as regras
#: em si (este script) e nada mais.
ISENCOES = (
    "scripts/varredura.py",
)


def arquivos() -> list[Path]:
    achados = []
    for p in RAIZ.rglob("*"):
        if not p.is_file():
            continue
        if any(parte in IGNORAR for parte in p.parts):
            continue
        if p.suffix.lower() not in EXTENSOES:
            continue
        achados.append(p)
    return sorted(achados)


def varrer() -> list[tuple[str, int, str, str]]:
    problemas = []
    compiladas = [(rot, re.compile(rx, re.IGNORECASE)) for rot, rx in REGRAS]

    for caminho in arquivos():
        rel = caminho.relative_to(RAIZ).as_posix()
        if rel in ISENCOES:
            continue
        try:
            texto = caminho.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            problemas.append((rel, 0, "arquivo binário ou não-UTF-8", ""))
            continue

        for n, linha in enumerate(texto.splitlines(), 1):
            for rotulo, rx in compiladas:
                m = rx.search(linha)
                if m:
                    problemas.append((rel, n, rotulo, linha.strip()[:100]))
    return problemas


def main() -> int:
    problemas = varrer()
    if not problemas:
        print(f"varredura ok - {len(arquivos())} arquivos, nada a esconder.")
        return 0

    print(f"VARREDURA FALHOU: {len(problemas)} achado(s)\n")
    for rel, n, rotulo, trecho in problemas:
        print(f"  {rel}:{n}")
        print(f"    {rotulo}: {trecho}")
    print("\nConserte antes de publicar. Se algum achado for falso positivo,")
    print("ajuste a regra em scripts/varredura.py - nao adicione isencao larga.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
