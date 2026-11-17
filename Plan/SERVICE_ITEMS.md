# Service items — prepared scripture and one-off text

Status: **design agreed 2026-10-19, revised 2026-10-21 for the setlist
workspace and the retirement of song-attached scripture. Phase 1 built
2026-10-22** (see *As built* at the end); read-along not started.
Judged against `docs/PRODUCT_PRINCIPLES.md`.

## The need

Pastors tell the tech team days ahead which passages and extra slides they
want: the sermon reading, a handful of cited verses, a call to worship,
sermon points, announcements, names for a baptism. Today scripture can only be
shown live (the verse box, or a spoken `show_scripture`). There is no way to
prepare a passage or a one-off slide in a service.

## What changed since 2026-10-19

| change | effect on this plan |
|---|---|
| **Song-attached scripture retired.** The editor left `SongEditor` on main (0f31aa4); the dead-code branch removes `FEATURES.scripture` and the suggester, moving its spikes to `research/dead_ends/song_scripture`. Live scripture stays. | Service items are now **the only way to prepare scripture**. `song_scriptures` (migration 005, already shipped) stays in the schema, unused — never delete a shipped migration. |
| **Setlist workspace** (d735703): prep and live share one three-pane screen — setlist rail, continuous slide grid, Songs/Scripture tabs on the right — with an explicit Prep vs Live state. | Gives every part of this feature a home without a new screen. See *Where it lives*. |
| **The Scripture tab is the live verse box even in prep.** In prep, Enter "shows" a passage with no service running. | The prep action should be **add to service**. One box, whose action follows Prep vs Live. |
| **Song editor rework** (ee8a75c, 854dfe5): 16:9 slide cards, click-to-edit, a raw-text toggle. The workspace grid renders every item with `SetlistSongSlides`. | Text and scripture items reuse those same cards and the same editing. No separate editor. |
| **Research scripts moved** on the dead-code branch: `research/eval/run_realtime_eval.py`, `research/eval/bench_spoken_requests.py`. | Read-along measurement uses those paths once that branch lands. |

## Decisions

| question | decision |
|---|---|
| Licensed translations (NIV, ESV, …) | Churches may paste their own text with a copyright line; the terms already cover this. We still never bundle or fetch licensed text. |
| First release content | **Text only.** No images, no PDF import yet. |
| Video, countdown | Out for now. |
| Reusable items (creed, liturgy) | **One library** holding songs and readings, plus **"start from last service"**. No separate collection. |
| AI read-along of spoken items | Planned from the start. |
| Song-attached scripture | Retired (2026-10-19). Prepared scripture belongs to the service, not to a song. |

## The two item kinds

`setlist_items.kind` has existed since `001_init.sql` (default `'song'`,
otherwise unused). Add a `content` JSON column and two kinds:

- **`scripture`** — one input: the reference. Stores the reference, the
  translation, and **the resolved text copied at prep time** (works offline,
  survives a Bible being removed, can be hand-edited). "Use my own text"
  replaces the resolved text with pasted text and one copyright line.
- **`text`** — the same slide model as a song body (`docs/SONG_FORMAT.md`): a
  blank line starts a new slide, edited through the same click-to-edit cards
  and raw-text toggle as songs. No title/body layout, no per-item styling;
  the display look decides everything. No chords.

Both resolve to slides through the same path as songs (`buildSetlistContent`,
`buildContext`), so the workspace grid, ordering, display looks, scroll mode
and peer sync work without a second mechanism.

Scripture splitting is automatic: fit to the current display look, avoid
breaking mid-verse, verse numbers, reference in a footer. No split settings.

### Rejected

- **One-offs as hidden songs** — pollutes the library and the song-identity
  index, the matcher would try to match a sermon as a song, confuses CCLI
  reporting, MCP song tools and band charts.
- **A separate "Items" collection** — a new page, noun and search.
- **Bringing back song-attached scripture for this** — already retired; a
  passage the pastor asked for belongs to that service.
- **A service timeline with cues** — that is ProPresenter.

## Where it lives in the workspace

No new screens, tabs or header buttons.

- **Right pane, Scripture tab.** The same box as today. In **Prep**, Enter or
  a result's main button **adds the passage to the service** (after the
  selected item, else at the end). In **Live**, it shows it, as now. Pasting
  the pastor's whole message here proposes several items at once.
- **Right pane, Songs tab.** Searches songs and saved readings together. When
  nothing matches, the last row offers **"New text slide"** seeded with what
  was typed — the one way to start a text item.
- **Rail.** Items sit in the setlist order with a small icon (book for
  scripture, text for text) instead of a key badge; drag to reorder as now.
- **Centre grid.** Items render as blocks of 16:9 cards like songs, with the
  same per-slide edit, duplicate and delete. A scripture block's header shows
  the reference and translation, plus "Use my own text".
- **Band and leader screens.** Items show their words without chords, so the
  band can see what's up. Nothing to configure.

## Getting items in

1. **Paste the pastor's message** into the Scripture tab. "Sermon: John
   3:1–21, also Rom 5:8 and Eph 2:8–9" becomes three proposed scripture items
   to approve. The model writes the references; our side only validates them
   against the verse store (same rule as `show_scripture` — no regex over
   prose).
2. **MCP:** an `add_setlist_item` tool on the library server so an agent can
   build the service from the same message.
3. **Live:** a spoken or typed request for a passage already in the service
   jumps to the prepared item instead of making a new one.

## Reusable items

"Save to library" on a text item stores it alongside songs, under the same
search and the same add-to-service flow. A "Songs / Readings" filter appears
only once a reading exists. "Start from last service" — a new action on Home
beside "New setlist" — copies the previous service's order so the weekly
liturgy carries over without saving anything.

Possible later: readings in the library index could let the AI notice the
congregation starting the Lord's Prayer unplanned, like an off-setlist song.
Needs measurement first — liturgy repeats phrases, and pre-service chatter is
the known false-fire path for unplanned song detection.

## AI behaviour

| current item | AI |
|---|---|
| song | as today |
| scripture / text | **read-along**: follows the words whether sung or spoken. No per-item setting. |
| between items | anything that assumes "next setlist item is the next song" must skip non-song items |

### Read-along — what it needs

- Items reach the sidecar as song-like content with a **spoken** flag.
- **Spoken rate is roughly twice sung rate.** The rate prior and the derived
  lead time (measured lag + sampling gap + margin) are tuned for singing;
  spoken items need their own.
- **Asides between verses.** The follower must hold on the current verse
  through commentary rather than drift forward. This is the main risk to
  measure.
- **Translation mismatch is rare** in prepared items — the church sets the
  passage in the translation the pastor reads. The realistic case is a live
  request loaded on the fly in the service's default translation; fuzzy
  matching is enough, not a design driver.
- **No recordings of readings exist.** Collect a few real ones (ideally a
  sermon reading with asides) and make goldens before building the follower;
  gate on `research/eval/run_realtime_eval.py`.

## Out of scope

Everything that needs a canvas: free layout, shapes, per-slide fonts,
transitions, layers, lower thirds, livestream outputs, PPTX fidelity, timeline
cues, custom stage layouts. Images, PDF import, video and countdown are
deferred, not designed here.

## Phases

1. **Scripture and text items** — schema; Scripture tab adds in prep;
   paste-to-create; "New text slide" from Songs search; auto-split; own text;
   live jump-to-prepared; save to library; start from last service.
2. **Read-along** — collect recordings, goldens, spoken profile, measure on
   the real-time harness before turning on.
3. Later, if wanted: images and PDF import.

## As built (phase 1, 2026-10-22)

- **Storage:** migration 009 adds `setlist_items.content` and a `readings`
  table (not rows in `songs`, so nothing that reads songs sees them).
  `add_service_item` inserts after the selected item.
- **One choke point:** `getSetlist` gives each passage or text item a
  song-shaped stand-in (`src/lib/serviceItems.ts`), so the grid, operator
  view, projector, band screens and scroll mode show them unchanged.
  Passages are cut at 160 characters a slide (medium size): measured on the
  stage's own layout, a 1280x720 projector is full at 230.
- **The engine finds items by position**, not by `item_idx`. A passage or
  text item is therefore sent as one wordless slide — never an opening, never
  matched — and while one is up the AI is anchored on the next song and may
  only leave it for that song's lyrics (`aiAnchor`, `aiMayLeave`).
- **Pasted messages:** `scripture/extract.py` — the hosted model when a key is
  on file, otherwise each phrase offered to the parser; every passage checked
  against the Bible either way.
- **MCP:** `add_scripture_to_setlist`, `add_text_to_setlist`; `update_setlist`
  keeps readings after the song they followed (it used to delete them). A
  passage added there has only its reference; the app fills in the text when
  the setlist is opened or started.
- **Peer sync:** bundles with readings are protocol 2 (older copies refuse
  them with "update"); songs-only bundles stay 1.

Not done: a text slide added during a live service; editing a reading in the
Library (re-save it from a service under the same title); workspace thumbnails
use a fixed 12px font, so a full scripture slide can look clipped there while
fitting on the projector.

## Read-along (phase 2), built 2026-10-24 — off by default

Switch: the `read_along` engine tunable (debug Tunables tab), read when a
service starts. With it on, a passage goes to the engine as its spoken words
(no verse numbers, reference or copyright line), marked `spoken`:

- timed as speech: `spoken_margin_ms` (-3000) on top of the singing lead;
- followed by the matcher, never the score follower (built for singing);
- never jumped into: the operator puts a reading up, sermons quote it;
- left out of song openings and in-service learning.

Measured on 8 LibriVox KJV readings (78 min, 494 slides, real-time harness,
MLX small.en), wrong-slide share of reading time:

| | clean | with 118 simulated asides |
|---|---|---|
| passage as a song (no spoken flag) | 0.31 | 0.49 |
| read-along | **0.07** | **0.27** |

No slide skipped; median 0.3 s early. During asides the screen is on the next
slide 86% of the time (it moved as the verse ended) and elsewhere 5%.
On the CPU speech path the window loses 21-48% of spoken words, so slower
machines will follow readings poorly. Before switching it on by default:
2-3 real service recordings of a reading, ideally with asides.

**On by default since 2026-10-29**, on sessions that transcribe with MLX
(Apple silicon), the only path measured fast enough. Elsewhere — the
processor path, Groq — a passage keeps its slides but no words and the AI
holds on it. Kill switch: `follow_readings` (renamed from `read_along`, whose
saved `false` would otherwise have kept it off). The app always sends a
passage's words; the engine decides.

**First live reading, 2026-10-29** (John 3, the user reading): every slide came
~4 s after the reader started it, so they paused at each line. Cause: the
-3000 ms spoken margin was swept with decisions stamped on the *spoken* clock,
which leaves out the recognition delay — it cancelled that allowance. Replayed
on the availability clock, margin 0 puts each slide up 0.8–4.1 s before the
reader reaches it. `spoken_margin_ms` is now 0. And a passage the AI follows
now always scrolls (continuous mode), whatever the display setting: the
reader keeps reading with the next verses in view. On the same recording the
column trails the reader by a median 3 words, within 8 words 83% of the time.
Score reading evals on the availability clock (`replay_live.py` pattern).

**Second live reading, 2026-10-29** (John 3 with asides, a hymn detour, voice
requests): timing good; three faults, fixed in 061fe9e. (1) The column moved
a verse at a time — it stopped at each slide's end and waited for the next
update (still 53% of reading time). A reading's column now runs on at the
reading rate, guesses at most 1 s past the last word heard (the user's "half
and half", instead of the full ~3 s recognition delay), coasts 2 s without new
words, slows rather than stops when a little ahead, and ignores a belief under
0.8 confidence (reading sits at 0.9+; an aside drops it while the mode creeps
on): still ~11%, and an aside leaves it about a line (~10 words) past the
reader rather than 15+. Cost: while reading, the centre of the screen sits ~4
words behind the reader. (2) "Let's go to John 3" landed on the blank lead-in
(the model was offered `other:0`), then the 5 s cooldown held the column: the
blank's section is no longer offered or landed on, and a reading put up or
moved by hand or voice is followed at once. (3) "Back to verse 16" went to a
separate John 3:16 item: a verse request looks in the passage on screen first.
