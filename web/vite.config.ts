/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 개발 서버(pnpm dev)는 로컬 스택의 web(8080)을 거쳐 허브에 붙는다. 포트는 HUB_DEV_URL 로 바꾼다.
const hub = process.env.HUB_DEV_URL ?? "http://localhost:8080";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": { target: hub, changeOrigin: false },
      "/ws": { target: hub.replace(/^http/, "ws"), ws: true },
    },
  },
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
