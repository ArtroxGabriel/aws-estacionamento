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

| Conjunto | Fotos | Tesseract (só local) | **Rekognition + Tesseract (AWS)** |
|---|---|---|---|
| `fotos/` (repo) | 5 | 5/5 | **5/5** |
| `dataset/openalpr-br` (reais, estacionamento em Salvador-BA) | 114 | 48 (42%) · 11 erradas | **110 (96%)** · 2 erradas · 2 não lidas |
| `dataset/sintetico` (Mercosul/antigas com ruído, desfoque, inclinação) | 300 | 263 (88%) · 26 erradas | **283 (94%)** · 16 erradas |

- Na AWS o worker usa o Amazon Rekognition (`OCR_ENGINE=rekognition`): entre os textos detectados, vence a **placa mais alta na foto** (o carro em primeiro plano, não os de fundo). Se ele falhar ou não achar placa, cai no Tesseract.
- O resultado nas 114 reais foi conferido também pelo sistema no ar (`task eval:aws DIR=examples/dataset/openalpr-br`): 110/114.
- Para não "decorar" o conjunto, os ajustes foram feitos olhando só metade das fotos reais. A outra metade (nunca usada nos ajustes) deu 96%.

## Dataset grande (`dataset/`, não versionado)

- `openalpr-br/`: 114 fotos de carros em estacionamentos de Salvador e região, de frente e de traseira, com placa cinza (antes de 2018). É de [OpenALPR benchmarks](https://github.com/openalpr/benchmarks/tree/master/endtoend/br) (AGPL-3.0), por isso as fotos são baixadas por script e não ficam no repositório.
- `sintetico/`: placas geradas com semente fixa (o mesmo comando gera o mesmo conjunto), 3/4 Mercosul e 1/4 antigas.
- Para incluir fotos suas: crie uma pasta com as fotos e um `gabarito.csv` (`arquivo,placa_esperada`) e rode `task ocr:dataset DIRS=sua/pasta` ou `task eval:aws DIR=sua/pasta`.

## Fotos (`fotos/`, gabarito em `fotos/gabarito.csv`)

| Arquivo | Placa esperada | Por que está aqui | Origem / licença |
|---|---|---|---|
| `sintetica-bra2e19.jpg` | BRA2E19 | Placa Mercosul gerada por código: caso ideal | Projeto |
| `placa-real-mercosul-lsn4i49.jpg` | LSN4I49 | Placa Mercosul real com holografia; o `I` costuma ser lido como `1` | [Navegador1133](https://commons.wikimedia.org/wiki/File:Brazilian_vehicle_license_plate_(2018-).jpg), CC BY 4.0 |
| `placa-real-antiga-klv8465.jpg` | KLV8465 | Placa cinza (formato antigo) real, com desgaste | [Ralf1963](https://commons.wikimedia.org/wiki/File:Placa_de_ve%C3%ADculo_Brasil_Pernambuco-Jaboat%C3%A3o_dos_Guararapes_KLV-8465_atr%C3%A1s.jpg), domínio público |
| `carro-argentino-estacionamento.jpg` | (nenhuma) | Carro real num estacionamento, com placa argentina: o sistema não deve inventar uma placa brasileira | [Mister Trapo](https://commons.wikimedia.org/wiki/File:Chevrolet_celta_2016.jpg), CC BY-SA 4.0 |
| `carro-argentino-rua.jpg` | (nenhuma) | Mesmo teste, placa argentina antiga | [Viva Chile!](https://commons.wikimedia.org/wiki/File:Chevrolet_Prisma_in_Santiago,_Chile.JPG), CC BY-SA 3.0 |

Resultado em 2026-10-03: **5/5**. Antes das correções no worker, era 1/5.

## Vídeos (`videos/`)

| Arquivo | O que mostra | Origem / licença |
|---|---|---|
| `porto-alegre-transito-640x480.webm` | 66 s de trânsito em Porto Alegre, filmado com câmera de mão (trecho final do vídeo original) | [Helton Moraes](https://commons.wikimedia.org/wiki/File:Carros_guinchados_ao_estacionar_no_Parque_Farroupilha.webm), CC BY 3.0 |

Resultado: **nenhuma placa confirmada**. Em 640×480, com os carros a vários metros, cada placa tem ~50×14 pixels e não dá para ler nem a olho. Houve uma leitura errada isolada em 1 de 66 quadros, descartada pela regra de exigir pelo menos 2 quadros concordando.

Câmeras de estacionamento de verdade ficam a 2–4 m da cancela, em HD, e a placa ocupa uma boa parte da imagem. É esse o cenário que o sistema atende.

As fotos e vídeos de terceiros mantêm as licenças originais (links acima).
