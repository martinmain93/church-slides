"""Learning how this church plays its songs (docs/done/LEARNED_REFERENCES.md).

An observer beside the live engine, never inside it:

* `session.LearningSession` logs what the session already produces (the
  transcript's timed words, every slide shown and who showed it) and, in a
  background thread a little behind live, aligns the words to the song
  (`lookback`) using only library order — no learned prior and no engine
  decision, so what it learns cannot feed on itself.
* When a song ends, its performance becomes one `summary.PerformanceSummary`
  row in the sidecar's own store (`store`): the order the band sang, when each
  line started and how its words fell, and how much to trust it.
* `profile` aggregates a song's rows on load into what the follower can use.

Nothing here changes what the screen shows until a reference built from it is
handed to the follower.
"""
