"""Roda o OCR do worker (mesmo código da AWS) em fotos e vídeos locais.

Executado dentro da imagem do worker (veja `task ocr:local`). Fotos: lê a
placa de cada uma e, se a pasta tiver um gabarito.csv, compara. Vídeos: amostra
quadros (padrão 1 por segundo), lê cada um e mostra a placa mais votada.

Uso: python ocr_local.py <arquivo_ou_pasta>... [--fps 1]
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

import cv2
from ocr.clean import normalize
from ocr.processor import extract_text

IMAGE_EXT = {".jpg", ".jpeg", ".png"}
# Uma leitura isolada num vídeo costuma ser engano (reflexo, placa ao fundo):
# a placa só é confirmada quando quadros diferentes concordam.
MIN_VOTES = 2
VIDEO_EXT = {".mp4", ".webm", ".mov", ".avi", ".mkv"}


def read_plate(image_bytes: bytes) -> str | None:
    result = extract_text(image_bytes)
    if not result.ok:
        return None
    plate = normalize(result.raw_text or "", mercosul=result.mercosul)
    return plate.plate if plate.ok else None


def load_answers(folder: Path) -> dict[str, str]:
    answers_file = folder / "gabarito.csv"
    if not answers_file.exists():
        return {}
    with answers_file.open() as f:
        return {row["arquivo"]: row["placa_esperada"] for row in csv.DictReader(f)}


def run_images(paths: list[Path]) -> None:
    answers: dict[str, str] = {}
    for folder in {p.parent for p in paths}:
        answers.update(load_answers(folder))

    hits = total = 0
    for path in paths:
        plate = read_plate(path.read_bytes())
        line = f"{path.name:42s} lida={plate or '-':9s}"
        if path.name in answers:
            expected = answers[path.name]
            ok = (plate or "") == expected
            hits += ok
            total += 1
            line += f" esperada={expected or '(nenhuma)':9s} {'OK' if ok else 'ERRO'}"
        print(line)
    if total:
        print(f"Acertos: {hits}/{total}")


def run_video(path: Path, fps: float) -> None:
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        print(f"{path.name}: não foi possível abrir o vídeo")
        return
    video_fps = capture.get(cv2.CAP_PROP_FPS) or 30.0
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    step = max(1, round(video_fps / fps))

    votes: Counter[str] = Counter()
    frame_index = sampled = 0
    while True:
        ok, frame = capture.read()
        if not ok:
            break
        if frame_index % step == 0:
            sampled += 1
            encoded, buffer = cv2.imencode(".jpg", frame)
            plate = read_plate(buffer.tobytes()) if encoded else None
            if plate:
                votes[plate] += 1
                print(f"  {frame_index / video_fps:6.1f}s  {plate}")
        frame_index += 1
    capture.release()

    print(f"{path.name}: {width}x{height}, {sampled} quadros analisados")
    if not votes:
        print("  nenhuma placa lida")
        return
    plate, count = votes.most_common(1)[0]
    if count >= MIN_VOTES:
        print(f"  placa confirmada: {plate} ({count} quadros)  leituras: {dict(votes)}")
    else:
        print(f"  nenhuma placa confirmada (mínimo {MIN_VOTES} quadros)  leituras: {dict(votes)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--fps", type=float, default=1.0, help="quadros por segundo nos vídeos")
    args = parser.parse_args()

    images: list[Path] = []
    videos: list[Path] = []
    for path in args.paths:
        files = sorted(path.iterdir()) if path.is_dir() else [path]
        for file in files:
            if file.suffix.lower() in IMAGE_EXT:
                images.append(file)
            elif file.suffix.lower() in VIDEO_EXT:
                videos.append(file)

    if images:
        run_images(images)
    for video in videos:
        run_video(video, args.fps)
    return 0


if __name__ == "__main__":
    sys.exit(main())
