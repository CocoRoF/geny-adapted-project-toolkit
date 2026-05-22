# M0-P3: 에이전트 + MCP bridge PoC — 진행 기록

> Plan: [`../../plan/m0/p3_agent_mcp_bridge.md`](../../plan/m0/p3_agent_mcp_bridge.md)
> Status: **in_progress**
> Started: 2026-05-23
> Owner: gkfua00 (CocoRoF)
> Depends on: ✅ M0-P1 (`a4de305`), ✅ M0-P2 (`f468b15`)

## 진입 조건 검증

- [x] M0-P1 + M0-P2 통과, CI/pre-commit 그린
- [x] `claude` CLI 2.1.126 호스트 설치 (`/home/hrjang/.local/bin/claude`)
- [x] OAuth credentials cached at `~/.claude/.credentials.json` (subscription path — `ANTHROPIC_API_KEY` env 없음)
- [x] geny-executor 2.1.0+ PyPI 의존 (server/pyproject.toml에 이미 명시)
- [x] [[reference_geny_executor_v2_1]] 일독 — 21단계, claude_code_cli provider, MCP 2 boundary, HookRunner, exec.*.* 코드 모두 숙지

## Plan 카드 update (M0-P2 시점에 확인된 환경 변경 반영)

- runtime base는 Ubuntu 24.04 (noble) — `docs/06` Dockerfile 변경 (PR3 시점에 적용 완료)
- Sysbox 0.7.0 (PR2 KI-1 fix로 inner dockerd compose 작동) — 본 PR 진입 시점에 이미 호스트에 설치
- docker-ce는 inner/host 모두 29.2.1 pin (M0-P2 PR4)
- SeaweedFS 0.7.0 호환 weed 클라이언트 already in runtime image

## PR 진행 로그

### PR 1 — runtime에 claude CLI 동봉 + sandbox 부팅 + auth 경로 (대기)
### PR 2 — `gapt_default.v0.json` manifest + Pipeline.from_manifest_async smoke (대기)
### PR 3 — MCP stdio bridge (~150 LoC) + 호스트 도구 카탈로그 + CLI MCP wrap (대기)
### PR 4 — HookRunner PolicyEngine 2-layer 게이트 검증 (대기)
### PR 5 — exec.*.* 4종 에러 재현 + audit JSONL (대기)
### PR 6 — 통합 스크립트 + M0-P3 종료 (대기)

## DoD 진행

[Plan 카드](../../plan/m0/p3_agent_mcp_bridge.md) DoD 8개 그대로:

- [ ] `poc/executor_agent/` + `poc/mcp_bridge/` 산출물
- [ ] `gapt_default.json` 초안 manifest 1개로 `Pipeline.from_manifest_async()` 부팅 성공
- [ ] `claude_code_cli` provider로 "Hello, what's 2+2?" 응답
- [ ] **MCP stdio bridge** (~150 LoC) — CLI가 `mcp__gapt__gapt_hello` 호출 → host registry → 결과 반환
- [ ] **`HookRunner.PRE_TOOL_USE`** veto 검증 — `ToolFailure(ACCESS_DENIED)` 시 dispatch 거부 + audit 기록
- [ ] `EventBus` 구독으로 `api.*` / `tool.call_start` / `tool.call_complete` 캡처되어 JSONL 기록
- [ ] 비용/토큰 누계가 응답에 표시
- [ ] 에러 시나리오 4개 (`exec.cli.binary_not_found` / `exec.cli.auth_failed` / `exec.cli.timeout` / `exec.cli.permission_denied`) 재현 + audit에 코드 그대로 기록

## Drift (cycle 종료 시 작성)

*(아직 종료되지 않음)*
