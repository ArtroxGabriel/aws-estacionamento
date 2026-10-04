# Exemplos

Fotos e vídeos para testar e demonstrar a leitura de placas.

| Comando | O que faz |
|---|---|
| `task ocr:local` | Roda o OCR do worker (mesmo código da AWS) localmente em `fotos/` e `videos/` |
| `task dataset:baixar` | Baixa 114 fotos reais de carros brasileiros com gabarito para `dataset/openalpr-br/` |
| `task dataset:sintetico N=300` | Gera placas sintéticas (Mercosul e antigas) com variações em `dataset/sintetico/` |
| `task ocr:dataset` | Mede o OCR em todos os conjuntos (OK / leu errado / não leu); `ENGINE=rekognition` usa o mesmo motor da AWS |
| `task eval:aws DIR=...` | Envia cada foto de uma pasta ao sistema no ar como uma entrada real e compara com o gabarito |
| `task smoke:aws` | Fluxo completo (entrada → OCR → pagamento → placa digitada → exclusão) |

## Resultados (2026-10-03)

| Conjunto | Fotos | Tesseract (só local / reserva) | **Rekognition + Tesseract (AWS)** |
|---|---|---|---|
| `fotos/` (repo) | 6 | 4/6 | **5/6**: a `AA 562 AN` tem as letras `AA` gastas; o Rekognition lê só `562 AN` e ela vira "falha no OCR" |
| `dataset/openalpr-br` (reais, estacionamentos em Salvador-BA) | 114 | 59 (52%) · 18 erradas | **110 (96%)** · 2 erradas · 2 não lidas |
| `dataset/sintetico` (4 países do Mercosul, com ruído, desfoque e inclinação) | 400 | 292 (73%) · 65 erradas | **391 (98%)** · 9 erradas |

- Na AWS o worker usa o Amazon Rekognition (`OCR_ENGINE=rekognition`): entre os textos detectados, vence a **placa mais alta na foto** (o carro em primeiro plano, não os de fundo). Se ele falhar ou não achar placa, cai no Tesseract.
- O resultado nas 114 reais foi conferido também pelo sistema no ar (`task eval:aws DIR=examples/dataset/openalpr-br`): 110/114.
- Para não "decorar" o conjunto, os ajustes foram feitos olhando só metade das fotos reais. A outra metade (nunca usada nos ajustes) deu 96%.
- As medições locais rodam com `OMP_THREAD_LIMIT=1`. Sem isso, o Tesseract abre uma thread por CPU, os processos paralelos disputam a máquina e algumas fotos estouram o limite de 10 s: o Tesseract caía para 32% nas reais só por isso.

## Dataset grande (`dataset/`, não versionado)

- `openalpr-br/`: 114 fotos de carros em estacionamentos de Salvador e região, de frente e de traseira, com placa cinza (antes de 2018). É de [OpenALPR benchmarks](https://github.com/openalpr/benchmarks/tree/master/endtoend/br) (AGPL-3.0), por isso as fotos são baixadas por script e não ficam no repositório.
- `sintetico/`: placas geradas com semente fixa (o mesmo comando gera o mesmo conjunto), dos 4 países do Mercosul, com o nome do país na faixa: Brasil Mercosul ~48%, Brasil antiga ~16%, Argentina Mercosul ~15%, Argentina antiga ~3%, Paraguai ~9% e Uruguai ~9%.
- Para incluir fotos suas: crie uma pasta com as fotos e um `gabarito.csv` (`arquivo,placa_esperada`) e rode `task ocr:dataset DIRS=sua/pasta` ou `task eval:aws DIR=sua/pasta`.

## Fotos (`fotos/`, gabarito em `fotos/gabarito.csv`)

| Arquivo | Placa esperada | Por que está aqui | Origem / licença |
|---|---|---|---|
| `sintetica-bra2e19.jpg` | BRA2E19 | Placa Mercosul gerada por código: caso ideal | Projeto |
| `placa-real-mercosul-lsn4i49.jpg` | LSN4I49 | Placa Mercosul real com holografia; o `I` costuma ser lido como `1` | [Navegador1133](https://commons.wikimedia.org/wiki/File:Brazilian_vehicle_license_plate_(2018-).jpg), CC BY 4.0 |
| `placa-real-antiga-klv8465.jpg` | KLV8465 | Placa cinza (formato antigo) real, com desgaste | [Ralf1963](https://commons.wikimedia.org/wiki/File:Placa_de_ve%C3%ADculo_Brasil_Pernambuco-Jaboat%C3%A3o_dos_Guararapes_KLV-8465_atr%C3%A1s.jpg), domínio público |
| `carro-argentino-estacionamento.jpg` | AA562AN | Carro real num estacionamento, com placa Mercosul argentina | [Mister Trapo](https://commons.wikimedia.org/wiki/File:Chevrolet_celta_2016.jpg), CC BY-SA 4.0 |
| `carro-argentino-rua.jpg` | MWV724 | Placa argentina antiga (preta, 6 caracteres, com "ARGENTINA") | [Viva Chile!](https://commons.wikimedia.org/wiki/File:Chevrolet_Prisma_in_Santiago,_Chile.JPG), CC BY-SA 3.0 |
| `carro-placa-coberta.jpg` | (nenhuma) | A primeira foto com a placa borrada: o sistema deve dar "falha no OCR" sem inventar placa (usada no `smoke:aws`) | Derivada da foto de Mister Trapo, CC BY-SA 4.0 |

Resultado em 2026-10-03: **5/5**. Antes das correções no worker, era 1/5.

## Vídeos (`videos/`)

| Arquivo | O que mostra | Origem / licença |
|---|---|---|
| `porto-alegre-transito-640x480.webm` | 66 s de trânsito em Porto Alegre, filmado com câmera de mão (trecho final do vídeo original) | [Helton Moraes](https://commons.wikimedia.org/wiki/File:Carros_guinchados_ao_estacionar_no_Parque_Farroupilha.webm), CC BY 3.0 |

Resultado: **nenhuma placa confirmada**. Em 640×480, com os carros a vários metros, cada placa tem ~50×14 pixels e não dá para ler nem a olho. Houve uma leitura errada isolada em 1 de 66 quadros, descartada pela regra de exigir pelo menos 2 quadros concordando.

Câmeras de estacionamento de verdade ficam a 2–4 m da cancela, em HD, e a placa ocupa uma boa parte da imagem. É esse o cenário que o sistema atende.

As fotos e vídeos de terceiros mantêm as licenças originais (links acima).
