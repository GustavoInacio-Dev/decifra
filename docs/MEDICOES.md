# Medições

Todo número citado no projeto vem daqui, e todo número aqui tem procedência.
Onde a amostra é pequena ou o teste não é uma prova, está dito.

Uma ressalva vale para a página inteira: **as taxas foram medidas num tipo
específico de captcha** — 4 caracteres, fonte serifada, sem distorção geométrica,
com um canal de áudio que narra caractere por caractere em português. Em outro
captcha os números mudam. Não trate nada aqui como garantia para o seu caso; use
como ponto de partida e **meça o seu**.

---

## Leitura de imagem (OCR)

Banco de 150 imagens rotuladas à mão. Modelo `anuashok/ocr-captcha-v3`, em CPU.

| Configuração | Acerto | n |
|--------------|:------:|:-:|
| Modelo cru, saída sem tratar | 16% | 100 |
| **+ descartar não-alfanuméricos, pegar últimos N** | **64%** | 100 |
| Variante: pegar os *primeiros* N | 30% | 100 |
| Em 2 tentativas | ~85% | — |
| Em 3 tentativas | ~94% | — |
| Em 4 tentativas | ~98% | — |

As taxas por tentativa acumulada assumem que cada nova tentativa vê um captcha
novo e independente (é o que acontece quando errar recarrega a página).

Ampliar a imagem antes de ler **não** ajudou: 64% em 1x, 63% em 2x, 63% em 3x —
mesmo custo de tempo, nenhum ganho. Fica em 1x.

Tempo por leitura: 1,3–1,9 s em CPU isolada; 3,4–4,4 s com outro processo
disputando a máquina. Modelo em disco: 1,34 GB, baixado uma vez.

---

## Leitura de áudio

50 amostras com gabarito. "Wit" = Wit.ai; "Google SR" = `speech_recognition`.

### O intervalo entre os caracteres (variando só isto, Wit, n=20)

| intervalo | acerto |
|----------:|:------:|
| 0,05s | 30% |
| 0,15s | 35% |
| **0,30s** | **50%** |
| 0,40s | 25% |
| 0,55s | 10% |
| 0,70s | 0% |

Com o áudio original, sem reespaçar: 4%. O detalhe de por que a curva tem esse
formato está em [COMO_FUNCIONA.md, seção 2](COMO_FUNCIONA.md).

### Os dois motores

| Motor | Pré-processamento | Acerto |
|-------|-------------------|:------:|
| Wit | blocos reespaçados a 0,30s | 50% |
| Google SR | silêncio de 0,6s nas pontas | 42% |

Cada motor tem o **seu** pré-processamento: o reespaçamento que ajuda o Wit
*piora* o Google SR (cai para 20%). Por isso a cascata não manda o mesmo áudio
para os dois.

### Por que os dois juntos valem

Em 50 amostras: Wit acerta 25, Google acerta 21, mas a **união** é 33 (interseção
de 13). Eles erram em amostras diferentes, então somar cobre mais do que o melhor
sozinho.

---

## Imagem + áudio combinados

| Via | Cobertura em 50 amostras |
|-----|:------:|
| Só imagem | 68% (34/50) |
| Só áudio (cascata) | 50% |
| **Imagem, e áudio nos casos que a imagem erra** | **86% (43/50)** |

Quando imagem e áudio, lidos de forma independente, dão **a mesma** resposta, o
acerto foi 100% em 26 casos de 50. Isso é usado como sinal de confiança "alta" —
mas **26 casos é pouco para virar promessa**, e o código trata como sinal, não
como garantia.

---

## O que NÃO foi medido, e não se finge que foi

- Desempenho em captchas de outro tipo (mais caracteres, distorção geométrica,
  fundo colorido). Esperado: pior. Não medido.
- Vazão da API sob carga real (muitas requisições simultâneas). O desenho
  serializa o OCR por um lock; o teto real não foi levantado.
- Estabilidade da taxa em volume grande. As amostras são de dezenas a ~150
  itens; nenhuma medição de milhares.

Um número que não está aqui é um número que eu não tenho.
