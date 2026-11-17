## What and why

<!-- One or two sentences. For a change users can see, link the approved Proposal issue. -->

Approved issue: #

## Checklist

See [CONTRIBUTING.md](../CONTRIBUTING.md) for what each line means.

- [ ] **Approved story.** A change users can see links an issue labelled `approved`, and builds only what it says. Otherwise, it's a fix, test, doc or speedup with no visible change.
- [ ] **Service-safe.** It works with no internet, nothing slow runs on the UI thread, and a failure falls back without a dialog in front of the room.
- [ ] **No clutter.** No new permanent buttons, badges or panels on the operator, projector, leader or band screens. No new setting that could be a default instead.
- [ ] **Screenshots.** Before and after, for every screen that changed.
- [ ] **Benchmarks.** No number got worse beyond noise on Apple-silicon Mac, Intel Mac or Windows. Engine changes have before and after numbers below. Any trade-off is named.
- [ ] **Tests.** New behaviour has tests, and `pnpm test`, `pytest` and `cargo test` pass.
- [ ] **Free and local.** No licence or plan checks, and no new data leaving the computer.
- [ ] **Shareable content.** Public-domain lyrics only, and new dependencies are MIT-compatible with notices updated.
- [ ] **Signed off.** Every commit has `Signed-off-by` (`git commit -s`).

## Numbers

<!-- Engine or performance changes: before -> after, per platform. Delete if not relevant. -->
