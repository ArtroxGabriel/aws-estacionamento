"""Gera placas sintéticas (Mercosul e antigas) com variações realistas.

O dataset real (OpenALPR) só tem placas brasileiras antigas, de antes de 2018.
Aqui geramos placas dos 4 países do Mercosul (docs/DECISOES.md, D8), com o nome
do país na faixa como nas placas reais, e cor de carro, tamanho, inclinação,
desfoque, ruído e compressão JPEG aleatórios, cada uma com o gabarito. A semente é fixa:
o mesmo comando gera sempre o mesmo conjunto.

Roda dentro da imagem do worker (Pillow + NumPy): veja `task dataset:sintetico`.
Uso: python gerar_sinteticas.py <pasta_destino> [quantidade]
"""

from __future__ import annotations

import csv
import random
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
DIGITS = "0123456789"
BAND_BLUE = (30, 70, 190)


# (nome, peso, faixa azul com o nome do país, formato: L = letra, D = dígito)
KINDS = [
    ("br-mercosul", 45, "BRASIL", "LLLDLDD"),
    ("br-antiga", 15, None, "LLLDDDD"),
    ("ar-mercosul", 15, "REPUBLICA ARGENTINA", "LLDDDLL"),
    ("ar-antiga", 5, None, "LLLDDD"),
    ("py-mercosul", 10, "PARAGUAY", "LLLLDDD"),
    ("uy-mercosul", 10, "URUGUAY", "LLLDDDD"),
]


def random_plate(template: str, rng: random.Random) -> str:
    return "".join(rng.choice(LETTERS if k == "L" else DIGITS) for k in template)


def render(kind: str, plate: str, band: str | None, rng: random.Random) -> Image.Image:
    body = tuple(rng.randint(30, 235) for _ in range(3))
    img = Image.new("RGB", (1600, 900), body)
    draw = ImageDraw.Draw(img)
    x, y, w, h = 400, 330, 800, 260
    font_top = ImageFont.load_default(size=40)
    font_plate = ImageFont.load_default(size=150)
    if kind == "ar-antiga":
        # Placa argentina antiga: fundo preto, letras brancas, "ARGENTINA" no topo.
        draw.rectangle(
            [x, y, x + w, y + h], fill=(15, 15, 15), outline=(200, 200, 200), width=10
        )
        draw.text(
            (x + w // 2, y + 45), "ARGENTINA", fill="white", anchor="mm", font=font_top
        )
        draw.text(
            (x + w // 2, y + 165),
            f"{plate[:3]} {plate[3:]}",
            fill="white",
            anchor="mm",
            font=font_plate,
        )
    else:
        draw.rectangle(
            [x, y, x + w, y + h], fill="white", outline=(20, 20, 20), width=10
        )
        if band:
            draw.rectangle([x + 10, y + 10, x + w - 10, y + 85], fill=BAND_BLUE)
            draw.text(
                (x + w // 2, y + 48), band, fill="white", anchor="mm", font=font_top
            )
            text = (
                plate
                if kind != "ar-mercosul"
                else f"{plate[:2]} {plate[2:5]} {plate[5:]}"
            )
        else:
            draw.text(
                (x + w // 2, y + 38),
                "SP - SAO PAULO",
                fill=(30, 30, 30),
                anchor="mm",
                font=ImageFont.load_default(size=36),
            )
            text = f"{plate[:3]}-{plate[3:]}"
        draw.text(
            (x + w // 2, y + 165), text, fill=(25, 25, 25), anchor="mm", font=font_plate
        )

    img = img.rotate(rng.uniform(-6, 6), fillcolor=body)
    scale = rng.choice([0.35, 0.5, 0.7, 1.0])
    img = img.resize((int(1600 * scale), int(900 * scale)), Image.Resampling.LANCZOS)
    blur = rng.choice([0, 0, 0.8, 1.5, 2.2])
    if blur:
        img = img.filter(ImageFilter.GaussianBlur(blur))
    noise = rng.choice([0, 6, 12, 20])
    if noise:
        arr = np.asarray(img).astype(np.int16)
        arr += (
            np.random.default_rng(rng.randint(0, 2**31))
            .normal(0, noise, arr.shape)
            .astype(np.int16)
        )
        img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    return img


def main() -> int:
    dest = Path(sys.argv[1])
    count = int(sys.argv[2]) if len(sys.argv) > 2 else 200
    dest.mkdir(parents=True, exist_ok=True)
    rng = random.Random(42)

    rows = []
    names = [k[0] for k in KINDS]
    weights = [k[1] for k in KINDS]
    for i in range(count):
        kind = rng.choices(names, weights)[0]
        _, _, band, template = KINDS[names.index(kind)]
        plate = random_plate(template, rng)
        name = f"{i:03d}-{kind}-{plate}.jpg"
        render(kind, plate, band, rng).save(
            dest / name, "JPEG", quality=rng.choice([55, 70, 85, 95])
        )
        rows.append((name, plate))

    with (dest / "gabarito.csv").open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["arquivo", "placa_esperada"])
        writer.writerows(rows)
    print(f"{count} placas sintéticas em {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
