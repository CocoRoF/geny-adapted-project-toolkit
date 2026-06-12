/** SPA basename — mirrors Vite's `base: "/_gapt/app/"`. Every
 * react-router path (`/projects`, `/login`, ...) is implicitly
 * prefixed at the URL level, so the catch-all 404 in Caddy outside
 * `/_gapt/*` is what actually fires for leaked preview URLs — by
 * design. Lives in its own module (not App.tsx) so non-component
 * consumers (e.g. the chat pop-out's `window.open`) can import it
 * without tripping react-refresh's only-export-components rule. */
export const ROUTER_BASENAME = "/_gapt/app";
