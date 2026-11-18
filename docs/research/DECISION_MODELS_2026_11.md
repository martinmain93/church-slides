# Small decision models, November 2026

**Question:** the last two months brought a wave of small, fast, locally
runnable "decision models" and better small tool-calling models. Do any of
them belong in the app?

**Short answer:** not yet. The one place they could matter is the on-device
fallback for spoken requests. That fallback is weaker than we thought (20/45),
and no new small model measured so far beats it safely. The purpose-built
decision models still don't fit how we ask questions.

## Where the app makes text decisions

| Place | Today | Could a small local model help? |
| --- | --- | --- |
| Spoken requests (`decision/instructions.py`) | Hosted `gpt-oss-20b` on Groq, `qwen2.5:3b-instruct` when offline | **Yes, the fallback.** It only runs when the network is gone, so it has to be good on its own |
| "Is this a request at all?" before the model is called | Lexical + acoustic wake gate | Possibly: a calibrated yes/no is exactly what decision models sell |
| Following the singing | Score follower on audio + ASR | No. The error is audio lag and confidence, not a text decision ([LIVE_LATENCY](LIVE_LATENCY.md)) |
| Chord suggestions, voice charting, scripture extraction | Hosted, editor-time only | Low value: not live, a person approves the result, and the hosted model is fine |

## The landscape

**Decision models** (a typed question in, a choice plus a probability out, no
free text):

- **Jev** (TypeSafe), closed and hosted. It started the category.
- **Strands Decider 2B** (AWS, Apache-2.0) and **Quyet 1.0** (Apache-2.0,
  153M to 4.7B). We tried both on 2026-10-05: routing 7/19 and 8/19. Decider's
  advertised ~115 ms took 2.7 s on an M1 because its conv kernel has no Mac path.
- **Liquid d1-3B / d1-2B / d1-omni-600M** (Oct 7 and Oct 27). These use the
  LFM Open License, which is free only below $10M revenue. That's not
  MIT-compatible, so they can't ship as a dependency.
- **LEV** (arXiv 2610.20951) loops its layers instead of generating tokens:
  1.4B to 3B, under 50 ms, and 63% → 76% on JevBench as the loop deepens. It's
  research code, and we found no released weights.
- The **JevBench** leaders are about 12B (Cygnet on Gemma 4 12B, Winnow-12B)
  or 4B (JevK5, Plumb-4B). Licences are mostly unstated.

**Small general tool-callers:** Qwen3.5 (0.8B, 2B, 4B, 9B), Gemma 4 E2B/E4B
(Apache-2.0, native tool tokens) and LFM2.5 (same licence problem as d1).
Vendor and blog benchmarks put Qwen3.5-4B at the top below 7B.

**Why decision models still don't fit:** our router's answers aren't a fixed
list of options. They are "song 2, bridge", a search query or a Bible
reference. A decision model can only pick between options we list, and the
last spike showed they default to "the song we're on". The yes/no gate is the
real fit. Decider did separate requests from lyrics there (AUC 0.93), but its
probabilities weren't calibrated for our domain.

## Spike: the spoken-request benchmark on local models

`research/eval/bench_spoken_requests.py` sends 47 typed utterances (song
requests, scripture, half-said requests, sung lines, a live set's verbatim
lines) to each model, using the real prompt and tools. 45 are scored. Run on
an M1 with 16 GB through ollama, at temperature 0, thinking off unless noted.
The runner just points the bench at an arbitrary ollama tag, and its output is
in `auto-presenter-research/work/decision_models_2026_11/`.

| Model | Size | Right | Median s/call | Requests: go / search / show | **Not requests: none** |
| --- | --- | ---: | ---: | --- | ---: |
| qwen2.5:3b-instruct (shipped fallback) | 1.9 GB | 20/45 | 1.8 | 9/10, 2/5, 1/11 | 7/10 |
| qwen2.5:7b-instruct | 4.7 GB | 34/45 | 4.6 | 9/10, 4/5, 11/11 | 5/10 |
| qwen3.5:0.8b | 1.3 GB | 19/45 | 1.8 | 8/10, 1/5, 8/11 | **0/10** |
| qwen3.5:0.8b, thinking on | 1.3 GB | 17/45 | 7.8 | 5/10, 3/5, 6/11 | 2/10 |

No model got a half-said request ("Okay let's go to…") right: `wait_for_more`
scored 0/3 for every model.

What it says:

1. **The fallback has fallen behind the benchmark.** It scored 13/15 when it
   was chosen (2026-09-17), before scripture was added. Now it gets 1 in 11
   scripture requests right. An offline church that asks for John 3:16 mostly
   gets nothing.
2. **Qwen3.5 0.8B ties the score, but it would never ship.** It acts on all
   ten things that aren't requests ("Amen, come on somebody", a lyric line).
   A wrong move in front of the room is worse than a missed one, so the
   *none* column matters more than the total.
3. **Thinking doesn't pay here:** 4x slower and slightly worse.
4. Latency at this size is mostly prefill. The prompt and six tools run to
   thousands of tokens, so 0.8B is no faster than 3B on an M1.

**Not measured:** qwen3.5:2b and 4b, and gemma4:e2b and e4b. The disk filled
during the download (about 2 GB free). These are the candidates that matter.

## Recommendation

- Don't adopt a decision-model class (Jev, Decider, Quyet, d1). Our questions
  are open-slot, and the open models we can license don't route.
- **Do re-pick the offline fallback.** Run qwen3.5:4b and gemma4:e4b (and the
  2B/E2B sizes) through this bench, and judge them on *none* first, then
  scripture, then latency. The bar is qwen2.5:7b's 34/45 at under ~2.5 s. If
  nothing below 3 GB reaches it, consider offering 7B to machines that can
  carry it.
- Report the false-action rate (*none*) as its own number in the bench. A
  single total hides the failure that matters most.
- Revisit decision models only for the yes/no request gate, and only once one
  ships calibrated probabilities with an MIT-compatible licence and a Mac
  path under ~200 ms.

## Sources

- [Strands Decider 2B](https://strandsagents.com/blog/introducing-strands-decider/), [The New Stack](https://thenewstack.io/aws-strands-decider-model/)
- [Quyet](https://github.com/ncchinh/quyet)
- [Decision-model leaderboard (BenchLM, JevBench v1.5.4)](https://benchlm.ai/decision-models)
- [LEV: looped decision models](https://arxiv.org/abs/2610.20951)
- [LFM2.5-2.6B](https://www.liquid.ai/en/blog/lfm2-5-2-6b)
- [On-device tool calling: Qwen3, Gemma 4, Phi-4](https://www.ertas.ai/blog/on-device-tool-calling-2026-qwen3-gemma4-phi4)
