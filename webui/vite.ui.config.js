import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev-only entry for the shared/ui component gallery (`npm run dev:ui`).
// Deliberately has no `build` target wired into build_web_ui.sh /
// build_registry_ui.sh — the gallery never ships in either app's static
// assets, it only exists to preview shared/ui components on this machine.
export default defineConfig({
  base: "./",
  plugins: [react()],
  build: {
    rollupOptions: {
      input: "ui.html",
    },
  },
  server: {
    host: process.env.TAKT_UI_HOST || "127.0.0.1",
    port: Number(process.env.TAKT_UI_PORT) || 5175,
  },
});
