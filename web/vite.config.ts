/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// `npm run build` writes web/dist, which FastAPI serves at http://localhost:8000.
// `npm run dev` (for working on the page) serves on http://localhost:5173 and forwards
// /api requests to the running API, so both behave like one site.
export default defineConfig({
  plugins: [react()],
  server: {
    host: "localhost", // this PC only
    port: 5173,
    strictPort: true,
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
  build: { outDir: "dist", emptyOutDir: true },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
  },
});
