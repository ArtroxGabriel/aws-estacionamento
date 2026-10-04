import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, "worker")
from ocr.clean import normalize

COUNTRIES = ("ARGENTINA", "PARAGUAY", "URUGUAY")


def pick(lines, threshold):
    lines = sorted(
        lines, key=lambda d: d["Geometry"]["BoundingBox"]["Height"], reverse=True
    )
    countries = [
        d["DetectedText"]
        for d in lines
        if any(c in d["DetectedText"].upper() for c in COUNTRIES)
    ]
    for d in lines:
        p = normalize("\n".join([d["DetectedText"], *countries]))
        if p.ok:
            return p.plate if d["Confidence"] >= threshold else None
    return None


sets = sys.argv[1:]
print(f"{'limite':>6} | " + " | ".join(f"{Path(s).name[:18]:>26}" for s in sets))
for t in (0, 70, 80, 85, 90, 93, 95, 97):
    cells = []
    for s in sets:
        dumps = json.loads(Path(s, "rekognition.json").read_text())
        ans = {
            r["arquivo"]: r["placa_esperada"]
            for r in csv.DictReader(Path(s, "gabarito.csv").read_text().splitlines())
        }
        ok = wrong = 0
        for name, exp in ans.items():
            got = pick(dumps[name], t)
            if got == (exp or None):
                ok += 1
            elif got is not None:
                wrong += 1
        n = len(ans)
        cells.append(f"ok {100 * ok / n:4.1f}% err {100 * wrong / n:4.1f}%")
    print(f"{t:>6} | " + " | ".join(f"{c:>26}" for c in cells))
