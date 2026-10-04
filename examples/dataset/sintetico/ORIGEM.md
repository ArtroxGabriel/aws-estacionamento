# Origem: placas sintéticas

- **Geradas por nós** com `task dataset:sintetico N=400` (`scripts/gerar_sinteticas.py`, semente 42: o mesmo comando gera exatamente estas fotos).
- **Conteúdo:** placas dos 4 países do Mercosul, com o nome do país na faixa:
  - Brasil Mercosul ~48%, Brasil antiga ~16%;
  - Argentina Mercosul ~15%, Argentina antiga ~3%;
  - Paraguai ~9%, Uruguai ~9%.
- **Variações aleatórias:** cor do carro, tamanho, inclinação, desfoque, ruído e compressão JPEG.
- **Respostas guardadas:** `rekognition.json`, `rekognition_framed.json` e `tesseract.json`, como em `../roboflow-cafuringa/ORIGEM.md`.
