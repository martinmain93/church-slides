/**
 * Light or dark, following the device (the phone's own setting): dark in a
 * dim room, light in daylight. Run before the first paint so the page
 * doesn't flash the wrong one. Not an inline script: the page's security
 * policy only allows its own script files.
 */
const query = window.matchMedia?.("(prefers-color-scheme: dark)");

function apply(dark: boolean) {
  document.documentElement.classList.toggle("dark", dark);
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", dark ? "#0d0c11" : "#ffffff");
}

apply(query ? query.matches : true);
query?.addEventListener?.("change", (e) => apply(e.matches));
