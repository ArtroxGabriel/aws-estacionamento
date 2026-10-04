# Origem: recortes de placas do Roboflow Universe

- **Dataset:** "Placas" (versão 1), workspace `cafuringa`, Roboflow Universe — https://universe.roboflow.com/cafuringa/placas-whmhj
- **Licença:** [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/), que permite redistribuir com crédito.
- **Alterações feitas por nós:**
  - sorteio (semente 42) de 1.000 entre as 3.241 placas únicas em formato válido;
  - arquivos renomeados para `<n>-<PLACA>.jpg`;
  - `gabarito.csv` montado a partir das caixas de cada caractere (`scripts/baixar_roboflow.py`).
- **Respostas guardadas** (para reproduzir as simulações de `scripts/analise-ocr` sem chamar a AWS):
  - `rekognition.json`: Amazon Rekognition em cada foto;
  - `rekognition_framed.json`: Rekognition com a foto numa moldura, só onde a primeira chamada não achou placa;
  - `tesseract.json`: pipeline Tesseract do worker.
- **Para baixar de novo:** `ROBOFLOW_API_KEY=... task dataset:roboflow N=1000`.
