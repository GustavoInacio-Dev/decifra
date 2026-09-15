# Como funciona — e o que aprendi errando

Este documento é o que eu queria ter lido antes de começar. Não é um tutorial da
biblioteca; é o registro honesto de sete coisas que eu achei que sabia e estava
errado, cada uma descoberta medindo em vez de supondo.

A ordem é mais ou menos cronológica. Cada seção segue o mesmo formato:
**o que eu achava → o que a medição mostrou → o que ficou no código.**

---

## 1. "O áudio tem só 0,1 segundo" — não, o cabeçalho mentia

**O que eu achava.** Baixei o WAV da narração, abri com o módulo `wave` do
Python, e ele me disse: 1112 amostras, 0,10 segundos. Concluí que o download
vinha truncado, ou que o servidor mandava um clique em vez do áudio. Passei um
tempo investigando a rede.

**O que a medição mostrou.** O arquivo tinha 126 KB — não cabem 126 KB em 0,1s
de áudio. O problema não era o download; era o **cabeçalho**. Um WAV começa com
um campo `RIFF size` que declara o tamanho do conteúdo. Neste arquivo, o campo
dizia 2260 bytes quando o arquivo real tinha 126476. O módulo `wave` confia nesse
campo e lê só o que ele manda ler.

**O que ficou no código.** Uma função de três linhas que reescreve só o campo de
tamanho antes de entregar o áudio ao decodificador ([`consertar_riff`](../leitor/_cascata.py)):

```python
def consertar_riff(dados: bytes) -> bytes:
    if len(dados) < 12 or dados[:4] != b"RIFF":
        return dados
    return dados[:4] + struct.pack("<I", len(dados) - 8) + dados[8:]
```

**A lição.** Uma medição de ferramenta (`wave` diz 0,1s) não é uma medição do
mundo (o áudio tem 5,7s). Antes desse conserto eu havia até concluído que o áudio
tinha "ruído de fundo contínuo" — eu estava analisando 0,1 segundo de um clique.
Toda conclusão que tirei enquanto o bug existia estava contaminada.

---

## 2. "O reconhecedor de fala é ruim" — não, ele parava cedo

**O que eu achava.** Com o áudio já consertado, joguei no reconhecedor de fala e
recebi "seis" para um captcha "6455". Um caractere de quatro. Achei que o
reconhecedor simplesmente não dava conta de áudio de captcha.

**O que a medição mostrou.** A narração dita "seis... (silêncio de 1,1s)...
quatro... (silêncio)... cinco...". O reconhecedor de fala tem um comportamento
chamado *endpointing*: quando ouve um silêncio longo, ele assume que a frase
acabou e para. Ele não estava errando — estava **desistindo depois do primeiro
caractere**, porque o silêncio parecia um ponto final.

**O que ficou no código.** Em vez de trocar de reconhecedor, eu reescrevo o
áudio: recorto os pedaços falados e recolo com um intervalo curto e uniforme,
para que nenhum silêncio pareça fim de frase. Variando **só** o tamanho desse
intervalo, o pico é nítido:

| intervalo | acerto |
|----------:|:------:|
| 0,05s | 30% |
| 0,30s | **50%** |
| 0,70s | 0% |

Intervalo curto demais funde "seis" e "quatro" numa coisa só; longo demais traz
de volta o endpointing. 0,30s é o equilíbrio. Levou o áudio de 4% para 50%.

**A lição.** "A ferramenta é ruim" quase nunca é a explicação. O reconhecedor era
ótimo — eu é que estava entregando o áudio de um jeito que acionava um
comportamento dele que eu não conhecia. O ganho não veio de mais potência, veio
de entender o mecanismo.

---

## 3. OCR de 16% para 64%, sem trocar de modelo

**O que eu achava.** O modelo de OCR acertava 16% dos captchas. Achei que
precisaria de um modelo melhor, ou de treinar um específico.

**O que a medição mostrou.** Olhei as respostas cruas em vez de só a taxa:

```
'/Cnyx'   deveria ser  CNYX
'o/EdTc'  deveria ser  EDTC
'29JRQ'   deveria ser  9JRQ
```

O modelo estava **lendo a resposta certa** — e grudando lixo nas pontas, quase
sempre no começo. Ele lê a sujeira do fundo da imagem como se fosse caractere, e
a leitura começa pela esquerda, então o lixo se acumula ali.

**O que ficou no código.** Descartar tudo que não é letra ou número, e pegar os
**últimos N** caracteres:

```python
limpo = re.sub(r"[^0-9A-Za-z]", "", bruto)
return limpo[-tamanho:].upper()
```

16% → 64%. Pegar os *primeiros* N dá 30% — confirma que o lixo mora no início.

**A lição.** A métrica agregada (16%) escondia a informação. A taxa dizia "está
ruim"; as amostras cruas diziam "está quase certo, com lixo previsível". Só a
segunda leva à correção. Olhe os erros um a um antes de concluir que precisa de
mais potência.

---

## 4. Um filtro "inteligente" que piorou tudo

**O que eu achava.** Numa versão de extração por visão computacional (anterior ao
uso do modelo pronto), adicionei um filtro que descartava manchas que tocavam a
borda inferior da imagem — "devem ser ruído de rodapé". Parecia razoável.

**O que a medição mostrou.** A taxa caiu de 70% para 20%. O filtro estava
matando as **descidas** das letras — a perna do `g`, do `p`, do `j`, do `y`. São
letras legítimas cuja parte de baixo, por natureza, encosta embaixo.

**O que ficou no código.** O filtro saiu. A versão que usa modelo pronto nem tem
essa etapa. Mas a cicatriz virou regra: uma heurística "que faz sentido" precisa
ser medida contra o caso real, não aceita porque soa plausível.

**A lição.** "Faz sentido" é uma hipótese, não um resultado. Toda regra que eu
adicionei por parecer razoável, e não medi, foi das que mais me custaram.

---

## 5. 23 testes verdes que não pegaram um erro 500

**O que eu achava.** A API tinha 23 testes passando, cobrindo validação de
entrada, autenticação, limites. Achei que a fronteira de entrada estava coberta.

**O que a medição mostrou.** Mandei bytes que não eram uma imagem de verdade
(um PNG truncado). A API respondeu **HTTP 500** — enquanto a documentação dela
prometia "sempre 422 com o motivo, nunca 500". Os 23 testes não pegaram porque
nenhum deles chegava a rodar o modelo: chegar lá custa carregar 1,34 GB, então
todos paravam antes. A validação toda morava *antes* do ponto caro, e o ponto
caro nunca era exercitado.

**O que ficou no código.** Uma guarda no ponto que as rotas compartilham, e
testes que trocam o modelo por um falso que levanta o mesmo erro — cobrindo a
fronteira real sem carregar 1,34 GB.

**A lição.** "Suíte verde" e "caminho coberto" são coisas diferentes. Um teste
que substitui a parte cara para rodar rápido pode estar, sem querer, nunca
exercitando a parte que quebra. Vale perguntar: algum teste realmente atravessa
o componente caro?

---

## 6. Ausência de erro não é prova de sucesso

**O que eu achava.** Num ponto do sistema, quando uma página não continha
nenhum marcador de captcha, eu concluía "então está tudo certo, siga em frente".

**O que a medição mostrou.** Uma página de bloqueio (403, corpo minúsculo, sem
formulário) também não tem marcador de captcha. Minha lógica dizia "siga" para
uma página onde não havia nada a fazer — e o programa seguiria extraindo vazio,
em silêncio, marcando como concluído o que nunca foi feito.

**O que ficou no código.** A regra virou: só se conclui "liberado" com evidência
**positiva** de que a página certa está na tela (o formulário esperado presente),
nunca pela *ausência* de um marcador de problema.

**A lição.** Um sinal negativo ausente não é um sinal positivo presente. "Não
achei problema" e "está tudo bem" são afirmações diferentes, e confundir as duas
produz o pior tipo de falha: a silenciosa, que se disfarça de sucesso.

---

## 7. Medição vira fato; causa vira hipótese

**O que eu achava.** Um endpoint devolvia 403. Escrevi na documentação: "é uma
regra de bloqueio do servidor". Afirmei a causa com a confiança de quem tinha
medido — mas eu só tinha medido o *sintoma* (o 403), não a *causa*.

**O que a medição mostrou.** Testei variando uma coisa só: o mesmo cliente, o
mesmo endereço, URLs diferentes do mesmo host. Todas abriam, menos uma. Isso
mostrou que o bloqueio era daquela URL específica — não do cliente, que era a
explicação alternativa que eu nunca tinha descartado. A conclusão original até
acertou por acaso, mas o caminho até ela não valia nada: eu não tinha dado
diferenciado as duas hipóteses.

**A lição, e talvez a mais importante deste documento.** Um cabeçalho e um código
de status dizem *o que* voltou, não *por que*. "O que voltou" é fato; "por que"
é hipótese — e hipótese precisa do teste que a separa da alternativa, escrito ao
lado. A regra que adotei: **para saber se o bloqueio é seu ou do alvo, varie uma
coisa só.** Custa 30 segundos e vale mais que qualquer leitura de cabeçalho.

---

## O fio que liga todas

Reparando, as sete têm a mesma forma. Em cada uma, uma explicação plausível —
"ferramenta ruim", "download truncado", "regra do servidor", "filtro razoável",
"suíte verde" — estava entre mim e o mecanismo real. A medição não serviu para
provar que eu estava certo; serviu para descobrir que eu estava errado, mais
barato do que descobrir em produção.

É por isso que este repositório existe. O código lê captcha; o método é o que
vale levar para o próximo problema.
