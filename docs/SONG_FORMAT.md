# Song body-text format

Church Slides songs are stored as a single text blob with section tags.
The Python parser in `sidecar/auto_presenter/library/parser.py` is the
canonical implementation; the TypeScript parser in `src/lib/parseSong.ts`
is a fast preview mirror that must produce byte-identical results.

## Example

```
[verse 1]
Amazing grace how sweet the sound
That saved a wretch like me

I once was lost but now am found
Was blind but now I see

[chorus]
Grace upon grace now covers me
My shepherd and my Savior leads me home

[verse 2]
'Twas grace that taught my heart to fear
And grace my fears relieved
```

This produces 4 slides:
1. `(verse, 1)` — "Amazing grace how sweet the sound / That saved a wretch like me"
2. `(verse, 1)` — "I once was lost but now am found / Was blind but now I see"
3. `(chorus, 1)` — "Grace upon grace now covers me / My shepherd and my Savior leads me home"
4. `(verse, 2)` — "'Twas grace that taught my heart to fear / And grace my fears relieved"

## Rules

1. **Section tags** are lines whose stripped form matches `[…]`. They go on their own line.
2. **Empty lines** within a section split slides.
3. **Within a slide**, single newlines are preserved as-is.
4. **A new section tag implicitly starts a new slide** even without a blank line.
5. **Content before any section tag** becomes a slide tagged `(other, 1)`.
6. **Whitespace** at the start/end of each line is trimmed; whitespace at the start/end of a slide is trimmed.
7. **Empty section tags** (a tag followed immediately by another tag with nothing between) are skipped — they produce no slide.

## Tag normalization

Tag text inside the brackets is matched case-insensitively, with spaces
and hyphens treated equivalently. The trailing number is optional and
defaults to `1`.

| Input examples | Section type | Section number |
|---|---|---|
| `[verse 1]`, `[Verse 1]`, `[VERSE 1]`, `[v1]`, `[V 1]` | `verse` | 1 |
| `[verse]`, `[v]` | `verse` | 1 |
| `[verse 2]`, `[v2]` | `verse` | 2 |
| `[chorus]`, `[Chorus]`, `[c]` | `chorus` | 1 |
| `[chorus 2]`, `[c2]` | `chorus` | 2 |
| `[bridge]`, `[b]` | `bridge` | 1 |
| `[pre-chorus]`, `[pre chorus]`, `[prechorus]`, `[p]` | `pre-chorus` | 1 |
| `[tag]`, `[t]` | `tag` | 1 |
| `[intro]`, `[i]` | `intro` | 1 |
| `[outro]`, `[ending]`, `[o]` | `outro` | 1 |
| `[instrumental]` | `instrumental` | 1 |
| `[anything else]` | `other` | 1 |

`other` preserves the original tag text in a future "raw_label" field
(not yet used). For v1 we collapse all unknown tags to `other`.

## Slide IDs

Slide IDs are deterministic so that editing a song produces stable IDs
for unchanged slides:

```
slide.id = sha1(song_id || ":" || order_in_song || ":" || lyrics)[:16]
```

Editing the lyrics of slide `N` only changes that slide's ID. Inserting
a slide reshuffles every following slide's ID — this is fine because
the IDs are an in-memory artifact, not a database key (slides are
derived from `body_text`, not stored).

## Tokenization (for the AI matcher)

`Slide.words` is the slide's lyrics stripped of punctuation, lowercased,
split on whitespace. Apostrophes inside words are preserved (so `it's`
stays `it's`, not `its`). Smart quotes (`'`, `"`) are normalized to
ASCII before splitting.

```
"My God, my Shepherd has carried me!" →
  ["my", "god", "my", "shepherd", "has", "carried", "me"]
```

Phase 6's particle filter consumes `Slide.words`. The format must be
stable; changes here invalidate every saved metric in `BASELINE_NUMBERS.md`.

## Chords

Chords never go into `body_text`. A song's chords live in its chart
(`song_charts.chart_json`, see `src/lib/chords/chart.ts`): sections of
stanzas of lines, each line its lyric `text` and the chords placed on it at
character offsets. The projector and the AI read only `body_text`; the band
screens and the song editor align the chart to the slides by their words, so
the two don't have to break lines the same way.

### What the editor's chord text accepts

With chords on, the song editor shows the body merged with its chart as
**chords over lyrics** (`src/lib/chords/editorText.ts`). Every edit is split
back into a plain body and a fresh chart.

```
[verse 1]
G               C          G
Amazing grace how sweet the sound
                      D
That saved a wretch like me

[intro]
G  C  G  D
```

- **Chord lines.** A line of nothing but chords sits above the lyric line
  it belongs to; each chord lands on the character under it.
- **Inline ChordPro** (`A[G]mazing grace`) is still read, for pasting from
  other tools, but the editor writes chords over lyrics.
- **Several chords on one syllable**, such as `G D/F#` above one word, are
  kept in order.
- **Chord-only lines** (an intro, a turnaround) are chart-only: they show
  as their own line and leave nothing in the body. A section that holds
  only chords, like `[intro]` above, is dropped from the body too.
- **Trailing and held chords.** A chord placed after a line's last word
  (`G G G` for three held bars) belongs to that line and plays after it.
- **Nashville numbers.** Typed or spoken numbers (`1`, `4`, `6m`, `5/7`)
  are resolved in the song's key when entered, and stored as letters. The
  editor can show the chart either way.

### Addressing a word

The chord model and voice charting refer to a chord's place as a line id
(`s{slide}-l{line}`, from the parsed slides) plus a **word index**: the
line split on whitespace, counting only tokens with a letter or digit, so
`Holy, holy — holy` is words 0, 1 and 2. The sidecar
(`chords/words.py`) and the editor (`lineWords.ts`) count the same way.
