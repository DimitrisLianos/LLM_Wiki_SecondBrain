import { defineConfig } from "vite";

export default defineConfig({
  build: {
    outDir: "dist",
    emptyOutDir: true,
    // disable the modulepreload polyfill — it emits `data:text/javascript;base64,…`
    // scripts at runtime which our CSP (`script-src 'self'`) correctly blocks.
    // all target browsers (safari 17+, chrome 66+, firefox 115+) support
    // <link rel="modulepreload"> natively.
    modulePreload: {
      polyfill: false,
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:3000",
        changeOrigin: true,
      },
    },
  },
});
