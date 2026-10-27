import "@siemens/ix/dist/siemens-ix/siemens-ix.css";
import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./styles.css";
import { applyTheme, loadTheme } from "./theme";

// A ?theme=light|dark URL parameter wins (used for screenshots); otherwise the remembered choice.
const param = new URLSearchParams(window.location.search).get("theme");
applyTheme(param === "light" || param === "dark" ? param : loadTheme());

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
