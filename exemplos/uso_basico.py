"""
Uso mínimo, sem browser: ler uma imagem e um áudio de um arquivo.

    python exemplos/uso_basico.py caminho/para/captcha.png
    python exemplos/uso_basico.py caminho/para/audio.wav --audio --tamanho 6

Não precisa de servidor: chama o leitor direto. A primeira leitura de imagem
paga a carga do modelo (~20 s); as seguintes são rápidas.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> int:
    ap = argparse.ArgumentParser(description="Lê um captcha de um arquivo.")
    ap.add_argument("arquivo", help="imagem (PNG/JPEG) ou áudio (WAV)")
    ap.add_argument("--audio", action="store_true", help="tratar como áudio")
    ap.add_argument("--tamanho", type=int, default=4,
                    help="quantos caracteres a resposta tem (default 4)")
    args = ap.parse_args()

    dados = Path(args.arquivo).read_bytes()

    if args.audio:
        from leitor import _cascata
        ok, motivo = _cascata.disponivel()
        if not ok:
            print(f"áudio indisponível: {motivo}")
            return 1
        texto = _cascata.ler_bytes(dados, args.tamanho)
    else:
        from leitor import imagem
        texto = imagem.ler(dados, args.tamanho)

    if texto:
        print(f"lido: {texto}")
        return 0
    print("não foi possível ler (nada que feche o número de caracteres pedido)")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
