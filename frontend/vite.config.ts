import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const buildTime = Date.now();

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: false,
    rollupOptions: {
      output: {
        entryFileNames: `assets/[name]-[hash]-${buildTime}.js`,
        chunkFileNames: `assets/[name]-[hash]-${buildTime}.js`,
        assetFileNames: `assets/[name]-[hash]-${buildTime}.[ext]`,
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
