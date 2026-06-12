/** Long-form help for the Stack Re-route panel.
 *
 * The body is long-form prose with code blocks + a scenarios table.
 * Putting it into the i18n catalogue would bloat en.ts/ko.ts with
 * dozens of multi-paragraph keys; keeping it inline as JSX trees
 * (one per locale) reads better at edit time and the i18n parity
 * test stays happy. */

import { useI18n } from "@/app/providers/i18n-context";
import { Modal } from "@/ui/Modal";
import { Button } from "@/ui/Button";

interface Props {
  open: boolean;
  onClose: () => void;
}

export function StackRerouteHelpModal({ open, onClose }: Props) {
  const { locale, t } = useI18n();

  return (
    <Modal
      open={open}
      onClose={onClose}
      size="xl"
      title={t("deploy.stack.help.title")}
      description={t("deploy.stack.help.subtitle")}
      footer={
        <Button variant="primary" onClick={onClose}>
          {t("deploy.stack.help.close")}
        </Button>
      }
    >
      <div className="max-h-[70vh] overflow-auto pr-1">
        {locale === "ko" ? <BodyKo /> : <BodyEn />}
      </div>
    </Modal>
  );
}

function H({ children }: { children: React.ReactNode }) {
  return (
    <h3 className="mt-4 mb-1.5 text-[13px] font-semibold tracking-tight text-fg first:mt-0">
      {children}
    </h3>
  );
}

function P({ children }: { children: React.ReactNode }) {
  return <p className="mb-2 text-[12.5px] leading-relaxed text-fg-muted">{children}</p>;
}

function Code({ children }: { children: React.ReactNode }) {
  return (
    <code className="rounded bg-bg-subtle px-1 py-[1px] font-mono text-[11.5px] text-fg">
      {children}
    </code>
  );
}

function Pre({ children }: { children: React.ReactNode }) {
  return (
    <pre className="mb-3 overflow-auto whitespace-pre rounded-md border border-border bg-bg-subtle px-3 py-2 font-mono text-[11px] leading-snug text-fg-muted">
      {children}
    </pre>
  );
}

function UL({ children }: { children: React.ReactNode }) {
  return (
    <ul className="mb-3 ml-5 list-disc space-y-1 text-[12.5px] leading-relaxed text-fg-muted">
      {children}
    </ul>
  );
}

function Table({ headers, rows }: { headers: string[]; rows: string[][] }) {
  return (
    <div className="mb-3 overflow-x-auto rounded-md border border-border">
      <table className="w-full text-left text-[11.5px]">
        <thead className="bg-bg-subtle text-[10.5px] uppercase tracking-wider text-fg-subtle">
          <tr>
            {headers.map((h) => (
              <th key={h} className="px-2.5 py-1.5 font-semibold">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} className="border-t border-border align-top">
              {row.map((cell, j) => (
                <td key={j} className="px-2.5 py-1.5 text-fg-muted">
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Warn({ children }: { children: React.ReactNode }) {
  return (
    <p className="mb-2 rounded-md border border-warn/40 bg-warn/10 px-3 py-1.5 text-[12px] leading-relaxed text-warn">
      {children}
    </p>
  );
}

// ─────────────────────────────────────────────────────────── ko ──

function BodyKo() {
  return (
    <>
      <H>1. 이게 무엇을 하는가</H>
      <P>
        배포된 prod 컨테이너를 외부 브라우저에서 보려면 GAPT 의 reverse-proxy (<Code>Caddy</Code>)
        가 <Code>{`gapt.hrletsgo.me/preview/<slug>/*`}</Code> 또는{" "}
        <Code>{`<slug>.<preview-domain>/*`}</Code> 트래픽을 그 컨테이너로 보내야 합니다.{" "}
        <strong>[Re-route]</strong> 버튼은 그 라우팅 규칙을 Caddy admin API 로 동적 등록/갱신합니다.
        배포는 다시 안 돌립니다 — 이미 떠 있는 컨테이너에 대한 <em>라우팅만</em> 다시 그립니다.
      </P>

      <H>2. 언제 누르나</H>
      <UL>
        <li>배포 직후인데 URL 이 404 / 502 나는 경우</li>
        <li>compose 의 primary service 이름이나 포트가 바뀐 경우</li>
        <li>서버 재시작 후 Caddy 가 룰을 잃었다고 의심될 때 (보통은 자동 복구되지만 수동 강제)</li>
        <li>
          라우팅 정책을 바꾸고 싶을 때 (path ↔ subdomain, strip_prefix toggle, HTTPS upstream, Host
          헤더 등)
        </li>
      </UL>
      <P>
        오버라이드 폼을 <strong>비우고 누르면</strong> = 저장된 값 그대로 다시 등록.
        <br />
        오버라이드를 <strong>채워서 누르면</strong> = 그 필드만 override + 그 값이{" "}
        <Code>Environment.deploy_target_config</Code> 에 영속됨 → 다음 deploy / reroute 에도 자동
        적용.
      </P>

      <H>3. 두 가지 라우팅 전략 (Routing strategy)</H>
      <Pre>{`PATH 모드 (기본)                  SUBDOMAIN 모드
─────────────────                  ─────────────────
URL: <apex>/preview/<slug>/*       URL: <slug>.<preview-domain>/*
키:  URL path                      키:  Host 헤더
DNS: 1개 (A record)                DNS: 와일드카드 *. (CNAME / A)
TLS: 기존 cert 재사용              TLS: Caddy on-demand 발급 (자동)
장점: 즉시 동작, 인프라 변경 0     장점: 앱의 모든 root-relative URL 안전
단점: 앱이 /api/foo 같은 URL을      단점: 첫 요청은 cert 발급으로 1-5초
     내보내면 GAPT 본체와 충돌            느림. DNS 와일드카드 필요.
     → 쿠키 + Referer 로 완화
적합: basePath-aware 앱            적합: basePath 못 박는 앱, 멀티 테넌트
     (Next basePath / Vite base
     / FastAPI root_path)`}</Pre>
      <P>
        기본은 <strong>PATH 모드</strong>. 보통 충분합니다. 앱이 <Code>/api/...</Code>,{" "}
        <Code>/_next/...</Code>, <Code>/favicon.png</Code> 같은 root-relative URL 을 발행하면서
        basePath 를 모르는 구조면 <strong>subdomain 모드</strong> 로 가는 게 가장 견고합니다.
      </P>

      <H>4. PATH 모드의 3중 안전망</H>
      <P>apex 를 GAPT 본체와 공유하기 때문에 한 binding 이 Caddy 라우트 3개로 펼쳐집니다:</P>
      <Pre>{`1) Primary       /preview/<slug>/*           →  컨테이너  + Set-Cookie gapt_preview=<slug>
2) Referer fb.   Referer ~ /preview/<slug>/*  →  컨테이너  (favicon.png 같은 누수)
3) Cookie fb.    Cookie gapt_preview=<slug>   →  컨테이너  (refresh / 새 탭)
                 AND Sec-Fetch-Site=same-origin`}</Pre>
      <P>
        쿠키 TTL 기본 <strong>5분</strong> — 다른 탭에서 GAPT 본체를 만지는 동안 false catch 가
        일어날 수 있는 윈도우를 짧게 유지하기 위함입니다. 완전한 격리가 필요하면{" "}
        <strong>subdomain 모드</strong> 로 전환.
        <Code>Sec-Fetch-Site=same-origin</Code> 가드가 top-level 새 탭 진입 (None) 과 cross-site
        진입을 걸러줍니다.
      </P>

      <H>5. 오버라이드 필드 7개 자세히</H>

      <H>Routing strategy</H>
      <UL>
        <li>
          <Code>inherit</Code> — 저장된 값 그대로 (없으면 path)
        </li>
        <li>
          <Code>path</Code> — 위 §3 PATH 모드. <strong>subdomain → path 되돌릴 때</strong>도 이걸로
          명시.
        </li>
        <li>
          <Code>subdomain</Code> — preview 도메인에 <Code>*.preview.도메인</Code> 와일드카드 DNS 가
          떠 있어야 함. <Code>Caddyfile.prod</Code> 에 on-demand TLS 는 이미 wired (
          <Code>/api/preview/ask</Code> 가 ask endpoint).
        </li>
      </UL>

      <H>Primary service</H>
      <P>compose 의 service 이름. 비워두면 자동 탐색 (우선순위 순):</P>
      <UL>
        <li>
          reverse-proxy 류 (<Code>nginx</Code> / <Code>proxy</Code> / <Code>gateway</Code> /{" "}
          <Code>traefik</Code> / <Code>caddy</Code> / <Code>envoy</Code>)
        </li>
        <li>
          frontend 류 (<Code>frontend</Code> / <Code>web</Code> / <Code>app</Code>)
        </li>
        <li>첫 번째 실행 중 컨테이너</li>
      </UL>
      <Warn>
        자체 nginx 가 있는 스택은 거의 항상 nginx 로 보내야 정답. frontend 컨테이너로 직접 보내면
        SPA 가 같은 origin 으로 호출하는 <Code>/api/*</Code> XHR 이 다 깨집니다.
      </Warn>

      <H>Primary port</H>
      <P>
        컨테이너 <em>내부</em> 포트 (host port 아님). 기본 3000.
        <br />
        Primary service 가 reverse-proxy 류로 잡혔는데 port 가 비어있으면 자동으로{" "}
        <strong>80</strong> 으로 default.
      </P>

      <H>Upstream scheme</H>
      <UL>
        <li>
          <Code>http</Code> — 기본. 대부분의 dev / app 컨테이너.
        </li>
        <li>
          <Code>https</Code> — upstream 자체가 TLS terminator 인 경우 (자체 nginx 가 80 → 301 → 443
          강제 등).
        </li>
      </UL>

      <H>Host header (rewrite)</H>
      <P>
        upstream 의 nginx / traefik 가 <Code>server_name yourdomain.com</Code> 매칭으로만
        라우팅하면, 그 공개 도메인을 박아 보내야 합니다. 비워두면 GAPT 가 받은 그대로 forward
        (passthrough — 보통 GAPT 의 preview 도메인).
      </P>

      <H>TLS verification</H>
      <P>
        <Code>scheme=https</Code> 일 때만 의미. upstream 의 cert 가 self-signed 이거나 도메인
        불일치면 <strong>skip verify</strong>. 안 그러면 Caddy 가 verify 실패로 <Code>502</Code>.
        사용자 prod 스택이 공개 도메인 cert 만 들고 있고 GAPT 는 내부 docker DNS 이름으로 dial 하는
        경우가 전형.
      </P>

      <H>Strip /preview/&lt;slug&gt;</H>
      <P>
        <strong>path 모드 한정.</strong>
      </P>
      <UL>
        <li>
          <Code>true</Code> — Caddy 가 prefix 를 떼고 forward. 앱은 자기를 <Code>/</Code> 로 인식.
          basePath 못 박는 dev 서버 (<Code>next dev</Code>, <Code>vite</Code>) 용.
        </li>
        <li>
          <Code>false</Code> — prefix 유지. 앱이 <Code>{`/preview/<slug>/...`}</Code> 로 자기 URL 을
          emit 해야 함. <strong>basePath-aware prod build</strong> 표준.
        </li>
      </UL>
      <P>
        false 일 때 <Code>/favicon.png</Code> 같은 누수는 Referer fallback 에서 자동으로 prefix 가
        재작성되어 forward. true 일 때는 prefix 없이 그대로 forward (재작성하면 오히려 404 — 이미 한
        번 dev-mode 버그로 잡힘).
      </P>

      <H>6. 시나리오별 권장 설정</H>
      <Table
        headers={["시나리오", "mode", "strip", "scheme", "기타"]}
        rows={[
          ["Next.js dev (basePath 없음)", "path", "true", "http", "—"],
          ["Next.js prod (basePath 빌드)", "path", "false", "http", "images.unoptimized:true 권장"],
          ["Vite dev (base:/)", "path", "true", "http", "—"],
          [
            "자체 nginx + 공개 TLS only",
            "path",
            "false",
            "https",
            "tls=skip verify · host_header=공개도메인 · primary_service=nginx · port=443",
          ],
          ["단일 컨테이너 app", "—", "—", "—", "전부 비우고 [Re-route] (자동 픽업)"],
          ["멀티 테넌트 robust", "subdomain", "—", "http", "와일드카드 DNS 필요"],
        ]}
      />

      <H>7. 안 풀릴 때 트러블슈팅</H>
      <UL>
        <li>
          <strong>404</strong> — Caddy 라우트가 등록 안 됨. [Re-route] 한 번 더. 그래도 안 되면
          success 메시지의 <Code>upstream=...</Code> 줄이 맞는지 확인.
        </li>
        <li>
          <strong>502 Bad Gateway</strong> — Caddy 는 정상이지만 upstream 도달 실패.{" "}
          <Code>docker exec &lt;name&gt; ss -tlnp</Code> 로 listen 확인,{" "}
          <Code>docker network inspect gapt-net</Code> 로 컨테이너 attach 확인. HTTPS upstream 이면{" "}
          <strong>TLS verification = skip verify</strong> 시도.
        </li>
        <li>
          <Code>/api/*</Code> <strong>가 GAPT 본체로 새어나감</strong> — path 모드 cookie fallback
          의 5분 TTL 이 끊겼거나, cookie 없는 origin. subdomain 모드 전환 권장.
        </li>
        <li>
          <strong>favicon / _next/image 깨짐</strong> — Referer fallback 이 안 잡힘. Next.js 면{" "}
          <Code>images.unoptimized:true</Code> 설정. 자세히는 코드베이스의{" "}
          <Code>feedback_nextjs_basepath_quirks</Code> 참고.
        </li>
        <li>
          <strong>subdomain 모드 첫 요청 timeout</strong> — on-demand TLS 발급 중 (5-30 초).{" "}
          <Code>/api/preview/ask</Code> 가 해당 host 에 yes 응답 하는지 확인. 와일드카드 DNS 자체가
          안 되어 있으면 cert 발급 자체가 실패합니다.
        </li>
        <li>
          <strong>
            compose <Code>--no-deps</Code> rebuild 후 끊김
          </strong>{" "}
          — external network 가 떨어진 케이스. [Re-route] 가 자동으로{" "}
          <Code>docker network connect gapt-net</Code> 을 idempotent 하게 재시도합니다.
        </li>
      </UL>

      <H>8. 저장 동작</H>
      <P>
        오버라이드한 필드는 <Code>Environment.deploy_target_config</Code> 의 같은 이름 키로
        저장됩니다. 다음 deploy 가 <Code>LocalComposeTarget</Code> 에 같은 값을 넘기고, 다음
        [Re-route] 도 이 값들을 시드로 폼을 채웁니다. 값을 지우려면 입력을 비우고 (select 는{" "}
        <Code>inherit</Code> 로) 다시 [Re-route] 하세요.
      </P>
    </>
  );
}

// ─────────────────────────────────────────────────────────── en ──

function BodyEn() {
  return (
    <>
      <H>1. What this does</H>
      <P>
        To make a deployed prod container reachable from a browser, GAPT's reverse-proxy (
        <Code>Caddy</Code>) needs to know that requests to{" "}
        <Code>{`gapt.hrletsgo.me/preview/<slug>/*`}</Code> (or{" "}
        <Code>{`<slug>.<preview-domain>/*`}</Code>) should be forwarded to that container.{" "}
        <strong>[Re-route]</strong> registers/replaces that routing rule via the Caddy admin API. It
        does NOT redeploy — it only re-paints the routing for the already-running stack.
      </P>

      <H>2. When to click it</H>
      <UL>
        <li>Just deployed but the URL 404s / 502s</li>
        <li>compose's primary service name or port changed</li>
        <li>
          You suspect Caddy lost its rules after a server restart (usually auto-recovered, but force
          it)
        </li>
        <li>
          You want to change routing policy (path ↔ subdomain, strip_prefix, HTTPS upstream, Host
          header, ...)
        </li>
      </UL>
      <P>
        Pressing with the overrides form <strong>empty</strong> = re-register with the saved values.
        <br />
        Pressing with any field filled = override + <strong>persist</strong> that field to{" "}
        <Code>Environment.deploy_target_config</Code> for future deploys / reroutes.
      </P>

      <H>3. Two routing strategies</H>
      <Pre>{`PATH mode (default)                SUBDOMAIN mode
─────────────────                  ─────────────────
URL:  <apex>/preview/<slug>/*      URL:  <slug>.<preview-domain>/*
Key:  URL path                     Key:  Host header
DNS:  one A record                 DNS:  wildcard *. (CNAME / A)
TLS:  reuse existing cert          TLS:  Caddy on-demand (auto)
Pros: works instantly, 0 infra     Pros: every root-relative URL the
      changes                            app emits Just Works
Cons: app-emitted /api/foo style   Cons: first request slow 1-5s for
      URLs collide with GAPT             cert issuance, wildcard DNS
      → mitigated by cookie +            needed
      Referer fallback
Fits: basePath-aware apps          Fits: apps that can't be told their
      (Next basePath / Vite base /       basePath, multi-tenant robust
      FastAPI root_path)`}</Pre>
      <P>
        Default is <strong>PATH mode</strong> — usually enough. If the app emits root-relative URLs
        (<Code>/api/...</Code>, <Code>/_next/...</Code>, <Code>/favicon.png</Code>) without being
        aware of any basePath, switch to <strong>subdomain mode</strong> for true isolation.
      </P>

      <H>4. PATH mode's 3-route safety net</H>
      <P>
        Because the apex is shared with GAPT itself, one binding fans out to three Caddy routes:
      </P>
      <Pre>{`1) Primary      /preview/<slug>/*           →  upstream  + Set-Cookie gapt_preview=<slug>
2) Referer fb.  Referer ~ /preview/<slug>/*  →  upstream  (catches /favicon.png etc.)
3) Cookie fb.   Cookie gapt_preview=<slug>   →  upstream  (refresh / new tab)
                AND Sec-Fetch-Site=same-origin`}</Pre>
      <P>
        Cookie TTL defaults to <strong>5 minutes</strong> — keeps the false-catch window short while
        you're using GAPT in another tab. For full isolation use <strong>subdomain mode</strong>.
        The <Code>Sec-Fetch-Site=same-origin</Code> guard filters out top-level new tabs (None) and
        cross-site entries.
      </P>

      <H>5. The 7 override fields</H>

      <H>Routing strategy</H>
      <UL>
        <li>
          <Code>inherit</Code> — keep saved value (defaults to path)
        </li>
        <li>
          <Code>path</Code> — see §3. Use this explicitly when reverting subdomain → path.
        </li>
        <li>
          <Code>subdomain</Code> — needs <Code>*.preview.&lt;domain&gt;</Code> wildcard DNS.{" "}
          <Code>Caddyfile.prod</Code> already wires on-demand TLS (the ask endpoint is{" "}
          <Code>/api/preview/ask</Code>).
        </li>
      </UL>

      <H>Primary service</H>
      <P>compose service name. Leave blank to auto-detect in priority order:</P>
      <UL>
        <li>
          reverse-proxy names (<Code>nginx</Code> / <Code>proxy</Code> / <Code>gateway</Code> /{" "}
          <Code>traefik</Code> / <Code>caddy</Code> / <Code>envoy</Code>)
        </li>
        <li>
          frontend names (<Code>frontend</Code> / <Code>web</Code> / <Code>app</Code>)
        </li>
        <li>first running container</li>
      </UL>
      <Warn>
        Stacks that ship their own nginx almost always want routing to point at nginx. Pointing at
        the frontend container directly breaks every <Code>/api/*</Code> XHR the SPA makes
        (same-origin assumption).
      </Warn>

      <H>Primary port</H>
      <P>
        Container <em>internal</em> port (NOT the host port). Default 3000.
        <br />
        If primary service was auto-picked as a reverse-proxy and port is left blank, defaults to{" "}
        <strong>80</strong>.
      </P>

      <H>Upstream scheme</H>
      <UL>
        <li>
          <Code>http</Code> — default. Most dev / app containers.
        </li>
        <li>
          <Code>https</Code> — upstream is itself a TLS terminator (own nginx forcing 80 → 301 → 443
          etc.).
        </li>
      </UL>

      <H>Host header (rewrite)</H>
      <P>
        If the upstream nginx/traefik routes by <Code>server_name yourdomain.com</Code>, you have to
        send that public domain in the Host header. Leave empty to passthrough what Caddy received
        (usually GAPT's preview domain).
      </P>

      <H>TLS verification</H>
      <P>
        Only meaningful when <Code>scheme=https</Code>. If the upstream cert is self-signed or
        domain-mismatched, choose <strong>skip verify</strong>. Otherwise Caddy returns{" "}
        <Code>502</Code> on verify failure. Typical when the user's prod stack has a public-domain
        cert and GAPT dials by internal docker DNS name.
      </P>

      <H>Strip /preview/&lt;slug&gt;</H>
      <P>
        <strong>path mode only.</strong>
      </P>
      <UL>
        <li>
          <Code>true</Code> — Caddy strips the prefix before forwarding. App sees itself at{" "}
          <Code>/</Code>. For dev servers that can't be told a basePath (<Code>next dev</Code>,{" "}
          <Code>vite</Code>).
        </li>
        <li>
          <Code>false</Code> — prefix kept. App must emit URLs under{" "}
          <Code>{`/preview/<slug>/...`}</Code>. Standard for{" "}
          <strong>basePath-aware prod builds</strong>.
        </li>
      </UL>
      <P>
        With false, leaked <Code>/favicon.png</Code>-style requests are auto-rewritten WITH the
        prefix by the Referer fallback. With true, the Referer fallback forwards as-is (re-adding
        the prefix would 404 — that was a real dev-mode bug we hit).
      </P>

      <H>6. Recommended settings per scenario</H>
      <Table
        headers={["Scenario", "mode", "strip", "scheme", "other"]}
        rows={[
          ["Next.js dev (no basePath)", "path", "true", "http", "—"],
          [
            "Next.js prod (basePath build)",
            "path",
            "false",
            "http",
            "images.unoptimized:true recommended",
          ],
          ["Vite dev (base:/)", "path", "true", "http", "—"],
          [
            "Own nginx + public-TLS only",
            "path",
            "false",
            "https",
            "tls=skip verify · host_header=public domain · primary_service=nginx · port=443",
          ],
          ["Single-container app", "—", "—", "—", "leave blank, [Re-route] (auto-pick)"],
          ["Multi-tenant robust", "subdomain", "—", "http", "wildcard DNS required"],
        ]}
      />

      <H>7. Troubleshooting</H>
      <UL>
        <li>
          <strong>404</strong> — Caddy route not registered. [Re-route] again. If still bad, check
          the <Code>upstream=...</Code> line in the success output.
        </li>
        <li>
          <strong>502 Bad Gateway</strong> — Caddy fine, upstream unreachable. Verify the container
          actually listens (<Code>docker exec &lt;name&gt; ss -tlnp</Code>), verify it's on gapt-net
          (<Code>docker network inspect gapt-net</Code>). For HTTPS upstreams try{" "}
          <strong>TLS verification = skip verify</strong>.
        </li>
        <li>
          <Code>/api/*</Code> <strong>leaking to GAPT itself</strong> — cookie fallback's 5-min TTL
          expired, or different origin. Switch to subdomain mode for real isolation.
        </li>
        <li>
          <strong>favicon / _next/image broken</strong> — Referer fallback not catching. For
          Next.js, set <Code>images.unoptimized:true</Code>. See{" "}
          <Code>feedback_nextjs_basepath_quirks</Code> in memory.
        </li>
        <li>
          <strong>subdomain first request times out</strong> — on-demand TLS provisioning (5-30s).
          Verify <Code>/api/preview/ask</Code> answers yes for the host. If wildcard DNS isn't set
          up, cert issuance fails outright.
        </li>
        <li>
          <strong>Broken after a partial compose rebuild</strong> — external network was stripped.
          [Re-route] idempotently re-runs <Code>docker network connect gapt-net</Code>.
        </li>
      </UL>

      <H>8. Persistence</H>
      <P>
        Each overridden field is saved to the same-named key in{" "}
        <Code>Environment.deploy_target_config</Code>. The next deploy passes the same values into{" "}
        <Code>LocalComposeTarget</Code>, and the next [Re-route] seeds the form from them. To clear
        a value, empty the input (select to <Code>inherit</Code>) and [Re-route] again.
      </P>
    </>
  );
}
