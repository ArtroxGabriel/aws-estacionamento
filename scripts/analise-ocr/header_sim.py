import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, "worker")
sys.path.insert(0, "scripts/analise-ocr")
from ocr.clean import normalize
from policy_sim import tess

COUNTRIES = ("ARGENTINA", "PARAGUAY", "URUGUAY")


def same_plate_header(plate, lines):
    """Linha BRASIL/MERCOSUL logo acima da placa e alinhada com ela."""
    pb = plate["Geometry"]["BoundingBox"]
    for d in lines:
        t = d["DetectedText"].upper().replace(" ", "")
        if "BRASIL" not in t and "MERCOSUL" not in t:
            continue
        b = d["Geometry"]["BoundingBox"]
        overlap = min(b["Left"] + b["Width"], pb["Left"] + pb["Width"]) - max(
            b["Left"], pb["Left"]
        )
        gap = pb["Top"] - (b["Top"] + b["Height"])
        if (
            overlap > 0.3 * pb["Width"]
            and -0.2 * pb["Height"] <= gap <= 1.5 * pb["Height"]
        ):
            return True
    return False


def rek(lines, header):
    lines = sorted(
        lines, key=lambda d: d["Geometry"]["BoundingBox"]["Height"], reverse=True
    )
    countries = [
        d["DetectedText"]
        for d in lines
        if any(c in d["DetectedText"].upper() for c in COUNTRIES)
    ]
    for d in lines:
        extra = ["BRASIL"] if header and same_plate_header(d, lines) else []
        p = normalize("\n".join([d["DetectedText"], *countries, *extra]))
        if p.ok:
            return p.plate
    return None


sets = sys.argv[1:]
for header in (False, True):
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
            got = (
                rek(R[f], header)
                or (rek(F[f], header) if f in F else None)
                or tess(T[f], 1, True)
            )
            if got == (exp or None):
                ok += 1
            elif got is not None:
                err += 1
        cells.append(f"ok {ok:4d} err {err:3d}/{len(ans)}")
        to += ok
        te += err
    print(
        f"{'BRASIL da mesma placa' if header else 'sem (atual)':22} | "
        + " | ".join(cells)
        + f" | total {to}/{te}"
    )
