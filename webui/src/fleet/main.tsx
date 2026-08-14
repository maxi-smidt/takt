import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import FleetApp from "./FleetApp";

const root = document.getElementById("root");
if (!root) throw new Error("TAKT Fleet root element is missing.");
createRoot(root).render(<StrictMode><FleetApp /></StrictMode>);
