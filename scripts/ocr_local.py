"""Roda o OCR do worker (mesmo código da AWS) em fotos e vídeos locais.

Rode com OMP_THREAD_LIMIT=1 (a task já faz isso): o Tesseract abre uma thread
por CPU e, com vários processos em paralelo, estoura o limite de 10 s por foto.

Executado dentro da imagem do worker (veja `task ocr:local`). Fotos: lê a
placa de cada uma e, se a pasta tiver um gabarito.csv, compara. Vídeos: amostra
quadros (padrão 1 por segundo), lê cada um e mostra a placa mais votada.

Com gabarito, cada foto é classificada como OK, ERRADA (leu outra placa, o
pior caso: cobraria o carro errado) ou NAO_LEU (o caixa digita a placa).

Uso: python ocr_local.py <arquivo_ou_pasta>... [--fps 1] [--erros] [--jobs N]
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import cv2
from ocr.clean import normalize
from ocr.processor import extract_text

# Motor de OCR, escolhido por --engine antes de criar os processos: "tesseract"
# (padrão, igual ao Floci) ou "rekognition" (igual à AWS, com Tesseract de
# reserva; precisa de credenciais AWS no ambiente).
OCR = extract_text

IMAGE_EXT = {".jpg", ".jpeg", ".png"}
# Uma leitura isolada num vídeo costuma ser engano (reflexo, placa ao fundo):
# a placa só é confirmada quando quadros diferentes concordam.
MIN_VOTES = 2
VIDEO_EXT = {".mp4", ".webm", ".mov", ".avi", ".mkv"}


def read_plate(image_bytes: bytes) -> str | None:
    result = OCR(image_bytes)
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


def use_engine(engine: str) -> None:
    """Escolhe o motor em cada processo (o forkserver não herda o global)."""
    global OCR
    if engine == "rekognition":
        import boto3
        from ocr.rekognition import HybridOcr

        OCR = HybridOcr(boto3.client("rekognition", region_name="us-east-1"))


def read_file(path: Path) -> str | None:
    return read_plate(path.read_bytes())


def run_images(paths: list[Path], only_errors: bool, jobs: int, engine: str) -> None:
    started = time.monotonic()
    with ProcessPoolExecutor(
        max_workers=jobs, initializer=use_engine, initargs=(engine,)
    ) as pool:
        plates = list(pool.map(read_file, paths, chunksize=4))

    by_folder: dict[Path, list[tuple[Path, str | None]]] = {}
    for path, plate in zip(paths, plates, strict=True):
        by_folder.setdefault(path.parent, []).append((path, plate))

    for folder, results in by_folder.items():
        answers = load_answers(folder)
        counts: Counter[str] = Counter()
        print(f"== {folder}")
        for path, plate in results:
            if path.name not in answers:
                print(f"{path.name:42s} lida={plate or '-'}")
                continue
            expected = answers[path.name]
            if (plate or "") == expected:
                verdict = "OK"
            elif plate is None:
                verdict = "NAO_LEU"
            else:
                verdict = "ERRADA"
            counts[verdict] += 1
            if not only_errors or verdict != "OK":
                print(
                    f"{path.name:42s} lida={plate or '-':9s} esperada={expected or '(nenhuma)':9s} {verdict}"
                )
        total = sum(counts.values())
        if total:
            summary = " · ".join(
                f"{v} {counts[v]}/{total} ({100 * counts[v] / total:.0f}%)"
                for v in ("OK", "ERRADA", "NAO_LEU")
            )
            print(f"Resumo: {summary}")
    print(f"{len(paths)} fotos em {time.monotonic() - started:.0f}s ({jobs} processos)")


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
        print(
            f"  nenhuma placa confirmada (mínimo {MIN_VOTES} quadros)  leituras: {dict(votes)}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument(
        "--fps", type=float, default=1.0, help="quadros por segundo nos vídeos"
    )
    parser.add_argument(
        "--erros", action="store_true", help="lista só as fotos com erro"
    )
    parser.add_argument(
        "--jobs", type=int, default=os.cpu_count() or 1, help="processos em paralelo"
    )
    parser.add_argument(
        "--engine", choices=("tesseract", "rekognition"), default="tesseract"
    )
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
        run_images(images, args.erros, args.jobs, args.engine)
    use_engine(args.engine)
    for video in videos:
        run_video(video, args.fps)
    return 0


if __name__ == "__main__":
    sys.exit(main())
