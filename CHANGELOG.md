# Changelog

What's new in each version of Church Slides, in plain words for the people
using it. This file feeds the download site's changelog page, the GitHub
release notes, and the "update available" pop-up in the app.

Add a line under "Unreleased" as each change lands. `scripts/bump_version.sh`
turns that heading into the new version and its date, and `scripts/release.sh`
refuses to release a version that has no entry here.

## Unreleased

## 0.3.0 — 23 November 2026

- Changed: **the AI hears the band better and sooner** on Macs with Apple silicon. It uses a newer speech engine: on our test services the wrong slide was up a little less often, and 179 of 225 slide changes landed on time, up from 169. Its speech model is a bigger one-time download, about 2.2 GB.
- Changed: **live AI now needs a Mac with Apple silicon (M1 or newer) on macOS 14 or later.** On other computers, including Intel Macs and Windows, the AI is off and you present by hand, as always. The app says so before offering any download.
- Removed: **Groq's online speech** in Settings. Its free tier couldn't keep up with a service. Spoken requests still use a Groq key for now.
- Changed: the terms of use now say plainly that the app's own source code is under the MIT licence, which they don't limit, and the privacy page that a service's live audio stays on your computer.
- New: **the worship leader can stop a surprise song jump.** With nobody on the operator view, when the AI goes to a different song (not just the next one at the end of a song), the leader's screen shows "Going to …" and the words it heard for 3 seconds, with Go now and Stop. If nobody answers, it goes.

## 0.2.4 — 23 November 2026

- Fixed: **a song the band starts that isn't in the setlist is found again.** In Auto, the app now recognises it from your library, usually within a minute of singing, and adds it to the setlist. Before, this almost never happened.

## 0.2.3 — 18 November 2026

- Changed: **adding songs to a setlist no longer shuffles the list.** A song you add stays where it is, with a tick, so the next click lands on the song you meant. Click it again to add a reprise.
- Changed: **a new setlist you leave empty is tidied away** however you leave it, not only with the back button, so Home doesn't fill with empty "New setlist" entries.
- Changed: "Start from last service" is now **"Copy last service"**, so it doesn't read like "Continue last service".
- Changed: the projector's look has one name everywhere, **"Display look"** (it was "Lyrics style" in some places and "Display" in Settings). The look is now kept in the library too, so it travels with a library backup.
- Fixed: **the app could freeze when macOS asked for Keychain access** (after an update, for the online room's sign-in). It now waits for the answer without freezing, even mid-service.
- Fixed: the band page only connects to the official Church Slides servers and to http(s) addresses on your own network, so a crafted link can't point band members' screens somewhere else.

## 0.2.2 — 16 November 2026

- Fixed: **slides changing before the last line was sung.** The AI now waits to hear a slide's last words (or the next slide's first) before moving on. On the test services it left a slide early about a third as often, and the line being sung was off screen about a sixth less.
- Fixed: **a skipped slide in songs where one slide repeats another with an extra line** ("…we live for you" once, then twice). The AI treated the longer one as a different place and jumped ahead; it now keeps the slide that comes next in order.
- Fixed: **jumping back to the first slide near the end of a song.** In songs that end on a short repeated line, the AI expected the first slide to come after the last full one; it now expects the band to repeat it or go back to an earlier slide.
- Fixed: **a song's first words coming up late** (Apple-silicon Macs). The first slide used to appear about 5 seconds after the band started singing; while a band is playing the AI now puts it up as soon as it clearly hears a line of the song. On 20 test songs the first words came up about a second and a half sooner.

## 0.2.1 — 12 November 2026

- New: **"Fits next" on the leader's band screen.** The song picker suggests up to three library songs that would sit well after the one being played: close key (the key it's being played in), similar tempo, same time signature, each with the reason. A song that fits better moved a little is offered moved ("Play in E (+2)"), and adding it keeps that key for the service.
- Fixed: **band members joining online got the old band screen after an app update** (no Invite button, the song strip not following along). Opening the online room now updates your online relay first, when the app has a newer one.

## 0.2.0 — 10 November 2026

- New: **slides change sooner while the band plays** (Apple-silicon Macs). The AI now listens for the first words of the next line and moves as soon as it hears them. On 20 test songs it put up the wrong slide about a sixth less often. It only does this while a band is playing, never moves off a blank slide, and never jumps back to the slide before.
- New: **Assist on slower computers.** A computer too slow for "Change slides for me" can still use Assist: the AI watches and suggests, and the operator clicks. The AI check in Settings says which a computer can do. Computers checked before are checked again.
- Fixed: **fewer false slide changes during talking.** Over 4 hours of sermons and announcements the AI used to change slides 21 times by mistake; now it doesn't. When no band is playing it needs to hear a song's own words before moving to it, and it blanks the screen if a song it chose isn't then heard.
- New: **readings follow the reader** (Apple-silicon Macs). A Bible passage read aloud scrolls as it's read, a few words behind the reader, and stops near a pause. A long passage asked for during the service ("all of John 3") becomes a reading in the service, a screen at a time, and "next verse" moves through it.
- New: **share a whole library** with another computer on the same network, chords included. You see a summary first (new, already here, different) and choose once for every song whose words differ. The send/get panel shows a short network code: computers showing the same code are on the same Wi-Fi.
- New: **invite band members from the stage.** The band screen has an Invite button with a QR code of the band link (never the leader's link).
- New: **get a setlist from another computer on Home**, without opening a setlist first.
- New: **Screens… in the operator menu** (with more than one display): move the audience and band screens between displays, mark a display "Don't use", and see which display is which. The app remembers it for next time.
- Changed: the leader's song strip slides along to the current song at each song change.
- Changed: going from Assist to "Change slides for me" starts from the slide on screen, not the AI's own guess.
- New: **drop song files on Home or a setlist**, not only the Library. Dropped on a setlist, the songs are also added to the end of that service.
- Fixed: **the AI could lose track of the screen.** After an operator click, a spoken request, or a move the app didn't make, it could sit a slide behind or think a different song was up. Editing the setlist mid-service could make it jump to another song on a couple of stray words. It now goes by what the screen really shows.
- Fixed: **after a click, the AI really does wait.** It used to time its 5-second pause from its own last move, so it could move again a second after the operator.
- Fixed: on Intel Macs and Windows, the screen could step back a slide 16 seconds into an instrumental break.
- Fixed: one failed speech pass on Intel Macs and Windows ended live AI for the rest of the service; now it's skipped. One bad number from the music follower can no longer send the screen to a song's blank slide.
- Fixed: a slide change no longer waits for a spoken request to be understood first.
- Changed: **with the AI off, it really is off**: no listening for chords or next lines in the background.
- Security: **only Church Slides can talk to its AI engine.** A web page open on the same computer could have sent it commands, including deleting a folder.
- Security: the band leader PIN can no longer be guessed by trying them all. After 5 wrong PINs a device waits 5 minutes.
- Fixed: **a section written twice showed twice over.** A song with no slide order and two [Chorus] blocks showed every chorus slide at each block (4 where 2 were written). In one church's library, 53 of 311 songs were affected.
- Fixed: chord sheet PDFs with a slash chord over a minor chord (G#m7/D#m), lines of only "N.C.", or a third heading line came in with chord names in the words or an extra slide.
- Changed: AI assistants connected to Church Slides can look up many songs at once, find chord sheets by file name, and build a setlist with each song's key.

## 0.1.0 — 27 October 2026

- Changed: **one Import menu.** Presentation apps found on this computer are listed at the top ("From ProPresenter"), followed by song files, a folder and hymns. Choosing an app reads it straight away.
- Changed: **a summary before anything is imported**: how many new songs, chords added, songs already in your library, repeat copies skipped and services, with one Import button. The song-by-song list is still there to open. Only the 8 most recent services come in unless you tick the box for older ones.
- Fixed: closing the preview of a very large import (thousands of songs) could hang for several seconds.
- New: **a file the app can't read now says so**, with a link to the formats it does read and a way to have an AI assistant read it instead. With error reports on, a short report of the file's type is sent (its kind and size — never its name or contents); otherwise a button lets you send a note to the developers.
## 0.0.12 — 27 October 2026

- New: **bring your songs from another app.** Library → Import → From another app… finds ProPresenter, OpenLP, FreeShow or OpenSong on this computer and brings in their songs, and their setlists as services, after you say yes. Exported files work too: ProPresenter `.pro` and `.proplaylist`, and OpenLP service files (`.osz`). New installs with an almost empty library are offered this once. Nothing is uploaded, and the other app's files are only read, never changed.
- New: **a different key for one service.** Click a song's key in the setlist or during the service and pick the key you're playing it in this time. The band and leader screens show the chords in that key; the song in your library, and other services, keep theirs. When the worship leader changes key from their screen, it's kept with the setlist too.
- Fixed: **chord sheets imported with the wrong section names.** SongSelect sheets with "Chorus 1A / 1B" came in as "Other", a sung interlude came in as "Instrumental", and a hymn with no headings became one long verse. They now come in as chorus, tag and verses 1, 2, 3, with a repeated chorus written once.
- Changed: **adding chords to a song with plain "verse 1, verse 2…" names** (from a slide export) now names its sections from the chord sheet — chorus, bridge and so on — while keeping your words and slide breaks.
- Fixed: some PDF chord sheets lost the letters "fi", "fl" and "ff" ("di erent", "re ection"), and a sheet whose CCLI number belonged to another song in your library added its chords to that song instead of the one with the same title.

## 0.0.11 — 26 October 2026

- Changed: **the AI changes slides sooner.** It now moves to the next slide while the band is singing the last line of the current one, so the words are up before they're sung, and it no longer flicks back and forth between two slides at the end of a verse.
- Fixed: **talking near the microphone could move the slides.** Conversation while a song was on screen (between songs, or before the band starts) sometimes made the AI jump to another slide or start the song early. It now holds the slide until the song's words are actually sung.
- Changed: **one style for everything on screen.** A Bible passage now looks like the lyrics (same colours, typeface and size) instead of having its own position, size and font settings. To give scripture a different look, use **Override default style** in Settings → Scripture; **Use default style** puts it back. And any song or passage in a setlist can have its own style for that service: click the palette icon beside it.
- New: **a redesigned chord editor.** Chords sit above the words, the way a chord sheet is written, instead of in `[G]` brackets inside them. Click any word on a slide to give it a chord from the song's key, drag a chord to a different syllable, add a passing chord, or hold a chord for extra bars at the end of a line. Pick the key from a colour-coded picker, switch between letters and Nashville numbers, and transpose in one step.
- New: **Fill from PDF** in the song editor reads a chord sheet (including SongSelect PDFs) or a lead sheet straight onto the song.
- New: **Suggest** in the song editor offers the chords it can work out — a second chorus that matches the first, a typo in a chord name, a slide that should be split — and, while you chart, a faded preview of the next chord that Tab accepts.
- New: with a Groq key saved (Settings → AI), a **Voice** button in the song editor lets you chart by speaking ("the verses are 1 4 5 1, hold the 5 for two bars at the end of the chorus"). It sends your recording and the song's words and chords to Groq, and doesn't appear without a key.
- New: **search your library by topic, occasion or key.** The same search box now understands "songs about victory in Jesus", "good songs for Easter that we haven't played in the last 3 services" or "hymns in the key of E about the blood of Jesus", in the library and when adding songs to a setlist. Each song shows the lines that matched, and what the search understood appears as small tags you can remove. It also understands who wrote a song ("songs by Chris Tomlin", "songs from Bethel"), a CCLI number, "new songs", tempo ("fast songs", "slow songs", "around 72 bpm"), time signature ("songs in 6/8") and what sits well next to a song ("songs that fit with How Great Thou Art" finds ones in a nearby key, at a similar tempo and in the same time). It runs on your computer, without the internet. Searching for a title or a line of lyrics works exactly as before.
- New: the song editor has **tempo and time signature** next to the key, so fast, slow and 6/8 searches can find the songs you've filled in. Songs imported from ChordPro, OnSong or SongSelect files bring theirs with them.
- Fixed: searching for a title with a comma in a number, like "10,000 Reasons", found nothing.
- Changed: **a setlist is one screen before and during the service.** The songs are on the left, every slide in the middle and the song library and Scripture on the right, and Start service keeps the same layout. Arrow keys move between slides, Enter edits one, and hovering a slide offers edit, duplicate, delete and drag.
- Changed: **Home** can search your setlists, by name or by a song in them, shows them all on request, and lets you delete one. Dates older than a week read "Jun 9".
- Changed: section names (verse, chorus, bridge…) and song keys have their own colours throughout, and checkboxes, sliders and menus look the same on every Mac, iPad and PC.
- Fixed: a setlist made with New setlist and left straight away with no songs is removed, so Home doesn't fill with empty ones — but one you named is kept.
- Fixed: the app's memory grew by about 50 MB each time a service was stopped and started again, on Windows and Mac alike, and it was never given back until the app was closed. Long days with many starts and stops (rehearsal, then two services) no longer creep upwards.

## 0.0.10 — 18 October 2026

- New: **AI assist**, a third way to run the AI in the operator view. You change the slides; the AI listens, and when the band has gone somewhere the screen hasn't followed, the slide they're on glows yellow. If it's scrolled out of sight, a note at the bottom says what they're singing, with **Go**, **×** to dismiss it, and **Something else** for other places the band might be. A lighter, quicker glow means the next slide is due. Choose Assist, Auto or off from the AI button; the operator view now starts in Assist.
- New: a big **Show Blank Slide** button at the bottom of the song list in the operator view, replacing the small Blank button. Clicking a slide or using the arrow keys takes the blank down again.
- New: the screen **blanks by itself while nobody is singing** (prayer, talking, long silences) when the AI is changing the slides, and comes back when the singing does. Your own Blank always wins, and a Bible passage you've put up stays up.
- New: **spoken requests**. While the AI is on, the worship leader can say "go to the chorus" or "let's sing Way Maker" and the slides follow, including songs that aren't in the setlist yet. It turns on by itself once you save a free Groq key (Settings → AI), the same key as Groq's online speech, and needs the internet during the service. It waits for the end of the sentence before acting, and doesn't mistake a song title in the lyrics being sung for a request.
- New: **put a Bible passage on screen** during a service, from the operator view's new Scripture tab, or by asking out loud ("bring up John 3:16") with spoken requests on. Download any of about 110 public-domain translations, or import your church's own Bible file, in Settings → Scripture, where you also choose how passages look.
- New: **hymns**. A new library starts with four complete hymns, and you can add more from the Open Hymnal under Library → Import, each with its copyright status shown before you add it.
- New: **Settings has separate pages** (Audio, AI, Display, Scripture, Other), and the "Turn on AI" button beside Start service now tells you before the service whether the AI is ready, and what's missing if it isn't.
- New: the AI's health is shown during a service: a small tick or cross in the corner on a single screen, and a note under the header in the operator view when something's wrong.
- New: Settings → Other → **Open-source licences** lists the open-source software the app is built with.
- Changed: the lyrics display choice (Automatic, Slides, Scrolling) now lives in the **Display look** dialog with the colours and fonts, and **Band screens** has moved into the ⋯ menu.
- Fixed: after a song was added to the setlist during a service, a line from another song could switch the screen to it.
- Fixed: if the AI engine stopped and restarted during a service, the screen never reconnected to it and the AI stayed off. It now reconnects within a second.
- Fixed: the AI was too quick to switch to another song on a phrase two songs share.

## 0.0.9 — 15 October 2026

- New: **Send feedback**, in the sidebar and in Settings. Report a problem, suggest an idea, or say what worked well, and it goes straight to the developer. You can include the app's recent log with a problem, and if you're offline you can send it by email instead.
- New: Settings → **Privacy** brings together everything the app sends, each with its own switch. The app asks once whether you'd like to share usage statistics that don't say who you are, such as how long services run, how often the AI changed the slides, and which app version and system you're on; nothing is shared unless you say yes. The details are on the website's new privacy page.
- Changed: the app no longer records services unless you ask it to. Recording has moved to Settings → Developer tools, explains what it captures before it starts, shows **Recording** on the operator screen while it's on, and deletes recordings after 30 days (you can choose). If an earlier version was recording for you, it has stopped; any recordings it made are deleted after 30 days.
- New: **terms of use** on the website, and Settings → Privacy can ask for your data to be deleted in one click.
- New on Intel Macs and Windows: live AI now follows the songs the way it does on Apple-silicon Macs, scoring what the band sings against every line of the song. In our tests it was within about 7% of an Apple-silicon Mac's accuracy. It needs the AI's speech models (about 626 MB, downloaded once) and a quick check, the first time, that this computer can keep up (about half a minute).
- Changed: on a computer too slow to follow the band well, live AI is no longer offered, and the home screen says so before the service, instead of the AI falling behind during it. If you close other apps or plug the computer in, Settings → "AI that follows the songs" → Check again runs the check again. Presenting by hand works as always.
- The AI's chord listening now takes about half the computer's effort it used to.
- Changed: the app now asks once about error reports, alongside usage statistics, before sending any. Error reports start ticked and usage statistics unticked, and you can change either in Settings → Privacy. Error reports also no longer include people's names from device names, such as "Sarah's AirPods".

## 0.0.8 — 14 October 2026

- Fixed: the first line the band sang in a service could take a second or two longer to be picked up, while the AI loaded its pronunciation dictionary. That now happens when the service starts.
- When a song has the same words on more than one slide (Reckless Love's chorus ends on its opening line, say), the AI now shows the one the band is actually at, instead of always the first: in our tests the time spent on the wrong copy fell by about a third, and Reckless Love's went to none.
- If the AI falls behind because the computer is saving power (Low Power Mode, or a nearly flat battery, which slows a Mac down even once it's plugged in), it now says so and what to do, instead of suggesting a faster computer.
- The AI's speech model is now kept in Church Slides' own folder, with your library, instead of a folder shared with other apps, so removing the app's data removes it too. A model you've already downloaded is moved across the first time this version starts, so nothing downloads twice.
- Closing the operator window now quits the app, closing the projector screen and any band screens with it. Before, the projector screen stayed up with nothing to close it from.
- On a new computer, audio setup now downloads the AI's speech model straight away (it says so first, with its size), and the home screen shows how far the download has got, or a Download button if it hasn't happened yet. Before, nothing mentioned it until a service started, and live AI just waited.

## 0.0.7 — 13 October 2026

- Fixed: with Groq's online speech, the AI could stop following partway through a service (often just after it moved to a new song). The app asked Groq for more than its free plan allows, so most requests were turned away. It now keeps within Groq's limit, and if Groq turns requests away anyway, the AI status says so and suggests switching to speech on this computer.
- Fixed: the update download bar ran backwards at first, and didn't keep up with the percentage beside it.
- Dropdown menus now look the same on every Mac, instead of a grey gradient on newer versions of macOS.

## 0.0.6 — 13 October 2026

- When something goes wrong, the app sends an error report to the developers so it can be fixed: what went wrong, this computer's model and the app's recent log. Never audio, or anything said into the microphone. Turn it off, or add a contact so we can follow up, in Settings → Error reports.
- The app now keeps a log. If something goes wrong, Settings → Developer tools → Save diagnostics puts a file in your Downloads folder that you can email to us.
- Fixed: the first time the AI downloaded its speech model, the rest of the app could stop getting answers from the AI engine. Settings showed no audio devices, or stayed on "Checking…" until the app was restarted. Downloads now show how far they've got, and a download cut off halfway is no longer mistaken for a finished one.
- If the AI can't start for a service (a microphone that won't open, say), the status pill now says so, with the reason and a retry, instead of "AI: starting" for the whole service.
- A microphone or audio interface plugged in after the app started now shows up in Settings without restarting.
- Fixed: on Apple silicon Macs, 0.0.5's speech recognition didn't actually run on the graphics chip, so it used more of the processor and a less accurate model than intended. It does now.
- Lead sheets: import a song's lead sheet PDF (from SongSelect, say) in the import window, and it's attached to the matching song in your library.
- On Apple silicon Macs with macOS 14 or later, using the built-in speech recognition (not Groq's online speech), the AI now follows every song in the setlist by its music as well as its words: in our tests it showed the wrong slide about half as often, stays on the blank slide until the singing starts, and no longer flicks back and forth between lines that sound alike. A song with a lead sheet, or one the AI has heard at past services, is followed better still.
- On those Macs, the AI also learns how your band plays each song (the order you sing the parts in, and how long each line lasts) from the services it follows, and uses that the next time the song comes up. What it learns is kept on this computer, beside your song library.
- On those Macs, when the band starts a different song from the setlist than the one on screen, the AI now notices as soon as the new song's opening line is sung, and moves to it.
- On those Macs, in scrolling (overhead-projector) mode, the lyrics now move with the AI's new music-and-words following, so the column keeps pace with the band.
- On those Macs, when the band repeats a chorus or a tag line more times than the slides show, the AI now expects it and stays put, instead of jumping ahead to the next slide: in our tests King Of My Heart's repeated chorus showed the wrong slide half as often.
- The recording inspector shows the music under the waveform: the chords and the melody's pitch, which now come with the app.

## 0.0.5 — 2 October 2026

- On Apple silicon Macs with macOS 14 or later, the AI's speech recognition now runs on the graphics chip: in our tests about a fourteenth of the processor it used before, with a more accurate speech model, and no audio missed.
- If this computer may be too slow for the AI to keep up, the app says so when you switch the AI on (and if it starts falling behind), and suggests Groq's online speech instead.
- An experimental Windows version, for Windows 10 and 11 (64-bit).
- Each release now shows how the AI engine performed in its build tests (CPU, memory, speed and slide timing) on this page.
- Lyrics and recordings save correctly on Windows whatever characters they contain.

## 0.0.4 — 2 October 2026

- The app now tells you when a new version is out, and can update itself and restart with one click.
- Band screens over the internet, through your church's own Cloudflare account, for musicians who aren't on the church Wi-Fi.
- The online band room keeps the same links and QR codes from one service to the next, so the band can bookmark it. "Make new links" replaces them if one gets shared too widely.
- The band leader can move the whole band to another key from the band screen, and each musician can set their own capo.
- Band screens can open on this computer's own monitors.
- One Start button that picks the right way to present for the screens you have connected.
- New setlists are named after the date of the service.
- A new stained-glass logo and app icon.
- A tidier Settings page, with setup you only do once tucked away.

## 0.0.3 — 30 September 2026

- Send a setlist from one operator's laptop to another's over the local network.

## 0.0.2 — 30 September 2026

- Each song's key is shown in its own colour on the leader sheets.
- Features that aren't ready yet (scripture and spoken requests) are hidden.

## 0.0.1 — 30 September 2026

- First preview release: slides that follow the band, a song library and setlists, operator and projector screens, band screens with chord charts, and display looks including a scrolling overhead-projector mode.
