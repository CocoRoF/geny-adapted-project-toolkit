# M1.5 — Dogfood Readiness

> Status: planned
> Estimated: 1–2 작업주 / 5–8 PR
> Depends on: M1 close (`cdf12b2`) + post-close hardening
> ([`progress/m1/_post_close_hardening.md`](../progress/m1/_post_close_hardening.md))
> Blocks: M2-E1 (멀티 프로젝트 동시 운영)
> Relates to: [`08_web_ide_ux.md`](../08_web_ide_ux.md),
> [`12_geny_case_study.md`](../12_geny_case_study.md),
> [`plan/m1/e4_integration_dogfood_geny.md`](m1/e4_integration_dogfood_geny.md)

## 목적 (한 줄)

M2 로 넘어가기 전에, **사용자가 GAPT 안에서 외부 IDE 없이 1 cycle 의
inner loop** (파일 열기 → 편집 → 채팅 → 도구 호출 → diff → apply →
commit → PR) **을 끝까지 통과**한다. 새 기능은 추가하지 않는다 —
이미 wired 되어 있는 surface 가 *실제로* 작동하는지 확인하고, 깨지는
곳만 fix.

## 왜 M2 가 아닌가

M1 close 후 사용자의 첫 라운드 dogfood (2026-05-24~25) 가 **10개 이상의
post-close fix 를 강제**했다. M1 자동 테스트는 mock 백엔드 + canned
응답으로 통과했지만, 실제 사용자의 첫 클릭이 surface 의 빈 곳을 그대로
보여줬다 — Files 패널이 빈 채로 나오는 게 대표적. M2 (멀티 프로젝트 +
워크트리) 는 *지금* 의 1 사이클이 통과한 다음에 의미가 있다. 아직
열어본 적 없는 Editor / Chat / Deploy / CI / Preview 5개 surface 를 한
번도 안 돌려보고 새 surface 를 5개 더 짓는 건 [[feedback_durable_instructions]]
와도 충돌한다.

## 진입 조건

- [x] M1 자동 테스트 그린 (server 350 / web 89, ruff/mypy/typecheck clean)
- [x] post-close hardening commit 들이 main 에 있음 (`375e87f` ~ `dd77783`)
- [x] 2026-05-25 세션의 5개 파일 commit (Files API 호스트 실행 +
  Settings UI + credentials_resolver) — *plan 검토 직후*
- [x] 사용자가 적어도 1개 프로젝트를 GAPT 에 등록해놓은 상태
  (현재: `01KSD6CM5M1Q0S6JM6Z77J76AH` test / `01KSD7W13S8TD3G8SQAXMZZ8P9` smoke-fix)

## DoD

`docs/12_geny_case_study.md` 의 Step 1~9 를 외부 IDE 없이 1 회 완수.
각 항목은 *사용자의 클릭만으로* 가능해야 한다.

| # | 시나리오 | 통과 기준 |
|---|---|---|
| 1 | Settings 에서 `github_token` 저장 | 저장 후 새 워크스페이스 생성 시 private repo 도 클론 가능 (현재 public repo 만 통과 확인) |
| 2 | 워크스페이스 IDE 진입 후 트리에서 파일 클릭 | Monaco editor 가 내용을 표시 (현재 wired, 실측 미통과) |
| 3 | Monaco 에서 1줄 수정 → Ctrl+S | `PUT /api/workspaces/{wid}/file` 200, "Saved" 표시, 디스크에 반영 |
| 4 | 채팅 패널 "세션 시작" 클릭 → 짧은 프롬프트 1개 | SSE 스트림에서 첫 토큰 등장 + 헤더에 비용 누적 |
| 5 | 도구 호출 (예: `gapt_read_file`) 발화 | ToolCallCard 가 패널 안에 렌더 + 사용자 Approve/Reject 가능 |
| 6 | Diff 표시 | 가장 작은 형태라도 `Diff` 탭에서 변경 파일 미리보기 (현재 placeholder — 본 cycle 의 *유일한 신규 코드*) |
| 7 | 변경 commit 후 PR 만들기 (executor 도구) | `git push` 가 stored github_token 으로 인증 성공, PR URL 반환 |
| 8 | CI 패널에서 그 PR 의 GitHub Actions run 노출 | `GET /api/projects/{pid}/ci/runs` 반환값이 패널에 보임 |
| 9 | 1 cycle 비용 ≤ $1 + 전체 audit trail 깨끗 | CostPanel 누계 + AuditPanel 필터 |

## 작업 카테고리 (cycle 단위)

### Cycle 1.5-A — credentials propagation 마무리 (1 PR)

이미 2026-05-25 세션에서 wiring 완료, **plan 검토 + 별도 commit + smoke
test** 만 남음.

- [x] `MockSandboxBackend.exec_in` 호스트 실행
- [x] `_default_clone_runner_with_creds` (HTTP basic via extraHeader)
- [x] `credentials_resolver` 컨테이너 wiring
- [x] `_credentials_to_env` (sandbox env 전파)
- [x] Settings UI (`github_token` / `openai_api_key` / `anthropic_api_key`)
- [ ] commit + main push
- [ ] private repo 클론 1회 통과 검증 (사용자 측)

산출물: 1 commit (`feat(server,web): user-scoped credentials → sandbox + Settings UI`),
post-cycle smoke test 노트.

### Cycle 1.5-B — Editor + Save 실측 통과 (1 PR)

이미 wired. 깨지는 곳만 잡는다.

- [ ] 트리 클릭 → 에디터 표시까지의 latency / error 경로 1회 확인
- [ ] 큰 파일 (MAX_FILE_BYTES=1MB 근처) 처리 시 UX 확인
- [ ] base64 (binary) 경로에서 에디터가 깨지지 않는지
- [ ] 저장 후 디스크 반영 확인 (`stat` mtime 비교)
- [ ] 저장 실패 시 사용자 메시지 확인 (e.g. read-only 파일)

산출물: 발견되는 fix 수만큼 PR. 0이면 카드만 마감.

### Cycle 1.5-C — Chat 1 cycle 통과 (1~2 PR)

가장 위험도 큰 cycle — executor / MCP / SSE / cost / tool-call card / hook
의 전부가 한 번에 묶임.

- [ ] "세션 시작" → manifest selector 노출 → 가장 가벼운 manifest 1개로 진행
- [ ] 첫 토큰 latency 측정 (DoD #6: ≤ API + 100 ms)
- [ ] `exec.cli.permission_denied` stream-path 패치 (M0-P3 finding —
  [[reference_geny_executor_v2_1]]) — 이 cycle 에서 fix 안 되면 plan/m1-e2
  와 함께 deferred 표기
- [ ] ToolCallCard 의 Approve/Reject 버튼 hook 호출 확인
- [ ] 누적 비용이 헤더에 라이브 반영 확인 (M1 DoD #5)

산출물: 1~2 PR (executor 의존 버전 olin 가능성 있음 — 그 경우 executor
PR 1개 추가, [[feedback_extend_executor_not_adapter_layer]])

### Cycle 1.5-D — Diff 패널 최소 구현 (1 PR)

M1 의 *유일한 명시적 placeholder*. 가장 작은 형태:

- [ ] `GET /api/workspaces/{wid}/diff` — 단일 파일의 워킹트리 vs HEAD
  diff (`git diff --unified=3 <path>`)
- [ ] `DiffPanel` — 변경된 파일 목록 + 선택된 파일의 unified diff 렌더
  (Monaco DiffEditor 가 이미 의존 트리에 있다면 활용; 없다면 정적 syntax-
  highlighted 블록)
- [ ] 채팅의 tool-call 이 만들어낸 diff 와 별개로, 사용자가 직접 편집한
  변경도 잡아야 함

산출물: 1 PR. M2 에서 "diff 카드 그룹핑 + Approve all" (M2-E4) 로 확장
예정 — 본 cycle 은 *최소* 까지.

### Cycle 1.5-E — Push / PR / CI 연결 (1 PR)

- [ ] executor 도구 `gapt_create_pr` (이미 M1-E2 에 있음) 호출 시 stored
  github_token 사용 확인 — `_credentials_to_env` 가 이미 `GITHUB_TOKEN`
  + `GH_TOKEN` 둘 다 export 함
- [ ] PR 생성 후 `GET /api/projects/{pid}/ci/runs` 가 새 run 을 잡는지
  확인 (M1-E4 4.3 surface)
- [ ] CI 실패 시 패널에서 logs URL 노출 (이미 wired)

산출물: 0~1 PR (executor 측 도구 시그니처가 token 환경변수를 읽도록
이미 되어 있는지 확인 필요).

### Cycle 1.5-F — retrospective + M2 진입 결정 (commit X)

- [ ] `docs/analysis/2026-XX-XX_m1_5_retrospective.md` — 무엇이 깨졌고
  무엇이 의외였는지
- [ ] M1 DoD 6 항목 재검증 — 3 ✓ / 3 ~ 에서 6 ✓ 로 갱신 가능 여부
- [ ] [`m2_m5_outline.md`](m2_m5_outline.md) §M2 의 6개 epic 우선순위
  재정렬 (현재 dogfood 가 드러낸 페인 포인트 기반으로)

산출물: 1 progress 카드 + outline 갱신.

## 비-목표 (M2 로 deferred)

본 cycle 에서 *건드리지 않을* 것:

- 멀티 프로젝트 동시 운영 / 워크트리 1급 → M2-E1, M2-E2
- 와일드카드 프리뷰 도메인 / cloudflared 정식 → M2-E3
- Plan/Act 모드 UX 다듬기 / diff 그룹핑 / 단축키 → M2-E4
- 모바일 PWA push → M2-E5
- 빌드 캐시 최적화 → M2-E6
- L3/L4 PolicyEngine, ARQ CI poller, SMTP magic-link → 기존 M2 deferred 리스트 유지

## 리스크

| 리스크 | 영향 | 대응 |
|---|---|---|
| Chat cycle 에서 executor 의존성 (manifest, MCP bridge) 가 dev 환경에서 끊김 | 큼 — 1.5-C 통과 불가 | M0-P3 의 `poc/executor_agent/` 4개 스크립트를 dev 컨테이너에서 1회 재실행해서 baseline 확인 |
| Diff API 가 worktree dirty 가 아닌 staged 변경만 잡으면 사용자가 혼란 | 중 | DiffPanel 의 `mode={unstaged | staged | both}` selector 1개 |
| GitHub PAT 권한 부족 (private repo / push 권한 없음) → 7번 항목 실패 | 중 | Settings UI 에 권장 scope 명시 (`repo` 또는 `public_repo` + `workflow`) |
| Korean ISP ↔ GitHub 22 KB/s 로 PR 1개 cycle 이 1분+ → DoD #6 fail | 작 | 이미 인증 시 token 사용 + 추후 `cloudflared proxy` 로 mitigate (M2 에서) |
| 1.5-C 가 *예상 외로 크게* 흘러 1.5-D~F 까지 시간 부족 | 중 | C 의 *최소 통과* 만 잡고 D~F 는 짧게 — retrospective 에서 학습 |

## 검증 시나리오 (사용자 측)

1. **권한 있는 repo 로 어댑트**: `https://github.com/CocoRoF/hr_blog2.0`
   를 본인이 fork → fork URL 로 프로젝트 등록 → push 권한 확인.
2. **README 한 줄 수정 PR**: M1 DoD #1 (dogfood) 의 가장 작은 형태.
   외부 IDE 0회.
3. **재현 가능한 manifest**: `geny-executor` 의 가장 가벼운 manifest 1개
   를 dev 에 미리 등록. 본인 OPENAI/ANTHROPIC 키 Settings 에 저장.
4. **비용 cap $1**: 의도된 작은 cap. 80%/100% 알림 발화 시점 확인.
5. **소액 audit trail**: 사이클 종료 시 AuditPanel 에서 `tool.*` + `policy.*`
   이벤트가 시간순으로 깨끗하게 잡혔는지.

## 관련 docs

- [`08_web_ide_ux.md`](../08_web_ide_ux.md) — IDE shell 전체 설계
- [`12_geny_case_study.md`](../12_geny_case_study.md) — Step 1~9 의 어댑트
  체크리스트
- [`plan/m1/e2_agent_and_git.md`](m1/e2_agent_and_git.md) — Agent + Git
  surface
- [`plan/m1/e3_web_ide_shell.md`](m1/e3_web_ide_shell.md) — IDE 패널
  wiring
- [`progress/m1/_post_close_hardening.md`](../progress/m1/_post_close_hardening.md)
  — 직전 라운드의 fix 들
- [[feedback_durable_instructions]] — cadence
- [[feedback_extend_executor_not_adapter_layer]] — executor 측에 능력
  추가, GAPT 어댑터 레이어 X
- [[feedback_policy_config_not_hardcode]] — config-driven policy 유지
