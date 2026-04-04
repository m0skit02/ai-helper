import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/health": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/chat": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/task": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/action": { target: "http://127.0.0.1:8000", changeOrigin: true },
    },
  },
});
