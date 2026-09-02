# STORES Tech Conf 2026 "World 2"

The real thing this tool was written for: a day trip from Hamamatsu to a conference in
Tokyo and back, 106 photos, one boards-and-frames set. The article built from these
images is here:

**https://zenn.dev/nemut_ai/articles/stores-tech-conf-2026-world2**

## Files

| File | What it is |
| --- | --- |
| `schedule.json` | The itinerary as it actually ran: trains, sessions, the way home, and writing the article |
| `times.json` | Checkpoints pinned by hand — the trains taken and the moments read off the photos |

## Commands used

```bash
# HEIC straight off the phone → JPEG, EXIF kept
python tools/heic2jpg.py "G:/My Drive/World2" photos/world2 --max 3200

# every board and frame
python rta_day.py \
  --schedule examples/stores-tech-conf-2026/schedule.json \
  --photos photos/world2 \
  --manual examples/stores-tech-conf-2026/times.json \
  --interpolate

# the finish card, with the elapsed time overlaid on the photo
python rta_day.py --schedule examples/stores-tech-conf-2026/schedule.json \
  --photos photos/world2 --manual examples/stores-tech-conf-2026/times.json \
  --interpolate --only now --hero photos/world2/IMG_0367.jpg \
  --overlay-label "起床からの経過" --overlay "RTA {RTA} / clock {RTC}"
```

The photos and the speakers' pictures are not in this repository. The `session.avatar`
paths in `schedule.json` point at files that were downloaded locally from the event's
own site; without them the rows fall back to icon glyphs.

## What the times mean

Departure checkpoints look late if you take them from photos, because you photograph
the train after boarding. The four in `times.json` that are not from photos are the
trains actually taken (`小田原駅 08:53`, `新宿駅 10:30`) and two moments read off the
frames (`浅草橋駅 10:52:58`, `受付 10:56:01`). Everything else came from EXIF, and
`--interpolate` filled the six checkpoints with no photo.

Final time: 23:55:37 against a plan of 23:17, so +38:37 — most of it lost by missing
one Odakyu express at Odawara.
