# Progress — M2 Phase N (scaffold-based new project creation)

> Plan: [`../plan/m2_phase_n.md`](../plan/m2_phase_n.md)
> Status: in_progress (started 2026-06-04)

## 사용자 검토 결과 (2026-06-04)

- **Q1 (slug vs repo name)**: 별도 허용. 사용자 입력 둘 다 받음.
- **R1 (토큰 소스)**: `Settings → Credentials → github_token` 시크릿이 필수.
  미설정 시 412 + Settings 페이지 링크. legacy host_github_token 은 폴백.
- **프리셋 우선순위**: 전부 (5개) v1 에 포함. empty 는 docker compose 없음
  (dev/prod 미사용), 나머지 4개는 완전한 풀 스택.
- **버튼 UX**: split-button 아닌 일반 드롭다운. `[+ 새 프로젝트 ▾]` 클릭 시
  "새로 만들기" / "불러오기" 메뉴.

## Sub-phase tracking

| ID | 범위 | Status | 노트 |
|---|---|---|---|
| N.2.1 | GithubClient + vault token resolver + scope verify | pending | — |
| N.2.2 | ScaffoldPreset registry + RenderContext + listing endpoint | pending | — |
| N.2.3 | 5 종 프리셋 (empty + fullstack + backend + frontend + static) | pending | — |
| N.2.4 | `pusher.py` git push 헬퍼 | pending | — |
| N.2.5 | `POST /projects/scaffold` 전체 트랜잭션 + alembic migration | pending | — |
| N.2.6 | 프론트 위저드 + import modal rename + 드롭다운 | pending | — |
| N.2.7 | 라이브 검증 + drift 정리 | pending | — |

## Timeline

- **2026-06-04** — 계획서 작성, 사용자 검토 완료, 답변 반영. 진행 시작.
</content>
