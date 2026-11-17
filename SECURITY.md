# Security

Please report security problems privately, not in a public issue. Use
**Report a vulnerability** on the repository's Security tab (GitHub's private
vulnerability reporting), or email martinmain93@gmail.com.

Include what you found, how to reproduce it, and which version and platform
you used. You'll get a reply within a week. Please give us a reasonable chance
to ship a fix before you publish anything.

Worth knowing when you look:

- The app runs services offline. Its network surfaces are the band and leader
  screens on the local network, laptop-to-laptop setlist sharing, the online
  band relay (`relay/`), the in-app updater, and opt-in error reports and
  usage statistics.
- Updates are signed; the app installs only updates signed by the project's
  key (public half in `src-tauri/tauri.conf.json`).
- Only the latest release gets security fixes.
