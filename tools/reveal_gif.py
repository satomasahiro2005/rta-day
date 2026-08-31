"""行が下に積み上がっていく一言アニメ GIF を作る。

- 文字の位置は最初に全部決める（後から行が増えても既出の行は動かない）
- 出るときは濃度だけ変える（位置は動かさない）
- 「……」は本文より少し遅れて出せる

  python tools/reveal_gif.py beats-kuro.json out/kuro.gif

beats.json:
  {
    "size": "960x540", "font": 0.085, "area": "right",
    "bg": 0, "fg": 255, "left_gray": 205,
    "fps": 20, "fade_ms": 300,
    "beats": [
      {"text": "kuro は契約終了", "tail": "……", "ms": 700, "tail_ms": 900}
    ]
  }
"""

import argparse
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))
from focus_text import FONT  # noqa: E402


def layout(beats, size, base_px, area, draw):
    """行の位置を先に全部決めてしまう。あとから動かさないため。"""
    w, h = size
    if area == "right":
        cx, span = w * 0.75, w / 2
    elif area == "left":
        cx, span = w * 0.25, w / 2
    else:
        cx, span = w / 2, float(w)

    rows = []
    for b in beats:
        px = int(base_px * b.get("scale", 1.0))
        font = ImageFont.truetype(FONT, px)
        full = b["text"] + b.get("tail", "")
        while draw.textlength(full, font=font) > span * 0.88 and px > 10:
            px -= 2
            font = ImageFont.truetype(FONT, px)
        rows.append({"font": font, "px": px, "lh": px * 1.5,
                     "text": b["text"], "tail": b.get("tail", ""),
                     "w_full": draw.textlength(full, font=font),
                     "w_text": draw.textlength(b["text"], font=font)})
    total = sum(r["lh"] for r in rows)
    y = (h - total) / 2
    for r in rows:
        r["x"] = cx - r["w_full"] / 2
        r["y"] = y + (r["lh"] - r["px"]) / 2
        y += r["lh"]
    return rows


def frame(rows, size, states, left_gray, bg, fg, x_split):
    """states[i] = (本文の濃さ 0-1, 「……」の濃さ 0-1)"""
    w, h = size
    im = Image.new("L", size, bg)
    d = ImageDraw.Draw(im)
    if left_gray is not None and x_split > 0:
        d.rectangle((0, 0, int(x_split) - 1, h), fill=left_gray)
    for r, (a_text, a_tail) in zip(rows, states):
        if a_text > 0:
            d.text((r["x"], r["y"]), r["text"], font=r["font"],
                   fill=int(bg + (fg - bg) * a_text), anchor="la")
        if r["tail"] and a_tail > 0:
            d.text((r["x"] + r["w_text"], r["y"]), r["tail"], font=r["font"],
                   fill=int(bg + (fg - bg) * a_tail), anchor="la")
    return im


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("beats", type=Path)
    ap.add_argument("out", type=Path)
    args = ap.parse_args()

    spec = json.loads(args.beats.read_text(encoding="utf-8"))
    w, h = (int(x) for x in spec.get("size", "960x540").lower().split("x"))
    base = int(h * spec.get("font", 0.085))
    fps = spec.get("fps", 20)
    fade = spec.get("fade_ms", 300)
    bg, fg = spec.get("bg", 255), spec.get("fg", 0)
    gray = spec.get("left_gray")
    area = spec.get("area", "center")
    x_split = w / 2 if area == "right" else 0
    beats = spec["beats"]

    probe = ImageDraw.Draw(Image.new("L", (w, h)))
    rows = layout(beats, (w, h), base, area, probe)

    states = [[0.0, 0.0] for _ in rows]
    frames, durations = [], []
    step = max(1, int(1000 / fps))
    steps = max(1, int(fade / step))

    def smooth(t):
        return t * t * (3 - 2 * t)

    def shot(ms):
        frames.append(frame(rows, (w, h), [tuple(s) for s in states], gray, bg, fg, x_split))
        durations.append(ms)

    # 前の行の「……」と次の行の本文は同時に出す
    for i, b in enumerate(beats):
        targets = [(i, 0)]
        if i > 0 and rows[i - 1]["tail"]:
            targets.append((i - 1, 1))
        for k in range(1, steps + 1):
            for idx, slot in targets:
                states[idx][slot] = smooth(k / steps)
            shot(step)
        for idx, slot in targets:
            states[idx][slot] = 1.0
        shot(b.get("ms", 1200))

    if rows[-1]["tail"]:                       # 最後の行に「……」があるとき
        for k in range(1, steps + 1):
            states[-1][1] = smooth(k / steps)
            shot(step)
        states[-1][1] = 1.0
        shot(beats[-1].get("tail_ms", 1200))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    pal = [f.convert("P", palette=Image.ADAPTIVE, colors=32) for f in frames]
    pal[0].save(args.out, save_all=True, append_images=pal[1:], duration=durations,
                loop=0, optimize=False, disposal=1)
    mb = args.out.stat().st_size / 1024 / 1024
    print(f"→ {args.out} ({w}x{h}) {len(frames)} コマ {sum(durations)/1000:.1f}秒 {mb:.2f}MB")


if __name__ == "__main__":
    main()
