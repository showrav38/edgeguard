import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server on :5173. MQTT goes straight from the browser to mosquitto's websocket listener (:9001).
export default defineConfig({
  plugins: [react()],
  server: { port: 5173 },
  // iX + recharts + mqtt.js make one ~1.2 MB bundle; fine for a dashboard served on the local network.
  build: { chunkSizeWarningLimit: 1300 },
});
