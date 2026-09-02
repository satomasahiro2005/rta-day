#!/usr/bin/env python3
"""一日の行程 + 写真の EXIF から RTA 配信ふうの画像を作る。

タイマーは本物の LiveSplit (livesplit-core, MIT / Apache-2.0) を lsrender 経由で
描画し、その PNG を写真に合成する。

  python rta_day.py --photos "D:\\DCIM"            # 全部作る
  python rta_day.py --now 14:30                    # 今の時刻で一枚
  python rta_day.py --photos . --only structured,chapters

出力
  now.png            1920x1080。現在地のフレーム（写真が無ければプレースホルダー）
  structured.png     章は畳んで、今いる章だけ開いたボード
  chapterN_*.png     章ごとの詳細
  board.png          全チェックポイント通し
  frameNNN.png       写真ごとの配信フレーム
  result.png         背景写真つきの結果画像
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

try:  # iPhone の HEIC
    import pillow_heif

    pillow_heif.register_heif_opener()
except Exception:
    pillow_heif = None

ROOT = Path(__file__).resolve().parent
LSRENDER = ROOT / "lsrender" / "target" / "release" / "lsrender.exe"

PHOTO_EXT = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".tif", ".tiff", ".avif"}

FONT_TEXT = "C:/Windows/Fonts/YuGothB.ttc"
FONT_TEXT_R = "C:/Windows/Fonts/YuGothM.ttc"
FONT_NUM = "C:/Windows/Fonts/bahnschrift.ttf"

THEME = {
    "background": ["0b0e14ee", "151b26ee"],
    "text_color": "ffffffff",
    "ahead": "3fd45eff",
    "behind": "e04b4bff",
    "gold": "d8af1fff",
    "row_a": "ffffff0d",
    "row_b": "00000000",
}
# 予定差の "−" (U+2212) を持たないフォントだと豆腐になるので times は Segoe UI
FONTS = {"text": "Yu Gothic UI", "times": "Segoe UI", "timer": "Bahnschrift Condensed"}


# --------------------------------------------------------------------------- #
# 行程 → スプリット
# --------------------------------------------------------------------------- #
@dataclass
class Split:
    name: str
    plan: datetime | None
    block: str
    block_id: str
    detail: str = ""
    location: str = ""
    kind: str = ""
    window_end: datetime | None = None  # この行が受け持つ時間帯の終わり
    actual: datetime | None = None
    photo: Path | None = None
    photos: list[Path] = field(default_factory=list)
    icon: Path | None = None
    raw: dict = field(default_factory=dict)


def _hhmm(base: datetime, text: str, after: datetime | None = None) -> datetime:
    parts = [int(x) for x in text.split(":")[:3]]
    h, m = parts[0], parts[1]
    sec = parts[2] if len(parts) > 2 else 0
    t = (base.replace(hour=h % 24, minute=m, second=sec, microsecond=0)
         + timedelta(days=h // 24))
    while after is not None and t < after:  # 日をまたぐ行程
        t += timedelta(days=1)
    return t


def item_label(item: dict) -> tuple[str, str]:
    """(スプリット名, 補足) を組み立てる。short があればそれを優先。"""
    kind = item.get("type", "")
    station = item.get("station", "")
    detail = []
    if item.get("line"):
        detail.append(item["line"])
    if item.get("destination"):
        detail.append(f"{item['destination']}行")
    if item.get("platform"):
        detail.append(item["platform"])
    if item.get("location"):
        detail.append(item["location"])
    if item.get("session"):
        s = item["session"]
        who = " / ".join(x for x in [s.get("kind"), s.get("speaker"), s.get("org")] if x)
        if who:
            detail.append(who)
    if item.get("action"):
        detail.append(item["action"])
    if item.get("title") and item.get("short") and item["title"] != item["short"]:
        detail.insert(0, item["title"])

    if item.get("short"):
        name = item["short"]
    elif kind in ("depart", "arrive"):
        name = station if station.endswith("駅") else f"{station}駅"
    elif kind == "transfer":
        to = item.get("to", "") or item.get("from", "")
        name = to if to.endswith("駅") else f"{to}駅"
    else:
        name = item.get("title", "?")
    return name, " / ".join(detail)


def build_splits(schedule: dict, include: list[str] | None) -> list[Split]:
    date = schedule.get("event", {}).get("date")
    base = datetime.fromisoformat(date) if date else datetime.now().replace(hour=0, minute=0)
    splits: list[Split] = []
    last: datetime | None = None
    for block in schedule.get("blocks", []):
        if include and block.get("id") not in include:
            continue
        label = block.get("label", block.get("id", ""))
        for item in block.get("items", []):
            text = item.get("time") or item.get("start")
            if not text:
                continue
            when = _hhmm(base, text, last)
            last = when
            name, detail = item_label(item)
            splits.append(
                Split(
                    name=name, plan=when, block=label, block_id=block.get("id", label),
                    detail=detail, location=item.get("location") or item.get("station") or "",
                    kind=item.get("type", "session" if item.get("session") else ""), raw=item,
                )
            )

    return splits


def drop_transfer_arrivals(splits: list[Split]) -> list[Split]:
    """同じ駅ですぐ発車する「着」は行が二重になるだけなので落とす。
    終点の着（浅草橋 着 / 浜松 着）は次が別の駅なので残る。"""
    keep = []
    for i, s in enumerate(splits):
        nxt = splits[i + 1] if i + 1 < len(splits) else None
        station = s.raw.get("station")
        here = station or s.raw.get("to")
        if (s.kind in ("arrive", "transfer") and nxt is not None and nxt.kind == "depart"
                and here and nxt.raw.get("station") == here):
            continue
        keep.append(s)
    return keep


def dedupe_names(splits: list[Split]) -> list[Split]:
    seen: dict[str, int] = {}
    for s in splits:
        seen[s.name] = seen.get(s.name, 0) + 1
        if seen[s.name] > 1:
            s.name = f"{s.name} ({seen[s.name]})"
    return splits


def set_windows(splits: list[Split], base: datetime) -> list[Split]:
    # 各行が受け持つ時間帯。次の行の開始まで（最後だけ end か 30 分）
    for i, s in enumerate(splits):
        if i + 1 < len(splits):
            s.window_end = splits[i + 1].plan
        else:
            end = s.raw.get("end")
            s.window_end = _hhmm(base, end, s.plan) if end else s.plan + timedelta(minutes=30)
    return splits


# --------------------------------------------------------------------------- #
# 写真 + EXIF
# --------------------------------------------------------------------------- #
@dataclass
class Photo:
    path: Path
    when: datetime
    source: str
    gps: tuple[float, float] | None = None
    subsec_random: bool = False


def _f(v) -> float:
    try:
        return float(v)
    except Exception:
        return 0.0


def read_gps(exif) -> tuple[float, float] | None:
    try:
        gps = exif.get_ifd(0x8825)
    except Exception:
        return None
    if not gps:
        return None

    def dms(vals, ref, neg):
        d = _f(vals[0]) + _f(vals[1]) / 60 + _f(vals[2]) / 3600
        return -d if ref in neg else d

    try:
        return (dms(gps[2], gps.get(1, "N"), ("S",)), dms(gps[4], gps.get(3, "E"), ("W",)))
    except Exception:
        return None


def read_photo(path: Path, use_mtime: bool, override: datetime | None = None) -> Photo | None:
    when, source, gps, faked = None, "mtime", None, False
    if override is not None:
        try:
            with Image.open(path) as im:
                gps = read_gps(im.getexif())
        except Exception:
            gps = None
        return Photo(path=path, when=override, source="manual", gps=gps)
    try:
        with Image.open(path) as im:
            exif = im.getexif()
            ifd = exif.get_ifd(0x8769) if exif else {}
            raw = (ifd.get(0x9003) or ifd.get(0x9004) or (exif.get(0x0132) if exif else None))
            sub = str(ifd.get(0x9291) or ifd.get(0x9290) or "").strip() if ifd else ""
            if raw:
                try:
                    when = datetime.strptime(str(raw).strip(), "%Y:%m:%d %H:%M:%S")
                    source = "exif"
                except ValueError:
                    when = None
            if when is not None:
                if sub.isdigit():
                    when += timedelta(milliseconds=int(sub.ljust(3, "0")[:3]))
                else:
                    # EXIF に小数秒が無い機種が多い。RTA のタイマーは 1/100 秒まで
                    # 出るので、ファイルごとに固定の乱数で埋める（再実行しても同じ）
                    seed = int(hashlib.sha1(path.name.encode()).hexdigest()[:8], 16)
                    when += timedelta(milliseconds=random.Random(seed).randrange(1000))
                    faked = True
            gps = read_gps(exif) if exif else None
    except Exception as e:
        print(f"  ! 読めない: {path.name}: {e}", file=sys.stderr)
        return None
    if when is None:
        if not use_mtime:
            return None
        when = datetime.fromtimestamp(path.stat().st_mtime)
    return Photo(path=path, when=when, source=source, gps=gps, subsec_random=faked)


def collect_photos(dirs: list[Path], use_mtime: bool, day: datetime | None,
                   overrides: dict[str, datetime] | None = None) -> list[Photo]:
    overrides = overrides or {}
    photos: list[Photo] = []
    for d in dirs:
        files = [d] if d.is_file() else sorted(
            p for p in d.rglob("*") if p.suffix.lower() in PHOTO_EXT
        )
        for f in files:
            if f.suffix.lower() in {".heic", ".heif"} and pillow_heif is None:
                print(f"  ! HEIC は pillow-heif が要る: {f.name}", file=sys.stderr)
                continue
            p = read_photo(f, use_mtime, overrides.get(f.name) or overrides.get(str(f)))
            if p is None:
                continue
            if day and not (day <= p.when < day + timedelta(days=2)):
                continue
            photos.append(p)
    photos.sort(key=lambda p: p.when)
    return photos


# --------------------------------------------------------------------------- #
# 写真 → スプリットの割り当て（近さではなく時間帯で入れる）
# --------------------------------------------------------------------------- #
def assign(splits: list[Split], photos: list[Photo], slack_min: float) -> list[Photo]:
    """行 i の受け持ちは [plan_i, plan_(i+1))。写真はその中に入れる。

    行の実測時刻は、その時間帯に入った最初の写真（＝そこに着いた証拠として
    一番早いもの）。1 行に何枚入ってもいい。行程の前後にはみ出した分だけ
    slack 分の猶予で端の行に寄せる。
    """
    slack = timedelta(minutes=slack_min)
    orphans: list[Photo] = []
    for p in photos:
        target = next((s for s in splits if s.plan <= p.when < (s.window_end or s.plan)), None)
        if target is None:
            if splits[0].plan - slack <= p.when < splits[0].plan:
                target = splits[0]
            elif splits[-1].plan <= p.when <= (splits[-1].window_end or splits[-1].plan) + slack:
                target = splits[-1]
            else:
                orphans.append(p)
                continue
        target.photos.append(p.path)
        if target.actual is None or p.when < target.actual:
            target.actual = p.when
            target.photo = p.path
    return orphans


def apply_manual(splits: list[Split], manual: dict, base: datetime) -> list[Split]:
    pinned: list[Split] = []
    for key, value in manual.items():
        target = None
        if key.lstrip("-").isdigit():
            idx = int(key)
            if -len(splits) <= idx < len(splits):
                target = splits[idx]
        else:
            target = next((s for s in splits if s.name == key or s.name.startswith(key)), None)
        if target is None:
            print(f"  ! 手入力の行き先が無い: {key}", file=sys.stderr)
            continue
        target.actual = _hhmm(base, value)
        pinned.append(target)
    return pinned


def drop_conflicts(splits: list[Split], pinned: list[Split]) -> int:
    """手入力した時刻が正で、それと前後関係が合わない写真由来の実測は捨てる。

    例: 08:35 の写真が熱海駅の予定枠に入っていても、小田原駅を 08:35 と手で
    決めたなら、その写真は熱海のものではない。
    """
    dropped = 0
    for i, s in enumerate(splits):
        if s.actual is None or s in pinned:
            continue
        later = [x.actual for x in splits[i + 1:] if x in pinned and x.actual]
        earlier = [x.actual for x in splits[:i] if x in pinned and x.actual]
        if (later and s.actual >= min(later)) or (earlier and s.actual <= max(earlier)):
            s.actual, s.photo = None, None
            dropped += 1
    return dropped


# --------------------------------------------------------------------------- #
# 画像
# --------------------------------------------------------------------------- #
def load_oriented(path: Path) -> Image.Image:
    return ImageOps.exif_transpose(Image.open(path)).convert("RGB")


def make_icon(path: Path, out: Path, size: int = 160) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    ImageOps.fit(load_oriented(path), (size, size), Image.LANCZOS, centering=(0.5, 0.4)).save(out)
    return out


def lay_down(im: Image.Image, how: str) -> Image.Image:
    """縦長の写真は 16:9 に入れると大きく切られるので、横に倒す。"""
    if how == "none" or im.width >= im.height:
        return im
    return im.transpose(Image.ROTATE_270 if how == "cw" else Image.ROTATE_90)


def cover(im: Image.Image, size: tuple[int, int]) -> Image.Image:
    return ImageOps.fit(im, size, Image.LANCZOS, centering=(0.5, 0.45))


def font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def shadow(canvas: Image.Image, box: tuple[int, int, int, int], blur: int = 26) -> None:
    x0, y0, x1, y1 = box
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).rounded_rectangle((x0 + 6, y0 + 10, x1 + 6, y1 + 10), 12,
                                            fill=(0, 0, 0, 150))
    canvas.alpha_composite(layer.filter(ImageFilter.GaussianBlur(blur)))


def placeholder(size: tuple[int, int], label: str = "PLACEHOLDER") -> Image.Image:
    """写真が無いときの背景。写真と誤解しないよう斜線を入れておく。"""
    w, h = size
    im = Image.new("RGB", size, (18, 22, 32))
    d = ImageDraw.Draw(im)
    for y in range(h):
        t = y / h
        d.line([(0, y), (w, y)], fill=(int(18 + 26 * t), int(22 + 30 * t), int(32 + 46 * t)))
    for x in range(-h, w, 110):
        d.line([(x, h), (x + h, 0)], fill=(255, 255, 255), width=1)
    im = Image.alpha_composite(im.convert("RGBA"),
                               Image.new("RGBA", size, (10, 13, 20, 170))).convert("RGB")
    d = ImageDraw.Draw(im)
    d.text((w / 2, h * 0.11), label, font=font(FONT_TEXT, max(24, h // 28)),
           fill=(96, 108, 128), anchor="ma")
    return im


ICON_FONT = ROOT / "assets" / "MaterialIcons-Regular.ttf"
ICON_CODEPOINTS = ROOT / "assets" / "MaterialIcons-Regular.codepoints"
SPEAKER_DIR = ROOT / "assets" / "speakers"

# 章ごとの地色
BLOCK_ACCENT = {
    "outbound": ((42, 82, 150), (22, 44, 86)),
    "conference": ((178, 84, 34), (96, 42, 18)),
    "return": ((86, 52, 134), (44, 28, 74)),
}
BLOCK_ACCENT["morning"] = ((40, 60, 72), (22, 34, 42))
DEFAULT_ACCENT = ((48, 56, 74), (26, 32, 44))

# 行の名前だけを見る規則（補足文の「ライナー券を買う」等に引っ張られないように）
NAME_RULES = [
    (r"起床|目覚", "alarm"),
    (r"ゴミ|ごみ|資源", "delete"),
    (r"自転車|チャリ", "directions_bike"),
    (r"家を出る|出発|徒歩", "directions_walk"),
    (r"新幹線|ひかり|のぞみ|こだま", "train"),
    (r"ライナー", "airline_seat_recline_normal"),
    (r"小田急|快速急行|私鉄", "tram"),
    (r"帰宅", "home"),
    (r"懇親会", "sports_bar"),
    (r"開会|閉会", "celebration"),
    (r"開場", "meeting_room"),
    (r"受付", "how_to_reg"),
    (r"待機", "hourglass_empty"),
    (r"休憩", "local_cafe"),
    (r"ランチ|座談会|学生", "lunch_dining"),
    (r"直前枠", "casino"),
    (r"Keynote|キーノート", "mic"),
]
# 補足文まで見る規則（セッションの中身）
TEXT_RULES = [
    (r"ブース|展示|ポスター", "co_present"),
    (r"Database|DB", "storage"),
    (r"デザイン|プロトタイ", "palette"),
    (r"AI|Salesforce", "smart_toy"),
    (r"組織|フラクタル|PM|越境|プロダクトマネ", "explore"),
]
KIND_ICON = {"depart": "directions_railway", "arrive": "place", "transfer": "swap_horiz",
             "session": "record_voice_over", "event_end": "flag"}
BLOCK_ICON = {"morning": "alarm", "outbound": "directions_railway", "conference": "campaign",
              "return": "train"}

_codepoints: dict[str, str] = {}


def icon_char(name: str) -> str:
    if not _codepoints:
        for line in ICON_CODEPOINTS.read_text(encoding="utf-8").splitlines():
            key, _, code = line.partition(" ")
            if code:
                _codepoints[key] = chr(int(code, 16))
    return _codepoints.get(name, _codepoints.get("place", "?"))


def pick_symbol(name: str, detail: str, kind: str, block_id: str) -> str:
    for pattern, icon in NAME_RULES:
        if re.search(pattern, name, re.I):
            return icon
    if kind in ("depart", "arrive", "transfer"):
        return KIND_ICON[kind]
    for pattern, icon in TEXT_RULES:
        if re.search(pattern, f"{name} {detail}", re.I):
            return icon
    return KIND_ICON.get(kind) or BLOCK_ICON.get(block_id, "place")


def _tile(size: int, block_id: str) -> Image.Image:
    top, bottom = BLOCK_ACCENT.get(block_id, DEFAULT_ACCENT)
    tile = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(tile)
    for y in range(size):
        t = y / size
        d.line([(0, y), (size, y)],
               fill=tuple(int(a + (b - a) * t) for a, b in zip(top, bottom)) + (255,))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), size // 5, fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(tile, (0, 0), mask)
    return out


def symbol_icon(icon: str, block_id: str, out: Path, size: int = 160) -> Path:
    """Material Icons のグリフを章の地色のタイルに載せる。"""
    out.parent.mkdir(parents=True, exist_ok=True)
    im = _tile(size, block_id)
    d = ImageDraw.Draw(im)
    f = ImageFont.truetype(str(ICON_FONT), int(size * 0.60))
    d.text((size / 2, size / 2), icon_char(icon), font=f, fill=(255, 255, 255, 245), anchor="mm")
    im.save(out)
    return out


def avatar_icon(src: Path, block_id: str, out: Path, size: int = 160) -> Path:
    """発表者の顔。枠は付けずに丸く切るだけ。"""
    out.parent.mkdir(parents=True, exist_ok=True)
    face = ImageOps.fit(Image.open(src).convert("RGB"), (size, size), Image.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    im.paste(face, (0, 0), mask)
    im.save(out)
    return out


def fmt_hms(seconds: float, sign: bool = False) -> str:
    s = int(round(abs(seconds)))
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    body = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
    return (("-" if seconds < 0 else "+") + body) if sign else body


def slug(text: str) -> str:
    return re.sub(r"[^\w一-龥ぁ-んァ-ヶー]+", "_", text).strip("_")[:24]


# --------------------------------------------------------------------------- #
# lsrender
# --------------------------------------------------------------------------- #
def run_lsrender(spec: dict, spec_path: Path, exe: Path) -> None:
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
    r = subprocess.run([str(exe), str(spec_path)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print(r.stdout or "", file=sys.stderr)
        print(r.stderr or "", file=sys.stderr)
        raise SystemExit(f"lsrender が失敗した (exit {r.returncode})")
    if (r.stderr or "").strip():
        print(r.stderr.strip(), file=sys.stderr)


def segments_of(splits: list[Split], start: datetime, prev_plan: datetime) -> list[dict]:
    segs = []
    for s in splits:
        if s.plan is None:                      # 高さ合わせの空行
            segs.append({"name": s.name})
            continue
        seg = {"name": s.name, "plan": (s.plan - start).total_seconds(),
               "best": max((s.plan - prev_plan).total_seconds(), 0.0)}
        if s.actual:
            seg["actual"] = (s.actual - start).total_seconds()
        if s.icon:
            seg["icon"] = str(s.icon)
        prev_plan = s.plan
        segs.append(seg)
    return segs


def timer_height(args, longest: float) -> int:
    """タイマーの文字は幅に合わせて縮んでくれないので、桁数から高さを決める。
    レイアウトの横幅は 11.5 単位で、数字 1 文字がおよそ 0.5 x 高さ分を食う。"""
    hours = int(max(longest, 0) // 3600)
    digits = max(len(f"{hours}"), 2 if args.timebase == "clock" else 1) + 4
    digits += {"seconds": 0, "tenths": 1, "hundredths": 2}[args.timer_accuracy]
    seps = 2 + (0 if args.timer_accuracy == "seconds" else 1)
    fit = 24.0 * 11.0 / max(0.5 * digits + 0.25 * seps, 0.1)
    return max(28, min(args.timer_height, int(fit)))


def make_spec(args, title, category, subtitle, footer, segments, width, visible=0, preview=0,
              longest=0.0, fill_blank=False):
    return {
        "title": title,
        "category": category,
        "subtitle": subtitle,
        "footer": footer,
        "width": width,
        "auto_height": True,
        "fonts": FONTS,
        "theme": THEME,
        "accuracy": args.accuracy,
        "delta_accuracy": args.delta_accuracy,
        "timer_accuracy": args.timer_accuracy,
        "digits_format": "double_hours" if args.timebase == "clock" else "single_hours",
        "two_rows": args.two_rows,
        "fill_blank": fill_blank,
        "timer_height": timer_height(args, longest),
        "visual_split_count": visible,
        "split_preview_count": preview,
        "segments": segments,
        "frames": [],
        **({"layout_file": str(args.layout)} if args.layout else {}),
    }


def splits_done(splits: list[Split]) -> int:
    done = 0
    for i, s in enumerate(splits):
        if s.actual:
            done = i + 1
    return done


def render_board(args, out_png: Path, splits, start, prev_plan, title, category, subtitle,
                 footer, width, at: datetime, tmp: Path, game_icon=None, visible=0, preview=0,
                 quiet=False, fill_blank=False, done: int | None = None) -> Path:
    segs = segments_of(splits, start, prev_plan)
    longest = (max([(s.actual or s.plan) for s in splits if s.plan] + [at]) - start).total_seconds()
    spec = make_spec(args, title, category, subtitle, footer, segs, width, visible, preview,
                     longest=longest, fill_blank=fill_blank)
    if game_icon:
        spec["game_icon"] = game_icon
    spec["frames"] = [{"out": str(out_png), "splits": splits_done(splits) if done is None else done,
                       "time": max((at - start).total_seconds(), 0.0)}]
    run_lsrender(spec, tmp, args.lsrender)
    if not quiet:
        print(f"→ {out_png}")
    return out_png


def compose(bg: Image.Image, panel_png: Path, size: tuple[int, int],
            sub_timer: str | None = None, overlay: dict | None = None) -> Image.Image:
    """右にタイマーのペーン、左に写真。オーバーレイは載せない。"""
    fw, fh = size
    with Image.open(panel_png) as pn:
        pane = pn.convert("RGB")
        pw = max(1, round(pane.width * fh / pane.height))
        pane = pane.resize((pw, fh), Image.LANCZOS)
    canvas = Image.new("RGB", size, (10, 13, 20))
    if pw < fw:
        canvas.paste(cover(bg, (fw - pw, fh)), (0, 0))
    canvas.paste(pane, (fw - pw, 0))
    d = ImageDraw.Draw(canvas)
    d.line([(fw - pw, 0), (fw - pw, fh)], fill=(255, 255, 255), width=1)   # 継ぎ目
    d.line([(fw - pw - 3, 0), (fw - pw - 3, fh)], fill=(0, 0, 0), width=3)
    if sub_timer:
        f = font(FONT_NUM, int(pw * 0.075))
        d.text((fw - pw + 18, fh - int(pw * 0.105)), sub_timer, font=f, fill=(150, 160, 178))

    if overlay:
        # 写真側に result.png と同じ体裁で総合タイムを載せる
        left_w = fw - pw
        veil = Image.new("RGBA", size, (0, 0, 0, 0))
        vd = ImageDraw.Draw(veil)
        for y in range(int(fh * 0.45), fh):
            t = (y - fh * 0.45) / (fh * 0.55)
            vd.line([(0, y), (left_w, y)], fill=(4, 6, 12, int(215 * t ** 1.2)))
        canvas = Image.alpha_composite(canvas.convert("RGBA"), veil).convert("RGB")
        d = ImageDraw.Draw(canvas)
        x = int(left_w * 0.07)
        d.text((x, fh - 330), overlay.get("label", ""), font=font(FONT_TEXT, 40),
               fill=(210, 218, 232))
        d.text((x, fh - 272), overlay["time"], font=font(FONT_NUM, 150), fill=(255, 255, 255))
        if overlay.get("note"):
            d.text((x, fh - 96), overlay["note"], font=font(FONT_TEXT_R, 34), fill=(186, 196, 212))
    return canvas


def fit_panel_rows(args, splits, start, title, category, subtitle, footer, at, tmp, game_icon,
                   height: int, preview=2) -> int:
    """横幅（＝文字の大きさ）は固定したまま、画像の高さを埋める行数を探す。

    パネルは横幅で拡大率が決まるので、高さを合わせるために伸縮させると文字の
    大きさが変わってしまう。代わりに表示する行数を増減して高さを合わせる。
    """
    unit = args.frame_panel_width / 11.5          # 1 行ぶんの高さ
    n = max(4, round((height / unit) - 8.5))
    probe = args.out / ".probe.png"
    for _ in range(4):
        render_board(args, probe, splits, start, start, title, category, subtitle, footer,
                     args.frame_panel_width, at, tmp, game_icon, visible=n, preview=preview,
                     quiet=True, fill_blank=True)
        with Image.open(probe) as im:
            gap = height - im.height
        if abs(gap) < unit * 0.5 or not (1 <= n + round(gap / unit) <= 64):
            break
        n += round(gap / unit)
    probe.unlink(missing_ok=True)
    return max(1, n)   # 行が足りなければ空行で埋まる。横幅は動かさない


# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser(description="一日の行程を RTA 風の画像にする")
    ap.add_argument("--schedule", type=Path, default=ROOT / "schedule.json")
    ap.add_argument("--photos", type=Path, nargs="*", default=[])
    ap.add_argument("--out", type=Path, default=ROOT / "out")
    ap.add_argument("--blocks", nargs="*", help="使う block id を絞る")
    ap.add_argument("--only", default="now,structured,chapters,board,frames,result",
                    help="作るものをカンマ区切りで指定")
    ap.add_argument("--timebase", default="clock", choices=["clock", "run"],
                    help="clock=時計そのまま(RTC) / run=行程の開始からの経過")
    ap.add_argument("--start", help="計測開始 HH:MM（--timebase run のとき）")
    ap.add_argument("--now", help="現在時刻 HH:MM。既定はいまの時計")
    ap.add_argument("--open", dest="open_block", help="structured で開く章の id。all / none も可")
    ap.add_argument("--manual", type=Path, help='実測の手入力 JSON {"浅草橋 着": "10:27"}')
    ap.add_argument("--slack", type=float, default=45.0,
                    help="行程の前後にはみ出した写真を端の行に寄せる許容分数")
    ap.add_argument("--accuracy", default="seconds", choices=["seconds", "tenths", "hundredths"])
    ap.add_argument("--delta-accuracy", default="seconds",
                    choices=["seconds", "tenths", "hundredths"])
    ap.add_argument("--timer-accuracy", default="hundredths",
                    choices=["seconds", "tenths", "hundredths"])
    ap.add_argument("--timer-height", type=int, default=64)
    ap.add_argument("--frame-size", default="1920x1080")
    ap.add_argument("--panel-width", type=int, default=980, help="ボードの横幅＝文字の大きさ")
    ap.add_argument("--frame-panel-width", type=int, default=490,
                    help="ペーンの横幅＝文字の大きさ。高さは行数で合わせる")
    ap.add_argument("--no-assume", action="store_true",
                    help="写真が 1 枚も無くても「予定どおり通過した」扱いにしない")
    ap.add_argument("--interpolate", action="store_true",
                    help="写真が無いチェックポイントを直前のズレで推定して埋める（* 印）")
    ap.add_argument("--keep-arrivals", action="store_true",
                    help="同じ駅ですぐ発車する「着」の行も残す")
    ap.add_argument("--frame-name", default="index", choices=["index", "time"],
                    help="index=frame000.png（既定） / time=frame-HHMMSS.png")
    ap.add_argument("--icons", default="auto", choices=["auto", "symbol", "photo", "none"],
                    help="auto=顔→写真→グリフ / photo=写真→グリフ / symbol=グリフだけ / none=なし")
    ap.add_argument("--two-rows", action="store_true")
    ap.add_argument("--photo-time", nargs="*", metavar="FILE=HH:MM",
                    help="EXIF が無い写真に撮影時刻を手で与える")
    ap.add_argument("--use-mtime", action="store_true", help="EXIF が無い写真は更新時刻で代用")
    ap.add_argument("--rotate-portrait", default="cw", choices=["cw", "ccw", "none"],
                    help="縦長の写真を横に倒す向き")
    ap.add_argument("--footer-text", help="パネル下の 1 行を差し替える")
    ap.add_argument("--timer-sub", help="大きいタイマーの横に出すもう一方の時間 ({RTA} / {RTC})")
    ap.add_argument("--overlay", help="写真側に総合タイムを載せる。文言に {RTA} {RTC} が使える")
    ap.add_argument("--overlay-label", help="総合タイムの上に出す見出し")
    ap.add_argument("--hero", type=Path, help="背景に使う写真")
    ap.add_argument("--gif", action="store_true")
    ap.add_argument("--layout", type=Path, help="LiveSplit のレイアウトファイル (.lsl)")
    ap.add_argument("--lsrender", type=Path, default=LSRENDER)
    args = ap.parse_args()

    want = {w.strip() for w in args.only.split(",") if w.strip()}
    if not args.lsrender.exists():
        raise SystemExit(f"lsrender が無い: {args.lsrender}\n  cd lsrender && cargo build --release")

    schedule = json.loads(args.schedule.read_text(encoding="utf-8"))
    event = schedule.get("event", {})
    splits = build_splits(schedule, args.blocks)
    if not args.keep_arrivals:
        splits = drop_transfer_arrivals(splits)
    dedupe_names(splits)
    set_windows(splits, splits[0].plan.replace(hour=0, minute=0, second=0, microsecond=0))
    if len(splits) < 2:
        raise SystemExit("スプリットが作れなかった")

    base_day = splits[0].plan.replace(hour=0, minute=0, second=0, microsecond=0)
    schedule_start = splits[0].plan
    start_name = splits[0].name
    if args.timebase == "clock":
        start = base_day  # 0:00 起点にすると通過欄もタイマーも時計そのものになる
    else:
        start = _hhmm(base_day, args.start) if args.start else schedule_start
        if splits[0].plan <= start:  # 開始ちょうどの項目はスタート地点であって区間ではない
            splits.pop(0)

    # 日付をまたいだ後に「00:18」を行程の朝に丸めるとタイマーが巻き戻るので、
    # 開始時刻より前になる場合は翌日として扱う
    if args.now:
        now = _hhmm(base_day, args.now, after=schedule_start)
    else:
        real = datetime.now()
        now = real if real >= schedule_start else _hhmm(
            base_day, real.strftime("%H:%M"), after=schedule_start) + timedelta(
            seconds=real.second, microseconds=real.microsecond)

    out = args.out
    (out / "icons").mkdir(parents=True, exist_ok=True)
    tmp = out / ".spec.json"

    overrides = {}
    for pair in args.photo_time or []:
        name, _, t = pair.rpartition("=")
        overrides[name] = _hhmm(base_day, t)
    photos = collect_photos([Path(p) for p in args.photos], args.use_mtime, base_day, overrides)
    faked = sum(1 for p in photos if p.subsec_random)
    print(f"写真 {len(photos)} 枚 / チェックポイント {len(splits)} 個"
          + (f"（小数秒が無い {faked} 枚は乱数で補完）" if faked else ""))
    orphans = assign(splits, photos, args.slack) if photos else []
    if orphans:
        print(f"  行程の外の写真 {len(orphans)} 枚は使わなかった")
    if args.manual and args.manual.exists():
        pinned = apply_manual(splits, json.loads(args.manual.read_text(encoding="utf-8")), base_day)
        n = drop_conflicts(splits, pinned)
        if n:
            print(f"  手入力と噛み合わない写真由来の実測 {n} 個を外した")
    matched = [s for s in splits if s.actual]
    print(f"実測が付いたチェックポイント {len(matched)} 個")

    if not matched and not args.no_assume:
        for s_ in splits:
            if s_.plan <= now:
                s_.actual = s_.plan
        matched = [s_ for s_ in splits if s_.actual]
        if matched:
            print(f"  写真が無いので、過ぎた {len(matched)} 個は予定どおり通過した扱いにした")

    if args.interpolate and matched:
        delta = timedelta(0)
        for s in splits:
            if s.actual:
                delta = s.actual - s.plan
            elif s.plan <= now:
                s.actual = s.plan + delta  # 直前のズレを引きずった推定値
                if s is not splits[0]:     # スタート地点そのものには印を付けない
                    s.name += " *"
        prev = None
        for s in splits:
            if s.actual and prev and s.actual <= prev:
                s.actual = prev + timedelta(seconds=1)
            prev = s.actual or prev
        matched = [s for s in splits if s.actual]
        print(f"  推定で埋めた {sum(1 for s in splits if s.name.endswith(' *'))} 個 (* 印)")

    for i, s in enumerate(splits):
        # セッションは発表者の顔が一番わかりやすいので、写真より優先する
        avatar = (s.raw.get("session") or {}).get("avatar")
        avatar_path = (ROOT / avatar) if avatar else None
        if avatar_path and avatar_path.exists() and args.icons == "auto":
            s.icon = avatar_icon(avatar_path, s.block_id, out / "icons" / f"spk{i:02d}.png")
        elif s.photo and args.icons in ("auto", "photo"):
            s.icon = make_icon(s.photo, out / "icons" / f"seg{i:02d}.png")
        elif args.icons in ("auto", "symbol"):
            s.icon = symbol_icon(pick_symbol(s.name, s.detail, s.kind, s.block_id),
                                 s.block_id, out / "icons" / f"sym{i:02d}.png")

    title = event.get("name", "一日")
    category = f"{start_name} → {splits[-1].name.removesuffix(' *')}"
    venue = event.get("venue")
    if venue and len(venue) > 20:
        venue = re.split(r"[＆&]", venue)[0].strip()
    elapsed = (now - schedule_start).total_seconds()
    footer = args.footer_text or (
        f"{schedule_start.strftime('%H:%M')} {start_name} スタート"
        f"　/　現在 {now.strftime('%H:%M')}")
    footer = footer.replace("{RTA}", fmt_hms(elapsed)).replace("{RTC}", now.strftime("%H:%M:%S"))
    # 写真フレームは撮影時刻が主役なので「現在」を出さない
    photo_footer = f"{schedule_start.strftime('%H:%M')} {start_name} スタート　/　{event.get('date','')}"

    game_icon = None
    if args.hero and args.hero.exists():
        game_icon = str(make_icon(args.hero, out / "icons" / "game.png", 192))
    elif photos:
        game_icon = str(make_icon(photos[0].path, out / "icons" / "game.png", 192))
    elif args.icons != "none":
        game_icon = str(symbol_icon("flag", splits[0].block_id, out / "icons" / "game.png", 192))

    # --- 章ごとにまとめ、今いる章だけ開く ---
    order: list[str] = []
    groups: dict[str, list[Split]] = {}
    for s in splits:
        if s.block_id not in groups:
            groups[s.block_id] = []
            order.append(s.block_id)
        groups[s.block_id].append(s)

    current = next((s for s in splits if s.plan <= now < (s.window_end or s.plan)), None)
    if current is None:
        current = splits[-1] if now >= splits[-1].plan else splits[0]
    open_block = args.open_block or current.block_id

    def chapter_row(bid: str) -> Split:
        members = groups[bid]
        last = members[-1]
        return Split(name=f"{members[0].block}　{len(members)}項目", plan=last.plan,
                     block=members[0].block, block_id=bid, window_end=last.window_end,
                     actual=last.actual,
                     icon=(symbol_icon(BLOCK_ICON.get(bid, "place"), bid,
                                       out / "icons" / f"block_{bid}.png")
                           if args.icons != "none" else None))

    def structured_for(open_id: str, budget: int | None = None) -> list[Split]:
        """今いる章から順に開いていき、ペーンが埋まるところで止める。

        ペーンの横幅は固定なので、高さは行数でしか埋められない。空行を並べる
        より、近い章を開いて実際の行を出したほうが読める。
        """
        if open_id in ("all", "none"):
            return [x for bid in order
                    for x in (groups[bid] if open_id == "all" else [chapter_row(bid)])]
        idx = order.index(open_id) if open_id in order else 0

        def build(opened):
            return [x for i, bid in enumerate(order)
                    for x in (groups[bid] if i in opened else [chapter_row(bid)])]

        opened = {idx}
        rows = build(opened)
        nearest = sorted((i for i in range(len(order)) if i != idx),
                         key=lambda i: (i < idx, abs(i - idx)))
        for i in nearest:
            if budget is None or len(rows) >= budget:
                break
            opened.add(i)
            rows = build(opened)
        return rows

    STATION = ("depart", "arrive", "transfer")

    def leg_index(rows: list[Split], at: datetime) -> int:
        """その写真がどの行のものか。

        駅は「着いた時刻」なので、そこへ向かっている間の写真はその駅のもの。
        セッションや行動は「始まった時刻」なので、その後の写真がその行のもの。
        手入力は秒まで、写真は 1/100 秒まで持っているので秒で丸めて比べる。
        """
        cut = at.replace(microsecond=0)
        times = [(x.actual or x.plan).replace(microsecond=0) for x in rows]
        prev = -1
        for i, t in enumerate(times):
            if t <= cut:
                prev = i
        if prev >= 0 and times[prev] == cut:
            return prev
        nxt = prev + 1
        if nxt < len(rows) and rows[nxt].kind in STATION:
            return nxt
        return prev if prev >= 0 else 0

    def done_count(rows: list[Split], at: datetime, here: Split | None = None) -> int:
        """LiveSplit の青い行＝これから走る区間。写真を撮った瞬間はその行を
        走っている最中なので、その行を割らずに残して青くする。"""
        k = 0
        for i, x in enumerate(rows):
            if x.actual and x.actual <= at:
                k = i + 1
        if k and (rows[k - 1] is here or (here is None and rows[k - 1].actual == at)):
            k -= 1
        # 章の項目を通過し終えていたら、空行を飛ばして次の章の行を現在地にする
        if k and k < len(rows) and all(x.plan is None for x in rows[k:-1]):
            k = len(rows) - 1
        return k

    structured = structured_for(open_block)
    chapter_title = groups[current.block_id][0].block
    structured_png = out / "structured.png"
    if want & {"structured", "now", "result"}:
        render_board(args, structured_png, structured, start, start, title, chapter_title,
                     venue, footer, args.panel_width, now, tmp, game_icon)

    if "chapters" in want:
        for n, bid in enumerate(order, 1):
            members = groups[bid]
            prev_plan = start
            for s in splits:
                if s is members[0]:
                    break
                prev_plan = s.plan
            render_board(args, out / f"chapter{n}_{slug(members[0].block)}.png", members, start,
                         prev_plan, title, members[0].block, venue, footer, args.panel_width,
                         now, tmp, game_icon)

    if "board" in want:
        render_board(args, out / "board.png", splits, start, start, title, category, venue,
                     footer, args.panel_width, now, tmp, game_icon)

    fw, fh = (int(x) for x in args.frame_size.lower().split("x"))

    # --- 今の一枚（既定 1920x1080） ---
    if "now" in want:
        panel = out / ".panel_now.png"
        rows = fit_panel_rows(args, structured, start, title, chapter_title, venue,
                              footer, now, tmp, game_icon, fh)
        structured = structured_for(open_block, budget=rows)
        render_board(args, panel, structured, start, start, title, chapter_title, venue, footer,
                     args.frame_panel_width, now, tmp, game_icon,
                     visible=rows, preview=2, fill_blank=True,
                     done=done_count(structured, now, current))
        # 「今」の絵なので、直近に撮った写真を背景にする
        bg_path = args.hero if args.hero and args.hero.exists() else (
            current.photo or (photos[-1].path if photos else None))
        bg = (lay_down(load_oriented(bg_path), args.rotate_portrait) if bg_path
              else placeholder((fw, fh), "PLACEHOLDER — 写真が入ればここが背景になる"))
        sub = args.timer_sub
        if sub:
            sub = (sub.replace("{RTA}", fmt_hms((now - schedule_start).total_seconds()))
                      .replace("{RTC}", now.strftime("%H:%M:%S")))
        over = None
        if args.overlay:
            over = {"label": args.overlay_label or "",
                    "time": fmt_hms((now - schedule_start).total_seconds()),
                    "note": args.overlay.replace("{RTA}", fmt_hms((now - schedule_start).total_seconds()))
                                        .replace("{RTC}", now.strftime("%H:%M:%S"))}
        canvas = compose(bg, panel, (fw, fh), sub, over)
        canvas.save(out / "now.png")
        panel.unlink(missing_ok=True)
        print(f"→ {out / 'now.png'} ({fw}x{fh})")

    # --- 写真ごとのフレーム ---
    made: list[Path] = []
    if photos and "frames" in want:
        rows = fit_panel_rows(args, splits, start, title, category, venue, photo_footer,
                              photos[-1].when, tmp, game_icon, fh)
        jobs_named: list[tuple] = []
        for i, p in enumerate(photos):
            here = splits[leg_index(splits, p.when)]
            block_now = here.block_id
            rows_list = structured_for(block_now, budget=rows)
            k = leg_index(rows_list, p.when)
            stamp = p.when.strftime("%H%M%S")
            name = f"frame-{stamp}" if args.frame_name == "time" else f"frame{i:03d}"
            if args.frame_name == "time" and any(x[4] == name for x in jobs_named):
                name += f"-{sum(1 for x in jobs_named if x[4].startswith(name)) + 1}"
            jobs_named.append((None, None, None, None, name))
            panel = out / f".panel{i:03d}.png"
            render_board(args, panel, rows_list, start, start, title,
                         groups[block_now][0].block, venue, photo_footer,
                         args.frame_panel_width, p.when, tmp, game_icon,
                         visible=rows, preview=2, quiet=True, fill_blank=True, done=k)
            dst = out / f"{name}.png"
            compose(load_oriented(p.path), panel, (fw, fh)).save(dst)
            made.append(dst)
            panel.unlink(missing_ok=True)
        print(f"→ フレーム {len(made)} 枚")

        if args.gif:
            imgs = [Image.open(m).convert("RGB").resize((fw // 2, fh // 2), Image.LANCZOS)
                    for m in made]
            imgs[0].save(out / "run.gif", save_all=True, append_images=imgs[1:], duration=1500,
                         loop=0, optimize=True)
            print(f"→ {out / 'run.gif'}")

    # --- 結果画像 ---
    if "result" in want and structured_png.exists():
        hero = args.hero if args.hero and args.hero.exists() else (
            photos[0].path if photos else None)
        bg = (lay_down(load_oriented(hero), args.rotate_portrait) if hero
              else placeholder((fw, fh), "PLACEHOLDER"))
        canvas = Image.new("RGBA", (fw, fh), (8, 10, 16, 255))
        canvas.paste(ImageEnhance.Brightness(
            cover(bg, (fw, fh)).filter(ImageFilter.GaussianBlur(20))).enhance(0.42), (0, 0))
        with Image.open(structured_png) as pn:
            pn = pn.convert("RGBA")
            margin = 56
            if pn.height > fh - 2 * margin:
                scale = (fh - 2 * margin) / pn.height
                pn = pn.resize((int(pn.width * scale), int(pn.height * scale)), Image.LANCZOS)
            px, py = fw - pn.width - 80, (fh - pn.height) // 2
            shadow(canvas, (px, py, px + pn.width, py + pn.height))
            canvas.alpha_composite(pn, (px, py))
        d = ImageDraw.Draw(canvas)
        left = 84
        d.text((left, fh // 2 - 210), event.get("name", ""), font=font(FONT_TEXT, 56),
               fill=(255, 255, 255))
        d.text((left, fh // 2 - 130), category, font=font(FONT_TEXT_R, 34), fill=(198, 208, 224))
        if matched:
            last = matched[-1].actual
            d.text((left, fh // 2 - 40), last.strftime("%H:%M:%S"), font=font(FONT_NUM, 150),
                   fill=(255, 255, 255))
            if len(matched) == len(splits):
                delta = (last - splits[-1].plan).total_seconds()
                col = (63, 212, 94) if delta <= 0 else (224, 75, 75)
                d.text((left, fh // 2 + 140), f"予定差 {fmt_hms(delta, sign=True)}",
                       font=font(FONT_TEXT, 42), fill=col)
            else:
                d.text((left, fh // 2 + 140),
                       f"{len(matched)}/{len(splits)} 通過　{event.get('date','')}",
                       font=font(FONT_TEXT_R, 34), fill=(180, 190, 205))
        canvas.convert("RGB").save(out / "result.png")
        print(f"→ {out / 'result.png'}")


if __name__ == "__main__":
    main()
