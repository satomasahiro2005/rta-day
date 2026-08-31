"""パイプライン検証用のダミー写真を作る（EXIF DateTimeOriginal + GPS 付き）。"""

import sys
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "testphotos")
OUT.mkdir(parents=True, exist_ok=True)

SHOTS = [
    ("06:52", "静岡 ホーム", (34, 52, 84)),
    ("07:01", "ライナー車内", (60, 40, 74)),
    ("07:44", "沼津 乗り換え", (30, 66, 70)),
    ("08:09", "熱海 4番線", (78, 52, 32)),
    ("08:38", "小田原 窓口", (44, 44, 44)),
    ("10:31", "浅草橋 到着", (28, 62, 44)),
    ("11:33", "受付 2F", (66, 34, 48)),
    ("13:12", "Opening Keynote", (26, 40, 90)),
    ("16:44", "直前枠", (72, 60, 24)),
    ("19:10", "懇親会", (48, 30, 62)),
]

DATE = "2026:08:31"
GPS = (35.6993, 139.7856)  # 浅草橋


def dms(v):
    d = int(v)
    m = int((v - d) * 60)
    s = (v - d - m / 60) * 3600
    return (float(d), float(m), round(s, 4))


font = ImageFont.truetype("C:/Windows/Fonts/YuGothB.ttc", 96)
small = ImageFont.truetype("C:/Windows/Fonts/YuGothM.ttc", 56)

for i, (hhmm, label, color) in enumerate(SHOTS):
    im = Image.new("RGB", (2016, 1512), color)
    d = ImageDraw.Draw(im)
    for y in range(0, 1512, 6):
        d.line([(0, y), (2016, y)], fill=tuple(min(255, c + y // 24) for c in color))
    d.text((120, 620), label, font=font, fill=(255, 255, 255))
    d.text((120, 760), hhmm, font=small, fill=(220, 225, 235))

    exif = Image.Exif()
    stamp = f"{DATE} {hhmm}:{(i * 7) % 60:02d}"
    exif[0x0132] = stamp  # DateTime (IFD0)
    ifd = exif.get_ifd(0x8769)
    ifd[0x9003] = stamp  # DateTimeOriginal
    ifd[0x9004] = stamp
    if label.startswith("浅草橋") or "受付" in label:
        gps = exif.get_ifd(0x8825)
        gps[1] = "N"
        gps[2] = dms(GPS[0])
        gps[3] = "E"
        gps[4] = dms(GPS[1])

    im.save(OUT / f"IMG_{i:04d}.jpg", quality=88, exif=exif)

print(f"{len(SHOTS)} 枚を {OUT} に作成")
