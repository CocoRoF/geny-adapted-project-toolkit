/** Walks the operator through fixing the "Cloudflare wildcard
 * cert missing" failure case — the single remaining manual step
 * once the provider + tunnel + ingress are all green.
 *
 * Three escape hatches (in priority order):
 *
 *   1. **Total TLS** — free, works on all plans, auto-issues
 *      certs for every subdomain in the zone. One API call away
 *      when the token has the right scope; otherwise a deep-link
 *      to the dashboard.
 *
 *   2. **Advanced Certificate** — $10/mo per zone, fully manual
 *      via dashboard. Surfaced as a deep-link.
 *
 *   3. **Custom Hostnames** — Enterprise only, mentioned for
 *      completeness; we don't offer in-app automation.
 *
 * After the operator acts (or thinks they have), a "재검증"
 * button re-runs the diagnose and reports whether the e2e
 * handshake now succeeds. */
import { useCallback, useEffect, useState } from "react";
import { ExternalLink, Loader2, RefreshCw, ShieldCheck, X, Zap } from "lucide-react";

import { ApiError } from "@/api/client";
import { diagnoseSubdomainMode, type SubdomainDiagnose } from "@/api/environments";
import {
  type CertStatusResponse,
  enableTotalTls,
  getCertStatus,
} from "@/api/providers";
import { useI18n } from "@/app/providers/i18n-context";
import { Button } from "@/ui/Button";

interface Props {
  open: boolean;
  onClose: () => void;
}

export function WildcardCertGuide({ open, onClose }: Props) {
  const { t } = useI18n();
  const [cert, setCert] = useState<CertStatusResponse | null>(null);
  const [diag, setDiag] = useState<SubdomainDiagnose | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState<"enable" | "verify" | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const [c, d] = await Promise.all([
        getCertStatus(),
        diagnoseSubdomainMode().catch(() => null),
      ]);
      setCert(c);
      setDiag(d);
    } catch (e) {
      setErr(
        e instanceof ApiError
          ? e.reason
          : e instanceof Error
            ? e.message
            : String(e),
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (open) void refresh();
  }, [open, refresh]);

  if (!open) return null;

  const enable = async () => {
    setBusy("enable");
    setErr(null);
    setOutcome(null);
    try {
      const r = await enableTotalTls();
      setOutcome(r.message);
      if (r.ok) {
        // Refresh status so the UI flips to "Total TLS enabled".
        await refresh();
      }
    } catch (e) {
      setErr(
        e instanceof ApiError
          ? e.reason
          : e instanceof Error
            ? e.message
            : String(e),
      );
    } finally {
      setBusy(null);
    }
  };

  const reverify = async () => {
    setBusy("verify");
    setErr(null);
    setOutcome(null);
    try {
      await refresh();
      if (diag?.e2e_reachable) {
        setOutcome(t("cert_guide.verify.ok"));
      }
    } finally {
      setBusy(null);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 px-4 py-6"
      onClick={onClose}
    >
      <div
        className="relative max-h-[90vh] w-full max-w-2xl overflow-auto rounded-lg border border-border bg-bg shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="sticky top-0 z-10 flex items-center justify-between border-b border-border bg-bg px-4 py-3">
          <div className="flex items-center gap-2">
            <ShieldCheck className="h-4 w-4 text-accent" />
            <h2 className="text-[14px] font-semibold text-fg">
              {t("cert_guide.title")}
            </h2>
          </div>
          <Button variant="ghost" size="icon" onClick={onClose}>
            <X className="h-4 w-4" />
          </Button>
        </header>

        <div className="space-y-4 px-4 py-4">
          <p className="text-[12px] leading-relaxed text-fg-muted">
            {cert?.alternative_preview_domain && cert?.preview_domain
              ? t("cert_guide.intro_with_alt").replace(
                  "{preview}",
                  cert.preview_domain,
                )
              : t("cert_guide.intro")}
          </p>

          {/* Two-path picker — surfaced ONLY when needs_acm AND an
              alternative exists. This is the choice the operator
              actually has to make. Neither is "the" recommended
              one; cost and trade-offs are stated plainly. */}
          {cert?.needs_acm && cert?.alternative_preview_domain && cert?.preview_domain ? (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <section className="rounded-md border border-border bg-bg-subtle px-3 py-3">
                <h3 className="mb-1 text-[13px] font-semibold text-fg">
                  {t("cert_guide.path_a.title").replace(
                    "{preview}",
                    cert.preview_domain,
                  )}
                </h3>
                <p className="mb-2 text-[11px] leading-relaxed text-fg-muted">
                  {t("cert_guide.path_a.body")}
                </p>
                <p className="mb-2 inline-block rounded bg-warn/10 px-1.5 py-0.5 text-[10.5px] font-mono text-warn">
                  {t("cert_guide.path_a.cost")}
                </p>
                {cert.dashboard_url ? (
                  <a
                    href={cert.dashboard_url}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex w-full items-center justify-center gap-1 rounded border border-border bg-bg px-2.5 py-1 text-[11.5px] font-medium text-fg hover:bg-surface-hover"
                  >
                    <ExternalLink className="h-3.5 w-3.5" />
                    {t("cert_guide.path_a.button")}
                  </a>
                ) : null}
              </section>
              <section className="rounded-md border border-border bg-bg-subtle px-3 py-3">
                <h3 className="mb-1 text-[13px] font-semibold text-fg">
                  {t("cert_guide.path_b.title").replace(
                    "{alt}",
                    cert.alternative_preview_domain,
                  )}
                </h3>
                <p className="mb-2 text-[11px] leading-relaxed text-fg-muted">
                  {t("cert_guide.path_b.body")
                    .split("{preview}")
                    .join(cert.preview_domain)
                    .split("{alt}")
                    .join(cert.alternative_preview_domain)}
                </p>
                <p className="mb-2 inline-block rounded bg-success/10 px-1.5 py-0.5 text-[10.5px] font-mono text-success">
                  {t("cert_guide.path_b.cost")}
                </p>
                <div className="space-y-0.5 rounded border border-border bg-bg px-2 py-1.5 font-mono text-[10.5px]">
                  <p className="text-fg">
                    GAPT_CADDY_PREVIEW_DOMAIN={cert.alternative_preview_domain}
                  </p>
                  <p className="text-fg-subtle">
                    {t("cert_guide.option0.step2")}
                  </p>
                  <p className="text-fg-subtle">
                    {t("cert_guide.option0.step3")}
                  </p>
                </div>
              </section>
            </div>
          ) : null}

          {/* Live state summary. */}
          {loading && !cert ? (
            <p className="flex items-center gap-1 text-[12px] text-fg-subtle">
              <Loader2 className="h-3 w-3 animate-spin" /> {t("app.loading")}
            </p>
          ) : cert ? (
            <div className="rounded-md border border-border bg-bg-subtle px-3 py-2 text-[11.5px]">
              <p className="mb-1.5 font-medium text-fg">
                {t("cert_guide.status.label")}
              </p>
              <ul className="space-y-1 text-fg-muted">
                <li>
                  Zone:{" "}
                  <span className="font-mono">
                    {cert.zone_name ?? "(not selected)"}
                  </span>
                </li>
                <li>
                  Wildcard:{" "}
                  <span className="font-mono">{cert.wildcard_hostname ?? "—"}</span>
                </li>
                <li>
                  Active wildcard cert:{" "}
                  <span
                    className={
                      cert.has_wildcard_cert
                        ? "font-mono text-success"
                        : "font-mono text-warn"
                    }
                  >
                    {cert.has_wildcard_cert ? "yes" : "no"}
                  </span>
                </li>
                <li>
                  Total TLS:{" "}
                  <span
                    className={
                      cert.total_tls_enabled === true
                        ? "font-mono text-success"
                        : cert.total_tls_enabled === false
                          ? "font-mono text-warn"
                          : "font-mono text-fg-subtle"
                    }
                  >
                    {cert.total_tls_enabled === true
                      ? "enabled"
                      : cert.total_tls_enabled === false
                        ? "disabled"
                        : "unknown (token may lack scope)"}
                  </span>
                </li>
                <li>
                  HTTPS handshake:{" "}
                  <span
                    className={
                      diag?.e2e_reachable
                        ? "font-mono text-success"
                        : "font-mono text-danger"
                    }
                  >
                    {diag?.e2e_reachable ? "ok" : "failing"}
                  </span>
                </li>
              </ul>
              <p className="mt-2 text-fg">{cert.message}</p>
            </div>
          ) : null}

          {/* Legacy single-recommendation Option 0/1/2 layout is
              suppressed when the 2-path picker above is rendering
              — that one covers needs_acm + alternative cases more
              cleanly. Fallback layout still shows for the simpler
              "preview_domain is apex, just toggle Total TLS"
              scenario. */}
          {!(cert?.needs_acm && cert?.alternative_preview_domain) &&
          cert?.alternative_preview_domain ? (
            <section className="rounded-md border-2 border-success/40 bg-success/5 px-3 py-3">
              <h3 className="mb-1 inline-flex items-center gap-1.5 text-[13px] font-semibold text-success">
                <ShieldCheck className="h-3.5 w-3.5" />
                {t("cert_guide.option0.title")}
              </h3>
              <p className="mb-2 text-[11.5px] leading-relaxed text-fg-muted">
                {t("cert_guide.option0.body")
                  .split("{alt}")
                  .join(cert.alternative_preview_domain)
                  .split("{apex_wildcard}")
                  .join(`*.${cert.alternative_preview_domain}`)}
              </p>
              <div className="mb-2 grid grid-cols-1 gap-1 rounded border border-border bg-bg px-2 py-1.5 text-[10.5px] sm:grid-cols-2">
                <div>
                  <p className="font-medium text-fg-subtle">
                    {t("cert_guide.option0.before")}
                  </p>
                  <p className="font-mono text-warn">
                    &lt;slug&gt;.{cert.preview_domain}
                  </p>
                  <p className="font-mono text-warn">
                    ✗ needs *.{cert.preview_domain} (ACM, $10/mo)
                  </p>
                </div>
                <div>
                  <p className="font-medium text-fg-subtle">
                    {t("cert_guide.option0.after")}
                  </p>
                  <p className="font-mono text-success">
                    &lt;slug&gt;.{cert.alternative_preview_domain}
                  </p>
                  <p className="font-mono text-success">
                    ✓ covered by *.{cert.alternative_preview_domain} (free)
                  </p>
                </div>
              </div>
              <div className="space-y-1 rounded border border-border bg-bg px-2 py-1.5 font-mono text-[10.5px]">
                <p className="text-fg-subtle">
                  # 1) {t("cert_guide.option0.step1")}
                </p>
                <p className="text-fg">
                  GAPT_CADDY_PREVIEW_DOMAIN={cert.alternative_preview_domain}
                </p>
                <p className="mt-1 text-fg-subtle">
                  # 2) {t("cert_guide.option0.step2")}
                </p>
                <p className="mt-1 text-fg-subtle">
                  # 3) {t("cert_guide.option0.step3")}: {cert.preview_domain} →{" "}
                  {cert.alternative_preview_domain}
                </p>
              </div>
              <p className="mt-2 text-[11px] text-success">
                {t("cert_guide.option0.cost_note")}
              </p>
            </section>
          ) : null}

          {/* Show the existing certs so the operator sees what's
              already in their zone. */}
          {cert?.existing_covering_certs && cert.existing_covering_certs.length > 0 ? (
            <div className="rounded-md border border-border bg-bg-subtle px-3 py-2 text-[11.5px]">
              <p className="mb-1 font-medium text-fg">
                {t("cert_guide.existing_certs")}
              </p>
              <ul className="space-y-0.5 font-mono text-[10.5px] text-fg-muted">
                {cert.existing_covering_certs.map((h, i) => (
                  <li key={i}>• {h}</li>
                ))}
              </ul>
            </div>
          ) : null}

          {/* Legacy detailed options — only rendered when the
              2-path picker isn't covering the case. Keeps the
              simple "preview_domain is apex, just toggle Total TLS"
              flow intact. */}
          {!(cert?.needs_acm && cert?.alternative_preview_domain) ? (
          <>
          {/* Option 1: Total TLS. Note the ACM caveat on Free plans. */}
          <section className="rounded-md border border-accent/30 bg-accent/5 px-3 py-3">
            <h3 className="mb-1 inline-flex items-center gap-1.5 text-[13px] font-semibold text-accent">
              <Zap className="h-3.5 w-3.5" />
              {t("cert_guide.option1.title")}
            </h3>
            <p className="mb-2 text-[11.5px] leading-relaxed text-fg-muted">
              {cert?.needs_acm
                ? t("cert_guide.option1.body_needs_acm")
                : t("cert_guide.option1.body")}
            </p>
            <div className="flex flex-wrap items-center gap-2">
              {/* Hide the in-app enable button when needs_acm — the
                  API call would 403 because ACM isn't on the zone.
                  Operator still gets the dashboard link to verify
                  their plan / purchase ACM. */}
              {cert?.can_enable_via_api &&
              cert?.total_tls_enabled !== true &&
              !cert?.needs_acm ? (
                <Button
                  onClick={enable}
                  disabled={!!busy}
                  variant="primary"
                >
                  <Zap className="mr-1 h-3.5 w-3.5" />
                  {busy === "enable"
                    ? t("cert_guide.option1.enabling")
                    : t("cert_guide.option1.enable_button")}
                </Button>
              ) : null}
              {cert?.dashboard_url ? (
                <a
                  href={cert.dashboard_url}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1 rounded border border-border bg-bg px-2.5 py-1 text-[11.5px] font-medium text-fg hover:bg-bg-subtle"
                >
                  <ExternalLink className="h-3.5 w-3.5" />
                  {t("cert_guide.option1.dashboard_link")}
                </a>
              ) : null}
            </div>
            {cert?.needs_acm ? (
              <p className="mt-1.5 text-[11px] text-warn">
                {t("cert_guide.option1.acm_blocked")}
              </p>
            ) : !cert?.can_enable_via_api ? (
              <p className="mt-1.5 text-[11px] text-fg-subtle">
                {t("cert_guide.option1.scope_missing")}
              </p>
            ) : null}
          </section>

          {/* Option 2: Advanced Certificate. */}
          <section className="rounded-md border border-border bg-bg-subtle px-3 py-3">
            <h3 className="mb-1 text-[13px] font-semibold text-fg">
              {t("cert_guide.option2.title")}
            </h3>
            <p className="mb-2 text-[11.5px] leading-relaxed text-fg-muted">
              {t("cert_guide.option2.body")}
            </p>
            {cert?.dashboard_url ? (
              <a
                href={cert.dashboard_url}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1 rounded border border-border bg-bg px-2.5 py-1 text-[11.5px] font-medium text-fg hover:bg-surface-hover"
              >
                <ExternalLink className="h-3.5 w-3.5" />
                {t("cert_guide.option2.dashboard_link")}
              </a>
            ) : (
              <p className="text-[11px] text-fg-subtle">
                {t("cert_guide.no_zone")}
              </p>
            )}
          </section>
          </>
          ) : null}

          {/* Option 3: Custom Hostnames (just a note). */}
          <section className="rounded-md border border-border bg-bg-subtle px-3 py-3">
            <h3 className="mb-1 text-[13px] font-semibold text-fg">
              {t("cert_guide.option3.title")}
            </h3>
            <p className="text-[11.5px] leading-relaxed text-fg-muted">
              {t("cert_guide.option3.body")}
            </p>
          </section>

          {outcome ? (
            <div className="rounded border border-success/40 bg-success/5 px-2.5 py-1.5 text-[11.5px] text-success">
              {outcome}
            </div>
          ) : null}
          {err ? (
            <div className="rounded border border-danger/40 bg-danger/5 px-2.5 py-1.5 text-[11.5px] text-danger">
              {err}
            </div>
          ) : null}
        </div>

        <footer className="sticky bottom-0 flex items-center justify-between border-t border-border bg-bg px-4 py-3">
          <Button variant="ghost" onClick={reverify} disabled={!!busy}>
            <RefreshCw className="mr-1 h-3.5 w-3.5" />
            {busy === "verify"
              ? t("cert_guide.verify.running")
              : t("cert_guide.verify.button")}
          </Button>
          <Button onClick={onClose}>{t("app.close")}</Button>
        </footer>
      </div>
    </div>
  );
}
