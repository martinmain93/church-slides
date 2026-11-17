# Fast channel (parked spike)

A second, low-latency speech listener (sherpa-onnx streaming zipformer, ~1 s
behind the singing against Whisper's ~3 s), meant to let slide switches land
on time instead of catching up. Write-up: `Plan/FAST_CHANNEL.md`.

**Status: parked, not rejected.** Taken out of the app on 2026-10-19: its
runtime was never in the release build (sherpa-onnx is excluded from the
frozen engine), so it only ever ran in dev.

What was measured (2026-09-08, seven approved goldens, MLX Whisper):

- It hears less than half the words Whisper does (1,136 vs 2,680).
- Alone it is *later* and far less accurate (wrong-slide 0.587 vs 0.265):
  with so few words the matcher takes longer to be sure.
- Merged with Whisper it buys ~0.8 s of anticipation for +0.09 wrong-slide
  (0.265 → 0.357) and nearly doubles flicker (17 → 31).
- It fixes short slides (< 4 s: 2 s early → on time) but throws 7–10 s slides
  2 s early.

**The untested idea** is to use it selectively: as a trigger only where a
slide is shorter than Whisper's lag, or as confirmation rather than full
evidence. Worth re-measuring against today's engine, which now has a fast
non-word signal (chords) the September numbers predate.

## Contents

- `engine/fast_channel.py` — was `sidecar/auto_presenter/asr/fast_channel.py`.
- `engine/server_fast_channel.py` — the server wiring (frame fan-out, the
  `set_fast_channel` toggle, its broadcast, and the advance-rate retune when
  it feeds the matcher). Was `sidecar/auto_presenter/server_fast_channel.py`.
- `ui/` — the debug panel's Fast channel tab and its store.
- `tests/` — were `sidecar/tests/asr/test_fast_channel*.py`.
- `setup_fast_channel.py` — installs sherpa-onnx (the sidecar's `fast` extra)
  and the model.

To revive it, check out `fbb0c14` (the last commit with it wired in) for the
protocol messages (`SetFastChannelRequest`, `FastChannelMessage`) and the
`server.py` / `server_state.py` hooks, and move `engine/` back into the
package.
