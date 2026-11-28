"""While the room sounds like talk, a move needs the words to back it.

A move the service plan predicts — the next slide of the song on screen, or
the first slide of the next song — is cheap. Any other move (a skip, a jump
back, another song, an opening blank straight to verse 3) bets against the
plan. While the singing detector's talking score is high, such a move must be
backed by the words themselves: several distinct content words of the target
slide heard in the last few seconds, making up a fair share of what was heard.
A planned move is held the same way, but only once the room clearly sounds
like talk (a higher bar).

Why the talking score and not the plan alone: on the approved recordings, 101
of 298 real AI moves were off-plan (back to a chorus, a skipped slide), and the
sung words that should back them are mostly garbled or still 3 s away. Asking
every off-plan move for words took wrong-slide 0.303 -> 0.481 offline. During
singing those moves score low on talking (median 0.2), while the false moves
on talk scored 0.57-0.93, so the bar only applies when it reads as talk.

Traced 2026-11-05 on 4.4 h of talk only (meetings and read sermons,
`research/eval/run_speech_negatives.py`): 21 false slide changes, 20 of them
off-plan. Ordinary sentences shared two or three words with a lyric
("...with all that you've found..." -> "for all that you've done"), or talk the
singing detector took for singing landed on a repetitive slide ("day and night,
night and day") that loosely fits any babble. Neither carries the slide's own
content words.

Why not hold every move while the room sounds like talking: tried as
`blank_on_talking`; real sung transcripts are often garbage that reads as
talk, and holding cost wrong-slide 0.273 -> 0.306. This only asks more of
moves the plan does not predict, or any move once talk is clear.

Why a playing band overrides the talking score: the talking score really
measures "the words don't fit the song on screen", so it misfires during real
singing (15% of one golden's samples read as talk). Replaying the 9 goldens'
captured transcripts, the gate cost wrong-slide 0.306 -> 0.347 (0.380 -> 0.391,
0.178 -> 0.214, 0.475 -> 0.499 on single-song recordings); with the band
exemption every single-song recording is unchanged. None of the 21 false moves
on talk happened with the band signal reading "playing" (harmony 0.36 at most
against a 0.5 bar), so they are all still held. Later (2026-11-27, hosted
speech) a meeting's room tone did read as a band at two false moves, so the
exemption now needs the band held over 45 s (`music.band`, `sustained`).
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field

from auto_presenter.index.types import IndexedSetlist

#: Function words, fillers and the commonest sung syllables: none of them
#: says which slide this is.
STOP_WORDS = frozenset(
    """a an the and or but if so of to in on at by for with from as is are was were be been being am
    i me my mine we us our ours you your yours he him his she her it its they them their this that these
    those there here what who whom which when where why how all any some no not nor too very just can will
    would shall should could may might must do does did done have has had having oh ah yeah hey o la na uh
    um mm hmm okay ok well now then than up down out over into onto off again ever never one two let lets
    im youre were its thats dont wont cant ill youll well ive youve weve gonna wanna like get got go going
    come came say said see know think right yes because about more most only own same such through while
    whoa woah ooh""".split()
)


def content_word(word: str) -> str:
    """Lower-case letters only, a plural or possessive `s` dropped; "" for a
    stop word or a fragment."""
    w = re.sub(r"[^a-z]", "", word.lower().replace("'s", "").replace("’s", ""))
    if len(w) > 4 and w.endswith("s") and not w.endswith("ss"):
        w = w[:-1]
    if len(w) < 3 or w in STOP_WORDS:
        return ""
    return w


@dataclass
class PlanGate:
    #: Distinct content words of the target slide that must have been heard
    #: (fewer if the slide has fewer). 0 disables the gate.
    min_words: int = 3
    #: How far back, in audio seconds, the words may have been heard.
    window_s: float = 8.0
    #: Of the content words heard in that window, the share that must belong
    #: to the target slide: a sermon says "God" and "love" too.
    min_share: float = 0.4
    #: The share is taken over this many most recent content words (0: the
    #: whole window), so a song change isn't outvoted by the last song's words.
    share_of_last: int = 0
    #: Talking score (singing detector, 0 = singing, 1 = talk) at or above
    #: which an off-plan move needs the words.
    off_plan_talking: float = 0.5
    #: ... and at or above which even a planned move needs them.
    on_plan_talking: float = 0.6
    #: ... and for an unplanned jump into another song. 0: such a jump always
    #: needs the words — on talk those jumps landed mid-song on loose matches
    #: ("we want to see", "day and night"), while a real song change is heard
    #: by its own opening words.
    cross_song_talking: float = 0.0
    _heard: deque[tuple[float, str]] = field(default_factory=deque, init=False, repr=False)

    def hear(self, words) -> None:
        """Final transcript words (anything with `.text` and `.t_end`)."""
        for w in words:
            c = content_word(w.text)
            if c and w.t_end is not None:
                self._heard.append((float(w.t_end), c))
        if self._heard:
            newest = self._heard[-1][0]
            while self._heard and self._heard[0][0] < newest - 4 * self.window_s:
                self._heard.popleft()

    def reset(self) -> None:
        self._heard.clear()

    @staticmethod
    def on_plan(setlist: IndexedSetlist, screen: tuple[int, int], target: tuple[int, int]) -> bool:
        (i, s), (j, t) = screen, target
        if i == j:
            item = setlist.item(i)
            if t == s + 1:
                return True
            if item is None:
                return False
            # the same words as the planned next slide, or as the slide on screen
            # (a chorus written out twice): nothing on screen bets against the plan
            want = item.slide(t)
            return want is not None and any(
                (other := item.slide(k)) is not None and other.words == want.words for k in (s, s + 1)
            )
        # the next song in the setlist, from its start
        return j == i + 1 and t <= 1 and setlist.item(j) is not None

    def words_back(self, setlist: IndexedSetlist, target: tuple[int, int], now: float) -> bool:
        item = setlist.item(target[0])
        slide = item.slide(target[1]) if item is not None else None
        if slide is None:
            return False
        want = {c for c in (content_word(w) for w in slide.words) if c}
        if not want:
            # a blank or instrumental slide: nothing to hear, nothing to back it
            return False
        recent = [c for t, c in self._heard if now - self.window_s <= t <= now + 1.0]
        hits = [c for c in recent if c in want]
        tail = recent[-self.share_of_last:] if self.share_of_last else recent
        return (
            len(set(hits)) >= min(self.min_words, len(want))
            and sum(c in want for c in tail) >= self.min_share * len(tail)
        )

    def allows(
        self,
        setlist: IndexedSetlist,
        screen: tuple[int, int] | None,
        target: tuple[int, int],
        now: float,
        talking: float = 1.0,
        band_playing: bool = False,
    ) -> bool:
        """`talking` is the singing detector's smoothed talking score; the
        default treats an unknown room as talk (strictest). `band_playing`
        (`music.band`) overrides it: a band is playing, so this is not talk —
        except for an unplanned jump into another song, which always needs
        its words."""
        if self.min_words <= 0 or screen is None:
            return True
        if band_playing:
            talking = 0.0
        if self.on_plan(setlist, screen, target):
            bar = self.on_plan_talking
        elif target[0] != screen[0]:
            bar = self.cross_song_talking
        else:
            bar = self.off_plan_talking
        return talking < bar or self.words_back(setlist, target, now)
