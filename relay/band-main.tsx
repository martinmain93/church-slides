/**
 * The band page as the online relay serves it: just the band screen, none of
 * the desktop app around it (so the Worker stays small).
 */
import React from "react";
import ReactDOM from "react-dom/client";

import { BandView } from "@/components/band/BandView";
import "@/styles/globals.css";

// A hosted band link lives in the fragment, and opening another link in the
// same tab changes only the fragment: start again with the new one.
window.addEventListener("hashchange", () => window.location.reload());

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BandView />
  </React.StrictMode>,
);
