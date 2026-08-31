"""HEIC/HEIF を JPEG（または PNG）に変換する。EXIF はそのまま引き継ぐ。

  python tools/heic2jpg.py "G:\\マイドライブ\\World2" photos/world2
  python tools/heic2jpg.py <src> <dst> --format png --max 2400
"""

import argparse
import re
import sys
from pathlib import Path

from PIL import Image

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
except Exception:
    print("pillow-heif が要る: pip install pillow-heif", file=sys.stderr)
    raise

SRC_EXT = {".heic", ".heif", ".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("src", type=Path)
    ap.add_argument("dst", type=Path)
    ap.add_argument("--format", default="jpg", choices=["jpg", "png"])
    ap.add_argument("--quality", type=int, default=92)
    ap.add_argument("--max", type=int, default=0, help="長辺の上限。0 なら等倍")
    ap.add_argument("--force", action="store_true", help="変換済みでも作り直す")
    ap.add_argument("--pattern", default="*", help="対象のグロブ (例: IMG_0*)")
    ap.add_argument("--flat", action="store_true", help="サブフォルダを見ない")
    args = ap.parse_args()

    args.dst.mkdir(parents=True, exist_ok=True)
    walk = args.src.glob if args.flat else args.src.rglob
    files = sorted(p for p in walk(args.pattern) if p.suffix.lower() in SRC_EXT)
    done = skipped = 0
    for f in files:
        # Drive の "IMG_0280 (1)" のような重複コピーは同じ名前に寄せて 1 枚にする
        stem = re.sub(r"\s*\(\d+\)$", "", f.stem)
        out = args.dst / (stem + ("." + args.format if args.format == "png" else ".jpg"))
        if out.exists() and not args.force:
            skipped += 1
            continue
        try:
            im = Image.open(f)
            exif = im.info.get("exif")            # 撮影時刻と GPS をそのまま渡す
            im = im.convert("RGB")
            if args.max and max(im.size) > args.max:
                im.thumbnail((args.max, args.max), Image.LANCZOS)
            if args.format == "png":
                im.save(out, optimize=True, exif=exif) if exif else im.save(out, optimize=True)
            else:
                im.save(out, quality=args.quality, subsampling=1,
                        **({"exif": exif} if exif else {}))
            done += 1
        except Exception as e:
            print(f"  ! {f.name}: {e}", file=sys.stderr)
    print(f"{done} 枚を変換、{skipped} 枚は既にあった → {args.dst}")


if __name__ == "__main__":
    main()
