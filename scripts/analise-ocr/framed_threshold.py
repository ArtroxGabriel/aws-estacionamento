import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, "worker")
sys.path.insert(0, "scripts/analise-ocr")
from ocr.clean import normalize
from policy_sim import rek, tess

COUNTRIES = ("ARGENTINA", "PARAGUAY", "URUGUAY")


def rek_conf(lines):
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
            return p.plate, d["Confidence"]
    return None, 0


sets = sys.argv[1:]
print(
    f"{'limite moldura':>14} | "
    + " | ".join(f"{Path(s).name[:18]:>24}" for s in sets)
    + " | total"
)
for t in (None, 0, 70, 80, 85, 90, 95):
    cells, to, te = [], 0, 0
    for s in sets:
        R = json.loads(Path(s, "rekognition.json").read_text())
        F = json.loads(Path(s, "rekognition_framed.json").read_text())
        T = json.loads(Path(s, "tesseract.json").read_text())
        ans = {
            r["arquivo"]: r["placa_esperada"]
            for r in csv.DictReader(Path(s, "gabarito.csv").read_text().splitlines())
        }
        ok = err = 0
        for f, exp in ans.items():
            got = rek(R[f])
            if got is None and t is not None and f in F:
                plate, conf = rek_conf(F[f])
                got = plate if conf >= t else None
            if got is None:
                got = tess(T[f], 1, True)
            if got == (exp or None):
                ok += 1
            elif got is not None:
                err += 1
        cells.append(f"ok {ok:4d} err {err:3d}/{len(ans)}")
        to += ok
        te += err
    print(
        f"{'sem moldura' if t is None else t:>14} | "
        + " | ".join(f"{c:>24}" for c in cells)
        + f" | {to}/{te}"
    )
