import { type ReactNode } from "react";
import { Link } from "react-router-dom";

import { ThemeSwitcher } from "@/app/ThemeSwitcher";
import { useAuth } from "@/app/providers/auth-context";
import { useI18n } from "@/app/providers/i18n-context";
import { LanguageSwitcher } from "@/i18n/LanguageSwitcher";
import { NotificationBell } from "@/notifications/NotificationBell";

/** Top-level chrome shared by every authenticated route. Header has
 * locale switcher + sign-out; main slot is the route. */
export function AppShellLayout({ children }: { children: ReactNode }) {
  const { t, locale, setLocale } = useI18n();
  const { me, signOut, status } = useAuth();

  return (
    <div className="app-shell">
      <header className="app-header">
        <Link to="/projects">
          <h1>{t("app.title")}</h1>
        </Link>
        <nav className="app-header-actions">
          {status === "signed_in" && me ? <NotificationBell /> : null}
          <ThemeSwitcher />
          <LanguageSwitcher locale={locale} onChange={setLocale} />
          {status === "signed_in" && me ? (
            <button type="button" className="app-header-signout" onClick={() => void signOut()}>
              {me.email} · {t("auth.logout")}
            </button>
          ) : null}
        </nav>
      </header>
      <main className="app-body">{children}</main>
      <footer className="app-footer">
        <small>{t("app.footer")}</small>
      </footer>
    </div>
  );
}
