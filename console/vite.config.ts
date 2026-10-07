import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In dev, the Python console (python -m cua console) serves the API.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://127.0.0.1:8770", "/files": "http://127.0.0.1:8770" } },
});
