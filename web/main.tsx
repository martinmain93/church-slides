/** The web app at app.churchslides.app: planning and the library, no presenting. */
import "./theme";
import React from "react";
import ReactDOM from "react-dom/client";

import { WebApp } from "@/web/App";
import "@/styles/globals.css";
import "./web.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <WebApp />
  </React.StrictMode>,
);
