import { BrowserRouter } from "react-router-dom";

import { AppPaletteActions } from "@/app/AppPaletteActions";
import { CommandPalette } from "@/app/CommandPalette";
import { AppRouter } from "@/app/router";
import { AuthProvider } from "@/app/providers/AuthProvider";
import { I18nProvider } from "@/app/providers/I18nProvider";
import { PaletteProvider } from "@/app/providers/PaletteProvider";
import { ThemeProvider } from "@/app/providers/ThemeProvider";

export default function App() {
  return (
    <BrowserRouter>
      <ThemeProvider>
        <I18nProvider>
          <AuthProvider>
            <PaletteProvider>
              <AppPaletteActions />
              <CommandPalette />
              <AppRouter />
            </PaletteProvider>
          </AuthProvider>
        </I18nProvider>
      </ThemeProvider>
    </BrowserRouter>
  );
}
