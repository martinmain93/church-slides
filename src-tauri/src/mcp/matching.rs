//! Fuzzy matching for the requests people actually make of an assistant: a
//! pasted list of songs ("None but Jesus - Hillsong (chorus: the one I lean
//! upon…)") to find in the library, and a folder of chord sheets saved
//! under whatever names their authors chose ("WorthyOfItAllBb.pdf",
//! "How Great Is Our God (Chris Tomlin) Chord Chart in G.pdf").
//!
//! Scores are 0..1 and only ever compared with each other — they rank
//! candidates for the model to confirm, they don't decide on its behalf.

use std::collections::HashSet;

/// Lowercase words, apostrophes dropped ("You're" → "youre") so a title
/// typed with or without them reads the same.
pub fn words(s: &str) -> Vec<String> {
    s.to_lowercase()
        .replace(['\'', '’', '‘'], "")
        .split(|c: char| !c.is_alphanumeric())
        .filter(|w| !w.is_empty())
        .map(str::to_string)
        .collect()
}

/// All the letters and digits run together: "WorthyOfItAll" and
/// "Worthy of It All" both become "worthyofitall".
pub fn squashed(s: &str) -> String {
    words(s).concat()
}

/// `squashed`, without a leading "the"/"a": "Heart of Worship" is "The
/// Heart Of Worship".
fn unarticled(s: &str) -> String {
    let w = words(s);
    let skip = usize::from(w.len() > 1 && (w[0] == "the" || w[0] == "a"));
    w[skip..].concat()
}

/// How well a library title answers what the user asked for. The asked text
/// is often longer than the title (an artist, "(Original)") so containment
/// counts as much as equality.
pub fn title_score(asked: &str, title: &str) -> f64 {
    let (a, t) = (unarticled(asked), unarticled(title));
    if a.is_empty() || t.is_empty() {
        return 0.0;
    }
    if a == t {
        return 1.0;
    }
    // "Holy Holy Holy" asked, "Holy Holy Holy (Nicaea)" in the library.
    if t.starts_with(&a) {
        return 0.92;
    }
    // The reverse is weaker the more of the asked title is left over: an
    // artist after the title, or "Holy" for "Holy, Holy, Holy".
    if a.starts_with(&t) {
        return 0.6 + 0.3 * t.len() as f64 / a.len() as f64;
    }
    let aw: HashSet<String> = words(asked).into_iter().collect();
    let tw: HashSet<String> = words(title).into_iter().collect();
    let shared = aw.intersection(&tw).count() as f64;
    // Share of the title's words the user named: an artist they added
    // shouldn't count against a title that's otherwise all there.
    let recall = shared / tw.len().max(1) as f64;
    let precision = shared / aw.len().max(1) as f64;
    0.85 * (0.7 * recall + 0.3 * precision)
}

/// Share of a remembered line's words that appear in the lyrics — the
/// tiebreak between two songs with near-identical titles.
pub fn hint_score(hint: &str, body: &str) -> f64 {
    let hw: Vec<String> = words(hint).into_iter().filter(|w| w.len() > 2).collect();
    if hw.is_empty() {
        return 0.0;
    }
    let bw: HashSet<String> = words(body).into_iter().collect();
    hw.iter().filter(|w| bw.contains(*w)).count() as f64 / hw.len() as f64
}

/// Words a sheet's file name carries that aren't its title.
const FILE_NOISE: &[&str] = &[
    "chords", "chord", "chart", "column", "columns", "copy", "key", "in", "lead", "sheet",
    "full", "final", "new", "mod", "rev", "lyrics", "pdf",
];

/// How well a chord sheet's file name names `title`. File names run words
/// together and add keys, CCLI numbers and "Chord Chart" — so the title
/// squashed inside the name squashed is the strong signal.
pub fn file_score(title: &str, file_stem: &str) -> f64 {
    let t = squashed(title);
    let f = squashed(file_stem);
    if t.len() < 3 || f.is_empty() {
        return 0.0;
    }
    if f.contains(&t) {
        // Penalise names much longer than the title: "Holy" inside
        // "Holy Spirit Come Fill This Place" isn't the song "Holy". Keys,
        // numbers and "Chord Chart" don't count as longer.
        let meaningful: String = words(file_stem)
            .into_iter()
            .filter(|w| w.len() > 2 && !FILE_NOISE.contains(&w.as_str()) && !w.chars().all(|c| c.is_ascii_digit()))
            .collect();
        let extra = meaningful.len().saturating_sub(t.len()) as f64;
        return (1.0 - extra / 60.0).max(0.6);
    }
    let tw: HashSet<String> = words(title).into_iter().collect();
    let fw: HashSet<String> = words(file_stem)
        .into_iter()
        .filter(|w| !FILE_NOISE.contains(&w.as_str()))
        .collect();
    let shared = tw.intersection(&fw).count() as f64;
    0.8 * shared / tw.len().max(fw.len()).max(1) as f64
}

/// The key a sheet's file name gives, the way churches save them:
/// "Holy Holy Holy-C", "Jesus We Love You-chords-Gb", "… Chord Chart in G",
/// "WorthyOfItAllBb", "How-Great-is-Our-God-Key-A".
pub fn key_from_file_name(stem: &str) -> Option<String> {
    let tokens: Vec<&str> = stem
        .split(['-', '_', ' ', '(', ')', '.'])
        .filter(|t| !t.is_empty())
        .collect();
    // From the end, past noise like "copy", "2", "2 Column".
    for tok in tokens.iter().rev().take(4) {
        let lower = tok.to_lowercase();
        if lower.chars().all(|c| c.is_ascii_digit()) || FILE_NOISE.contains(&lower.as_str()) {
            continue;
        }
        if let Some(k) = key_token(tok) {
            return Some(k);
        }
        // The key glued to the end of a run-together title: "WorthyOfItAllBb".
        if tokens.len() == 1 || tok.len() > 3 {
            return glued_key(tok);
        }
        return None;
    }
    None
}

fn key_token(tok: &str) -> Option<String> {
    let mut chars = tok.chars();
    let root = chars.next()?;
    if !('A'..='G').contains(&root) {
        return None;
    }
    let rest = chars.as_str();
    let k = match rest {
        "" | "b" | "#" | "m" | "bm" | "#m" => format!("{root}{rest}"),
        _ => return None,
    };
    Some(k)
}

/// A key written straight after a CamelCase title: the trailing capital
/// (and a flat/sharp) once the title's own last word has ended in lowercase.
fn glued_key(tok: &str) -> Option<String> {
    let chars: Vec<char> = tok.chars().collect();
    let n = chars.len();
    for take in [2, 1] {
        if n <= take + 2 {
            continue;
        }
        let tail: String = chars[n - take..].iter().collect();
        let before = chars[n - take - 1];
        if before.is_lowercase() && tail.starts_with(|c: char| ('A'..='G').contains(&c)) {
            if let Some(k) = key_token(&tail) {
                return Some(k);
            }
        }
    }
    None
}

/// Pitch class of a key's tonic, 0 = C.
pub fn key_pitch(key: &str) -> Option<i32> {
    let mut chars = key.trim().chars();
    let base: i32 = match chars.next()? {
        'C' => 0,
        'D' => 2,
        'E' => 4,
        'F' => 5,
        'G' => 7,
        'A' => 9,
        'B' => 11,
        _ => return None,
    };
    let shift = match chars.next() {
        Some('#') | Some('♯') => 1,
        Some('b') | Some('♭') => -1,
        _ => 0,
    };
    Some((base + shift).rem_euclid(12))
}

/// Semitones from one key to another the short way round (-5..=+6), the way
/// a band says it: "up 2", "down 1".
pub fn semitone_shift(from: &str, to: &str) -> Option<i32> {
    let d = (key_pitch(to)? - key_pitch(from)?).rem_euclid(12);
    Some(if d > 6 { d - 12 } else { d })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn titles_rank_the_song_the_user_meant() {
        assert_eq!(title_score("Holy, Holy, Holy", "Holy Holy Holy"), 1.0);
        assert!(title_score("Holy, Holy, Holy", "Holy Holy Holy (Nicaea)") > 0.9);
        // An artist left in the title still ranks the song first.
        assert!(
            title_score("How great is our God - Chris tomlin", "How Great Is Our God")
                > title_score("How great is our God - Chris tomlin", "Our God")
        );
        assert!(
            title_score("God you’re so good", "God You're So Good")
                > title_score("God you’re so good", "God So Good")
        );
        assert_eq!(title_score("Heart of Worship", "The Heart Of Worship"), 1.0);
        assert!(title_score("Holy, Holy, Holy", "Holy") < 0.75);
        assert!(title_score("Jesus we love you", "Jesus") < 0.75);
        assert!(title_score("Indescribable", "Holy Is The Lord") < 0.1);
    }

    #[test]
    fn a_remembered_line_finds_the_lyrics() {
        assert_eq!(hint_score("over the mountains and the seas", "Over the mountains and the seas"), 1.0);
        assert!(hint_score("when the music fades", "Here I am to worship") < 0.5);
    }

    #[test]
    fn sheet_names_match_their_songs() {
        assert!(file_score("Worthy Of It All", "WorthyOfItAllBb") > 0.8);
        assert!(file_score("None But Jesus", "none-but-jesus-c-brooke-fraser1") > 0.6);
        assert!(file_score("How Great Is Our God", "How Great Is Our God (Chris Tomlin) Chord Chart in G") > 0.6);
        assert!(file_score("Holy", "Holy Spirit Come Fill This Place Chord Chart") < file_score("Holy", "Holy-C"));
        assert_eq!(file_score("Indescribable", "Build-Your-kingdom-here"), 0.0);
    }

    #[test]
    fn keys_come_out_of_file_names() {
        assert_eq!(key_from_file_name("Holy Holy Holy-C").as_deref(), Some("C"));
        assert_eq!(key_from_file_name("Jesus We Love You-chords-Gb").as_deref(), Some("Gb"));
        assert_eq!(key_from_file_name("How Great Is Our God (Chris Tomlin) Chord Chart in G").as_deref(), Some("G"));
        assert_eq!(key_from_file_name("How-Great-is-Our-God-Key-A").as_deref(), Some("A"));
        assert_eq!(key_from_file_name("WorthyOfItAllBb").as_deref(), Some("Bb"));
        assert_eq!(key_from_file_name("none-but-jesus-A copy").as_deref(), Some("A"));
        assert_eq!(key_from_file_name("Fill This Place (27205) - Chord Chart - E - 2 Column").as_deref(), Some("E"));
        assert_eq!(key_from_file_name("I Could Sing Of Your Love Forever"), None);
        assert_eq!(key_from_file_name("The Heart Of Worship"), None);
        assert_eq!(key_from_file_name("I will rejoice"), None);
    }

    #[test]
    fn shifts_go_the_short_way() {
        assert_eq!(semitone_shift("Gb", "G"), Some(1));
        assert_eq!(semitone_shift("A", "G"), Some(-2));
        assert_eq!(semitone_shift("Bb", "C"), Some(2));
        assert_eq!(semitone_shift("C", "F#"), Some(6));
        assert_eq!(semitone_shift("E", "D"), Some(-2));
        assert_eq!(semitone_shift("Em", "Em"), Some(0));
    }
}
