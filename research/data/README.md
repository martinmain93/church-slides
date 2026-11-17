# Public research data

Public, openly licensed audio for tuning the engine and widening its benchmarks.
It's kept apart from the church's own service recordings, which stay on the
developer's machine (and never go to Kaggle or any other cloud service).

Get it (standard library only, about 930 MB, resumable, checksum-verified):

    python research/data/fetch_public_worship.py            # all sets
    python research/data/fetch_public_worship.py --sets speech

By default it lands in `../auto-presenter-research/public_worship`, a sibling of
the checkout, because it's too big for the repo. Every file is pinned in
`public_worship_manifest.json` (URL, licence, SHA-256).

## Sets

| Set | What | Files | Licence | Lyrics / timing |
|---|---|---|---|---|
| `librivox` | Volunteer-sung hymns and carols: Hymn Collection 001, Army and Navy Hymnal v1+v2, Hymns of the Christian Church, Christmas Carol Collections 2011/2014/2018/2020 | 188 (~9 h) | Public domain (US) | Texts are public domain (hymnary.org, Open Hymnal); not fetched |
| `jamendo` | MTG-Jamendo tracks tagged christian or gospel, at most 3 per artist | 57 from 35 artists (~3.6 h) | Creative Commons, per track (see its jamendo.com page); MTG-Jamendo is for research use | None |
| `jamendolyrics` | JamendoLyrics, English subset, with hand-made line and word timings | 20 songs, 9 genres | CC per song (`license` field); dataset code MIT | Yes: `lyrics/`, `lines/`, `words/` |
| `speech` | Talk only, for the "nothing here may change a slide" benchmark | 5 meetings + 3 sermons (~4.5 h) | AMI: CC BY 4.0; sermons: public domain | n/a |

Chosen for variety over quantity: many singers, styles and recording conditions.

### Caveats
- "Hymns of the Christian Church" mixes sung and READ tracks; filter before use.
- The carol collections include some French, German and Latin carols; filter by language.
- Some `jamendo` tracks are instrumental, and many aren't congregational worship
  (metal, EBM, rap). They're there for variety of sound, not as worship examples.
- All sung material here is cleaner than a room mic in a real service. Score it as
  its own set; keep the real service captures as the honest benchmark.

### Speech set
- `speech/ami/*.Array1-01.wav`: AMI Meeting Corpus, one far-field array mic (the
  closest match to a room or lobby mic). ES2002a, IS1000a, TS3003a: design
  meetings at three sites; IB4001: non-scenario; EN2001a: 88 min unscripted. 16 kHz mono.
  Credit: "AMI Meeting Corpus, University of Edinburgh et al., CC BY 4.0".
- `speech/sermons/`: three LibriVox read sermons (Wesley, Calvin on Psalm 119,
  Baring-Gould to children). The hard negatives: church vocabulary and scripture
  quotes, though read aloud rather than conversational.

## Public goldens

`build_public_goldens.py` turns JamendoLyrics' hand-timed lines into replayable
golden recordings in `../auto-presenter-research/public_goldens/jamendolyrics`
(one folder per song: audio.wav, recording.json, golden.json). LibriVox hymns are
bootstrapped with `research/eval/bootstrap_golden.py` against a research-only
library of Open Hymnal texts, into `public_goldens/librivox`. None are approved;
score them as their own set, apart from the church's service recordings.

