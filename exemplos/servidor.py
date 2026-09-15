"""
Sobe a API decifra localmente.

    python exemplos/servidor.py

Com token (recomendado se for expor na rede):

    # PowerShell
    $env:DECIFRA_TOKEN = "algo-longo-e-aleatorio"
    python exemplos/servidor.py

    # bash
    DECIFRA_TOKEN="algo-longo-e-aleatorio" python exemplos/servidor.py
"""
import sys
from pathlib import Path

# deixa `import api` e `import leitor` funcionarem rodando de qualquer lugar
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api.app:app", host="0.0.0.0", port=8010, reload=False)
