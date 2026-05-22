# 08. Web IDE UX

> **상위**: [03](03_system_architecture.md) / [04](04_llm_agent_layer.md)
> **다음**: [09_security_authz_observability.md](09_security_authz_observability.md)

이 문서는 GAPT의 **사용자 인터페이스 셸**을 정의한다. *왜 풀 VS Code 임베드를 안 하는가*, dockview 기반 패널 레이아웃, 채팅·에디터·터미널·프리뷰의 통합, diff/패치 UX, LLM 스트리밍 렌더링, 모바일/PWA 대응을 다룬다.

핵심 결정 7개:

1. **Vite + React SPA** — Next.js 아닌 이유는 *클라이언트-stateful*이라서.
2. **Monaco Editor + dockview 자체 셸** — code-server 임베드 안 함.
3. **xterm.js + WebSocket** — 표준.
4. **LLM 채팅이 1급 패널** — 사이드 확장 아님.
5. **diff는 인라인 + 사이드-바이-사이드 토글** — Roo Code 패턴.
6. **PWA + 반응형** — 폰/태블릿 보조 사용.
7. **Open VSX 한정 확장 모델 (M3+)** — MS Marketplace EULA 회피.

---

## 8.1 왜 풀 VS Code(code-server)를 임베드 *안 하는가*

옵션 비교 (재게재):

| 축 | code-server 임베드 | Monaco + dockview 자체 셸 |
|---|---|---|
| 개발 속도 | 빠름 (LSP/디버거 공짜) | 느림 (직접) |
| AI 통합 자유도 | 제약 (VS Code 확장 모델) | 완전 자유 |
| 메모리/리소스 | iframe+Node ~300MB | <50MB |
| 사용자 익숙함 | ★★★ | ★★ (학습) |
| **LLM 채팅이 1급 시민?** | **❌ (사이드 확장)** | **✅** |
| 라이선스 함정 | Open VSX 한정 (MS EULA) | 무관 |
| iframe sandbox 오버헤드 | 큼 | 없음 |

**결정**: **Monaco + dockview 자체 셸이 1차.** 이유는 두 줄:
- *Cursor가 VS Code를 포크한 이유와 같다.* 확장 모델로는 LLM 채팅을 *코어 인터랙션*으로 못 만든다.
- 우리는 IDE가 아니라 *AI DevOps 콘솔*이다. IDE의 모든 기능이 필요하지 않다 (디버거, refactoring, ...). 필요한 것만 골라 넣는다.

**2차 (M3+)**: openvscode-server iframe을 "VS Code로 열기" 보조 모드로. 사용자가 정말 IDE급 디버깅이 필요할 때만. Open VSX 한정.

---

## 8.2 메타프레임워크 선택

| 옵션 | SSR | 라우팅 | IDE-스러움 |
|---|---|---|---|
| Next.js App Router | 강 | 파일 | RSC와 stateful client 충돌 잦음 |
| Remix / React Router v7 | 강 | 파일 | 좋음 |
| SvelteKit | 강 | 파일 | 좋음, 번들 작음 |
| **Vite + React (SPA)** | 없음 | client | **IDE에 가장 깔끔** |

**결정**: **Vite + React SPA**.
- IDE는 본질적 stateful client. SSR 이득 적음.
- 백엔드 API는 별도 FastAPI라 분리가 자연스러움.
- 빌드/번들 단순 (`vite build`).
- 핫리로드 빠름.

상태 관리: **Zustand** (가벼움) + **TanStack Query** (서버 상태). Redux/MobX는 과함.

UI 컴포넌트: **shadcn/ui**(Radix 기반) — 디자인 시스템 self-host, 커스터마이즈 자유. Mantine/MUI는 종속 깊음.

스타일: **Tailwind CSS** + CSS variables(다크모드 토큰).

---

## 8.3 레이아웃 — dockview 기반

```
┌────────────────────────────────────────────────────────────────────┐
│ [Top Bar] gapt | Project ▼ | Workspace ▼ | env: dev | cost: $0.42  │
├────────┬───────────────────────────────────────────────────────────┤
│        │ ┌─Editor─┬─Diff─────────────┐ ┌─Chat────────────────────┐│
│        │ │ src/.. │  +++ added       │ │ ─ Plan / Act           ││
│ [Tree] │ │ ...    │  --- removed     │ │ ─ user: "PR 올려줘"    ││
│ files  │ │        │                  │ │ ─ agent: thinking...   ││
│        │ └────────┴──────────────────┘ │ ─ [tool: Edit src/x.py]││
│ tabs:  │ ┌─Terminal─────────────────┐ │ ─ [diff preview]       ││
│ - main │ │ $ pytest                  │ │ ─ [▶ Approve] [✕ Deny]││
│ - feat │ │ ...passed                 │ │                        ││
│        │ └───────────────────────────┘ └─────────────────────────┘│
│        │ ┌─Preview iframe──────────┐ ┌─CI/Logs/Audit─────────────┐│
│        │ │ https://geny.preview... │ │ workflow #42 running ⏳   ││
│        │ │ (사용자 앱 라이브)      │ │ ...                       ││
│        │ └─────────────────────────┘ └───────────────────────────┘│
├────────┴───────────────────────────────────────────────────────────┤
│ [Status Bar] CPU 24% | Mem 1.2/4 GB | sandbox: running             │
└────────────────────────────────────────────────────────────────────┘
```

### 8.3.1 dockview 선택 이유

| 라이브러리 | 강점 | 약점 |
|---|---|---|
| **dockview** | IDE급 (탭/그룹/드래그/플로팅/팝아웃), zero-dep | 학습곡선 |
| react-resizable-panels | 단순, 견고 | 도킹/탭 없음 |
| golden-layout | 성숙 | 오래됨, React 통합 거침 |
| rc-dock | 가벼움 | 유지보수 둔화 |

dockview가 *팝아웃 윈도우* 지원 — 사용자가 프리뷰를 별 모니터에 띄울 수 있다. 큰 가치.

### 8.3.2 레이아웃 프리셋

- **Focus**: 에디터 + 채팅 (좌-우 2분할). 코딩 집중.
- **Review**: diff + 채팅 + CI 로그. PR 리뷰.
- **Debug**: 에디터 + 터미널 + 프리뷰. 버그 추적.
- **Custom**: 사용자가 dockview 상태 저장.

전환은 `Ctrl+Alt+1/2/3/4`.

---

## 8.4 채팅 패널 — 1급 시민

### 8.4.1 메시지 종류

| 종류 | 시각 표현 |
|---|---|
| User input | 우측 정렬, 사용자 아바타 |
| Agent thinking | 회색 텍스트, "사고 중..." 표시 (옵션 토글) |
| Agent text response | 좌측, markdown 렌더링 |
| Tool call | 카드 (도구 이름 + 인자 요약 + 진행 상태) |
| Tool result | 카드 (성공/실패 + 결과 요약 + "전체 보기") |
| Diff preview | 인라인 diff 미리보기 + Apply/Deny 버튼 |
| Plan | 체크리스트 카드 + "Act" 버튼 |
| Error | 빨간 카드 + 원인 + "재시도" |
| Cost spike | 노란 카드 + 누적 USD + "중단" |

### 8.4.2 Plan/Act 모드 (Roo Code 패턴)

기본 흐름:
1. 사용자가 작업 요청 → 에이전트가 *Plan*만 출력 (코드 변경 없음).
2. 사용자가 Plan을 검토. 일부 단계 편집/삭제 가능.
3. "Act" 클릭 → 에이전트가 Plan을 실행. 단계별 진행/실패 표시.

스킵 가능:
- 사용자가 채팅에서 "그냥 해줘" → Act 모드 즉시.
- 작은 변경(예: 1줄 수정)은 Plan 생략 옵션.

### 8.4.3 diff 적용 UX

LLM이 Edit 도구 호출 → 결과는 *자동 적용*되지 않는다 (기본). 채팅에 diff 카드가 나타남:
- 인라인 (작은 변경, < 20줄)
- 사이드-바이-사이드 (Monaco DiffEditor, 큰 변경)
- *적용 전*: Approve / Deny / Edit
- *적용 후*: 워크스페이스 파일 변경 + 워치/재기동 트리거

`permissionMode = yolo`인 경우 자동 적용 + 사용자에게 *나중에* 변경 요약.

### 8.4.4 토큰 단위 스트리밍

- SSE로 토큰 단위 수신.
- React state 누적 + memoized markdown render (`react-markdown` + `rehype`).
- 긴 코드블록은 *생성 중*에도 syntax highlight 점진 적용.
- 사용자가 스크롤을 위로 올린 상태면 *자동 스크롤 정지* (Slack 패턴).

### 8.4.5 가상 스크롤

긴 세션(100+ 메시지)에서 메시지 가상 스크롤 (`tanstack-virtual` 또는 `react-virtuoso`). 필수.

### 8.4.6 채팅 입력창

- 다중 라인 + `Shift+Enter`로 줄바꿈, `Enter`로 전송.
- `@file path/to/x.py` 자동완성 → 파일 컨텍스트 첨부.
- `@tool ToolName` → 직접 도구 호출.
- `@/clear` → 새 세션 시작 (확인).
- 슬래시 명령: `/plan`, `/act`, `/review`, `/deploy`, `/cost`.
- 첨부 파일 (이미지, PDF) → Anthropic 멀티모달 입력.

---

## 8.5 에디터 — Monaco

### 8.5.1 핵심 기능

- syntax highlight (Monaco 기본 + Tree-sitter 추가 언어)
- 자동 들여쓰기, bracket matching, code folding
- 다중 커서, 멀티 선택
- 찾기/바꾸기 (regex)
- LSP 통합 (`monaco-languageclient`) — M3+
- diff 뷰어 (Monaco DiffEditor)
- minimap
- 사용자 키바인딩 (VS Code/Vim/Emacs 프리셋)

### 8.5.2 파일 트리

- 좌측 패널, 가상화 (큰 레포 대비)
- 파일 작업: 생성/이름 변경/삭제 (확인)
- git 상태 표시 (변경/추가/삭제 색 dot)
- `.gitignore` 패턴 회색 처리
- 검색 (`Ctrl+Shift+F`) — rg/fd 백엔드 (데몬에서 실행)

### 8.5.3 LSP는 어디서

- M0~M2: Monaco 기본 (단순 highlight + 자동완성 정도).
- M3+: 데몬이 컨테이너 안에서 `pyright`/`tsserver`/`gopls` 등 실행, WebSocket bridge.
- M5+: 사용자 정의 LSP (`.gapt/lsp.json`)

오버 엔지니어링 위험. *진짜 IDE 디버깅이 필요하면 "VS Code로 열기" 보조 모드*가 더 합리적일 수 있음.

### 8.5.4 자동 저장 vs 명시 저장

- 기본: **자동 저장** (300ms 디바운스).
- 비활성화 옵션 (사용자 선호).
- 저장 시 *백엔드에 patch* (전체 파일 아님) — 큰 파일에서 대역폭 절약.

### 8.5.5 멀티 사용자 협업 (M3+)

- CRDT (Yjs) 기반 동시 편집.
- 같은 워크스페이스에 2명이 들어오면 색 커서 표시.
- 충돌은 CRDT가 자동 해결.
- M0~M2는 단일 사용자 모드, 동시 편집 시 마지막 저장 승리 + 경고.

---

## 8.6 터미널 — xterm.js

### 8.6.1 구성

- **xterm.js v5+** + addons (fit, web-links, search, serialize).
- WebSocket으로 데몬 PTY에 attach.
- 다중 탭 (한 워크스페이스에 N개 터미널).
- 분할 (가로/세로).
- 사용자 셸 선택 (bash 기본, zsh/fish 옵션).

### 8.6.2 LLM과의 상호작용

- LLM이 `Bash` 도구 호출 → 별도 *epheremal* PTY에서 실행, 결과만 캡처.
- 사용자가 *직접* 터미널 사용 — 같은 컨테이너지만 *별도* PTY. 서로 간섭 없음.
- 사용자가 *명시적*으로 "현재 터미널 LLM에 공유" 가능 (그러면 LLM이 stdout/stdin 볼 수 있음).

### 8.6.3 출력 보존

- 세션당 마지막 N라인 (예: 10,000) DB 보존.
- 사용자가 *과거 출력 검색* 가능.
- 무한 출력 폭주 (예: tail -f) 대비 ring buffer.

---

## 8.7 프리뷰 패널

- iframe (subdomain URL 직접 또는 path-based proxy)
- "외부 브라우저로 열기" 버튼
- QR 코드 (모바일 테스트)
- 자동 리프레시 (워치 재기동 시 트리거)
- 다중 디바이스 사이즈 시뮬레이션 (responsive 검증)
- 외부 공유 토글 (cloudflared, → [07](07_cicd_and_preview.md))

---

## 8.8 CI / Audit / Logs 패널

dockview의 한 그룹:

- **CI 탭**: GitHub Actions / Woodpecker 진행 + 로그 라이브 스트림.
- **Audit 탭**: 본 세션의 도구 호출/파일 변경/배포 이벤트. 필터링.
- **Logs 탭**: compose 서비스 로그 (사용자 선택 서비스의 stdout).
- **Cost 탭**: 본 세션의 비용 그래프.

---

## 8.9 알림 & 토스트

- 우측 하단 토스트: 작업 완료/실패/CI 그린.
- *Center Notifications* 패널: 시간순.
- 옵션: web push (PWA 활성화 시), 이메일, Slack/Discord.

---

## 8.10 명령 팔레트 (Command Palette)

`Ctrl+K` (Cursor/Linear 패턴) — 모든 액션의 단일 진입.

예: `Switch workspace`, `Deploy to staging`, `Reset session`, `Toggle dark mode`, `Open settings: secrets`, `Run /plan`...

전체 검색 (파일/세션/명령/설정) 통합.

---

## 8.11 단축키

| 단축 | 액션 |
|---|---|
| `Ctrl+K` | Command palette |
| `Ctrl+Enter` (채팅) | 메시지 전송 |
| `Shift+Enter` (채팅) | 줄바꿈 |
| `Ctrl+P` | 파일 열기 |
| `Ctrl+Shift+F` | 워크스페이스 검색 |
| `Ctrl+Shift+P` | 명령 팔레트 (Command palette 별칭) |
| `Ctrl+\`` | 터미널 토글 |
| `Ctrl+Alt+1/2/3/4` | 레이아웃 프리셋 전환 |
| `Ctrl+S` | 저장 (자동 저장 안 쓸 때) |
| `Esc` | LLM 응답 중단 |
| `Ctrl+Z` (에디터) | undo |

사용자 정의 가능 (`~/.gapt/keybindings.json` 패턴).

---

## 8.12 다크/라이트 모드 & 디자인 토큰

- 기본 다크 (개발자 선호).
- 디자인 토큰은 CSS variables, Tailwind에서 사용.
- 자체 디자인 시스템 (shadcn 위), 폰트는 *Inter* + 코드용 *Geist Mono* 또는 *JetBrains Mono*.

[[feedback_no_decorative_chrome]] 준수: **장식 이모지/포인터 카드/크로스-스테이지 빵부스러기 없음**. 정보 밀도 높게.

---

## 8.13 PWA / 모바일

- PWA manifest + service worker (캐시, offline 셸).
- 폰/태블릿에서:
  - 좌측 트리는 슬라이드 메뉴.
  - 에디터 비활성, 채팅 + 프리뷰 + 터미널 (읽기 위주) 활성.
  - 단순 액션 ("Deploy 승인", "PR 머지") 가능.
  - 본격 코딩은 데스크탑에서.

P1 시나리오: *출장 중 폰으로 한 줄 LLM에 시키고 Deploy*.

---

## 8.14 접근성

- WCAG 2.1 AA 목표 (M3+).
- 키보드 only 가능.
- 명시적 focus ring.
- screen reader 친화 ARIA.
- 색맹 친화 팔레트 (이미지 색만으로 정보 표현 금지).

---

## 8.15 국제화

- 1차: 영어 + 한국어 (P1 사용자).
- i18n 라이브러리: `i18next` + `react-i18next`.
- 메시지 키 기반, 컴포넌트에 하드코딩 금지.
- LLM 응답은 LLM 언어 그대로 (다국어 prompting 통해 사용자가 제어).

---

## 8.16 성능 목표

| 지표 | 목표 (M1) |
|---|---|
| 첫 의미있는 페인트 (FMP) | < 1.5s |
| 워크스페이스 진입 → 채팅 입력 가능 | < 3s |
| LLM 토큰 첫 표시 | API 응답 + 50ms 이내 |
| 에디터 키 입력 → 화면 | 16ms (60fps) |
| 파일 트리 1000개 노드 | 가상화로 매끄럽게 |
| dockview 패널 드래그 | 60fps |

`Lighthouse` Performance ≥ 85, `Web Vitals` Good.

---

## 8.17 에러 / 빈 상태

- 모든 패널이 *빈 상태*를 가진다 ("워크스페이스 없음 — 생성하기").
- 네트워크 에러: 끊김 토스트 + 자동 재연결.
- LLM 실패: 카드에 원인 + "재시도" / "다른 모델" 옵션.
- compose 부팅 실패: 디버깅 가이드 링크.

---

## 8.18 본 문서가 보장하는 인터페이스

1. **LLM 채팅은 1급 패널.** 사이드 확장 아님.
2. **diff는 항상 사용자가 *적용 전*에 본다** (yolo 모드 제외).
3. **Open VSX만 사용한다 (M3+ 확장 추가 시).**
4. **자동 저장은 기본 ON, 명시 끄기 가능.**
5. **데스크탑이 1차, 폰/태블릿은 보조 — 인터페이스가 PWA로 동작.**
6. **모든 액션이 Command Palette에서 도달 가능.**
7. **[[feedback_no_decorative_chrome]] 준수 — 정보 밀도 우선, 장식 없음.**

[09](09_security_authz_observability.md)는 *권한 모델 / 감사 / 관측*을 다룬다.
