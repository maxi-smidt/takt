import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { Gallery } from "./Gallery";
import "../shared/ui/ui.css";
import "./gallery.css";

const root = document.getElementById("root");
if (!root) throw new Error("TAKT UI gallery root element is missing.");
createRoot(root).render(
  <StrictMode>
    <Gallery />
  </StrictMode>,
);
