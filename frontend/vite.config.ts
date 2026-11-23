import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
const port = process.env.STUDIO_PORT || "7860";
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": { target: "http://127.0.0.1:" + port, changeOrigin: false },
    },
  },
  test: { environment: "jsdom", include: ["src/**/*.test.tsx"] },
});
