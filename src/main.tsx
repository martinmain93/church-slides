import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./styles/globals.css";
import "@fontsource/fraunces/600.css";
import { installUiLogForwarding } from "./lib/uiLog";

installUiLogForwarding();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
