# API decifra

Uma API HTTP fina em volta do leitor. Existe por um motivo prático: o modelo de
OCR ocupa 1,34 GB em memória. Em vez de cada programa carregar o seu, você sobe
**uma** instância e todos mandam os bytes e recebem o texto.

Bytes entram, texto sai. Sem estado, sem sessão, sem banco.

## Subir

```bash
pip install -r requirements.txt
python -m uvicorn api.app:app --host 0.0.0.0 --port 8010
```

## Autenticação

Se `DECIFRA_TOKEN` estiver no ambiente, todo endpoint exige o header
`X-Decifra-Token` com esse valor. Sem a variável, a API fica aberta — bom para
rodar local, perigoso exposto na rede.

```bash
# PowerShell
$env:DECIFRA_TOKEN = "algo-longo-e-aleatorio"
python -m uvicorn api.app:app --host 0.0.0.0 --port 8010

# bash
DECIFRA_TOKEN="algo-longo-e-aleatorio" python -m uvicorn api.app:app --port 8010
```

## Endpoints

### `GET /saude` (ou `/health`)

O que a instância consegue fazer agora, sem carregar o modelo para responder.

### `POST /ler/imagem`

```json
{"imagem_b64": "iVBORw0KGgo...", "tamanho": 4}
```

Aceita **data URI inteiro** (`data:image/png;base64,...`), então não precisa
fatiar a string. Resposta:

```json
{"resolvido": true, "texto": "9JBA", "via": "ocr_imagem", "bruto": "9JBA", "ms": 1282}
```

`bruto` é a saída do modelo **antes** do pós-processamento — guarde no log, é ali
que se vê se o modelo leu errado ou se o corte pegou no lugar errado.

Há também `POST /ler/imagem/arquivo` (multipart, campos `arquivo` e `tamanho`)
para quem prefere subir o arquivo. Resposta idêntica.

### `POST /ler/audio`

```json
{"audio_b64": "UklGR...", "tamanho": 4, "idioma": "pt-BR"}
```

Para `pt-BR` usa a cascata medida (Wit reespaçado → Google SR). `texto` vem
`null` quando a transcrição não fecha `tamanho` caracteres — de propósito:
palpite parcial só gasta uma tentativa do cliente.

### `POST /ler` — as duas vias numa chamada

```json
{"imagem_b64": "...", "audio_b64": "...", "tamanho": 4, "idioma": "pt-BR"}
```

Tenta a imagem primeiro (mais barata), o áudio se a imagem não fechar. Quando as
duas dão a mesma resposta, `confianca` vem `"alta"`. Uma via ilegível não derruba
a outra.

## Entrada malformada é 422, nunca 500

Bytes que não decodificam como imagem ou como WAV devolvem `422` com o motivo —
não `500`. Limites: imagem 2 MB, áudio 8 MB, `tamanho` de 1 a 16; acima disso,
`413`/`422` com motivo. Falha de **rede** (transcritor fora do ar) continua sendo
erro da API, não 422 — para o cliente saber que deve repetir, não desistir.

## Cliente pronto

[`cliente.py`](cliente.py) é um cliente de um arquivo (só precisa de `requests`)
que traduz a resposta em decisão:

```python
from cliente import Decifra

api = Decifra("http://192.168.0.10:8010", token="...")
r = api.ler(imagem=src_do_img, audio=bytes_do_wav, tamanho=4)

if r.resolvido:
    campo.send_keys(r.texto)
elif r.reenfileirar:      # API fora, token errado, bytes inválidos: não insista
    ...
else:                     # não leu: recarregue o captcha e tente de novo
    ...
```

A diferença entre `reenfileirar` (não adianta insistir) e o simples "não leu"
(tente de novo) está no tipo de retorno de propósito: tratá-las igual faz o
cliente queimar tentativas contra um problema que não é o captcha.

## Testes

```bash
python -m unittest api.test_api -v
```

Cobrem o conjunto fechado de rotas, uma guarda de fonte contra a API passar a
dirigir browser, autenticação em todas as rotas, limites e validação, e entrada
malformada (422, sem uma via derrubar a outra).
