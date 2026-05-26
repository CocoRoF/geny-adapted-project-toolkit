import { type ReactNode } from "react";
import { Link, NavLink } from "react-router-dom";
import { LogOut } from "lucide-react";

import { ThemeSwitcher } from "@/app/ThemeSwitcher";
import { useAuth } from "@/app/providers/auth-context";
import { useI18n } from "@/app/providers/i18n-context";
import { LanguageSwitcher } from "@/i18n/LanguageSwitcher";
import { NotificationBell } from "@/notifications/NotificationBell";
import { Button } from "@/ui/Button";
import { cn } from "@/ui/cn";

/** Top-level chrome shared by every authenticated route.
 *
 * Topbar: brand, primary nav, signed-in actions (bell/theme/lang/sign-out).
 * The main area gives routes a full-bleed container — each route owns
 * its own padding so the IDE shell can reach edge-to-edge.
 */
export function AppShellLayout({ children }: { children: ReactNode }) {
  const { t, locale, setLocale } = useI18n();
  const { me, signOut, status } = useAuth();
  const signedIn = status === "signed_in" && !!me;

  return (
    <div className="flex h-full flex-col bg-bg text-fg">
      <header className="sticky top-0 z-30 flex h-12 shrink-0 items-center gap-4 border-b border-border bg-bg-elevated/90 px-4 backdrop-blur">
        <Link to="/projects" className="flex items-center gap-2 text-fg hover:text-accent">
          <span
            aria-hidden
            className="grid h-6 w-6 place-items-center rounded bg-accent text-[11px] font-bold text-accent-fg"
          >
            G
          </span>
          <span className="text-[13px] font-semibold tracking-tight">{t("app.title")}</span>
        </Link>

        {signedIn ? (
          <nav className="hidden items-center gap-1 sm:flex">
            <TopLink to="/projects">{t("nav.projects")}</TopLink>
            <TopLink to="/cost">{t("nav.cost")}</TopLink>
            <TopLink to="/performance">{t("nav.performance")}</TopLink>
            <TopLink to="/settings">{t("nav.settings")}</TopLink>
          </nav>
        ) : null}

        <div className="ml-auto flex items-center gap-2">
          {signedIn ? <NotificationBell /> : null}
          <ThemeSwitcher />
          <LanguageSwitcher locale={locale} onChange={setLocale} />
          {signedIn ? (
            <div className="ml-1 flex items-center gap-2 border-l border-border pl-3">
              <span
                title={me.display_name ?? me.user_id}
                className="hidden max-w-[180px] truncate text-[12px] text-fg-muted md:block"
              >
                {me.display_name ?? me.user_id}
              </span>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => void signOut()}
                aria-label={t("auth.logout")}
                title={t("auth.logout")}
              >
                <LogOut className="h-3.5 w-3.5" />
                <span className="hidden md:inline">{t("auth.logout")}</span>
              </Button>
            </div>
          ) : null}
        </div>
      </header>

      <main className="flex-1 overflow-y-auto">{children}</main>

      <footer className="shrink-0 border-t border-border bg-bg-elevated px-4 py-2">
        <small className="text-[11px] text-fg-subtle">{t("app.footer")}</small>
      </footer>
    </div>
  );
}

function TopLink({ to, children }: { to: string; children: ReactNode }) {
  return (
    <NavLink
      to={to}
      className={({ isActive }) =>
        cn(
          "rounded-md px-2.5 py-1.5 text-[13px] font-medium transition-colors",
          isActive
            ? "bg-bg text-fg shadow-[inset_0_-2px_0_var(--color-accent)]"
            : "text-fg-muted hover:bg-surface-hover hover:text-fg",
        )
      }
    >
      {children}
    </NavLink>
  );
}
