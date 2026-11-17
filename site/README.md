# Church Slides download page

Two static pages: `index.html` (download buttons, system requirements, a short
description) and `changelog.html` (what's new in each version, with downloads of
older ones; its release list is filled from `CHANGELOG.md` by `scripts/changelog.py`).
No build step. The touch icon is copied from `src-tauri/icons` on each release. The favicon
(`favicon.svg`, plus a 32px `favicon.png`) and the header logo (`logo.svg`) are copies of
`brand/church-slides-mark-light.svg`, the favicon squared up with a wider viewBox. The
heading font is Fraunces, self-hosted in `fonts/`. It's hosted on Netlify; the installers are too big for Netlify
(it recommends files under 10 MB) and live as GitHub release assets on the
public repo `martinmain93/church-slides-releases`. The buttons link to
`releases/latest/download/...`, so they always fetch the newest release.

## Releasing

Everything runs on this Mac: GitHub is source control only (Actions are
disabled, because macOS runners bill at 10x).

```sh
# first, describe the release under "## Unreleased" in CHANGELOG.md
scripts/bump_version.sh 0.0.2       # sets the version in 3 files, commits, tags v0.0.2
git push origin main v0.0.2         # the pre-push hook builds and publishes
```

The hook (`scripts/githooks/pre-push`) runs `scripts/release.sh`, which:

1. checks the tree is clean and HEAD is the tag
2. runs the JS and Python tests (`SKIP_TESTS=1` to skip)
3. builds the AI engine and the app for Apple silicon, then Intel (under Rosetta)
4. uploads both DMGs, the signed updater bundles (`*.app.tar.gz`), `latest.json`
   and `SHA256SUMS.txt` to a GitHub release on the releases repo
5. stamps the version and date into this page, builds the changelog page, and
   deploys both to Netlify

Installed apps (0.0.4 onwards) fetch `releases/latest/download/latest.json` at
startup and offer the update. The updater checks a signature from the key at
`~/.tauri/church-slides-updater.key` (path in `scripts/release.local.env`), whose public
half is in `src-tauri/tauri.conf.json`. **Back the key up**: without it, no
installed copy can be updated again except by a manual reinstall.

Then add the Windows installer (experimental):

```sh
scripts/release.sh --windows        # about 30 min
```

PyInstaller can't cross-compile, so `.github/workflows/windows.yml` builds it on
a GitHub Windows runner (started by hand only, never on push; Windows minutes
bill at 2x, macOS at 10x). The script waits for it, signs the installer for the
updater here (the key never goes to GitHub), adds it and the Windows entry in
`latest.json` to the release, and redeploys the page. The page's Windows button
points at the newest release that has a Windows installer.

If any step fails, the push is refused. `git push --no-verify` pushes a tag
without releasing. Only the page changed? `scripts/release.sh --site-only`.

## Performance report

Every build runs `scripts/perf_report.py` on each engine it freezes: the
fixture service in `data/fixtures/perf/` (100 s of a real song in a four-song
setlist) is played into the engine as if it were the microphone, and the
process is sampled from outside: CPU, memory (macOS `phys_footprint`, Windows
private bytes), threads, handles, idle wakeups. Next to those come the engine's
own speech-pass and matcher timings, lag, dropped audio, and how many slides
changed on time. About 2½ minutes on Apple silicon, 1 minute for the Intel
smoke run under Rosetta, and a few minutes on the Windows runner.

Numbers are compared with `data/fixtures/perf/expected.json` per platform.
Worse than expected is a warning in the report; only an extreme outlier or a
crash fails the build. When a change makes things legitimately different,
take the new numbers as the expectation:

```sh
python3 scripts/perf_report.py adopt build/perf/perf-macos-apple-silicon.json
```

The reports are merged into `perf.json` on the GitHub release, and the
changelog page shows its headline numbers under each version.
`SKIP_PERF=1` skips the runs.

## One-time setup on a new machine

```sh
git config core.hooksPath scripts/githooks
# and restore ~/.tauri/church-slides-updater.key from its backup
pnpm --package=netlify-cli@27 dlx netlify login
```

Destinations are in `scripts/release.local.env` (git-ignored; start from `scripts/release.env.example`).

## Preview locally

```sh
python3 -m http.server 8080 --directory site
```
