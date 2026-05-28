import { BrowserRouter } from "react-router-dom";

import { AppPaletteActions } from "@/app/AppPaletteActions";
import { CommandPalette } from "@/app/CommandPalette";
import { ProjectsPaletteActions } from "@/app/ProjectsPaletteActions";
import { AppRouter } from "@/app/router";
import { AuthProvider } from "@/app/providers/AuthProvider";
import { I18nProvider } from "@/app/providers/I18nProvider";
import { PaletteProvider } from "@/app/providers/PaletteProvider";
import { ThemeProvider } from "@/app/providers/ThemeProvider";

// SPA basename — mirrors Vite's `base: "/_gapt/app/"`. Every
// react-router path (`/projects`, `/login`, `/projects/:pid`...) is
// implicitly prefixed with `/_gapt/app` at the URL level, so the
// catch-all 404 in Caddy outside `/_gapt/*` is what actually fires
// for leaked preview URLs — by design.
const ROUTER_BASENAME = "/_gapt/app";

export default function App() {
  return (
    <BrowserRouter basename={ROUTER_BASENAME}>
      <ThemeProvider>
        <I18nProvider>
          <AuthProvider>
            <PaletteProvider>
              <AppPaletteActions />
              <ProjectsPaletteActions />
              <CommandPalette />
              <AppRouter />
            </PaletteProvider>
          </AuthProvider>
        </I18nProvider>
      </ThemeProvider>
    </BrowserRouter>
  );
}
