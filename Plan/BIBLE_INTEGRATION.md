# Scripture alongside the songs

Spike opened 2026-09-09. **Built 2026-09-10** — see "As built" at the end for
what shipped and what deliberately did not.

    uv run --project sidecar python -m pytest sidecar/tests/scripture -q
    pnpm vitest run src/__tests__/scripture*

    uv run --project sidecar python research/dead_ends/song_scripture/spike_scripture.py --fetch
    uv run --project sidecar python research/dead_ends/song_scripture/spike_scripture.py --lexical "Cornerstone"
    uv run --project sidecar python research/dead_ends/song_scripture/spike_scripture.py --suggest "Build My Life"
    uv run --project sidecar python research/dead_ends/song_scripture/spike_scripture.py --audit 20

Five asks, from the request that opened this:

1. songs carry scriptures, attached to particular lines or sections
2. a mode that shows them beside/above/below the lyrics, on **its own timeline**
3. a small set of formatting options
4. an AI/search module that suggests verses from song content
5. a `show_scripture` tool the live LLM can call, with full-screen/sidebar,
   translation, and multiple verses in series

They are not equally risky. (1) and (3) are ordinary schema and CSS work. (2)
is a design problem — the interesting one, because the timeline it hangs off is
*jittery* (see "The flicker is not a rendering problem"). (4) had a real
unknown, now measured. (5) is small to build and re-opens a question the
spoken-instructions work had already closed by measurement.

## Definitions

| Term | Meaning |
|---|---|
| **Passage** | One or more consecutive verses under one reference: `Psalm 103:1-5`. |
| **Anchor** | What ties a passage to a place in a song — a lyric line, not a slide id. See "Slide ids are not keys". |
| **Cue** | A resolved anchor: one passage plus the *span* of slides it stays up for. |
| **Track** | The ordered list of cues for one song. The second timeline. |
| **Span** | The slide range a cue owns, from its own anchor to the next cue's. |
| **Dwell floor** | The minimum time a cue stays up regardless of what the slide engine does. The anti-flicker mechanism. |

---

## 1. The licence decides the architecture

This is the finding that shapes everything else, so it goes first.

**Public-domain translations can be redistributed inside the app. The
translations most congregations actually read cannot.** NIV, ESV, NLT, NASB,
CSB and their peers are individually licensed; they are reachable through
[API.Bible](https://api.bible/), which requires attribution on the page the
text appears on and tracks usage through its Fair Use Management System, with
commercial licensing from around $10/month per translation and separate
non-commercial terms. A live per-lookup API call is not something to put in the
service-time path anyway — the standing constraint (`constraint_shipping`) is
that a worship service is where the wifi fails and the failure is public.

So the design splits, and the split is not a compromise — it is the only shape
that survives both the licence and the network:

- **Bundled, offline, always available**: one or more public-domain
  translations, downloaded once at install time into SQLite next to
  `library.db`. WEB, KJV, ASV are confirmed fetchable as a single JSON file
  each; BSB (public domain since 2023, and much the most readable of them) is
  available through the [Free Use Bible API](https://bible.helloao.org/), which
  states no copyright restrictions including commercial use.
- **Optional, licensed, online, editor-only**: if an operator wants NIV text,
  it is fetched *while they are preparing the setlist*, cached against that
  setlist, and never fetched during a service. Any live call is a bug.

`--fetch` proves the bundled half end to end:

| translation | download | verses | on disk (with FTS index) |
|---|---|---|---|
| WEB | 8.8 MB JSON, 0.6 s | 31,095 | **14.5 MB** |

14.5 MB per translation is affordable; it is also mostly the FTS index, which
is only needed for the lexical search in §4 and could be dropped for
translations that are display-only.

A second free dataset worth having: [OpenBible.info's cross
references](https://www.openbible.info/labs/cross-references/), ~340,000 links
derived largely from the public-domain *Treasury of Scripture Knowledge*, under
CC-BY. That is the offline "related verses" expansion for §4 — no model, no
network, one attribution line.

**Not settled**: which translation ships as the default. BSB reads best but
comes from a different source than the getbible.net files `--fetch` currently
uses; WEB is what the spike measured against. That is an hour of work, not a
risk.

---

## 2. Slide ids are not keys — anchor to the words

The obvious schema attaches a passage to a slide id. It would be wrong, and
`docs/SONG_FORMAT.md` says why in its own words: slide ids are
`sha1(song_id : order_in_song : lyrics)`, so **inserting one slide reshuffles
the id of every slide after it**. They are explicitly "an in-memory artifact,
not a database key." A scripture attached to slide 6 of *Cornerstone* would
silently detach the first time someone fixes a typo in slide 2.

Three further facts push the same way:

- The playing sequence is not the stored song. `resolveSlideSequence` expands
  `slide_order` (`V1, C, V2, C…`), so one stored chorus becomes several played
  slides, and `setlist_items.slides_override` can replace the list entirely for
  one setlist.
- The live sequence carries a synthetic leading blank slide
  (`leadingBlank: true`), so playback indices are offset from parser indices.
- `ResolvedSlide.sourceId` is stable across repeats — which is the hook that
  makes "the same verse every time this chorus comes round" expressible.

**So an anchor is a lyric line plus its section key**, resolved to slide
positions at load time, exactly as `spike_scripture.py`'s `anchor_to_slide`
does. It survives edits, repeats, overrides and the blank-slide offset, and it
is the same thing the suggester already produces (§4 requires the model to
quote the line verbatim, which is what makes this free).

Resolution rules, in order: exact normalized-text match within the section →
exact match anywhere in the song → unresolved, and an unresolved anchor is
shown to the operator in the editor rather than silently dropped.

### Schema

```sql
-- 005_song_scriptures.sql
CREATE TABLE song_scriptures (
  id           TEXT PRIMARY KEY,
  song_id      TEXT NOT NULL REFERENCES songs(id) ON DELETE CASCADE,
  reference    TEXT NOT NULL,          -- canonical: "Psalms 103:1-5"
  translation  TEXT,                   -- NULL = follow the service default
  section_key  TEXT,                   -- "chorus:1"; NULL = anywhere in the song
  anchor_lyric TEXT,                   -- verbatim line; NULL = whole section/song
  every_repeat INTEGER NOT NULL DEFAULT 1,  -- fire on each repeat, or only the first
  position     INTEGER NOT NULL,       -- order among this song's scriptures
  source       TEXT NOT NULL,          -- 'manual' | 'suggested'
  strength     TEXT,                   -- 'quotes' | 'alludes' | 'thematic'
  note         TEXT
);
CREATE INDEX song_scriptures_song ON song_scriptures(song_id, position);
```

Mirrored into Pydantic (`library/types.py` → `ScriptureRef`) so `pnpm codegen`
carries it into TypeScript; that is the established path and the one that keeps
frontend copies from drifting — a failure this repo has already had three times
(`bug_slider_clamped_lead_time`).

Setlist-scoped scriptures (a passage for *this service only*) fit the existing
`slides_override` precedent and should follow it rather than inventing a second
mechanism — but they are not needed for a first cut.

---

## 3. The flicker is not a rendering problem

The request says the verses must not flicker with every slide switch. Two
distinct causes, and only one of them is about slide switches.

**Cause 1 — the honest one.** Consecutive slides often share a verse. Solved by
spans: a cue owns the slide range from its own anchor to the next cue's anchor,
so advancing within a span is not a change at all. The audience surface is
keyed on the cue id, so React does nothing.

**Cause 2 — the one that will actually bite.** *This app's slide timeline is
jittery on purpose.* It is driven by a probabilistic matcher that jumps
backwards when it corrects itself, oscillates in lost mode, and freezes (`live_reckless_love_trace`, `diagnosis_three_live_faults`). A scripture pane
that reacts to slide index faithfully will inherit every one of those
corrections and strobe.

So the track needs its own hysteresis, and it should be *deliberately less
responsive than the lyrics*:

- **Dwell floor.** Once a cue is up it stays up for a minimum wall-clock time
  (start at 8 s) regardless of slide movement. A correction that lands inside
  the floor updates the *pending* cue, not the displayed one.
- **No backward flicker.** A backward slide move inside the same song does not
  retire a cue that is still within its floor — the common cause of backward
  movement is the matcher correcting itself, not the band repeating.
- **Song change clears immediately.** The one case where holding is wrong.
- **Confidence gate.** The `SlideChangeMessage` already carries `confidence`;
  a low-confidence AI move should not be allowed to change scripture at all.
  Operator overrides and voice commands (`source: "override" | "voice"`) bypass
  every rule above — explicit human intent, the same authority split
  `Director.on_override` already makes.

That was the design going in. It is testable without a renderer: a pure `src/lib/scripture/track.ts` (`buildTrack`, `cueAt`,
`advance`) in the shape of `presentation/state.ts`, with the golden recordings'
`decisions` streams replayed through it to count how often the pane *would*
have changed. There is an existing, better-than-guessing way to pick the dwell
floor: replay a golden, and tune until the scripture track changes about as
often as a human operator's ground-truth transitions do, not as often as the
AI's.

`research/dead_ends/song_scripture/spike_scripture_track.py` is that harness, and it scores two ways at
once so that the obvious cheat is visible: **changes/min** (how busy the pane
is, with the human timeline as the target rather than zero) and **wrong-verse**
(fraction of wall-clock time the displayed cue isn't the one the human timeline
puts there — the direct analogue of `wrong-slide`). A rule that lowers the
first while raising the second has made the pane calm and wrong.

### Measured, and the premise did not survive

Scored on a fresh `run_realtime_eval.py` pass over the four shortest approved
goldens (19 minutes of real audio, `tiny.en`, dense streams — 33 AI moves
against 36 human transitions on the longest one), with a synthetic cue every 3
slides. A **flash** is a cue replaced within 3 s of appearing; it is the metric
the request actually asked about, because an average rate hides exactly the
burst that people in a room would call flicker.

| rule | changes/min | wrong-verse | flashes |
|---|---|---|---|
| human timeline (target) | 2.4 | 0.000 | 0 |
| follow the slide | **2.4** | **0.435** | **1** |
| + dwell floor 8 s | 2.0 | 0.457 | 0 |
| + floor, backward-only hold | 2.2 | 0.444 | 0 |

**Spans alone are enough. The dwell floor is not justified.** Following the
displayed slide directly produces a pane that changes at exactly the human
rate (2.4/min vs 2.4/min) and flashes **once in nineteen minutes**. The dwell
floor removes that single flash and pays for it by being wrong 2.2 points more
of the time — the calm-and-incorrect trade the second metric exists to expose.

The reason is worth stating, because it is not what §3 assumed: the matcher's
corrections are mostly *small*, and a small correction usually lands inside the
span the pane is already showing. Span lookup absorbs the jitter for free. The
earlier draft of this section had the dwell floor as "the whole feature's real
content"; the measurement says it is a switch to leave off.

Three limits on that conclusion, in order of how much they should worry a
reader:

- **The pathological live behaviour is not in this sample.** The 51-second
  freeze and the lost-mode oscillation from `live_reckless_love_trace` are the
  cases a dwell floor would actually earn its keep on, and none of these four
  goldens contains one. Keep the rule implemented and off, not deleted.
- `tiny.en` is noisier than the production engine, and the synthetic track is
  denser than a real one. Both make this the *pessimistic* case for flicker,
  which is the right direction for a negative result.
- Four recordings, one operator, one band.

### Series (multiple verses on one slide)

The request's "sometimes multiple verses could be shown for one slide" is a
*cue group*: several passages sharing one span. They page on a timer inside the
span (dwell floor per passage), never on slide advance — otherwise the group
collapses back into the flicker case. Same mechanism serves §5's "multiple
verses in series", which is the same object arriving from a tool call instead
of from the song.

### Where it renders

`SlideStage` today is a flex box with `text-[clamp(2rem,5vw,5rem)]`. A side
pane breaks that: `vw` is the *window*, so lyrics keep sizing for the full
width while living in 70% of it. The stage becomes a two-region grid and the
lyric clamp moves to container units (`cqw`) with `container-type: inline-size`
on the lyric region. Small change, easy to get subtly wrong, worth naming.

Mocked and checked in a browser before being built (a scratch file, since
replaced by the real `ScripturePane`) across all five positions with real
lyrics and real WEB text.
Two things came out of looking at it:

- **The container-query fix works.** Switching from lower-third to a 30%
  sidebar re-sizes the lyric type from 60 px to 40 px on its own. With the
  current `vw` clamp it would have stayed at 60 px and overrun the region.
- **The font-size knob cannot be one number.** Expressed in container units,
  "1.9" is comfortable in a lower third and nearly unreadable in a sidebar
  (measured: 18 px vs 32 px on the same stage). The setting should be a
  semantic scale — small / medium / large — resolved to a different value per
  position, not a raw slider whose meaning changes underneath the operator.
- Lower-third is the better default on looking at it: it needs no lyric
  reflow at all, and it is what broadcast does for exactly this problem.

`SlidePayload` (operator → audience, `presentation/projection.ts`) gains a
`scripture: ScriptureCue | null`. The audience window stays a dumb renderer;
the operator window keeps owning the track state, exactly as it owns slide
state today.

---

## 4. Suggestion: measured, and it works

Two finders, measured against the user's own 312-song library and the WEB text.

### Lexical BM25 — free, offline, and only finds quotations

Whole-song FTS query over the verse table, top-5:

| song | expected | found at rank |
|---|---|---|
| Every Good and Perfect Gift | James 1:17 | **1** |
| Death Where Is Your Sting | 1 Cor 15:55 | **1** |
| 10,000 Reasons | Psalm 103:1 | not in top 5 (returned Ps 144/101/149) |
| Blessed Be Your Name | Job 1:21 | not in top 5 |
| Cornerstone | Isaiah 28:16 / Ps 62 | not in top 5 |
| Build My Life | Matthew 7:24 | not in top 5 |

Exactly the expected shape: it hits when the song quotes the verse nearly
verbatim and is blind otherwise. Keep it — it costs nothing, it works offline,
and it is the right tool for the editor's "find this line in scripture" box.
Do not build the suggester on it.

### Hosted LLM with a verification gate — good enough to ship as a *proposal*

One tool, arguments only, `temperature=0` — the same shape
`decision.instructions` already uses, against the Gemini key already saved in
`api_keys.json`. The model must return, per suggestion, a reference **and the
song line it sits behind, copied verbatim**. Then both halves are checked
locally: the reference is resolved against the verse table, and the lyric is
matched back against the song's own slides.

Across 20 songs of the library, `gpt-oss-120b` on Groq at ~3 s per song
(`gemini-3.5-flash` gives comparable output at ~11 s, and hit its free-tier
daily cap mid-audit). Two passes: 113 suggestions with 4 flagged, then — after
one schema tightening and two bug fixes, all described below — **109
suggestions, 0 unresolvable references, 0 unanchorable lyrics.**

- **No invented scripture, in either pass.** The one genuinely bad suggestion
  was not a hallucination: `"Psalm 3:3; Psalm 16:5"`, two real passages packed
  into one string. Saying "exactly one passage per entry" in the tool schema
  was enough to stop it. Keep the resolver strict about it either way —
  refusing that string is the safety property, not a defect.
- **The other three flagged suggestions were bugs in this spike, not in the
  model**, which is the more useful finding because both classes will recur in
  the real feature:
  - a reference came back with **U+2011**, a non-breaking hyphen, which an
    ASCII-only `[-–]` range rejects. A correct answer thrown away by the gate
    built to catch wrong ones. `_DASHES` now covers the family.
  - two anchors missed on a line with a curly apostrophe — a line
    that appears in that song four times. `library.parser` folds smart quotes
    to ASCII, so the slide holds `can't` while the model, quoting the raw
    `body_text`, hands back `can’t`; stripping without folding first turns one
    of them into `can t`. The parser already knew this; the anchor matcher had
    to learn it separately.
- **Anchoring works** once line breaks are normalized too: four earlier
  apparent misses were the model joining lines with `/` instead of a newline —
  a formatting habit, not a wrong answer, and worth about 10% of the anchor
  rate.
- **It abstains — but only if the tool call isn't forced.** The library's two
  non-songs (`Announcements`, `Annoucment Loop`) were the only two of the
  twenty to produce nothing at all, which is exactly right. Getting there took the same lesson `no_action` taught the
  spoken-instructions work, in a new disguise: with
  `tool_choice: {function: suggest_scriptures}` the model is *required* to
  call, and the two providers fail differently when it has nothing to say —
  Gemini returns an empty array, Groq's `gpt-oss-120b` refuses and the request
  comes back **HTTP 400 `tool_use_failed`**. The correct answer arrived
  looking like a transport error. `tool_choice: "auto"` fixes it outright:
  clean abstention on both non-songs, unchanged suggestions on real ones.
  Whoever builds `show_scripture` should not force its tool call either.
- The links are the well-known ones and land on the right line: Psalm 103:1
  under *"Bless the Lord O my soul"*, Job 1:21 under *"You give and take
  away"*, Matthew 7:24-25 under a line about building a life on a firm foundation,
  Joshua 6 under *"Walking around these walls"*.

**What has not been measured: recall.** Every suggestion checked out, but
nothing here says how many real scriptural links the model *missed*. That needs
a hand-labelled set, and it is the honest gap in this section — the same
caution `holdout_validation` applies to any test set built from the material it
is scored on.

**Placement in the app**: editor-time only, one call per song, results written
to `song_scriptures` with `source='suggested'` and shown for approval. Never at
service time. A local model is a later option; the quality bar here is much
higher than spoken instructions' (a wrong verse is on screen for 30 s in front
of everyone, not a slide someone can arrow past), so hosted-first is the right
default, and the verification gate is what makes either safe.

---

## 5. `show_scripture` as a live tool — small, but it re-opens a settled question

Cheap to build. `decision.instructions` already has the whole shape: catalog,
backend, wake gate, `resolve`, `Director.on_override` for authority,
`SlideChangeMessage` for the broadcast. A third tool:

```
show_scripture(references: [string], display: "full" | "sidebar",
               translation: string | null)
```

with the same fail-closed discipline the existing tool has: every reference is
resolved against the local verse store *before* anything reaches a screen, and
an unresolvable one produces nothing at all — the same way a hallucinated
`section_key` currently resolves to nothing rather than to slide 0.

Two things to say plainly before building it:

**It contradicts a measured decision.** `Plan/SPOKEN_INSTRUCTIONS.md` collapsed
to a single tool *specifically* to remove tool-selection from a 3B model's job,
and then found that even the abstention case needed its own named tool to work
at all. Adding a third tool re-opens exactly that. It is not a reason not to
build it — it is a reason to re-run that benchmark, extended with scripture
utterances *and* with the existing go-to-slide set, and to check the false-fire
rate on ordinary speech before it goes anywhere near a service.

**It inherits a trigger that is known not to work well.** The wake gate
constructed 7 utterances over 670 s on a real recording and *none* of them were
the genuine spoken moments (`feature_spoken_instructions`). Whatever
`show_scripture` scores in isolation, in a real room it will only fire as often
as the gate lets it. Fixing the gate is the higher-value work; this tool is
worth building after it, or alongside a manual trigger.

A quieter alternative worth considering first: the operator's own search box.
The same resolver, the same store, no model, no wake gate — one keystroke to
put a verse up. Most of the value, none of the reliability risk.

---

## Suggested order

| step | what | why it's first/last |
|---|---|---|
| 1 | verse store + `--fetch` at install; reference resolver | everything else needs text |
| 2 | `song_scriptures` schema + editor UI (manual entry) | the data model is the gate on all four remaining items |
| 3 | `scripture/track.ts` + audience rendering + formatting | the actual feature; spans do the work, dwell floor ships off |
| 4 | suggester wired into the song editor | measured; low risk |
| 5 | operator search box | most of §5's value, none of its risk |
| 6 | `show_scripture` tool | only after the wake-gate benchmark is re-run |

## Open questions

- Which translation ships bundled: BSB (best reading) vs WEB (already fetched).
- Does the dwell floor earn its keep on a recording that actually contains a
  freeze or a lost-mode episode? Nothing in the four goldens measured here
  does, and that is the one case it was designed for.
- Would driving the track from the *belief* rather than the displayed slide
  beat 0.435? (`eval_belief_vs_displayed` found 41% of visible error is the
  scheduler, not the belief — the scripture track may want the calmer signal,
  and unlike the lyrics it has no reason to wait for a commit.)
- Sidebar vs lower-third as the default position. Lower-third is what
  broadcast does for exactly this problem, and it does not disturb the lyric
  layout at all.
- Recall of the suggester — unmeasured, needs a hand-labelled set.

---

## As built, 2026-09-10

Shipped behind one switch — `Settings → Scripture → Show scripture`, off by
default. With it off there is no query, no timer, no sidecar traffic and no
extra render: `ScriptureStage` with a null passage *is* the old `SlideStage`.

| where | what |
|---|---|
| `sidecar/auto_presenter/scripture/` | `references`, `store`, `anchors`, `suggester` — no imports from the matcher, tracker or audio pipeline, and none of them import it |
| `sidecar/auto_presenter/server_scripture.py` | five handlers, every one off the event loop; split out of `server.py` like `server_music` |
| `src-tauri/migrations/005_song_scriptures.sql` | its own table; `songs` gains no column and `SongRow` gains no field |
| `src-tauri/src/scriptures.rs` | list / list-for-setlist / replace-for-song, plus `ScriptureSettings` nested in the existing settings blob |
| `src/lib/scripture/` | `track` (pure), `anchor` (pure), `client`, `settings`, `useScriptureCue`, `useScriptureSettings` |
| `src/components/scripture/` | `ScripturePane`, `ScriptureStage`, `ScriptureEditor`, `ScriptureSettingsPanel` |

**The seams in existing files, in full.** Each is one to five lines, and this
list is the honest measure of how decoupled the feature is:
`SlideStage` (vw → cqw, so lyrics reflow when a pane takes width),
`projection.ts` (three optional payload fields),
`AudienceView` + `PresentationView` (render `ScriptureStage`),
`OperatorView` (one hook, three payload fields),
`SongEditor` + `SettingsPage` (one mounted component each),
`tauri.ts` (one optional settings field),
`mockTauri.ts` (three command mocks, so the feature works in `pnpm dev`),
`db.rs`/`lib.rs`/`commands.rs`/`settings.rs` (registration only),
`server.py`/`protocol.py` (registration only).

**Parity is enforced, not hoped for.** Anchor matching exists twice — Python
verifies suggestions with it, TypeScript builds the track with it — so
`data/fixtures/scripture_anchor_cases.json` is asserted from both sides, the
same arrangement `parseSong.ts` has with `library/parser.py`. A disagreement
would mean a scripture that verified on save silently never appearing, which
is the quietest failure this feature can have.

**Two defects the tests found while building, both fixed:** a failed
translation build left its staging file behind, and the same verse displayed
as "Psalm 103:1" when typed but "Psalms 103:1" when found by search — every
display path now goes through one `format_reference`.

**Not built, deliberately:** the `show_scripture` live tool (§5 — still gated
on re-running the spoken-instruction benchmark and on the wake gate), licensed
translations (§1 — the editor-time fetch path), and setlist-scoped scriptures
(§2). The dwell floor is implemented and defaults to 0, per §3's measurement.

**Not yet verified live:** a projector showing a verse during a real service.
The chain is proven end to end through production code — resolve against the
real WEB store, and the suggester returning five verified passages anchored to
real slide indices for "Build My Life" — and the display is covered by
component tests, but nobody has watched it run in a room yet.

---

## As built, 2026-10-15: a verse on screen now (steps 5 and 6)

Behind its own flag, `FEATURES.liveScripture` (dev builds only), separate from
song-attached scripture so either can ship alone.

- **Operator box**: a Scripture tab beside Songs in the operator panel. One
  field takes a reference ("jer 29:11", "1 cor 13:4-7", "John 3.16") or words
  from the passage; Enter puts the first result up full screen. Book
  abbreviations resolve by unique prefix in the verse store. (Since
  2026-10-16 they go through the canonical book table first, so "phil" is
  Philippians, its standard abbreviation; "jo" is still refused.)
- **`show_scripture` / `hide_scripture`** spoken tools, offered only when a
  verse store is installed. **The model writes the reference** from whatever
  was said — spoken numbers, "chapter 8 verse 28", a paraphrase of the verse.
  Nothing on our side interprets speech; we only check the reference names a
  real passage, and do nothing if it doesn't.
- **One live passage, two ways to put it up.** `lib/scripture/liveScripture`
  holds it for both presenter views. *Full* replaces the lyrics and comes down
  when a person moves the slide (click, arrow, band leader, spoken "go to the
  chorus"); the AI following underneath does not take it down. *Beside*
  stays through slide changes. Leaving the service clears it.
- **The lookup bug from the 2026-09-15 review is fixed**: `resolve_passages`
  now returns `resolved` (as typed -> canonical), so a hand-typed or en-dash
  range finds its passage instead of silently never showing.

**Measured** (`research/eval/bench_spoken_requests.py`, typed Whisper-shaped lines,
hosted gpt-oss-20b): **24/24**, 0.47 s median — 9 song commands with no
regression from the two extra tools, 10 passage requests including a
paraphrase ("the verse about the plans I have for you" -> Jeremiah 29:11) and
"next to the lyrics", 5 non-requests. Two ambiguous lines (a preacher quoting
Romans 8:28 mid-sermon; "look at verse 12 with me") both came back as no
action. The two tools cost ~260 prompt tokens a call (~670 -> ~930), and the
free tier's per-minute *token* cap, not its request cap, is what 429'd at one
call every 2.5 s.

**The wake filter is still lexical.** Book names and scripture nouns were added
to it (each under 0.1% of sung slides except credit lines nobody sings, and
"kings" at 0.5%). Sending every sentence to the model instead is the cleaner
design and the accuracy argument for the filter is gone; what keeps it is the
hosted free tier's daily and per-minute token budget over a 90-minute service.

**Not yet tested on audio.** No existing recording was made with a reference
spoken aloud; whether Whisper's rendering of "Jeremiah twenty-nine eleven" and
the wake filter hold up live is the open question.

## Importing a church's own Bible (2026-10-16)

A church can now use the translation it actually reads from. Two routes, both
in Settings → Scripture:

- **Find a translation…** — eBible.org's public-domain translations, searched
  and downloaded on demand (`scripture/catalog.py`, revised 2026-10-17): **110
  translations in 47 languages, 34 in English**, filtered from eBible's
  `translations.csv` (~1,550 rows) to `downloadable` + copyright "public
  domain". The ~1,180 others that are redistributable under Creative Commons
  or similar licences are deliberately left out — nothing offered needs terms
  followed. The list is cached a week; offline, the cache answers. WEB, KJV
  and ASV now also download from eBible (`engwebp`, `eng-kjv2006`,
  `eng-asv`) but keep their old ids, so existing settings still name them.
- **Import a Bible file…** — any Bible the church already has, in any of the
  formats below. Two steps: the file is read and a **report** is shown
  (format, verse and book counts, deuterocanon, missing books, books much
  shorter than expected, books recognised only by their layout or not at all,
  Psalm numbering); nothing is installed until the operator fills in a name,
  a short name and a credit line, **and ticks "Our church has the right to use
  this translation"**. The imported text stays on that machine.

### Formats

| Format | Where it comes from | Notes |
|---|---|---|
| USFM (file, files, or zip) | Paratext, eBible.org (1,000+ translations) | `copr.htm` in eBible zips fills in title and rights |
| USX, DBL bundles | Digital Bible Library | `metadata.xml` fills in name, abbreviation, rights |
| USFX | eBible.org's XML | |
| OSIS | CrossWire, Bible societies | milestone and container verses |
| Zefania XML | Church projection software (Europe especially) | `bnumber` trusted over `bsname` — one real file labels 2 Chronicles "1Chr" |
| OpenSong XML | OpenSong / OpenLP users; usually no file extension | book names in any language |
| Verse per line | eBible VPL, most plain-text Bibles | `GEN 1:1 …` or `Genesis 1:1 …` |
| CSV / TSV | spreadsheets, database exports | header `book,chapter,verse,text` (or `b,c,v,t`), numbered books allowed |
| JSON | getbible.net, ad-hoc exports | three layouts |

**Not supported, deliberately:** SWORD modules (every reader — libsword,
pysword — is GPL) and the encrypted formats of ProPresenter, EasyWorship and
MediaShout (and their licences don't transfer). The import screen says so and
suggests asking the publisher for one of the formats above.

### Books: one identity, three ways to find it

`scripture/books.py` is the canonical table — USFM codes, which cover every
deuterocanonical book — with English names, OSIS ids and aliases. An importer
finds each book by, in order: a **code** the format states; a Zefania
**number** (1-66 universal, 67+ only with a second opinion); an English
**name**; or its **shape** — verses per chapter, against a reference
versification generated from the WEB with deuterocanon
(`scripture/_versification.py`). Shape is what makes non-English files work
without a name table per language: a Slovak Catholic OpenSong Bible (73 books,
every name in Slovak) imports with all 73 books right, including Esther with
its Greek chapters (16), Malachi in 3 chapters, and "3. Jánov" (one chapter of
15 verses, like the Prayer of Manasseh — reading order and the leading "3."
decide it). A book that matches nothing is kept under its own name and can be
looked up by it.

Lookups go through the same table, so every spelling reaches the book in every
translation. This fixed a live bug: the installed WEB names the book "Song of
Songs", and the reference parser rewrote that to "Song of Solomon" — so *neither*
spelling resolved on WEB.

### Measured against real files

All parsed, books identified, and verses resolved through the store:

| File | Format | Books | Verses | Time |
|---|---|---|---|---|
| BSB (eBible) | USFM zip | 66 | 31,086 | 2.5 s |
| Douay-Rheims (eBible) | USFM zip | 73 | 35,811 | 2.5 s |
| WEB + deuterocanon (eBible) | USFM zip | 81 | 38,030 | 2.8 s |
| BSB (eBible) | VPL zip | 66 | 31,086 | 0.4 s |
| KJV + Apocrypha (open-bibles) | OSIS | 81 | 36,820 | 0.7 s |
| Luther 1912 (open-bibles) | OSIS | 66 | 31,171 | 0.4 s |
| Douay-Rheims (open-bibles) | Zefania | 75 | 35,953 | 0.5 s |
| Russian Synodal (open-bibles) | Zefania | 66 | 31,352 | 0.4 s |
| WEB (open-bibles) | USFX | 84 | 37,624 | 0.6 s |
| Slovak Catholic (OpenSong) | OpenSong | 73 | 35,751 | 0.4 s |

The report earned its place on the first run: the open-bibles Douay-Rheims
Zefania file has **one verse of Judith** (of 339) — flagged as "much shorter
than expected" — and eBible's Douay-Rheims numbers the Psalms the Latin way,
flagged with what that means ("Psalm 22 here is Psalm 23 elsewhere").

### Licensing posture

- The church supplies the file and confirms it has the right to use it; the
  app never hosts, links to, or suggests a source for a copyrighted text.
- The credit line is required, and shown in Settings. Publishers' church
  terms (NIV, ESV) generally ask for the translation's abbreviation beside a
  projected verse — "Show translation" does that, and it defaults on. The
  credit line itself is **not yet shown on the projected screen**; if a
  publisher requires the full notice somewhere in a service, that's the gap.
- Imported text never leaves the machine as a whole: it isn't in peer setlist
  sync, the band relay, error reports or logs (the import handler logs the
  file name only). Single passages go where a passage already goes — the
  screen, and the hosted model when a suggestion is asked for.

### Multi-language: supportable, not first-class

Imports in any language work — books are found by layout, the store's search
index drops the English stemmer for non-English text, and references are
parsed with book names in any script. What's still English-only: references
are typed and displayed with English book names (the file's own names are
kept in the store's `books.local_name`, ready for display), search's
stop-word list, the suggester, and speech.

### Store changes

Each store now has `books` (canonical code, name, the file's own name, order)
and `meta` (name, abbreviation, credit line, language, source) beside
`verses`. Stores built before this still open: their book names are identified
on the fly and their metadata comes from the catalogue. Imported ids are
`my-<short name>` so they can't collide with a catalogue id added later. The
sidecar socket's message cap went from websockets' default 1 MiB to 128 MiB so
a Bible file (sent once, base64) fits; it is bound to 127.0.0.1.

### Revisions, 2026-10-17

- **Default vs. this service.** Settings now holds the *default* translation.
  The operator's Scripture tab has a picker for the current service (it starts
  at the default and resets when the presenter view closes), and song-attached
  passages follow it too.
- **Spoken.** `show_scripture` takes an optional `translation` ("read it from
  the King James"); the sidecar matches it to an installed translation by id,
  abbreviation or name (`store.match_translation`), and the service switches
  to it. Not installed or ambiguous: the verse shows in the current
  translation. The extra optional argument adds prompt tokens on every call —
  re-run `research/eval/bench_spoken_requests.py` before shipping (it was 24/24
  without it).
- **"Hold each passage" removed** from Settings; the engine ignores any saved
  value (passages follow the slides, which measured calmer and more accurate).
- The Scripture section in Settings is collapsed by default.

