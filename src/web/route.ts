/**
 * The web app's pages, in the address's fragment (`#/songs/abc`) so the app
 * is a static site with one page and the back button works.
 */

import { useEffect, useRef, useState } from "react";

export type Route =
  | { page: "songs" }
  | { page: "song"; id: string; key: string | null; from?: string | null }
  | { page: "edit-song"; id: string | null }
  | { page: "services" }
  | { page: "service"; id: string; created?: boolean }
  | { page: "account" };

export function parseRoute(hash: string): Route {
  const [path = "", query = ""] = hash.replace(/^#/, "").split("?");
  const parts = path.split("/").filter(Boolean).map(decodePart);
  const q = new URLSearchParams(query);
  switch (parts[0]) {
    case "songs":
      if (parts[1] === "new") return { page: "edit-song", id: null };
      if (parts[1] && parts[2] === "edit") return { page: "edit-song", id: parts[1] };
      if (parts[1]) return { page: "song", id: parts[1], key: q.get("key"), from: q.get("from") };
      return { page: "songs" };
    case "services":
      return parts[1] ? { page: "service", id: parts[1], created: q.get("new") === "1" } : { page: "services" };
    case "account":
      return { page: "account" };
    default:
      return { page: "services" };
  }
}

export function href(route: Route): string {
  switch (route.page) {
    case "songs":
      return "#/songs";
    case "song": {
      const q = new URLSearchParams();
      if (route.key) q.set("key", route.key);
      if (route.from) q.set("from", route.from);
      const query = q.toString(); // not `.size`: Safari before 17 lacks it
      return `#/songs/${encodeURIComponent(route.id)}${query ? `?${query}` : ""}`;
    }
    case "edit-song":
      return route.id ? `#/songs/${encodeURIComponent(route.id)}/edit` : "#/songs/new";
    case "services":
      return "#/services";
    case "service":
      return `#/services/${encodeURIComponent(route.id)}${route.created ? "?new=1" : ""}`;
    case "account":
      return "#/account";
  }
}

/** A malformed escape (`%E0`) is kept as typed rather than crashing the page. */
function decodePart(p: string): string {
  try {
    return decodeURIComponent(p);
  } catch {
    return p;
  }
}

export function go(route: Route, replace = false): void {
  if (replace) window.location.replace(href(route));
  else window.location.hash = href(route);
}

/** Asked before leaving the page (unsaved changes). Returns false to stay;
 * it may then ask, and call `go` itself once the person chooses to leave. */
type LeaveGuard = (to: Route) => boolean;
let guard: LeaveGuard | null = null;

/** Hold the page while `when` (unsaved changes): leaving calls `ask` with
 * where the person was going. Closing the tab asks the browser's question. */
export function useLeaveGuard(when: boolean, ask: (to: Route) => void): void {
  const askRef = useRef(ask);
  askRef.current = ask;
  useEffect(() => {
    if (!when) return;
    const mine: LeaveGuard = (to) => {
      askRef.current(to);
      return false;
    };
    guard = mine;
    const unload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      // Older Safari and Chrome ask only when this is set.
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", unload);
    return () => {
      if (guard === mine) guard = null;
      window.removeEventListener("beforeunload", unload);
    };
  }, [when]);
}

export function useRoute(): Route {
  const [route, setRoute] = useState(() => parseRoute(window.location.hash));
  useEffect(() => {
    let shown = window.location.hash;
    const on = () => {
      const next = parseRoute(window.location.hash);
      if (guard && !guard(next)) {
        // Stay: put the address back without another hashchange.
        window.history.replaceState(null, "", shown || "#");
        return;
      }
      shown = window.location.hash;
      setRoute(next);
      window.scrollTo(0, 0);
    };
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return route;
}

/** Leave without asking (after the person chose to discard). */
export function goUnguarded(route: Route): void {
  guard = null;
  go(route);
}
