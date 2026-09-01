import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./styles/tokens.css";
import PublicApp from "./pages/PublicApp";

// Flask injects `window.__COMP__` (the competition dict) and `window.__COMP_ID__`
// via a small <script> tag in templates/public.html — see backend integration notes.
const comp = window.__COMP__ || null;
const compId = window.__COMP_ID__ || comp?.id;

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <PublicApp compId={compId} comp={comp} />
  </StrictMode>
);
