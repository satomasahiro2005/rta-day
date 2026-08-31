# rta-day

一日の行程と写真から、RTA 配信のタイマーが乗った画像を作る。

タイマーは本物の **LiveSplit**（[livesplit-core](https://github.com/LiveSplit/livesplit-core), MIT / Apache-2.0）。
見た目を真似た自作描画ではないので、区間の色分け（金 / 緑 / 赤）も Previous Segment も
Current Pace も LiveSplit の計算そのまま。

```
schedule.json ──┐
                ├─→ rta_day.py ──→ spec.json ──→ lsrender (Rust, livesplit-core) ──→ パネル PNG
写真の EXIF ────┘                                                                      │
                                                       写真に合成 ←─────────────────────┘
```

## 使い方

```powershell
python rta_day.py                                  # 今の時刻で now.png ほか一式
python rta_day.py --now 14:30 --only now           # 時刻を指定して 1 枚だけ
python rta_day.py --photos "D:\DCIM\Camera" --gif   # 写真を食わせて全部
```

初回だけ Rust 側のビルドが要る。

```powershell
cd lsrender; cargo build --release
```

## 出力（既定は `out/`）

| ファイル | 中身 |
| --- | --- |
| `now.png` | **1920x1080**。現在地のフレーム。写真が無ければプレースホルダー背景 |
| `structured.png` | 章は畳んで、今いる章だけ開いたボード |
| `chapterN_*.png` | 章ごとの詳細 |
| `board.png` | 全チェックポイント通し（32 行）。参考用 |
| `frameNNN.png` | 写真 1 枚につき 1 枚の配信フレーム |
| `result.png` | 背景写真 + ボード + 到着時刻 |
| `run.gif` | `--gif` のときだけ |

`--only now,structured` のように絞れる。`--frame-size 1280x720` で寸法変更。

## 時間の見せ方

既定は `--timebase clock`（**RTC**）。0:00 を起点にするので、タイマーも「通過」欄も
そのまま時計の時刻になる。`--timebase run` にすると行程の開始（05:35）からの経過時間になる。

`--timer-accuracy hundredths`（既定）で 1/100 秒まで出る。EXIF に小数秒が無い機種では
ファイル名から作った固定の乱数でミリ秒を埋める（再実行しても同じ値）。全部 `.00` で
揃っていると嘘くさいため。

## 構造化

章（`blocks`）ごとに畳み、**今いる章だけ開く**。`structured.png` と `now.png` の
右パネルがこれ。開く章は `--open <block id>` で固定でき、`--open all` / `--open none` も可。

## 写真とチェックポイントの結び方

行 i の受け持ちは `[plan_i, plan_(i+1))`。写真は**その時間帯の行に入れる**（一番近い行を
探すのではない）。行の実測時刻は、その時間帯に入った最初の写真の時刻。1 行に何枚入ってもよく、
最初の 1 枚がその行のアイコンになる。行程の前後にはみ出した写真は `--slack`（既定 45 分）
だけ端の行に寄せ、それでも外なら捨てる。

- 写真が 1 枚も無いときは、過ぎたチェックポイントを「予定どおり通過した」扱いにする
  （`--no-assume` で切れる）。予定表として読めるようにするため。
- 写真はあるが撮り漏らした行は空欄のまま。`--interpolate` を付けると直前のズレを
  引きずった推定値で埋め、行名に `*` が付く。
- 手で直すなら `--manual times.json`:

```json
{ "浅草橋 着": "10:27", "受付": "11:31" }
```

## 行程の書き方

`schedule.json` の `blocks[].items[]` がそのままチェックポイントになる。
時刻は `time`（点）か `start`（範囲の開始）を見る。

| キー | 効果 |
| --- | --- |
| `short` | スプリット名。無ければ `type` と `station` から自動生成（`浜松 発` / `静岡 着`） |
| `title` | `short` と違えば補足行に回る |
| `type` | `depart` / `arrive` / `transfer` / その他 |
| `line` `destination` `platform` `location` `action` `session` | フレームの補足行に並ぶ |

`--blocks outbound` のように id で絞れる。行程を差し替えれば別の日にそのまま使える。

## アイコン

各行の左に付くアイコンは、優先順に **写真 → 発表者の顔 → Material Icons のグリフ**。
どれも章の地色のタイル（往路=青 / カンファレンス=橙 / 復路=紫）に載せる。
`--icons symbol` でアイコンだけ、`--icons photo` で写真のある行だけ、`--icons none` で無し。

- **発表者の顔**は公式サイト（storesinc.tech/conf/2026）のタイムテーブルから取ってきて
  `assets/speakers/` に置いてある。`schedule.json` の `session.avatar` で行に紐づく。
  サイトは STUDIO 製で、顔は `<img>` ではなく `div.image` 内の `<style>` の
  `:before { background-image: url(...) }`。DOM を読むだけでは出てこないので、
  要素を画面内にスクロールしてから style を読む。
- **グリフ**は Material Icons（Apache-2.0、`assets/` に同梱）。選び方は `rta_day.py` の
  `NAME_RULES`（行の名前だけを見る）と `TEXT_RULES`（補足文まで見る）。
  どれにも当たらなければ `type`（`depart` 電車 / `arrive` ピン / `transfer` 矢印）、
  最後は章の既定。行程を差し替えたらここに 1 行足せば付く。

## 文字の大きさ

`--panel-width`（既定 980）がそのまま文字の大きさ。パネルの高さは livesplit-core の
レイアウト単位（1 行 = 1.0、横幅 = 11.5）から計算するので、幅を変えても比率は崩れない。
記事に貼るなら 980〜1400。`now.png` の中のパネルは `--frame-panel-width`（既定 620）。

タイマーの数字だけは幅に合わせて縮んでくれないので、桁数から高さを決めている
（`--timer-height` が上限）。

## 覚え書き

- `lsrender/src/main.rs` の `layout_units()` は livesplit-core の
  `rendering::component::height()` の写し。あちらが private なので同じ式を持っている。
  livesplit-core を上げたときはここを確認する。
- 予定差の `−` は U+2212。Bahnschrift に無いので数字用フォントは Segoe UI。
- `--layout foo.lsl` で LiveSplit のレイアウトファイルをそのまま読める。
- `tools/make_test_photos.py <dir>` で EXIF 付きのダミー写真を作れる。
