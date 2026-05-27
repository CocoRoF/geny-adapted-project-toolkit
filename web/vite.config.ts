import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { VitePWA } from "vite-plugin-pwa";

export default defineConfig({
  // SPA lives under /_gapt/app/ — Caddy fans /_gapt/app/* here and
  // 302s the apex root to /_gapt/app/. Reserving a single prefix
  // for GAPT lets preview apps emit any root-relative URL without
  // colliding with GAPT itself.
  base: "/_gapt/app/",
  plugins: [
    react(),
    tailwindcss(),
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
        start_url: "/_gapt/app/projects",
        scope: "/_gapt/app/",
        icons: [
          {
            src: "vite.svg",
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
        navigateFallback: "/_gapt/app/index.html",
        navigateFallbackDenylist: [/^\/_gapt\/api\//, /^\/preview\//],
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
    // GAPT port convention: 3xxxx prefix. Vite dev → 35173 (was 5173).
    // The host server (uvicorn) → 38001 (was 8001). Cloudflare tunnel
    // for `gapt.hrletsgo.me` points at Caddy on 38080; bare vite is
    // only used during local development without Caddy in front.
    port: 35173,
    proxy: {
      // Pure-vite local dev (without Caddy in front): proxy
      // /_gapt/api → backend on 38001. Caddy already does this in
      // the normal dev path; the proxy is for the rare case of
      // hitting http://localhost:35173/_gapt/app/ directly.
      "/_gapt/api": {
        target: "http://localhost:38001",
        changeOrigin: false,
        ws: true,
      },
      "/health": {
        target: "http://localhost:38001",
        changeOrigin: false,
      },
      "/metrics": {
        target: "http://localhost:38001",
        changeOrigin: false,
      },
    },
    // Allow the tunnel hostname through Vite's HMR host check.
    // `allowedHosts: true` accepts any Host header — fine in dev,
    // but the explicit list is preferred when known.
    allowedHosts: ["localhost", ".hrletsgo.me", ".trycloudflare.com"],
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
