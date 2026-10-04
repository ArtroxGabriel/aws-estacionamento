# Análise do OCR

Scripts que embasaram as decisões de leitura de placa ([docs/DECISOES.md](../../docs/DECISOES.md), D1 e D10). Rode todos a partir da raiz do repositório.

## Respostas guardadas (custam chamadas à AWS)

Cada pasta de `examples/dataset/` com gabarito guarda as respostas brutas dos motores. Assim, as simulações rodam **offline e de graça**. Os datasets `roboflow-cafuringa` e `sintetico` já vêm com elas.

| Script | Gera | Custo |
|---|---|---|
| `rek_dump.py <pastas>` | `rekognition.json`: Rekognition em cada foto | US$ 0,001/foto (5 mil/mês grátis no 1º ano) |
| `rek_dump_framed.py <pastas>` | `rekognition_framed.json`: Rekognition com a foto numa moldura, só onde a 1ª chamada não achou placa | idem |
| `tess_dump.py <pastas>` | `tesseract.json`: pipeline Tesseract do worker. Roda no container do worker, com `OMP_THREAD_LIMIT=1` | grátis |

Os scripts do Rekognition precisam de credenciais AWS no ambiente:

```bash
eval "$(aws configure export-credentials --format env)"
uv run --project worker python scripts/analise-ocr/rek_dump.py examples/dataset/sintetico
```

O `tess_dump.py` roda dentro da imagem do worker:

```bash
docker run --rm --entrypoint python -e OMP_THREAD_LIMIT=1 -v "$PWD/examples:/app/examples" \
  -v "$PWD/scripts/analise-ocr:/app/analise" estacionamento-worker:local analise/tess_dump.py examples/dataset/sintetico
```

## Simulações (offline)

| Script | Pergunta que responde | Resultado |
|---|---|---|
| `policy_sim.py <pastas>` | Como usar o Tesseract de reserva: com correções, só exata, até 1 correção? | Até 1 correção com faixa Mercosul: mesmos acertos, menos placas falsas |
| `rek_threshold.py <pastas>` | Um limite de confiança do Rekognition ajuda? | Não: perde muitos acertos e quase não reduz erros |
| `framed_threshold.py <pastas>` | A 2ª chamada com moldura compensa? E com limite de confiança? | Compensa (+340 acertos em 1.520); o limite não separa acerto de erro |
| `header_sim.py <pastas>` | Usar o `BRASIL` da mesma placa como evidência ajuda? | Não (igual ou pior) |
| `aspect_test.py` | Por que o Rekognition erra recortes da placa? (100 fotos, chama a AWS) | Falta margem: 13/100 sem moldura, 83/100 com moldura |

```bash
uv run --project worker python scripts/analise-ocr/policy_sim.py \
  examples/dataset/roboflow-cafuringa examples/dataset/sintetico
```
