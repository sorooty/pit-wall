"""Terminal banner: waving chequered flag, PIT WALL 3D title, pixel art F1 car. Truecolor ANSI, 2 pixels per cell."""

import math
import sys
import time

import numpy as np
import pyfiglet
from PIL import Image, ImageDraw
from prompt_toolkit.output import create_output

TITLE_FONT = "ansi_shadow"
RED, WHITE, DIM, GREEN = (225, 6, 0), (230, 236, 255), (160, 172, 210), (0, 230, 140)
FLAG_W, FLAG_H, SQUARE, POLE_H = 24, 12, 4, 18
# Car drawn in an 84 x 22 unit box, side view, rear wing left, front wing right
CAR_BOX = (84, 22)
CAR_W = 66
CAR_H = round(CAR_W * CAR_BOX[1] / CAR_BOX[0])
SUPERSAMPLE = 8
WAVE_FRAMES, WAVE_FPS = 40, 25
TAGLINES = [("", DIM), ("RACE REPLAY", DIM), ("OPENF1 · 2023+", DIM), ("", DIM), ("by Sensey", GREEN)]
HINT = "Pick a season, a Grand Prix and a session. Back returns one step, Exit or Ctrl+C quits."


def _fg(rgb: tuple[int, int, int]) -> str:
    return f"\x1b[38;2;{rgb[0]};{rgb[1]};{rgb[2]}m"


def _bg(rgb: tuple[int, int, int]) -> str:
    return f"\x1b[48;2;{rgb[0]};{rgb[1]};{rgb[2]}m"


RESET = "\x1b[0m"


def _half_blocks(pixels: np.ndarray) -> list[str]:
    """RGBA pixels (h, w, 4) to text lines, one ▀ cell holds a top and a bottom pixel, alpha 0 is see through."""
    if pixels.shape[0] % 2:
        pixels = np.concatenate([pixels, np.zeros((1, *pixels.shape[1:]), dtype=pixels.dtype)])
    lines = []
    for top_row, bottom_row in zip(pixels[0::2], pixels[1::2]):
        cells = []
        for top, bottom in zip(top_row, bottom_row):
            seen_top, seen_bottom = top[3] > 0, bottom[3] > 0
            if seen_top and seen_bottom:
                cells.append(_fg(top[:3]) + _bg(bottom[:3]) + "▀" + RESET)
            elif seen_top:
                cells.append(_fg(top[:3]) + "▀" + RESET)
            elif seen_bottom:
                cells.append(_fg(bottom[:3]) + "▄" + RESET)
            else:
                cells.append(" ")
        lines.append("".join(cells))
    return lines


def _car_pixels() -> np.ndarray:
    """F1 car pixel art: vector shapes drawn supersampled, downscaled, then alpha thresholded for crisp pixels."""
    k = SUPERSAMPLE * CAR_W / CAR_BOX[0]
    dark, tyre, rim, grey, helmet = (130, 0, 0), (52, 52, 60), (175, 175, 185), (90, 90, 100), (255, 200, 0)
    img = Image.new("RGBA", (CAR_W * SUPERSAMPLE, CAR_H * SUPERSAMPLE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    def pts(*xy: tuple[float, float]) -> list[tuple[float, float]]:
        return [(x * k, y * k) for x, y in xy]

    # Rear wing endplate and its two elements
    draw.polygon(pts((2, 2), (13, 2), (13, 13), (9, 15), (2, 15)), fill=dark)
    draw.rectangle(pts((1, 2), (14, 4)), fill=RED)
    draw.rectangle(pts((1, 6), (14, 7.5)), fill=RED)
    # Floor, then body from engine cover over the airbox and cockpit down to the nose
    draw.polygon(pts((8, 17), (76, 17), (80, 18.5), (8, 18.5)), fill=grey)
    draw.polygon(
        pts((10, 15.5), (12, 11), (24, 9.5), (34, 6), (40, 5.5), (44, 7.5), (50, 9.5), (60, 10.5), (72, 12.5), (80, 15), (80, 17), (10, 17)),
        fill=RED,
    )
    draw.polygon(pts((22, 13.5), (58, 13.5), (66, 14.5), (22, 14.8)), fill=WHITE)
    # Helmet under the halo
    draw.ellipse(pts((41, 6.5), (46, 11)), fill=helmet)
    draw.line(pts((38, 9), (42, 5.5), (49, 5.5), (52, 9.5)), fill=grey, width=round(k))
    # Front wing with its endplate
    draw.polygon(pts((72, 17), (83, 16.5), (83, 19), (72, 19)), fill=dark)
    draw.rectangle(pts((81, 13.5), (83, 19)), fill=RED)
    for cx in (19, 66):
        draw.ellipse(pts((cx - 6, 10), (cx + 6, 22)), fill=tyre)
        draw.ellipse(pts((cx - 3, 13), (cx + 3, 19)), fill=rim)
        draw.ellipse(pts((cx - 1, 15), (cx + 1, 17)), fill=RED)
    pixels = np.array(img.resize((CAR_W, CAR_H), Image.LANCZOS))
    # Here I snap alpha to on/off, half transparent edges would print as muddy colours
    pixels[..., 3] = np.where(pixels[..., 3] > 110, 255, 0)
    return pixels


def _flag_pixels(t: float) -> np.ndarray:
    """Chequered flag on a pole at time t (s): a sine wave travels along it, shading follows the slope."""
    width, height = FLAG_W + 2, POLE_H
    pixels = np.zeros((height, width, 4), dtype=np.uint8)
    pixels[:, :1] = (150, 150, 160, 255)
    x = np.arange(FLAG_W)
    # Here the wave grows away from the pole, like cloth held on one side
    phase = 2 * math.pi * x / 9 - 6 * t
    offset = 1.6 * (x / FLAG_W) * np.sin(phase)
    shade = 0.78 + 0.22 * np.cos(phase)
    ys = np.arange(height)[:, None] - offset[None, :] - 1
    inside = (ys >= 0) & (ys < FLAG_H)
    checker = ((x[None, :] // SQUARE) + (np.floor(ys).astype(int) // SQUARE)) % 2 == 0
    # Dark squares stay lighter than a dark terminal background so the flag outline reads
    base = np.where(checker, 240, 62)[..., None] * shade[None, :, None]
    pixels[:, 2:, :3] = np.clip(base, 0, 255).astype(np.uint8)
    pixels[:, 2:, 3] = np.where(inside, 255, 0)
    return pixels


def _title_lines() -> list[str]:
    # Here PIT is red and WALL white, rendered apart then joined so each word keeps its colour
    pit, wall = (pyfiglet.figlet_format(w, font=TITLE_FONT).splitlines() for w in ("PIT", "WALL"))
    pit, wall = [line for line in pit if line.strip()], [line for line in wall if line.strip()]
    width = max(map(len, pit))
    return [_fg(RED) + p.ljust(width) + " " + _fg(WHITE) + w + RESET for p, w in zip(pit, wall)]


def _header(t: float, title: list[str]) -> list[str]:
    # Flag on the left, title vertically centred next to it
    flag = _half_blocks(_flag_pixels(t))
    pad = (len(flag) - len(title)) // 2
    rows = [""] * pad + title + [""] * (len(flag) - len(title) - pad)
    return [f + "  " + r for f, r in zip(flag, rows)]


def print_banner() -> None:
    """Print the banner, then wave the flag for a moment if the terminal is interactive."""
    title = _title_lines()
    header = _header(0.0, title)
    car = _half_blocks(_car_pixels())
    tags = [""] * (len(car) - len(TAGLINES)) + [_fg(c) + "\x1b[3m" + s + RESET for s, c in TAGLINES]
    body = [c + "   " + tag for c, tag in zip(car, tags)]
    footer = ["", _fg(DIM) + HINT + RESET, ""]
    lines = ["", *header, "", *body, *footer]
    if not sys.stdout.isatty():
        print("\n".join(lines))
        return
    # Here prompt_toolkit's output enables ANSI on Windows consoles, then I redraw the header lines in place
    out = create_output()
    out.write_raw("\n".join(lines) + "\n")
    out.flush()
    up = len(lines) - 1
    for i in range(1, WAVE_FRAMES):
        frame = _header(i / WAVE_FPS, title)
        out.write_raw(f"\x1b[{up}A\r" + "".join(f"\x1b[2K{line}\n" for line in frame) + f"\x1b[{up - len(frame)}B")
        out.flush()
        time.sleep(1 / WAVE_FPS)
