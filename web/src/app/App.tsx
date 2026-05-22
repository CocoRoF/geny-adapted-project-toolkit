import { useState } from "react";

import { LanguageSwitcher } from "@/i18n/LanguageSwitcher";
import { t, type Locale } from "@/i18n";

export default function App() {
  const [locale, setLocale] = useState<Locale>("ko");

  return (
    <main className="app-shell">
      <header className="app-header">
        <h1>{t("app.title", locale)}</h1>
        <LanguageSwitcher locale={locale} onChange={setLocale} />
      </header>

      <section className="app-body">
        <p>{t("app.phase0", locale)}</p>
        <p>
          <a href="https://github.com/CocoRoF/geny-adapted-project-toolkit">
            {t("app.repo_link", locale)}
          </a>
        </p>
      </section>

      <footer className="app-footer">
        <small>{t("app.footer", locale)}</small>
      </footer>
    </main>
  );
}
