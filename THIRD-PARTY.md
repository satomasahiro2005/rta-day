# Third-party notices

## livesplit-core

<https://github.com/LiveSplit/livesplit-core> — dual licensed under Apache-2.0 or MIT.
This project links it from `lsrender`, and `lsrender/src/main.rs::layout_units()` is a
transcription of its private `rendering::component::height()`. The MIT notice is
therefore reproduced here.

```
The MIT License (MIT)

Copyright (c) 2013 Christopher Serr and Sergey Papushin

Permission is hereby granted, free of charge, to any person obtaining a copy of this
software and associated documentation files (the "Software"), to deal in the Software
without restriction, including without limitation the rights to use, copy, modify,
merge, publish, distribute, sublicense, and/or sell copies of the Software, and to
permit persons to whom the Software is furnished to do so, subject to the following
conditions:

The above copyright notice and this permission notice shall be included in all copies
or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED,
INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A
PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT
HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF
CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE
OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
```

"LiveSplit" is the name of that project. This repository is not affiliated with it and
does not use its logo.

## Material Icons

<https://github.com/google/material-design-icons> — Apache License 2.0, Copyright Google
LLC. `assets/MaterialIcons-Regular.ttf` and `assets/MaterialIcons-Regular.codepoints`
are redistributed unmodified; the licence text is in
`assets/MaterialIcons-LICENSE.txt`.

## Fonts used at render time

Yu Gothic UI, Segoe UI and Bahnschrift are read from the operating system and are not
included in this repository. Rasterised text in an output image is not a redistribution
of a font.

## Images in this repository

`docs/board.png` and `docs/frame.png` are produced by this tool from
`examples/stores-tech-conf-2026/schedule.json`. They contain no photographs and no
speakers' pictures. Session titles and speaker handles are facts about a public event.

Photographs (`photos/`) and speakers' pictures (`assets/speakers/`) are excluded by
`.gitignore` and are not part of this repository.
