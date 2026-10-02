import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    watch: {
      ignored: [
        "**/src-tauri/target/**",
        "**/src-tauri/sidecar-source/build/**",
        "**/src-tauri/sidecar-source/dist/**",
        // Datasets and exports are user data, not source. Watching them
        // crashes the dev server with EBUSY when a file is locked by the
        // scientific engine (e.g. an open CSV/Parquet).
        "**/data test/**",
        "**/data/**",
        "**/*.csv",
        "**/*.tsv",
        "**/*.parquet",
        "**/*.xlsx",
        "**/*.xls",
      ],
    },
  },
  envPrefix: ["VITE_", "TAURI_ENV_"],
  build: { target: "chrome105", sourcemap: true },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
    css: true,
  },
});
