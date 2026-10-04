"""Baixa o dataset de placas brasileiras reais do OpenALPR benchmarks.

115 fotos de carros com placa no formato antigo (cinza), cada uma com a placa
anotada. O repositório de origem é AGPL-3.0, por isso as fotos NÃO são
versionadas aqui: ficam em examples/dataset/ (ignorado pelo git), com um
gabarito.csv gerado a partir das anotações.

Uso: python3 scripts/baixar_dataset.py [pasta_destino]
"""

from __future__ import annotations

import csv
import json
import sys
import urllib.request
from pathlib import Path

LISTING = "https://api.github.com/repos/openalpr/benchmarks/contents/endtoend/br"
SOURCE = "https://github.com/openalpr/benchmarks/tree/master/endtoend/br"
USER_AGENT = "aws-estacionamento-dataset/1.0"


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def main() -> int:
    dest = Path(sys.argv[1] if len(sys.argv) > 1 else "examples/dataset/openalpr-br")
    dest.mkdir(parents=True, exist_ok=True)

    entries = json.loads(fetch(LISTING))
    urls = {e["name"]: e["download_url"] for e in entries if e["type"] == "file"}
    annotations = sorted(name for name in urls if name.endswith(".txt"))

    rows: list[tuple[str, str]] = []
    for i, name in enumerate(annotations, 1):
        # Formato: "<arquivo>\t<x> <y> <largura> <altura>\t<PLACA>"
        fields = fetch(urls[name]).decode().split()
        image, plate = fields[0], fields[-1].upper()
        target = dest / image
        if not target.exists():
            target.write_bytes(fetch(urls[image]))
        rows.append((image, plate))
        print(f"\r{i}/{len(annotations)} {image}", end="", flush=True)
    print()

    with (dest / "gabarito.csv").open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["arquivo", "placa_esperada"])
        writer.writerows(rows)

    (dest / "ORIGEM.md").write_text(
        f"Fotos e anotações de {SOURCE} (OpenALPR benchmarks, licença AGPL-3.0).\n"
        "Não versionar: baixe com `task dataset:baixar`.\n"
    )
    print(f"{len(rows)} fotos em {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
