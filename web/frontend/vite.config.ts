import { defineConfig } from "vitest/config";
import { svelte } from "@sveltejs/vite-plugin-svelte";

export default defineConfig({
  base: "/",
  plugins: [svelte()],
  build: {
    outDir: "../src/usage_web/static",
    emptyOutDir: true,
  },
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8080",
      "/auth": "http://127.0.0.1:8080",
    },
  },
  test: { include: ["src/**/*.test.ts"] },
});
