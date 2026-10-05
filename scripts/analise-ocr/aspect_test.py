import csv
import io
import random
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import boto3
from PIL import Image

sys.path.insert(0, "worker")
from ocr.clean import normalize

client = boto3.client("rekognition", region_name="us-east-1")
rows = list(
    csv.DictReader(
        Path("examples/dataset/roboflow-cafuringa/gabarito.csv")
        .read_text()
        .splitlines()
    )
)
random.Random(1).shuffle(rows)
rows = rows[:100]


def read(img):
    b = io.BytesIO()
    img.save(b, "JPEG", quality=92)
    lines = [
        d
        for d in client.detect_text(Image={"Bytes": b.getvalue()})["TextDetections"]
        if d["Type"] == "LINE"
    ]
    for d in sorted(lines, key=lambda d: -d["Geometry"]["BoundingBox"]["Height"]):
        p = normalize(d["DetectedText"])
        if p.ok:
            return p.plate


def both(r):
    img = Image.open(f"examples/dataset/roboflow-cafuringa/{r['arquivo']}").convert(
        "RGB"
    )
    car = r["placa_esperada"]
    flat = img.resize((640, 213))  # desfaz o esticamento (placa ~3:1)
    framed = Image.new("RGB", (1280, 853), (90, 90, 90))
    framed.paste(flat, (320, 320))  # placa dentro de uma "foto"
    return [read(x) == car for x in (img, flat, framed)]


with ThreadPoolExecutor(4) as pool:
    res = list(pool.map(both, rows))
for i, name in enumerate(
    ["como veio (640x640)", "proporção 3:1", "3:1 dentro de uma foto"]
):
    print(f"{name:26s} acertos {sum(r[i] for r in res)}/100")
