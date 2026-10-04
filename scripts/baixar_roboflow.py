"""Baixa um dataset de caracteres de placa do Roboflow e monta o gabarito.

Datasets de OCR de placa no Roboflow anotam uma caixa por caractere. O texto
da placa é remontado ordenando as caixas por linha (placas de moto têm duas) e
depois da esquerda para a direita. Só entram no gabarito as placas num formato
dos 4 países do Mercosul (docs/DECISOES.md, D8), o que também confere se o
mapeamento classe → caractere está certo.

As fotos ficam em examples/dataset/ (ignorado pelo git): cada dataset tem a
própria licença, registrada em ORIGEM.md.

Uso (a chave vem do ambiente, nunca do repositório):
  ROBOFLOW_API_KEY=... python3 scripts/baixar_roboflow.py <workspace> <projeto> <versão> <destino> [--amostra N]
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import random
import re
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

PLATE = re.compile(
    r"^([A-Z]{3}[0-9][A-Z0-9][0-9]{2}|[A-Z]{2}[0-9]{3}[A-Z]{2}|[A-Z]{4}[0-9]{3}|[A-Z]{3}[0-9]{3})$"
)

# Datasets cujo export traz os nomes das classes corrompidos (trocados por
# números e por textos de licença). Mapeamento deduzido comparando as caixas
# com as fotos: as letras viraram 1..26, e 0/1 têm duas classes cada (fonte
# antiga e fonte Mercosul).
KNOWN_MAPS = {
    "cafuringa/placas-whmhj": {
        0: "0",
        **{i: chr(ord("A") + i - 1) for i in range(1, 27)},
        27: "1",
        28: "0",
        29: "4",
        30: "5",
        31: "6",
        32: "7",
        33: "8",
        34: "9",
        35: "3",
        36: "2",
        37: "1",
    },
}


def fetch(url: str) -> bytes:
    req = urllib.request.Request(
        url, headers={"User-Agent": "aws-estacionamento-dataset/1.0"}
    )
    with urllib.request.urlopen(req, timeout=600) as resp:
        return resp.read()


def class_map(key: str, names: list[str]) -> dict[int, str]:
    if key in KNOWN_MAPS:
        return KNOWN_MAPS[key]
    if all(len(n) == 1 and n.isalnum() for n in names):
        return {i: n.upper() for i, n in enumerate(names)}
    sys.exit(
        f"Nomes de classes não são caracteres ({names[:6]}...): adicione um mapa em KNOWN_MAPS."
    )


def plate_text(label_file: Path, mapping: dict[int, str]) -> str | None:
    boxes = []
    for line in label_file.read_text().splitlines():
        parts = line.split()
        if len(parts) < 5 or int(parts[0]) not in mapping:
            continue
        c, x, y, _, h = int(parts[0]), *map(float, parts[1:5])
        boxes.append((y, x, h, mapping[c]))
    if not boxes:
        return None
    # Linhas: uma quebra quando o centro vertical salta mais que 60% da altura
    # mediana de um caractere (placa de moto: letras em cima, números embaixo).
    median_h = sorted(b[2] for b in boxes)[len(boxes) // 2]
    boxes.sort()
    rows, current = [], [boxes[0]]
    for box in boxes[1:]:
        if box[0] - current[-1][0] > 0.6 * median_h:
            rows.append(current)
            current = []
        current.append(box)
    rows.append(current)
    return "".join(b[3] for row in rows for b in sorted(row, key=lambda b: b[1]))


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("workspace")
    parser.add_argument("projeto")
    parser.add_argument("versao")
    parser.add_argument("destino", type=Path)
    parser.add_argument(
        "--amostra",
        type=int,
        default=0,
        help="sorteia N fotos (semente fixa); 0 = todas",
    )
    args = parser.parse_args()

    key = os.environ.get("ROBOFLOW_API_KEY")
    if not key:
        sys.exit("Defina ROBOFLOW_API_KEY no ambiente.")
    base = f"https://api.roboflow.com/{args.workspace}/{args.projeto}"
    info = json.loads(fetch(f"{base}?api_key={key}"))["project"]
    export = json.loads(fetch(f"{base}/{args.versao}/yolov8?api_key={key}"))["export"][
        "link"
    ]

    raw = args.destino / "raw"
    if not raw.exists():
        print("baixando export...")
        zipfile.ZipFile(io.BytesIO(fetch(export))).extractall(raw)
    names = (raw / "data.yaml").read_text().split("names:")[1].split("\n")[0]
    mapping = class_map(
        f"{args.workspace}/{args.projeto}", json.loads(names.strip().replace("'", '"'))
    )

    # O Roboflow gera cópias aumentadas da mesma foto ("<id>_jpg.rf.<hash>"):
    # uma por foto original.
    unique: dict[str, tuple[Path, str]] = {}
    invalid = 0
    for label in sorted(raw.glob("*/labels/*.txt")):
        original = label.name.split("_jpg.rf.")[0].split(".rf.")[0]
        if original in unique:
            continue
        text = plate_text(label, mapping)
        if not text or not PLATE.match(text):
            invalid += 1
            continue
        image = next(
            label.parent.parent.joinpath("images").glob(label.stem + ".*"), None
        )
        if image:
            unique[original] = (image, text)

    items = sorted(unique.values())
    if args.amostra and args.amostra < len(items):
        items = random.Random(42).sample(items, args.amostra)

    rows = []
    for i, (image, text) in enumerate(sorted(items, key=lambda t: t[0].name)):
        name = f"{i:05d}-{text}{image.suffix}"
        shutil.copy(image, args.destino / name)
        rows.append((name, text))
    with (args.destino / "gabarito.csv").open("w", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(["arquivo", "placa_esperada"])
        writer.writerows(rows)
    (args.destino / "ORIGEM.md").write_text(
        f"# Origem\n\n- Dataset: {info.get('name', args.projeto)} (versão {args.versao}), "
        f"https://universe.roboflow.com/{args.workspace}/{args.projeto}\n"
        f"- Licença: {info.get('license', '?')}\n"
        f"- Alterações: sorteio de {len(rows)} placas únicas em formato válido (semente 42), "
        "arquivos renomeados e gabarito.csv montado a partir das caixas de cada caractere "
        "(scripts/baixar_roboflow.py).\n"
    )
    print(
        f"{len(unique)} placas únicas em formato válido ({invalid} descartadas: formato inválido ou sem caixas); "
        f"{len(rows)} copiadas para {args.destino}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
