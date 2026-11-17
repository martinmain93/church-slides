# Performance fixture

What `scripts/perf_report.py` plays into the AI engine on every build.

- `service.wav`: the first 100 seconds of "Thank You" by Rising Tides
  (16 kHz mono), written by Martin Main. It isn't covered by the repository's
  MIT licence: you may copy and redistribute it unchanged, as part of this
  repository or a fork of it, to test this software. All other rights are
  reserved.
- `service.json`: a four-song setlist (the slides the app would build), where
  "Thank You" is song 2 and the other three are public-domain hymns (Amazing
  Grace; It Is Well With My Soul, 1873; Great Is Thy Faithfulness, 1923) so
  the song tracker has realistic competition. Also the expected slide changes
  in the clip, which were machine-estimated by `research/eval/bootstrap_golden.py`
  rather than checked by hand, so the "slides on time" score is a trend to
  watch, not a precise measure.
- `expected.json`: what each platform's build measured last time it was
  accepted (`perf_report.py adopt` / `--update-expected`).
