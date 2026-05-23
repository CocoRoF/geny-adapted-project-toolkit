import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";

export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: "autoUpdate",
      manifest: {
        name: "GAPT — geny-adapted-project-toolkit",
        short_name: "GAPT",
        description:
          "Self-hosted AI DevOps platform — projects, workspaces, AI sessions in one browser tab.",
        theme_color: "#0b0d10",
        background_color: "#0b0d10",
        display: "standalone",
        start_url: "/projects",
        scope: "/",
        icons: [
          {
            src: "/vite.svg",
            sizes: "192x192",
            type: "image/svg+xml",
            purpose: "any maskable",
          },
        ],
      },
      workbox: {
        // Service worker is intentionally cache-light — we cache the
        // shell assets so the IDE chrome loads offline-fast, but the
        // API responses always go through the network. Offline shell
        // is a stretch goal; cache freshness is non-negotiable.
        navigateFallback: "/index.html",
        navigateFallbackDenylist: [/^\/api\//],
        runtimeCaching: [],
      },
    }),
  ],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    host: "0.0.0.0",
    port: 5173,
  },
  build: {
    target: "es2022",
    sourcemap: true,
    rollupOptions: {
      output: {
        manualChunks: {
          // Heavy deps get their own chunks so the initial paint
          // doesn't ship Monaco when the user is on /login.
          monaco: ["@monaco-editor/react"],
          dockview: ["dockview"],
          cmdk: ["cmdk"],
        },
      },
    },
  },
});
