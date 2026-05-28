# M2 Phase B-Hardening — robustness + 테스트 백필

> **상위**: [`00_master_plan.md`](00_master_plan.md) · [`m2_m5_outline.md`](m2_m5_outline.md)
>
> **선행**: M2 Phase A (serving capability) ✅ + Phase B (preview domain + Cloudflare provider) ✅
>
> **목적**: Phase A/B 에서 production-grade 기능이 한꺼번에 들어간 결과, 누적된 robustness 부채를 갚는 단계. 새 기능 추가 대신 *죽지 않고 회귀 안 나는 상태* 만들기.

---

## 진입 조건

- Phase B 종료 (Cloudflare provider live, subdomain mode 사용자 검증 완료)
- 사용자가 외부 브라우저로 `https://<slug>.<preview-domain>` 1회 이상 접근 확인
- 사용자 본인 1주일 일상 사용 후 누적된 마찰점 정리

## 주제

"이제 기능은 다 들어갔다 — 망가뜨려도 알아서 복구되고, 새 PR이 자신 있는 상태로 만든다."

---

## DoD (Phase B-Hardening → Phase C 게이트)

- [ ] Claude 세션 종료 / 노트북 절전 / OS 재부팅 어떤 경우에도 GAPT 서버가 10초 이내 자동 복귀 (외부 사용자 502 안 봄)
- [ ] `pnpm vitest && uv run pytest` 둘 다 green. Phase B 신규 모듈 라인 커버리지 70%+
- [ ] 스택 stop 후 외부 preview URL 방문 시 정확히 404 (GAPT 메인으로 falling-through redirect 없음)
- [ ] Migration history 테이블 + UI 에서 1-click revert 동작 검증
- [ ] Cloudflare API 토큰 vault corruption 발생 시 사용자가 1-click 으로 복구 가능

---

## 작업 항목

### B.H.1 — 서버 라이프사이클 강건화

**문제**: 현재 GAPT server (uvicorn) 가 host 에서 임시 shell 의 자식 프로세스로 띄워져 있어, shell 종료 / Claude task-stop / 절전 시 함께 죽음. cloudflared 가 origin 미응답 → 502.

**해결책**:
1. `compose/docker-compose.dev.yml` 에 `server` 서비스 추가
   - `restart: unless-stopped` + healthcheck `/health` 30s 간격
   - 환경변수는 host `.env.dev` 에서 inject
   - host bind-mount: workspace dir + (필요 시) vault sqlite
   - 네트워크: 기존 `gapt-net` 가입 (Caddy + Postgres 와 같은 망)
   - port 38001 publish (frontend Vite 가 host 에서 접근 가능하게)
2. `scripts/dev/server.sh` — host 에서 직접 띄울 때의 대안 경로
   - `start` / `stop` / `restart` / `status` / `logs` 서브커맨드
   - PID file: `/tmp/gapt-server.pid`
   - 로그: `/tmp/gapt-server.log`
   - `nohup ... & disown` 으로 부모 shell 의존성 차단
3. `compose/systemd/gapt-server.service` — systemd user unit 템플릿 (프로덕션 자기-호스팅용)
4. `docs/operations/dev_setup.md` — 권장 옵션 (compose) + 대안 (script/systemd) 설명

**산출물**:
- `compose/docker-compose.dev.yml` 변경
- `scripts/dev/server.sh` 신규
- `compose/systemd/gapt-server.service` 신규
- `docs/operations/dev_setup.md` 신규

**검증 시나리오**:
- compose 로 띄운 후 `docker compose -p gapt-dev kill server` → 자동 재시작 확인
- `kill -9` 로 강제 kill → 10초 이내 복귀
- Claude session 종료 후 30분 뒤 외부 URL 접근 → 200 응답

---

### B.H.2 — Phase B 테스트 백필

**문제**: Phase B 의 신규 모듈 (Cloudflare client/service/migration, providers router, subdomain manager 의 host-only splice) 모두 smoke 만 거침. 다음 변경 시 회귀 알 길 없음.

**테스트 목록**:

#### Server (Python / pytest)
1. `tests/domains/providers/cloudflare/test_client.py`
   - `verify_token`, `list_accounts`, `list_zones`, `list_tunnels`, `get/put_tunnel_configuration`, `list_dns_records`, `create_dns_record`, `get/enable_total_tls`
   - httpx mock 으로 400/403/5xx 에러 핸들링 확인
2. `tests/domains/providers/cloudflare/test_service.py`
   - `verify_and_discover`: empty accounts + non-empty zones → derived accounts 합성
   - `infer_tunnel_mode`: source / version / ingress 모양별 분기
   - `ensure_wildcard_ingress`: idempotent (이미 있으면 no-op), 새 entry 는 catch-all 직전 삽입
3. `tests/domains/providers/cloudflare/test_migration.py`
   - `extract_tunnel_uuid`: UUID 직접 / friendly name + credentials_file / 둘 다 없음
   - `_ensure_safe_tunnel_id`: regex 통과/거부 케이스 (shell injection 차단)
   - `generate_cutover_script`: 의도된 시스템 명령만 포함
   - `inspect_local`: 파일 없음 / 권한 없음 / YAML 손상 분기
4. `tests/routers/test_providers.py`
   - 14 엔드포인트 HTTP-level integration (Cloudflare API 는 mock)
   - 토큰 미설정 시 적절한 에러 코드
   - put_config 자동 ensure 동작 (preview_domain 변경 시)
5. `tests/routers/test_environments.py`
   - `_env_with_fallback`: stopped 존중 / success 만 fallback / 둘 다 없을 때
6. `tests/domains/caddy/test_subdomain.py`
   - Host-only 라우트 index 0 splice 확인
   - 슬러그 변경 시 옛 슬러그 unregister
   - `_resolve_preview_slug` validation regex
7. `tests/domains/deploy/test_stack_manager.py`
   - `logs()` smoke + tail/since 파라미터 통과

#### Web (TypeScript / vitest)
1. `tests/api/providers.test.ts` — API client 함수 시그니처 + URL 패턴
2. `tests/ide/CloudflareProviderCard.test.tsx` — verify → 자동 채움 동작
3. `tests/ide/WildcardCertGuide.test.tsx` — needs_acm + alternative 분기 표시
4. `tests/ide/DeployWorkspace.test.tsx` — 탭 전환 + LIVE/STOPPED 카드 렌더링
5. `tests/ide/LiveStackLogsSection.test.tsx` — pause/resume + auto-scroll

**목표**: server 53 → **75+ 파일**, web 21 → **30+ 파일**. 신규 모듈 라인 커버리지 70%+ (pytest `--cov`).

**산출물**: 위 테스트 파일들 + CI 설정 (있다면 coverage 보고서)

---

### B.H.3 — UX edge case 정리

**1. `*.<preview-domain>` catch-all 404 라우트**

스택 stop 후 사용자가 그 URL 방문하면 GAPT 메인으로 리다이렉트되는 버그. SubdomainManager 가 zone-wide fallback 404 라우트도 같이 관리:
- `host=*.<preview-domain> not=[gapt.<apex>]` → 404
- 이 라우트는 host-only 매처 그룹 안에서 가장 낮은 우선순위
- 특정 slug 등록되면 그 라우트가 위에 와서 정상 forward
- 등록 해제되면 catch-all 만 남아 404

**2. Cloudflare API 토큰 vault corruption 자동 복구**

`verify` 실패 시 vault 에서 토큰 raw bytes 검사 (개행/공백 trailing 등) → 자동 trim 후 재저장. 그래도 실패하면 "토큰 재입력" 1-click 가이드.

**3. Token scope 자기진단 UI**

verify 시 각 scope (`Account:Cloudflare Tunnel:Edit`, `Zone:DNS:Edit`, `Zone:SSL:Edit`) 별로 dry-run 호출 → 사용자가 어떤 scope 없는지 체크 표시. 없는 scope 가 있어도 차단하지 않고 "이 기능은 안 됨" 표시.

**4. Subdomain 진단 next_steps 중복 제거**

같은 상태가 여러 체크 라인에서 같은 메시지 produce → dedupe.

**5. EnvSettingsModal 의 state 동기화**

Save 후 자동 re-diagnose. 현재는 사용자가 "진단 실행" 다시 눌러야 함.

---

### B.H.4 — Migration 안전망

**1. 자동 backup**

cutover 실행 전에 자동 backup:
- 현재 systemd unit 의 `journalctl -u cloudflared --since "1 hour ago"` snapshot
- 현재 tunnel ingress JSON 전체 dump
- 이 둘을 vault 에 `migration.cloudflared.backup.<timestamp>` 키로 저장 (60일 TTL)

**2. 자동 rollback**

cutover 스크립트 실행 후 30초 동안 cloudflared healthy 안 되면:
- systemd drop-in 자동 제거
- `systemctl daemon-reload && restart cloudflared`
- backup 한 ingress 를 Cloudflare API 에 다시 PUT
- 사용자에게 "복구됨" 알림

**3. `provider_migrations` 테이블 + UI**

```sql
CREATE TABLE provider_migrations (
    id ULID PRIMARY KEY,
    kind TEXT NOT NULL,              -- "cloudflare.tunnel_remote_managed"
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    status TEXT,                     -- "in_progress" | "ok" | "rolled_back" | "failed"
    before_snapshot JSONB,
    after_snapshot JSONB,
    error TEXT,
    rolled_back_at TIMESTAMPTZ
);
```

Settings → Providers → Cloudflare 에 "Migration history" 표시 + 각 row 별 1-click revert.

**4. Cutover dry-run 모드**

`run-cutover` 에 `dry_run=true` 옵션:
- 모든 명령은 echo 만 하고 실행 안 함
- "이 명령들이 실행됩니다" 라는 preview 만 반환
- 사용자가 OK 하면 실제 실행 (`dry_run=false`)

---

## 리스크 + 대응

- **docker compose 안으로 server 옮길 때 vite (host) 와 통신 경로 변경** — `host.docker.internal` 명시 + dev_setup.md 에 호스트별 (Linux native vs Docker Desktop) 트러블슈팅
- **테스트 백필 중 잠복 버그 발견** — 해당 PR 에서 fix + 회귀 테스트 같이 추가. 별도 PR 안 만들고 한 묶음
- **자동 rollback 이 잘못된 시점에 트리거** — 30초 timeout 보다 더 정확한 cloudflared "ready" 시그널 활용 (connector registered 로그 grep)
- **migration audit 가 vault 용량 압박** — 60일 TTL + 압축

---

## 산출물 요약

```
compose/
  docker-compose.dev.yml         (수정)
  systemd/gapt-server.service    (신규)
scripts/dev/
  server.sh                      (신규)
docs/
  operations/dev_setup.md        (신규)
  plan/m2_phase_b_hardening.md   (본 파일)
server/
  migrations/versions/2026XXXX_provider_migrations.py  (신규)
  src/gapt_server/db/models.py   (ProviderMigration 모델 추가)
  src/gapt_server/routers/providers.py  (rollback / dry-run / history 엔드포인트 추가)
  src/gapt_server/domains/providers/cloudflare/migration.py  (auto-backup/restore)
  src/gapt_server/domains/caddy/subdomain.py  (catch-all 404)
  tests/                         (테스트 백필 ~22 파일)
web/
  src/ide/EnvSettingsModal.tsx   (state 동기화 + scope 진단)
  src/routes/Settings.tsx        (token corruption 복구 UI + migration history)
  tests/                         (테스트 백필 ~5 파일)
```

---

## 관련 docs

- [[project_gapt_cloudflare_provider]] — Phase B 의 통합 설계
- [[feedback_caddy_admin_quirks]] — DELETE-then-POST 패턴
- [[feedback_bind_mount_inode_pitfall]] — Caddy bind-mount 함정
- [[feedback_caddy_preview_safety_net]] — 404 fallback 의 iframe 함정
- [[reference_gapt_routing_model]] — 단일 도메인 path 기반 fan-out
