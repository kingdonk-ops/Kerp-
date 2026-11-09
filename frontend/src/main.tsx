import "./index.css";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { AppRoot } from "./app/AppRoot";

const container = document.getElementById("root");
if (!container) {
  throw new Error("Root element #root not found");
}

createRoot(container).render(
  <StrictMode>
    <AppRoot />
  </StrictMode>,
);
