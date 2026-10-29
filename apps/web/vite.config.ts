/// <reference types="vitest" />
import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const target = env.CLEARANCE_API_URL || "http://localhost:8000";
  return {
    base: env.CLEARANCE_WEB_BASE || "/",
    plugins: [react()],
    server: {
      host: true,
      port: 5173,
      proxy: {
        "/api": {
          target,
          changeOrigin: true,
          ws: true,
          rewrite: (p) => p.replace(/^\/api/, ""),
        },
      },
    },
    build: {
      target: "es2020",
      chunkSizeWarningLimit: 900,
      rollupOptions: {
        output: {
          manualChunks: {
            react: ["react", "react-dom", "react-router-dom"],
          },
        },
      },
    },
    test: {
      environment: "node",
      include: ["src/**/*.test.ts"],
    },
  };
});
