# gapt-web

> GAPT web IDE shell. Vite + React 19 + TypeScript strict. Renders the Phase 0 placeholder; the real `dockview` + Monaco + xterm IDE lands in M1-E3.

Status: **M0-P1 PR4 — skeleton only.** App renders a title + locale switcher + repo link. i18n catalog already reserves keys for `exec.*.*` error codes per [`docs/04 §4.10`](../docs/04_llm_agent_layer.md).

## Layout

```
web/
├── package.json
├── tsconfig.json + tsconfig.app.json + tsconfig.node.json
├── vite.config.ts                       # includes vitest config (happy-dom)
├── eslint.config.js                     # flat config, typescript-eslint
├── .prettierrc.json
├── index.html
├── src/
│   ├── main.tsx                         # StrictMode + createRoot
│   ├── app/App.tsx                      # placeholder shell
│   ├── i18n/
│   │   ├── index.ts                     # t(key, locale) + Locale union
│   │   ├── en.ts                        # source of truth
│   │   ├── ko.ts                        # mirrors en's key set
│   │   └── LanguageSwitcher.tsx
│   └── styles/index.css
└── tests/
    ├── setup.ts                         # @testing-library/jest-dom matchers
    ├── i18n.test.ts                     # key parity + exec.* coverage
    └── App.test.tsx
```

## Develop locally

```bash
cd web
pnpm install

pnpm dev                    # http://localhost:5173
pnpm build                  # tsc -b && vite build
pnpm test                   # vitest run
pnpm lint                   # eslint --max-warnings=0
pnpm format:check
pnpm typecheck
```

## i18n contract

- `src/i18n/en.ts` is the source of truth. Adding a key here forces it onto every other locale.
- `tests/i18n.test.ts` asserts `Object.keys(ko)` matches `Object.keys(en)` — adding a key without translating fails CI.
- Keys starting with `exec.` are reserved for [geny-executor stable identifiers](../docs/04_llm_agent_layer.md) — never rename them.

## Plan ↔ code mapping

- `docs/plan/m0/p1_monorepo_ci.md` cycle 4 (this PR)
- `docs/plan/m1/e3_web_ide_shell.md` cycle 3.1 (router + i18n shell)
