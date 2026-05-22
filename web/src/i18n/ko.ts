import { type en } from "@/i18n/en";

// Korean catalog. Keys must mirror en.ts; the i18n test enforces parity.
export const ko: Record<keyof typeof en, string> = {
  // --- app shell ---
  "app.title": "GAPT — geny-adapted-project-toolkit",
  "app.phase0": "Phase 0 — 문서 우선. 웹 셸은 렌더되지만 실제 IDE는 M1-E3에서 도착.",
  "app.repo_link": "리포지터리 열기",
  "app.footer": "Apache-2.0 · CocoRoF",

  // --- locale picker ---
  "locale.label": "언어",
  "locale.en": "English",
  "locale.ko": "한국어",

  // --- exec.*.* error codes (geny-executor stable identifiers) ---
  "exec.api.auth.invalid_key": "API 키가 잘못되었거나 제공자가 거부했습니다.",
  "exec.api.rate_limited": "호출 한도 초과 — 자동 재시도 중.",
  "exec.api.timeout": "제공자 타임아웃 — 재시도 중.",
  "exec.api.token_limit": "컨텍스트 한도 초과.",
  "exec.cli.binary_not_found": "claude CLI를 런타임 이미지에서 찾지 못했습니다.",
  "exec.cli.auth_failed": "claude CLI 인증이 만료됨. claude auth login을 다시 실행하세요.",
  "exec.cli.timeout": "claude CLI 서브프로세스 타임아웃.",
  "exec.cli.permission_denied": "claude CLI 권한 시스템이 호출을 차단했습니다.",
  "exec.stage.guard_rejected": "예산 또는 정책 한도에 도달했습니다.",
  "exec.tool.access_denied": "PolicyEngine이 이 도구 호출을 거부했습니다.",
  "exec.mutation.locked": "파이프라인 단계가 진행 중 — 다음 경계에서 재시도.",
  "exec.mcp.connect_failed": "MCP 서버에 도달할 수 없습니다.",
};
