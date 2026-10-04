import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, "worker")
from ocr.clean import _clean, normalize

COUNTRIES = ("ARGENTINA", "PARAGUAY", "URUGUAY")


def rek(lines):
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
            return p.plate
    return None


def dist(plate, raw):
    best = 99
    for line in (raw or "").splitlines():
        c = _clean(line)
        for i in range(len(c) - len(plate) + 1):
            best = min(best, sum(a != b for a, b in zip(plate, c[i : i + len(plate)])))
    return best


def tess(t, max_swaps, need_band=False):
    if not t["ok"]:
        return None
    p = normalize(t["raw"] or "", mercosul=t["mercosul"])
    if not p.ok:
        return None
    d = dist(p.plate, t["raw"])
    if d == 0 or (d <= max_swaps and (t["mercosul"] or not need_band)):
        return p.plate
    return None


policies = {
    "A rekognition sem reserva": lambda r, t: rek(r),
    "B reserva com correções": lambda r, t: rek(r) or tess(t, 9),
    "C reserva só exata": lambda r, t: rek(r) or tess(t, 0),
    "D reserva até 1 correção": lambda r, t: rek(r) or tess(t, 1),
    "E até 1 correção c/ faixa": lambda r, t: rek(r) or tess(t, 1, True),
}
sets = sys.argv[1:]
print(
    f"{'política':28} | "
    + " | ".join(f"{Path(s).name[:18]:>24}" for s in sets)
    + " | total ok/err"
)
for name, fn in policies.items():
    cells, tot_ok, tot_err, tot_n = [], 0, 0, 0
    for s in sets:
        R = json.loads(Path(s, "rekognition.json").read_text())
        T = json.loads(Path(s, "tesseract.json").read_text())
        ans = {
            r["arquivo"]: r["placa_esperada"]
            for r in csv.DictReader(Path(s, "gabarito.csv").read_text().splitlines())
        }
        ok = err = 0
        for f, exp in ans.items():
            got = fn(R[f], T[f])
            if got == (exp or None):
                ok += 1
            elif got is not None:
                err += 1
        cells.append(f"ok {ok:4d} err {err:3d}/{len(ans)}")
        tot_ok += ok
        tot_err += err
        tot_n += len(ans)
    print(
        f"{name:28} | "
        + " | ".join(f"{c:>24}" for c in cells)
        + f" | {tot_ok}/{tot_err} de {tot_n}"
    )
