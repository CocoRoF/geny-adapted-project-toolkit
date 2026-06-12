import { BrowserRouter } from "react-router-dom";

import { AppPaletteActions } from "@/app/AppPaletteActions";
import { CommandPalette } from "@/app/CommandPalette";
import { ProjectsPaletteActions } from "@/app/ProjectsPaletteActions";
import { AppRouter } from "@/app/router";
import { AuthProvider } from "@/app/providers/AuthProvider";
import { I18nProvider } from "@/app/providers/I18nProvider";
import { PaletteProvider } from "@/app/providers/PaletteProvider";
import { ThemeProvider } from "@/app/providers/ThemeProvider";
import { AppToaster } from "@/ui/AppToaster";

import { ROUTER_BASENAME } from "@/app/basename";

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
              <AppToaster />
            </PaletteProvider>
          </AuthProvider>
        </I18nProvider>
      </ThemeProvider>
    </BrowserRouter>
  );
}
