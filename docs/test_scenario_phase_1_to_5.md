# GAPT end-to-end 검증 시나리오 — Phase 1 ~ 5

이번 사이클로 새로 들어간 모든 기능을 **한 번에** 검증하는 단일 시나리오.
각 단계가 직전 단계 결과를 입력으로 받음.

소요 시간: 약 15~20분 (compose 빌드 시간 포함).

## 사전 조건

- [ ] `https://gapt.hrletsgo.me/` 열어서 로그인 가능
- [ ] `docker ps`로 다음 6개 컨테이너 떠있음:
  - `gapt-dev-postgres-1` (healthy)
  - `gapt-dev-caddy-1`
  - `gapt-dev-grafana-1` / `prometheus-1` (옵션)
  - `cloudflared` (systemd)
- [ ] GAPT 서버: `curl https://gapt.hrletsgo.me/health` → `{"status":"ok"}`
- [ ] gapt-workspace 이미지 최신 (gh CLI 포함):
  ```
  docker run --rm gapt-workspace:latest gh --version
  → gh version 2.92.0
  ```
- [ ] (선택) Settings → Secrets에 `ANTHROPIC_API_KEY` 또는 호스트 OAuth (`~/.claude/.credentials.json`) 존재

## 단계

### 1. 새 프로젝트 + 워크스페이스 (1분)
1. `https://gapt.hrletsgo.me/` → **프로젝트 → + 새 프로젝트**
2. GitHub URL: `https://github.com/CocoRoF/hr_blog2.0.git`
3. slug `phase-test`, display name 자유
4. 프로젝트 secret refs에 GitHub 토큰 바인딩 (Settings → Secrets에서 `github_token` 등록 후 프로젝트 매핑)
5. 워크스페이스 생성 (branch=main) → clone 끝까지 대기 → status `running`

**확인**:
- IDE 열림. FileTree에 `backend/`, `frontend/`, `docker-compose.prod.yml` 보임

### 2. 자동 감지 위자드 (1분) — **F1.1 ~ F1.5**
1. 워크스페이스 처음 진입 시 위자드 모달 자동 오픈
2. **요약 헤더**: `nextjs` 배지, `docker compose` 배지, **자신감 90%**
3. **"무엇을 찾았는지 보기"** 펼치면 4~5줄:
   - `compose: docker-compose.prod.yml (9 services)`
   - `primary service → frontend:3000`
   - `node: frontend/src/package.json → kind=nextjs`
   - `dev command: npm run dev (port 3000)`
   - `basePath-capable framework — config at frontend/src/next.config.ts`
4. **Dev 섹션**: command `npm run dev`, port `3000`, cwd `frontend/src`
5. **Prod 섹션**: 환경 이름 `prod`, compose `docker-compose.prod.yml`, primary `frontend`, port `3000`, `--build` 체크
6. **🛠️ Next.js basePath 자동 패치** 섹션 보임
7. **`패치 실행`** 클릭 → 2개 파일 patched (`frontend/src/next.config.ts`, `frontend/Dockerfile`)
8. **`이대로 적용`** 클릭 → 모달 닫힘, 토스트:
   - `started dev service 'dev' → cd frontend/src && npm run dev`
   - `created environment 'prod' → frontend:3000`

**확인**:
- 우측 하단 토스트 표시
- 좌측 파일 트리 새로고침 후 `next.config.ts` 안에 `// gapt: next-basepath-patch` marker 있음

### 3. .env 편집기 (30초) — **F1.4**
1. 툴바 `🔑 .env` 또는 `Ctrl+Shift+E`
2. 좌측에 `backend/.env`, `frontend/.env`, `backend/.env.example` 등 자동 나열
3. `backend/.env` 클릭 → 내용 표시
4. `ANTHROPIC_API_KEY=` 값 추가 → **`저장`** → 미저장 배지 사라짐

### 4. Dev 미리보기 (1분) — **F2.1 + F4.1**
1. **개발** 탭 클릭
2. 좌측 Services 패널에서 `dev` 서비스 `running` 상태 확인 (위자드가 자동 시작함)
3. 우측 Preview panel dropdown에서 `dev` 선택 (선택지 비어있으면 expose 한 번 누름)

   *주의: dev 서비스는 path-based preview 자동 생성 안 됨 — 직접 `Expose` 버튼 누르면 `gapt.hrletsgo.me/preview/<wid>-dev/` 형식 URL 받음*

4. 미리보기 iframe에 hr_blog2.0 dev 페이지 로드 → 본문 로드 + CSS/JS 정상

**확인 (HMR)**:
- IDE 에디터에서 `frontend/src/app/page.tsx` 등 열어서 텍스트 한 줄 수정 → 저장 (`Ctrl+S`)
- 1~3초 안에 Preview iframe이 자동 갱신 (WATCHPACK_POLLING + Caddy WebSocket pass-through 동작)

### 5. 테스트 러너 (30초) — **F2.3**
1. 툴바 `🧪 테스트` 또는 `Ctrl+Shift+T`
2. 감지된 명령 표시 (없으면 input 직접: `pytest --version` 또는 `node --version`)
3. **`실행`** → 출력 라인 실시간 스트림
4. 종료 시 `exit 0` 배지 + `0.X s` 시간

### 6. 에이전트 협업 (3~5분) — **에이전트 ↔ Dev preview loop**
1. **IDE** 탭으로 돌아가서 우측 Chat panel
2. "frontend/src/app/page.tsx의 페이지 제목을 'GAPT Test'로 바꿔" 입력 → 전송
3. 에이전트가 도구 호출 (Read, Edit, Write) → 결과 표시
4. **개발** 탭으로 돌아가서 Preview iframe 자동 갱신 (HMR로) → 변경 확인

### 7. Git commit + push + PR (1~2분) — **F4.2 + F4.3**
1. 툴바 `🌿 Git` 또는 `Ctrl+Shift+S`
2. 좌측에 변경된 파일들 (next.config.ts, Dockerfile, page.tsx 등) 체크박스 모두 체크
3. 파일 클릭 → 우측에 unified diff 표시
4. 커밋 메시지: `chore: GAPT basePath patch + agent edit`
5. **`커밋`** → 토스트 `커밋 abc1234 (gapt-test)`
6. **`푸시`** → 토스트 `푸시 완료 → origin/main`
7. **`PR`** → 토스트 `PR #N 생성됨` + 링크

**확인**:
- `https://github.com/CocoRoF/hr_blog2.0/pulls` 에서 새 PR 보임
- PR 본문에 커밋 메시지 들어있음

### 8. Prod 배포 (3~5분) — **Phase 3 history + rollback**
1. **🚀 배포** 탭
2. `prod` 환경 행에 위자드가 만든 설정 보임
3. **`배포`** 클릭 → 우측 로그 라이브 스트림 (compose pull → up -d --build → routing)
4. 1~2분 후 `success` 배지 + bound_url 링크
5. URL 클릭 → 새 탭에 hr_blog2.0 prod 페이지 (CSS/JS 모두 로드)

### 9. 배포 이력 + 롤백 (30초) — **F3.3 + F3.4**
1. 같은 환경 행에서 **`이력`** 버튼 클릭
2. 펼친 패널에 방금 한 배포 1개 row 보임:
   - `success` 배지
   - version 해시
   - `manual` trigger_kind
   - 배포 시각
3. **↶** 버튼 클릭 → 같은 버전으로 rollback → 새 row 추가 (`rolled_back` 상태, `rollback` trigger_kind)

### 10. Push webhook 자동 배포 (5분) — **F3.1**
1. **`프로젝트` 헤더 → Settings (또는 직접 API)**: webhook secret 발급
   ```bash
   curl -X POST -b "<session-cookie>" \
     https://gapt.hrletsgo.me/api/projects/<pid>/webhooks/secret
   # 응답의 secret 복사
   ```
2. GitHub repo Settings → Webhooks:
   - URL: `https://gapt.hrletsgo.me/api/projects/<pid>/webhooks/github`
   - Content type: `application/json`
   - Secret: 위에서 받은 값
   - Events: `Just the push event` 선택
3. Environment `prod`의 `deploy_target_config`에 trigger 추가 (DB 직접):
   ```sql
   UPDATE environments
   SET deploy_target_config = deploy_target_config || '{"trigger":{"branch":"main"}}'::jsonb
   WHERE id='<eid>';
   ```
   *(다음 사이클에서 Environments 편집 UI에 폼 필드로 추가 예정)*
4. **GitHub에서 main 브랜치에 commit + push** (Git 패널의 단계 7에서 PR merge하거나 별도 push)
5. ~5초 내 GAPT가 webhook 수신 → 백그라운드 deploy 시작 → 알림 벨에 `Deploy success: prod (push main)` 알림

**확인**:
- 이력 패널에 새 row가 `webhook:main` trigger_kind로 추가됨

### 11. gapt-net 자동 reconnect (1분) — **F5.1**
호스트 터미널에서:
```bash
docker network disconnect gapt-net new-web-frontend
# 1분 안에...
docker inspect new-web-frontend --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}'
# → gapt-net 다시 들어가있음
```

서버 로그(`/tmp/gapt-server.log`)에 `reconciler.reconnected container=new-web-frontend` 출력 확인.

---

## 모두 통과 시 검증된 흐름

```
[GitHub URL 붙여넣기]
   ↓
[GAPT가 자동 감지: Next.js + FastAPI + Postgres + ...]
   ↓
[1-클릭 위자드 적용 + basePath patch]
   ↓
[dev 자동 시작 + HMR 동작 + 미리보기 iframe]
   ↓
[에이전트가 코드 수정 → 즉시 preview 반영]
   ↓
[Git commit + push + PR 한 화면]
   ↓
[Prod 배포 + 외부 URL]
   ↓
[GitHub push 시 자동 재배포]
   ↓
[배포 이력 / rollback / 자동 reconnect]
```

이 시나리오 안에서 깨지는 게 있으면 그 단계가 다음 사이클 우선순위.

---

## 알려진 한계 (이 사이클로 해결 안 함)

- **Dev 서비스 자동 expose**: 위자드 적용 후 dev 서비스는 시작되지만, preview URL은 사용자가 Services 패널에서 `Expose` 한 번 더 눌러야 함. 다음 사이클에서 옵션 추가 검토.
- **Trigger 설정 UI**: webhook에 반응할 branch 설정이 아직 DB JSONB 수동 편집. EnvironmentEditorModal 폼 필드로 들어가야 함.
- **Wildcard SSL**: `*.gapt.hrletsgo.me` 미지원 (Cloudflare 무료 plan 한계). subdomain 모드는 유료 cert / zone-level wildcard 권장.
- **Files API 컨테이너 격리**: 워크스페이스 file CRUD는 아직 host에서 동작 (path traversal 가드만 있음). 다음 사이클에서 sandbox.exec()로 라우팅.
- **basePath 패처는 Next.js 전용**: Vite / Nuxt 자동 패처 미구현.
