"""Guarda a saída bruta do Tesseract (pipeline do worker) de cada foto."""

import csv
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, "/app")
from ocr.processor import extract_text


def dump(path):
    r = extract_text(Path(path).read_bytes())
    return Path(path).name, {
        "ok": r.ok,
        "raw": r.raw_text,
        "mercosul": r.mercosul,
        "error": r.error,
    }


if __name__ == "__main__":
    for folder in sys.argv[1:]:
        names = [
            r["arquivo"]
            for r in csv.DictReader(
                Path(Path(folder) / "gabarito.csv").read_text().splitlines()
            )
        ]
        with ProcessPoolExecutor(4) as pool:
            out = dict(
                pool.map(dump, [str(Path(folder) / n) for n in names], chunksize=4)
            )
        (Path(folder) / "tesseract.json").write_text(json.dumps(out))
        print(folder, len(out))
