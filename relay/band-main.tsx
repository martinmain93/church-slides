/**
 * The band page as the online relay serves it: just the band screen, none of
 * the desktop app around it (so the Worker stays small).
 */
import React from "react";
import ReactDOM from "react-dom/client";

import { BandView } from "@/components/band/BandView";
import "@/styles/globals.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BandView />
  </React.StrictMode>,
);
