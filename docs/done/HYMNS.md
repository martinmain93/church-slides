# Hymns: example songs and the Open Hymnal (2026-10-17)

A fresh library starts with four complete hymns, and any other hymn from the
Open Hymnal Project can be added on demand from **Library → Import → Hymns from
the Open Hymnal…** (also offered on the empty-library screen).

**Nothing from the Open Hymnal ships in the app** apart from the four examples.
Every other hymn is fetched from openhymnal.org when an operator opens it and
added only when they press Add. The church does the adding; the app shows each
hymn's copyright status first.

## Sources considered

| Source | Used? | Why |
|---|---|---|
| **Open Hymnal Project** (openhymnal.org) | Yes | Its Copying page places the project's own work (compilation, indexes, data files, transcriptions) in the public domain, and each hymn's file states its copyright for words, translation, music and setting separately. ~350 hymns. |
| Hymnary.org | No | "Website and special contents copyright 2007-present Harry Plantinga"; bot-protected; API access denied. |
| Cyber Hymnal (hymntime.com) | No | Couldn't load it to verify its terms. |
| Hymnal.net | No | Texts copyrighted by Living Stream Ministry. |
| CCEL, Project Gutenberg, Wikisource | No | Public-domain texts, but scanned books, not a hymn database. |

A "top 100" button was considered and dropped: the Open Hymnal doesn't rank its
hymns, and Hymnary.org's popularity list can't be fetched automatically.

## The copyright rule (`sidecar/auto_presenter/hymns/rights.py`)

The Open Hymnal judges public domain by **US law** (its Copying page says so).
That isn't enough to call something public domain everywhere: "Great Is Thy
Faithfulness" (1923) is public domain in the US, but Thomas Chisholm died in
1960, so it is in copyright in the UK, EU and Australia until 2030 and is still
licensed there. So the app applies a stricter rule to the **words** (slides show
words, so a copyrighted arrangement doesn't matter):

| Status | Meaning |
|---|---|
| **Public domain** | Words published 95+ years ago (the US rule) **and** every author and translator died 70+ years ago (the UK/EU/Canada/Australia rule). |
| **Public domain in the US** | The Open Hymnal says public domain, but the rule above fails or can't be checked; the explanation names the reason ("Chisholm died in 1960, so outside the US the words are in copyright until 2030"). |
| **Free for worship** | Copyrighted; the writer lets churches reproduce it for worship, unaltered, with the notice on every copy. The full notice is stored with the song. |
| **Check the notice** | Anything else. |

Anything that can't be verified falls to the more cautious status. The rule
moves with the calendar (Great Is Thy Faithfulness becomes "Public domain" in
2031). The dialog starts filtered to "Public domain"; the rest are one tick away.

Measured on all 351 Open Hymnal hymns: **312 public domain everywhere, 19 US
only, 17 free for worship, 2 check the notice**. Status from the site's index
pages agrees with status from each hymn's own file for 339; every disagreement
leans cautious, and the file (fetched before adding) has the final word.

Correction to an earlier claim in the conversation that led here: the Open
Hymnal's "Be Thou My Vision" is the Byrne/Hull translation (died 1931/1935)
with the arranger's small changes released to the public domain. It is public
domain everywhere; the site's people index wrongly lists a modern writer
against it.

## Converting a score to slides (`sidecar/auto_presenter/hymns/abc.py`)

The Open Hymnal publishes ABC scores. Lyrics are syllables under notes (`w:`
lines), interleaved verse by verse under each line of music, plus extra verses
as `W:` lines. There are **no poetic line breaks** in the sung lyrics, and the
site's lyrics page runs each verse into one line too. Line breaks come from the
**metre**: the file's `%OHMETRICAL 8 6 8 6` gives syllables per line, and the
score gives exactly one syllable per note. Breaks are chosen as a whole
(dynamic programming over word boundaries), preferring ones after punctuation
or before a capital when the count is off by a melisma or elision.

- **Refrain**: written under verse 1 only (other verses skip those notes), so
  it is what verse 1 sings beyond the others. A shared ending counts as a
  refrain only when it is two or more whole lines of the metre — a litany's
  repeated last line ("Hear our penitential cry!") stays in each verse.
- **Melody only**: `w:` lines under harmony voices are echoes and are ignored.
- **Dashes vs compounds**: `day\- Je-sus` is "day— Jesus"; `life\- im-part-ing`
  is "life-imparting" (the next word's capital decides).
- **Older files** (106 of 351) are Latin-1 and lack the `%OH` header; they
  fall back to the `C:` credits.

Measured on all 351 files: 350 convert (one is an instrumental). A rough check
— a line break should follow punctuation or come before a capital — flags
3.5% of breaks where the syllable count matched the metre exactly (mostly
hymns printed without line-initial capitals), 4.8% where it didn't, and 14.5%
for the ten hymns with no metre. Operators can edit any added hymn.

Known weak spot: where the file's metre counts musical phrases rather than
poetic lines (Holy, Holy, Holy is "6 6 6 6 6 6 5 5", not "11 12 12 10"), lines
come out halved. Correct words, short lines.

## The four example hymns (`src-tauri/src/seed_hymns.rs`)

Amazing Grace, Holy Holy Holy, It Is Well With My Soul, Abide With Me — all
public domain everywhere by the rule above. Converted once with the code above,
with two hand fixes: Holy Holy Holy joined into its real four lines, and a lost
dash in Abide With Me ("every plea?") printed as hymnals do ("every plea;").
They replace the old two-verse Amazing Grace fixture (same id, so its tests
still hold). Existing libraries are untouched: the seed only runs on an empty
library.

## Network

openhymnal.org's HTTPS certificate doesn't cover the domain, so the app uses
plain HTTP. What comes back is hymn text the operator reads before adding it.
The index (three pages, ~1 MB) is cached for a week in `<app data>/hymns/`;
offline, a stale cache is used rather than nothing. The privacy page lists the
connection.
