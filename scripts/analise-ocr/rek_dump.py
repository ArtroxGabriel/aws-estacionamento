"""Guarda a resposta bruta do Rekognition de cada foto (para ajustar sem pagar de novo)."""

import csv
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import boto3

sys.path.insert(0, "worker")
from ocr.rekognition import prepare

client = boto3.client("rekognition", region_name="us-east-1")


def dump(path):
    resp = client.detect_text(Image={"Bytes": prepare(path.read_bytes())})
    return path.name, [d for d in resp["TextDetections"] if d["Type"] == "LINE"]


for folder in sys.argv[1:]:
    folder = Path(folder)
    names = [
        r["arquivo"]
        for r in csv.DictReader(Path(folder / "gabarito.csv").read_text().splitlines())
    ]
    with ThreadPoolExecutor(4) as pool:
        out = dict(pool.map(dump, [folder / n for n in names]))
    (folder / "rekognition.json").write_text(json.dumps(out))
    print(folder, len(out))
