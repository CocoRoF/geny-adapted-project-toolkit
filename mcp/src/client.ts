/** GAPT HTTP client — session-cookie auth against the single-admin
 * backend.
 *
 * Config comes from env (set by the MCP host's `env` block):
 *   GAPT_BASE_URL  — e.g. https://gapt.example.com  (no trailing slash)
 *   GAPT_LOGIN_ID  — admin login id
 *   GAPT_LOGIN_PW  — admin login password
 *   GAPT_TIMEOUT_MS — optional per-request timeout (default 60000)
 *
 * Auth model: POST /_gapt/api/auth/login {id, password} → Set-Cookie
 * session. We keep the cookie string in memory and re-login ONCE on
 * any 401 (sessions expire server-side; MCP servers are long-lived).
 */

export interface GaptError {
  status: number;
  code: string;
  reason: string;
}

export class GaptApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    public readonly reason: string,
  ) {
    super(`[${status}] ${code}: ${reason}`);
  }
}

interface ClientConfig {
  baseUrl: string;
  loginId: string;
  loginPw: string;
  timeoutMs: number;
}

function readConfig(): ClientConfig {
  const baseUrl = (process.env.GAPT_BASE_URL ?? "").replace(/\/+$/, "");
  const loginId = process.env.GAPT_LOGIN_ID ?? "";
  const loginPw = process.env.GAPT_LOGIN_PW ?? "";
  if (!baseUrl || !loginId || !loginPw) {
    // stderr only — stdout is the MCP stdio channel.
    console.error(
      "[gapt-mcp] missing env: GAPT_BASE_URL / GAPT_LOGIN_ID / GAPT_LOGIN_PW are all required",
    );
    process.exit(1);
  }
  const timeoutMs = Number(process.env.GAPT_TIMEOUT_MS ?? "60000");
  return {
    baseUrl,
    loginId,
    loginPw,
    timeoutMs: Number.isFinite(timeoutMs) ? timeoutMs : 60000,
  };
}

export class GaptClient {
  private cfg: ClientConfig;
  private cookie: string | null = null;
  private loginInFlight: Promise<void> | null = null;

  constructor() {
    this.cfg = readConfig();
  }

  get baseUrl(): string {
    return this.cfg.baseUrl;
  }

  /** Login and capture the session cookie. Deduped so parallel tool
   * calls hitting a 401 at the same time trigger one login, not N. */
  private async login(): Promise<void> {
    if (this.loginInFlight) return this.loginInFlight;
    this.loginInFlight = (async () => {
      const resp = await this.rawFetch("/_gapt/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: this.cfg.loginId, password: this.cfg.loginPw }),
      });
      if (!resp.ok) {
        const body = await resp.text().catch(() => "");
        throw new GaptApiError(
          resp.status,
          "auth.login_failed",
          `GAPT login failed (check GAPT_LOGIN_ID / GAPT_LOGIN_PW): ${body.slice(0, 200)}`,
        );
      }
      // Node's fetch exposes multiple Set-Cookie headers via
      // getSetCookie(). Keep only the name=value pairs — path/expiry
      // attributes don't belong in a Cookie request header.
      const setCookies: string[] =
        (resp.headers as unknown as { getSetCookie?: () => string[] }).getSetCookie?.() ??
        (resp.headers.get("set-cookie") ? [resp.headers.get("set-cookie") as string] : []);
      const pairs = setCookies
        .map((c) => c.split(";")[0]?.trim())
        .filter((c): c is string => !!c);
      if (pairs.length === 0) {
        throw new GaptApiError(
          500,
          "auth.no_cookie",
          "GAPT login succeeded but returned no session cookie",
        );
      }
      this.cookie = pairs.join("; ");
    })().finally(() => {
      this.loginInFlight = null;
    });
    return this.loginInFlight;
  }

  private async rawFetch(path: string, init: RequestInit): Promise<Response> {
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), this.cfg.timeoutMs);
    try {
      return await fetch(`${this.cfg.baseUrl}${path}`, {
        ...init,
        signal: ctl.signal,
        redirect: "manual",
      });
    } finally {
      clearTimeout(timer);
    }
  }

  /** JSON request with session auth. Retries exactly once through a
   * fresh login when the session is missing/expired (401). */
  async request<T = unknown>(
    method: string,
    path: string,
    opts: { query?: Record<string, string | number | boolean | undefined | null>; body?: unknown } = {},
  ): Promise<T> {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(opts.query ?? {})) {
      if (v === undefined || v === null || v === "") continue;
      qs.set(k, String(v));
    }
    const fullPath = qs.size > 0 ? `${path}?${qs}` : path;

    const doFetch = async (): Promise<Response> => {
      if (!this.cookie) await this.login();
      const headers: Record<string, string> = { Cookie: this.cookie ?? "" };
      let body: string | undefined;
      if (opts.body !== undefined) {
        headers["Content-Type"] = "application/json";
        body = JSON.stringify(opts.body);
      }
      return this.rawFetch(fullPath, { method, headers, body });
    };

    let resp = await doFetch();
    if (resp.status === 401) {
      this.cookie = null;
      resp = await doFetch();
    }

    if (resp.status === 204) return undefined as T;

    const text = await resp.text();
    let parsed: unknown = text;
    try {
      parsed = text ? JSON.parse(text) : undefined;
    } catch {
      /* non-JSON body (plain-text log tails etc.) — pass through */
    }

    if (!resp.ok) {
      const detail = (parsed as { detail?: { code?: string; reason?: string } } | undefined)
        ?.detail;
      throw new GaptApiError(
        resp.status,
        detail?.code ?? `http.${resp.status}`,
        detail?.reason ?? (typeof parsed === "string" ? parsed.slice(0, 300) : "request failed"),
      );
    }
    return parsed as T;
  }

  get<T = unknown>(path: string, query?: Record<string, string | number | boolean | undefined | null>) {
    return this.request<T>("GET", path, { query });
  }

  post<T = unknown>(path: string, body?: unknown, query?: Record<string, string | number | boolean | undefined | null>) {
    return this.request<T>("POST", path, { body, query });
  }

  put<T = unknown>(path: string, body?: unknown) {
    return this.request<T>("PUT", path, { body });
  }

  patch<T = unknown>(path: string, body?: unknown) {
    return this.request<T>("PATCH", path, { body });
  }

  delete<T = unknown>(path: string, query?: Record<string, string | number | boolean | undefined | null>) {
    return this.request<T>("DELETE", path, { query });
  }
}
