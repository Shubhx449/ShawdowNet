import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The backend (python3 -m backend.api 8000) serves REST at /api and WebSocket at /ws.
// Proxying both means the browser only ever talks to the Vite origin: no CORS, no hard-coded ports.
const BACKEND = "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  test: { environment: "jsdom", setupFiles: ["./src/__tests__/setup.js"] },
  server: {
    port: 5173,
    proxy: {
      "/api": BACKEND,
      "/ws": { target: BACKEND, ws: true },
    },
  },
});
