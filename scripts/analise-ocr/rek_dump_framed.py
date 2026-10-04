"""Resposta do Rekognition na foto com moldura, só onde a 1ª chamada não achou placa."""

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import boto3

sys.path.insert(0, "worker")
from ocr.clean import normalize
from ocr.rekognition import framed

client = boto3.client("rekognition", region_name="us-east-1")
COUNTRIES = ("ARGENTINA", "PARAGUAY", "URUGUAY")


def has_plate(lines):
    countries = [
        d["DetectedText"]
        for d in lines
        if any(c in d["DetectedText"].upper() for c in COUNTRIES)
    ]
    return any(normalize("\n".join([d["DetectedText"], *countries])).ok for d in lines)


for folder in map(Path, sys.argv[1:]):
    first = json.loads((folder / "rekognition.json").read_text())
    todo = [n for n, lines in first.items() if not has_plate(lines)]

    def dump(n, folder=folder):
        r = client.detect_text(Image={"Bytes": framed((folder / n).read_bytes())})
        return n, [d for d in r["TextDetections"] if d["Type"] == "LINE"]

    with ThreadPoolExecutor(4) as pool:
        out = dict(pool.map(dump, todo))
    (folder / "rekognition_framed.json").write_text(json.dumps(out))
    print(folder, len(out))
