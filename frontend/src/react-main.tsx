import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./react/App";
import "./styles/index.css";

const root = document.getElementById("root");
if (!root) throw new Error("React root #root is missing");

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
