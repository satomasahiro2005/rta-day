//! Renders LiveSplit layouts to PNG frames from a JSON spec.
//!
//! Uses livesplit-core (MIT / Apache-2.0) so the timer, the split list, the
//! delta colours and the fonts are the real thing rather than a lookalike.

use std::{fs, path::PathBuf};

use livesplit_core::{
    component::{
        current_pace, previous_segment,
        splits::{self, ColumnKind, ColumnSettings, ColumnStartWith, ColumnUpdateTrigger,
                 ColumnUpdateWith, TimeColumn},
        text, timer, title,
    },
    layout::{self, ComponentState, Layout, LayoutDirection, LayoutState},
    rendering::software::Renderer,
    run::Segment,
    settings::{Alignment, Color, Font, FontStretch as Stretch, FontStyle as Style,
               FontWeight as Weight, Gradient, Image as LsImage, ListGradient},
    timing::formatter::{Accuracy, DigitsFormat},
    Run, Time, TimeSpan, Timer, TimingMethod,
};
use serde::Deserialize;

const PLAN: &str = "Schedule";

fn d_true() -> bool {
    true
}
fn d_width() -> u32 {
    460
}
fn d_height() -> u32 {
    1080
}

#[derive(Deserialize, Default)]
#[serde(default)]
struct Fonts {
    text: Option<String>,
    times: Option<String>,
    timer: Option<String>,
}

#[derive(Deserialize)]
#[serde(default)]
struct Theme {
    background: Option<[String; 2]>,
    text_color: Option<String>,
    ahead: Option<String>,
    behind: Option<String>,
    gold: Option<String>,
    row_a: Option<String>,
    row_b: Option<String>,
}

impl Default for Theme {
    fn default() -> Self {
        Self {
            background: None,
            text_color: None,
            ahead: None,
            behind: None,
            gold: None,
            row_a: None,
            row_b: None,
        }
    }
}

#[derive(Deserialize)]
struct SegSpec {
    name: String,
    #[serde(default)]
    plan: Option<f64>,
    #[serde(default)]
    actual: Option<f64>,
    #[serde(default)]
    best: Option<f64>,
    #[serde(default)]
    icon: Option<String>,
}

#[derive(Deserialize)]
struct FrameSpec {
    out: String,
    /// How many segments have been split before this frame is drawn.
    splits: usize,
    /// Current game time in seconds. Defaults to the last split time.
    #[serde(default)]
    time: Option<f64>,
    #[serde(default)]
    width: Option<u32>,
    #[serde(default)]
    height: Option<u32>,
}

#[derive(Deserialize)]
#[serde(untagged)]
enum TextSpec {
    Center(String),
    Split([String; 2]),
}

impl TextSpec {
    fn to_text(&self) -> text::Text {
        match self {
            TextSpec::Center(s) => text::Text::Center(s.clone()),
            TextSpec::Split([a, b]) => text::Text::Split(a.clone(), b.clone()),
        }
    }
}

#[derive(Deserialize)]
#[serde(default)]
struct Spec {
    title: String,
    category: String,
    subtitle: Option<TextSpec>,
    footer: Option<TextSpec>,
    game_icon: Option<String>,
    width: u32,
    height: u32,
    auto_height: bool,
    layout_file: Option<String>,
    fonts: Fonts,
    theme: Theme,
    accuracy: String,
    delta_accuracy: String,
    timer_accuracy: String,
    digits_format: String,
    visual_split_count: usize,
    split_preview_count: usize,
    two_rows: bool,
    fill_blank: bool,
    show_icons: bool,
    timer_height: u32,
    segments: Vec<SegSpec>,
    frames: Vec<FrameSpec>,
}

impl Default for Spec {
    fn default() -> Self {
        Self {
            title: String::new(),
            category: String::new(),
            subtitle: None,
            footer: None,
            game_icon: None,
            width: d_width(),
            height: d_height(),
            auto_height: d_true(),
            layout_file: None,
            fonts: Fonts::default(),
            theme: Theme::default(),
            accuracy: "seconds".into(),
            delta_accuracy: "seconds".into(),
            timer_accuracy: "hundredths".into(),
            digits_format: "single_hours".into(),
            visual_split_count: 0,
            split_preview_count: 1,
            two_rows: false,
            fill_blank: false,
            show_icons: true,
            timer_height: 90,
            segments: Vec::new(),
            frames: Vec::new(),
        }
    }
}

fn parse_color(s: &str) -> Color {
    let s = s.trim_start_matches('#');
    let v = u32::from_str_radix(s, 16).unwrap_or(0);
    match s.len() {
        8 => Color::rgba8(
            (v >> 24) as u8,
            (v >> 16) as u8,
            (v >> 8) as u8,
            v as u8,
        ),
        _ => Color::rgba8((v >> 16) as u8, (v >> 8) as u8, v as u8, 0xFF),
    }
}

fn digits_format(name: &str) -> DigitsFormat {
    match name {
        "double_hours" => DigitsFormat::DoubleDigitHours,
        "double_minutes" => DigitsFormat::DoubleDigitMinutes,
        "single_minutes" => DigitsFormat::SingleDigitMinutes,
        _ => DigitsFormat::SingleDigitHours,
    }
}

fn accuracy(name: &str) -> Accuracy {
    match name {
        "tenths" => Accuracy::Tenths,
        "hundredths" => Accuracy::Hundredths,
        _ => Accuracy::Seconds,
    }
}

fn font(name: &Option<String>, weight: Weight) -> Option<Font> {
    name.as_ref().map(|family| {
        let (family, stretch) = match family.strip_suffix(" Condensed") {
            Some(base) => (base.to_string(), Stretch::SemiCondensed),
            None => (family.clone(), Stretch::Normal),
        };
        Font {
            family,
            style: Style::Normal,
            weight,
            stretch,
        }
    })
}

fn time_of(seconds: f64) -> Time {
    let ts = TimeSpan::from_seconds(seconds);
    Time {
        real_time: Some(ts),
        game_time: Some(ts),
    }
}

fn load_icon(path: &str, buf: &mut Vec<u8>) -> Option<LsImage> {
    match LsImage::from_file(path, buf) {
        Ok(image) => Some(image),
        Err(e) => {
            eprintln!("warning: icon {path}: {e}");
            None
        }
    }
}

fn build_run(spec: &Spec) -> Run {
    let mut run = Run::new();
    run.set_game_name(spec.title.as_str());
    run.set_category_name(spec.category.as_str());
    run.set_attempt_count(1);

    let mut buf = Vec::new();
    if let Some(path) = &spec.game_icon {
        if let Some(icon) = load_icon(path, &mut buf) {
            run.set_game_icon(icon);
        }
    }

    let _ = run.add_custom_comparison(PLAN);

    let mut prev_plan = 0.0;
    for s in &spec.segments {
        let mut segment = Segment::new(&*s.name);
        if let Some(plan) = s.plan {
            let t = time_of(plan);
            *segment.comparison_mut(PLAN) = t;
            segment.set_personal_best_split_time(t);
            // Gold = beating the planned duration of this leg.
            let best = s.best.unwrap_or(plan - prev_plan).max(0.0);
            segment.set_best_segment_time(time_of(best));
            prev_plan = plan;
        }
        if let Some(path) = &s.icon {
            if let Some(icon) = load_icon(path, &mut buf) {
                segment.set_icon(icon);
            }
        }
        run.push_segment(segment);
    }
    run
}

fn build_layout(spec: &Spec) -> Layout {
    if let Some(path) = &spec.layout_file {
        let source = fs::read_to_string(path).expect("layout file");
        return layout::parser::parse(&source).expect("parse layout");
    }

    let acc = accuracy(&spec.accuracy);
    let delta_acc = accuracy(&spec.delta_accuracy);
    let mut layout = Layout::new();

    {
        let g = layout.general_settings_mut();
        g.direction = LayoutDirection::Vertical;
        g.text_font = font(&spec.fonts.text, Weight::SemiBold);
        g.times_font = font(&spec.fonts.times, Weight::Normal);
        g.timer_font = font(&spec.fonts.timer, Weight::Bold);
        if let Some([a, b]) = &spec.theme.background {
            g.background = Gradient::Vertical(parse_color(a), parse_color(b));
        }
        if let Some(c) = &spec.theme.text_color {
            g.text_color = parse_color(c);
        }
        if let Some(c) = &spec.theme.ahead {
            g.ahead_gaining_time_color = parse_color(c);
            g.ahead_losing_time_color = parse_color(c);
        }
        if let Some(c) = &spec.theme.behind {
            g.behind_gaining_time_color = parse_color(c);
            g.behind_losing_time_color = parse_color(c);
        }
        if let Some(c) = &spec.theme.gold {
            g.best_segment_color = parse_color(c);
        }
    }

    let mut title_settings = title::Settings::default();
    title_settings.show_attempt_count = false;
    title_settings.show_finished_runs_count = false;
    title_settings.text_alignment = Alignment::Auto;
    layout.push(title::Component::with_settings(title_settings));

    if let Some(sub) = &spec.subtitle {
        let mut s = text::Settings::default();
        s.text = sub.to_text();
        layout.push(text::Component::with_settings(s));
    }

    let mut splits_settings = splits::Settings::default();
    splits_settings.visual_split_count = spec.visual_split_count;
    splits_settings.split_preview_count = spec.split_preview_count;
    splits_settings.always_show_last_split = true;
    splits_settings.fill_with_blank_space = spec.fill_blank;
    splits_settings.separator_last_split = true;
    splits_settings.display_two_rows = spec.two_rows;
    splits_settings.show_column_labels = true;
    splits_settings.split_time_accuracy = accuracy(&spec.accuracy);
    splits_settings.segment_time_accuracy = acc;
    splits_settings.delta_time_accuracy = delta_acc;
    splits_settings.delta_drop_decimals = true;
    if let (Some(a), Some(b)) = (&spec.theme.row_a, &spec.theme.row_b) {
        splits_settings.background = ListGradient::Alternating(parse_color(a), parse_color(b));
    }
    splits_settings.columns = vec![
        ColumnSettings {
            name: "Time".into(),
            kind: ColumnKind::Time(TimeColumn {
                start_with: ColumnStartWith::ComparisonTime,
                update_with: ColumnUpdateWith::SplitTime,
                update_trigger: ColumnUpdateTrigger::OnEndingSegment,
                comparison_override: None,
                timing_method: None,
            }),
        },
        ColumnSettings {
            name: "+/−".into(),
            kind: ColumnKind::Time(TimeColumn {
                start_with: ColumnStartWith::Empty,
                update_with: ColumnUpdateWith::Delta,
                update_trigger: ColumnUpdateTrigger::Contextual,
                comparison_override: None,
                timing_method: None,
            }),
        },
    ];
    layout.push(splits::Component::with_settings(splits_settings));

    let mut prev = previous_segment::Settings::default();
    prev.accuracy = delta_acc;
    prev.drop_decimals = true;
    layout.push(previous_segment::Component::with_settings(prev));

    let mut pace = current_pace::Settings::default();
    pace.accuracy = acc;
    layout.push(current_pace::Component::with_settings(pace));

    if let Some(footer) = &spec.footer {
        let mut s = text::Settings::default();
        s.text = footer.to_text();
        layout.push(text::Component::with_settings(s));
    }

    let mut timer_settings = timer::Settings::default();
    timer_settings.height = spec.timer_height;
    timer_settings.accuracy = accuracy(&spec.timer_accuracy);
    timer_settings.digits_format = digits_format(&spec.digits_format);
    layout.push(timer::Component::with_settings(timer_settings));

    layout
}

/// livesplit-core lays components out in units where a normal row is 1.0 and the
/// nominal window is 11.5 wide. The renderer scales everything by
/// height / total_height, so the height has to follow the width or the timer
/// digits end up wider than the panel. `rendering::component::height` is
/// private, so the same arithmetic lives here.
const TWO_ROW_HEIGHT: f32 = 2.0 * 0.725 + (1.0 - 0.725);
const PSEUDO_PIXELS: f32 = 1.0 / 24.0;
const DEFAULT_VERTICAL_WIDTH: f32 = 11.5;

fn layout_units(state: &LayoutState) -> f32 {
    state
        .components
        .iter()
        .map(|c| match c {
            ComponentState::BlankSpace(s) => s.size as f32 * PSEUDO_PIXELS,
            ComponentState::DetailedTimer(_) => 2.5,
            ComponentState::Graph(s) => s.height as f32 * PSEUDO_PIXELS,
            ComponentState::KeyValue(s) => if s.display_two_rows { TWO_ROW_HEIGHT } else { 1.0 },
            ComponentState::Separator(_) => 0.1,
            ComponentState::Splits(s) => {
                s.splits.len() as f32 * if s.display_two_rows { TWO_ROW_HEIGHT } else { 1.0 }
                    + if s.column_labels.is_some() { 1.0 } else { 0.0 }
            }
            ComponentState::Text(s) => if s.display_two_rows { TWO_ROW_HEIGHT } else { 1.0 },
            ComponentState::Timer(s) => s.height as f32 * PSEUDO_PIXELS,
            ComponentState::Title(_) => TWO_ROW_HEIGHT,
        })
        .sum()
}

/// Replays the attempt up to `upto` splits and parks the game timer at `now`.
fn play(timer: &mut Timer, spec: &Spec, upto: usize, now: Option<f64>) {
    timer.reset(false);
    timer.start();
    timer.initialize_game_time();
    timer.pause_game_time();

    let mut last = 0.0;
    for seg in spec.segments.iter().take(upto) {
        match seg.actual {
            Some(t) => {
                timer.set_game_time(TimeSpan::from_seconds(t));
                timer.split();
                last = t;
            }
            // No photo and no recorded time: keep the row, leave it blank.
            None => timer.skip_split(),
        }
    }

    if let Some(t) = now.or(if upto > 0 { Some(last) } else { None }) {
        timer.set_game_time(TimeSpan::from_seconds(t));
    }
}

/// tiny-skia renders premultiplied alpha; undo that before writing the PNG so
/// the panel composites correctly over a photo.
fn save_png(renderer: &Renderer, width: u32, height: u32, path: &str) {
    let mut data = renderer.image_data().to_vec();
    for px in data.chunks_exact_mut(4) {
        let a = px[3] as u32;
        if a != 0 && a != 255 {
            for c in &mut px[..3] {
                *c = ((*c as u32 * 255 + a / 2) / a).min(255) as u8;
            }
        }
    }
    image::save_buffer(path, &data, width, height, image::ColorType::Rgba8).expect("save png");
}

fn main() {
    let mut args = std::env::args().skip(1);
    let spec_path = PathBuf::from(args.next().expect("usage: lsrender <spec.json>"));
    let spec: Spec =
        serde_json::from_str(&fs::read_to_string(&spec_path).expect("read spec")).expect("parse spec");

    let mut timer = Timer::new(build_run(&spec)).expect("build timer");
    timer.set_current_timing_method(TimingMethod::GameTime);
    let _ = timer.set_current_comparison(PLAN);

    let mut layout = build_layout(&spec);
    let mut renderer = Renderer::new();

    for frame in &spec.frames {
        play(&mut timer, &spec, frame.splits, frame.time);
        let state = layout.state(&timer.snapshot());

        let width = frame.width.unwrap_or(spec.width);
        let height = match frame.height {
            Some(h) => h,
            None if spec.auto_height => {
                (width as f32 * layout_units(&state) / DEFAULT_VERTICAL_WIDTH).round() as u32
            }
            None => spec.height,
        };
        renderer.render(&state, [width, height.max(1)]);

        save_png(&renderer, width, height, &frame.out);
        println!("{} {}x{}", frame.out, width, height);
    }
}
