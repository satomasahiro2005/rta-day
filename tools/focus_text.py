"""白地に黒の集中線＋中央に大きな文字の画像を作る。

  python tools/focus_text.py "一行目\n二行目" out/quote.png
  python tools/focus_text.py "..." out.png --size 1920x1080 --invert
"""

import argparse
import colorsys
import json
import math
import random
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

FONT = "C:/Windows/Fonts/YuGothB.ttc"


def fade_mask(size, hole, fade=1.7, gamma=1.6):
    """中心ほど薄く、外へ行くほど濃くなるマスク。線の内端を溶かすため。"""
    w, h = size
    y, x = np.ogrid[0:h, 0:w]
    r = np.hypot((x - w / 2) / (w * hole), (y - h / 2) / (h * hole))
    t = np.clip((r - 1.0) / (fade - 1.0), 0.0, 1.0) ** gamma
    return Image.fromarray((t * 255).astype(np.uint8), "L")


def focus_lines(size, fg, hole=0.34, count=460, seed=7, rainbow=False, opacity=1.0):
    """中央が抜けた集中線。内端はマスクで溶かす。"""
    w, h = size
    cx, cy = w / 2, h / 2
    far = math.hypot(w, h)
    rnd = random.Random(seed)
    layer = Image.new("RGB", size, (255, 255, 255) if fg[0] < 128 else (0, 0, 0))
    d = ImageDraw.Draw(layer)
    for _ in range(count):
        a = rnd.uniform(0, math.tau)
        r_in = hole * rnd.uniform(0.80, 1.35)      # 内端の位置をばらす
        ex, ey = w * r_in, h * r_in
        wide = math.radians(rnd.uniform(0.10, 0.85))
        color = fg
        if rainbow:
            hue = (a / math.tau + rnd.uniform(-0.015, 0.015)) % 1.0
            r, g, b = colorsys.hsv_to_rgb(hue, 0.95, rnd.uniform(0.85, 1.0))
            color = (int(r * 255), int(g * 255), int(b * 255))
        pts = []
        for da, inner in ((-wide / 2, True), (-wide / 2, False), (wide / 2, False), (wide / 2, True)):
            ang = a + da
            if inner:
                pts.append((cx + math.cos(ang) * ex, cy + math.sin(ang) * ey))
            else:
                pts.append((cx + math.cos(ang) * far, cy + math.sin(ang) * far))
        d.polygon(pts, fill=color)

    bg = Image.new("RGB", size, (255, 255, 255) if fg[0] < 128 else (0, 0, 0))
    mask = fade_mask(size, hole)
    if opacity < 1.0:
        mask = mask.point(lambda v: int(v * opacity))
    return Image.composite(layer, bg, mask)


def fit_font(draw, lines, max_w, max_h, start=200):
    for size in range(start, 20, -2):
        f = ImageFont.truetype(FONT, size)
        widths = [draw.textlength(x, font=f) for x in lines]
        line_h = size * 1.35
        if max(widths) <= max_w and line_h * len(lines) <= max_h:
            return f, line_h
    return ImageFont.truetype(FONT, 24), 32


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("text", nargs="?", help="改行は \n")
    ap.add_argument("--slug", help="出力名。out/quotes/quote-<slug>.png になる")
    ap.add_argument("--batch", type=Path, help='{"スラッグ": "本文"} の JSON')
    ap.add_argument("--outdir", type=Path, default=Path("out/quotes"))
    ap.add_argument("--out", type=Path, help="出力先を直接指定する")
    ap.add_argument("--size", default="1920x1080")
    ap.add_argument("--hole", type=float, default=0.34, help="中央の空きの大きさ")
    ap.add_argument("--lines", type=int, default=460, help="集中線の本数")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--invert", action="store_true", help="黒地に白線・白文字")
    ap.add_argument("--rainbow", action="store_true", help="集中線を虹色にする")
    ap.add_argument("--opacity", type=float, default=1.0, help="線の濃さ (0-1)")
    ap.add_argument("--font-size", type=int, default=0, help="文字の大きさを固定する (0=自動)")
    ap.add_argument("--text-width", type=float, default=0.62, help="文字を収める幅の割合")
    ap.add_argument("--uniform", action="store_true",
                    help="バッチ全体で同じ文字サイズに揃える（一番長い行に合わせる）")
    args = ap.parse_args()

    jobs: list[tuple[Path, str]] = []
    if args.batch:
        data = json.loads(args.batch.read_text(encoding="utf-8"))
        for slug, text in data.items():
            if text.strip():
                jobs.append((args.outdir / f"quote-{slug}.png", text))
    else:
        if not args.text:
            ap.error("本文か --batch のどちらかが要る")
        out = args.out or (args.outdir / f"quote-{args.slug or 'untitled'}.png")
        jobs.append((out, args.text))

    w, h = (int(x) for x in args.size.lower().split("x"))
    fg = (255, 255, 255) if args.invert else (0, 0, 0)

    size_override = args.font_size or 0
    if args.uniform and not size_override:      # 一番長い行に合わせて全部そろえる
        probe = ImageDraw.Draw(Image.new("RGB", (w, h)))
        sizes = []
        for _, text in jobs:
            ls = text.replace(chr(92) + "n", chr(10)).split(chr(10))
            sizes.append(fit_font(probe, ls, w * args.text_width, h * 0.42)[0].size)
        size_override = min(sizes)

    for out, text in jobs:
        im = focus_lines((w, h), fg, hole=args.hole, count=args.lines,
                         seed=args.seed + (hash(out.stem) % 1000 if args.batch else 0),
                         rainbow=args.rainbow, opacity=args.opacity)
        d = ImageDraw.Draw(im)
        lines = text.replace(chr(92) + "n", chr(10)).split(chr(10))
        if size_override:
            font = ImageFont.truetype(FONT, size_override)
            line_h = size_override * 1.35
        else:
            font, line_h = fit_font(d, lines, w * args.text_width, h * 0.42)
        y = (h - line_h * len(lines)) / 2 + (line_h - font.size) / 2
        for line in lines:
            d.text((w / 2, y), line, font=font, fill=fg, anchor="ma")
            y += line_h
        out.parent.mkdir(parents=True, exist_ok=True)
        im.save(out)
        print(f"→ {out} ({w}x{h}) 文字 {font.size}px")


if __name__ == "__main__":
    main()
