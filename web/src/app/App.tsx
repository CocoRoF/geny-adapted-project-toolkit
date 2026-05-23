import { BrowserRouter } from "react-router-dom";

import { AppRouter } from "@/app/router";
import { AuthProvider } from "@/app/providers/AuthProvider";
import { I18nProvider } from "@/app/providers/I18nProvider";

export default function App() {
  return (
    <BrowserRouter>
      <I18nProvider>
        <AuthProvider>
          <AppRouter />
        </AuthProvider>
      </I18nProvider>
    </BrowserRouter>
  );
}
