# Features

What the app does, with links to the design write-up behind each feature
where there is one. [CHANGELOG.md](../CHANGELOG.md) has the release-by-release
history.

## Running a service

- **Present** a service from one setlist workspace: songs on the left, every
  slide in the middle, library and scripture on the right. The same layout
  is used before and during the service.
- **Audience, band and leader screens.** The app assigns displays by itself;
  Screens… in the operator menu corrects it.
  ([ORIGINAL_PLAN](done/ORIGINAL_PLAN.md), [PHASE_2](done/PHASE_2.md))
- **Display looks**: themes, fonts and sizes, including an overhead-projector
  style and a continuous scroll mode.
- **Blank slide**: by hand, or automatically while nobody is singing.

## The AI that follows the band

- **Change slides for me**: the AI listens and moves the slides just before
  each line. ([ARCHITECTURE](ARCHITECTURE.md#the-ai-engine),
  [PHASE_6](done/PHASE_6.md), [PHASE_7](done/PHASE_7.md))
- **Next-line check**: moves about a second after a line starts, instead of
  waiting for the transcript. Apple silicon.
  ([NEXT_LINE_CHECK](done/NEXT_LINE_CHECK.md))
- **Score follower and learned references**: follows melody and chords
  against a lead sheet, or against how this church plays the song.
  ([LEARNED_REFERENCES](done/LEARNED_REFERENCES.md))
- **Assist**: the operator changes the slides and the AI highlights where the
  band has gone. On a computer too slow for automatic mode, this is what's
  offered. ([AI_ASSISTED_MODE](done/AI_ASSISTED_MODE.md))
- **Spoken requests**: "go to the chorus", "let's sing Way Maker", "bring up
  John 3:16". ([SPOKEN_INSTRUCTIONS](done/SPOKEN_INSTRUCTIONS.md))
- **Songs not in the setlist** are found from the whole library.
  ([PHASE_8](done/PHASE_8.md))
- **No moves on talk**: sermons and announcements don't change slides.

## Library

- **Songs** in a plain text format with section tags and a slide order.
  ([SONG_FORMAT](SONG_FORMAT.md), [PHASE_1](done/PHASE_1.md))
- **Import** from ProPresenter, OpenLP, FreeShow, OpenSong, and from song
  files: ChordPro, OnSong, OpenLyrics, SongSelect, PDF chord and lead sheets.
- **Chord editor** with Suggest and charting by voice; a different key for
  one service.
- **Search by meaning**: "songs about victory", "Easter songs we haven't
  played lately".
- **Hymns** from the Open Hymnal, with each hymn's copyright status.
  ([HYMNS](done/HYMNS.md))
- **AI assistants** (Claude Desktop and others) can read and edit the library
  and build setlists through the app's MCP server.

## Scripture

- **Bible passages** live or prepared ahead in a service, from ~110
  public-domain translations or a Bible the church imports.
  ([BIBLE_INTEGRATION](done/BIBLE_INTEGRATION.md),
  [SERVICE_ITEMS](done/SERVICE_ITEMS.md))
- **Readings follow the reader**: a passage scrolls as it's read aloud.
  Apple silicon.
- **Text slides** (announcements, sermon points) as service items.

## The team

- **Band and leader screens** on phones and tablets: lyrics and chords that
  follow the operator, joined by QR code. The leader can pick the next song,
  with "Fits next" suggestions by key and tempo.
- **Online band room** through the church's own Cloudflare relay, for
  networks that keep devices apart.
- **Share a setlist or a whole library** with another computer on the same
  network.

## Behind the scenes

- **In-app updates**, crash reports and opt-in usage stats (official builds
  only).
- **Recordings and the debug panel** for developing the engine
  ([PHASE_3](done/PHASE_3.md)).
