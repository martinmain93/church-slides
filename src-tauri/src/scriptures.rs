//! How scripture is shown: the display settings for live passages (the
//! operator's Scripture tab and passages asked for out loud).
//!
//! Persisted as one nested value inside the settings blob. Passages attached
//! to songs were removed on 2026-10-19 (research/dead_ends/song_scripture);
//! their table, `song_scriptures` (migration 005), stays because shipped
//! installs have it, and nothing reads it. A saved `enabled` (the old
//! song-passage switch) is ignored on load.

use serde::{Deserialize, Serialize};

fn default_true() -> bool {
    true
}

/// Everything about *how* scripture is shown. Persisted as one nested value
/// inside the existing settings blob, so `settings.rs` grows exactly one field
/// no matter how many display options this feature accumulates.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ScriptureSettings {
    /// Where the pane sits: "bottom" | "top" | "left" | "right".
    ///
    /// Lower third is the default because it needs no lyric reflow at all,
    /// and because it is what broadcast does for exactly this problem.
    #[serde(default = "default_position")]
    pub position: String,
    /// "small" | "medium" | "large" — a semantic scale, not a number.
    ///
    /// Deliberately not a raw font size: the pane is measured in container
    /// units, so one number means two very different things in a 30% sidebar
    /// and a full-width lower third (measured: 18px vs 32px on the same
    /// stage). The renderer resolves the scale per position.
    #[serde(default = "default_size")]
    pub size: String,
    /// "sans" | "serif".
    #[serde(default = "default_font")]
    pub font: String,
    #[serde(default = "default_true")]
    pub show_reference: bool,
    #[serde(default = "default_true")]
    pub show_translation: bool,
    /// Translation id from the sidecar's public-domain catalog.
    #[serde(default = "default_translation")]
    pub translation: String,
    /// Seconds a cue is protected from replacement. **0 by default, on
    /// evidence**: measured over 19 minutes of real audio, following the slide
    /// directly matches the human change rate and flashes once, while an 8s
    /// floor removes that flash and costs 2.2 points of accuracy. Kept because
    /// the freeze and lost-mode episodes it was designed for are absent from
    /// the goldens that were measured. See docs/done/BIBLE_INTEGRATION.md §3.
    #[serde(default)]
    pub dwell_floor_seconds: f32,
}

fn default_position() -> String {
    "bottom".to_string()
}

fn default_size() -> String {
    "medium".to_string()
}

fn default_font() -> String {
    "sans".to_string()
}

fn default_translation() -> String {
    "web".to_string()
}

impl Default for ScriptureSettings {
    fn default() -> Self {
        Self {
            position: default_position(),
            size: default_size(),
            font: default_font(),
            show_reference: true,
            show_translation: true,
            translation: default_translation(),
            dwell_floor_seconds: 0.0,
        }
    }
}
