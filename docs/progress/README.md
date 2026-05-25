# progress/

각 cycle의 진행 기록이 여기 들어간다.

## 사용 규칙

[`../plan/00_master_plan.md`](../plan/00_master_plan.md) §0.5 cycle 진행 흐름:

1. cycle 시작 시 → `m{n}/{cycle_id}.md` 파일 신규 생성 (Status: in_progress)
2. 매 PR 머지 시점 → 같은 파일에 한 줄 이상 추가 (날짜 + PR 링크 + 한 줄 요약)
3. cycle 종료 시 → 같은 파일 마지막에 *drift 절* 추가 (plan과 어떻게 달랐는가) + Status: done

## 현재 진행 중인 cycle

M1.5 완료 → **M2 Phase A** ([`../plan/m2_serve_capability.md`](../plan/m2_serve_capability.md))
시작 대기. Phase A 는 "다른 프로젝트 서빙" 의 inner-loop (Terminal / Logs /
Port exposure / Watch / Deploy UI / CI trigger) 를 채우는 6 cycle 묶음.
원래 M2 outline (E1~E6) 은 이 Phase A 가 끝난 *다음* 시작.

## cycle 상태 인덱스

| ID | 상태 | progress 파일 |
|---|---|---|
| M0-P1 | done | [`m0/p1_monorepo_ci.md`](m0/p1_monorepo_ci.md) |
| M0-P2 | done | [`m0/p2_isolation.md`](m0/p2_isolation.md) |
| M0-P3 | done | [`m0/p3_executor_agent.md`](m0/p3_executor_agent.md) |
| M1-E1 | done | [`m1/e1_backend_foundation.md`](m1/e1_backend_foundation.md) |
| M1-E2 | done | [`m1/e2_agent_and_git.md`](m1/e2_agent_and_git.md) |
| M1-E3 | done | [`m1/e3_web_ide_shell.md`](m1/e3_web_ide_shell.md) |
| M1-E4 | done | [`m1/e4_integration_dogfood_geny.md`](m1/e4_integration_dogfood_geny.md) |
| M1 post-close hardening | done | [`m1/_post_close_hardening.md`](m1/_post_close_hardening.md) |
| **M1.5** | done (assistant-side); user dogfood pending | [`m1_5_dogfood_readiness.md`](m1_5_dogfood_readiness.md) |

진입 조건은 [`../plan/dependencies.md`](../plan/dependencies.md).
