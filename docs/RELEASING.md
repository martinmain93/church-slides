# Releasing

How a version of Church Slides goes out, and what to check first. The
mechanics are scripted (`scripts/bump_version.sh`, `scripts/release.sh`); this
is the list of things a script can't judge.

## Every release

1. **Changelog.** Every change people will notice has a line under
   "Unreleased" in `CHANGELOG.md`, in plain words. It becomes the update
   pop-up, the release notes and the site's changelog page.
2. **Tests pass on main**, and anything previewable was seen working in the
   app.
3. **Bump and push:** `scripts/bump_version.sh X.Y.Z`, then
   `git push origin main vX.Y.Z`. The pre-push hook runs `release.sh`: tests,
   Apple-silicon and Intel builds, signing, the GitHub release, the update
   feed and the website. About 20 minutes; Low Power Mode off, plugged in.
4. **Windows:** `scripts/release.sh --windows vX.Y.Z` (about 30 minutes on
   GitHub's Windows runner).
5. **Check it's out:** the update feed (`latest.json` on the releases repo)
   lists the version for every platform, and churchslides.app shows it.
6. **Server first.** If the release uses a server change, deploy the server to
   production before releasing the app (see the server repo's README).

## Privacy and terms

The privacy page (`site/privacy.html`) and terms (`site/terms.html`) promise
specific things. A release must not make either of them untrue.

**With every change**, not just at release time: a change that sends, stores,
keeps or shows people's data differently (a new thing sent to us or another
company, a new server table, a new retention time, who in a team can see
what) updates the privacy page in the same change. A new feature that needs
an account, takes payment or changes what people may do updates the terms.

**Before a major release** (X.Y.0, or the first release where a preview
feature is switched on for everyone), review both pages against the app and
the server as they are, not as remembered:

- [ ] **What's sent.** Read what the app actually sends (`src/lib/usage.ts`,
      `src-tauri/src/telemetry.rs`, `src-tauri/src/feedback.rs`, `stats/`,
      every `account::api` call) and what the server stores (its D1
      migrations and `src/org/schema.ts`). Everything sent or stored is on
      the privacy page; nothing on the page is no longer true.
- [ ] **How long, and deleting.** Each retention time on the page matches the
      code, and each way to delete something described there really exists
      (a button, or a process we actually follow).
- [ ] **Who sees what.** Roles and visibility on the page match the server's
      role matrix (`src/org/roles.ts`) and what each screen shows.
- [ ] **Other companies.** Every company that receives data (Sentry,
      Cloudflare, Google, Hugging Face, Netlify, GitHub, any new one) is
      named, with where it is.
- [ ] **Terms cover what people can do.** Anything new people can do (an
      account feature, sharing, a paid plan) is covered, and nothing the terms
      describe has been removed.
- [ ] **Consistent with each other.** The two pages agree (the terms point to
      the privacy page for data), and the app's own words agree with both
      (Settings → Privacy, the first-run prompt, Settings → Account).
- [ ] **Dates and changelog.** "Last updated" is today on any page that
      changed, and a significant change has a line in `CHANGELOG.md`.
- [ ] **Mark it reviewed:** set `<meta name="reviewed-for" content="X.Y">` in
      both pages. `release.sh` refuses an X.Y.0 release until both say X.Y,
      so the review can't be skipped by accident.

The pages promise little on purpose: say what's true and what we can keep
doing, not more.
