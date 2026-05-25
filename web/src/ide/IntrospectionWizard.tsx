import { useCallback, useEffect, useState } from "react";
import {
  CheckCircle2,
  Loader2,
  Rocket,
  Server,
  Sparkles,
  XCircle,
} from "lucide-react";

import {
  type ApplyIntrospectionInput,
  type AutoPatchResponse,
  type IntrospectResponse,
  applyIntrospection,
  autoPatchNextjsBasePath,
  getIntrospection,
} from "@/api/introspect";
import { Badge } from "@/ui/Badge";
import { Button } from "@/ui/Button";
import { Modal } from "@/ui/Modal";

interface Props {
  open: boolean;
  workspaceId: string;
  onClose: () => void;
  /** Called after Apply succeeds — gives the workspace shell a hook
   * to refresh services / environments lists. */
  onApplied?: (result: { actions: string[] }) => void;
}

/** First-open wizard: shows what GAPT detected in the worktree
 * and lets the user accept or override before materialising a dev
 * Service + prod Environment. Opens once per workspace by default
 * (caller persists "dismissed" in localStorage). */
export function IntrospectionWizard({
  open,
  workspaceId,
  onClose,
  onApplied,
}: Props) {
  const [intro, setIntro] = useState<IntrospectResponse | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [overrides, setOverrides] = useState<ApplyIntrospectionInput>({});
  const [applying, setApplying] = useState(false);
  const [applyErr, setApplyErr] = useState<string | null>(null);
  const [patching, setPatching] = useState(false);
  const [patchResult, setPatchResult] = useState<AutoPatchResponse | null>(null);
  const [patchErr, setPatchErr] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setLoadErr(null);
    setIntro(null);
    setOverrides({});
    getIntrospection(workspaceId)
      .then(setIntro)
      .catch((e) => setLoadErr(e instanceof Error ? e.message : String(e)));
  }, [open, workspaceId]);

  const handlePatch = useCallback(async () => {
    setPatching(true);
    setPatchErr(null);
    setPatchResult(null);
    try {
      const res = await autoPatchNextjsBasePath(workspaceId);
      setPatchResult(res);
    } catch (e) {
      setPatchErr(e instanceof Error ? e.message : String(e));
    } finally {
      setPatching(false);
    }
  }, [workspaceId]);

  const handleApply = useCallback(async () => {
    setApplying(true);
    setApplyErr(null);
    try {
      const res = await applyIntrospection(workspaceId, overrides);
      onApplied?.({ actions: res.actions });
      onClose();
    } catch (e) {
      setApplyErr(e instanceof Error ? e.message : String(e));
    } finally {
      setApplying(false);
    }
  }, [overrides, onApplied, onClose, workspaceId]);

  return (
    <Modal
      open={open}
      onClose={onClose}
      size="lg"
      title="프로젝트 자동 감지"
      description="GAPT가 워크스페이스 내용을 살펴보고 dev 서버와 prod 배포 설정을 제안합니다."
      footer={
        <div className="flex items-center justify-between gap-2">
          <p className="text-[11px] text-fg-subtle">
            나중에 IDE → 개발 / 배포 탭에서 수정할 수 있습니다.
          </p>
          <div className="flex gap-1.5">
            <Button variant="secondary" onClick={onClose} disabled={applying}>
              건너뛰기
            </Button>
            <Button
              variant="primary"
              onClick={handleApply}
              disabled={applying || !intro}
            >
              {applying ? (
                <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
              ) : (
                <Sparkles className="mr-1 h-3.5 w-3.5" />
              )}
              이대로 적용
            </Button>
          </div>
        </div>
      }
    >
      {loadErr ? (
        <ErrorBox message={loadErr} />
      ) : !intro ? (
        <LoadingBox />
      ) : (
        <div className="space-y-4">
          {applyErr ? <ErrorBox message={applyErr} /> : null}
          <SummaryHeader intro={intro} />
          {intro.notes.length > 0 ? <NotesList notes={intro.notes} /> : null}

          {intro.dev_command ? (
            <DevSection
              intro={intro}
              overrides={overrides}
              setOverrides={setOverrides}
            />
          ) : null}

          {(intro.prod_compose_path || intro.has_compose) ? (
            <ProdSection
              intro={intro}
              overrides={overrides}
              setOverrides={setOverrides}
            />
          ) : null}

          {intro.env_files.length > 0 || intro.env_examples.length > 0 ? (
            <EnvFilesNote intro={intro} />
          ) : null}

          {intro.needs_basepath ? (
            <BasePathPatchSection
              configFile={intro.basepath_config_file}
              patching={patching}
              patchResult={patchResult}
              patchErr={patchErr}
              onPatch={handlePatch}
            />
          ) : null}
        </div>
      )}
    </Modal>
  );
}

function LoadingBox() {
  return (
    <div className="flex items-center gap-2 rounded-md border border-border bg-bg p-4 text-[12px] text-fg-muted">
      <Loader2 className="h-4 w-4 animate-spin" />
      워크스페이스를 살펴보는 중…
    </div>
  );
}

function ErrorBox({ message }: { message: string }) {
  return (
    <div className="flex items-start gap-2 rounded-md border border-danger/40 bg-danger/10 p-3 text-[12px] text-danger">
      <XCircle className="mt-[2px] h-4 w-4 shrink-0" />
      <span className="break-all">{message}</span>
    </div>
  );
}

function SummaryHeader({ intro }: { intro: IntrospectResponse }) {
  const conf = Math.round(intro.confidence * 100);
  const tone =
    conf >= 80 ? "success" : conf >= 40 ? "warn" : ("neutral" as const);
  return (
    <div className="flex flex-wrap items-center gap-2 text-[12px]">
      <Badge tone={tone}>
        {intro.kind === "unknown" ? "프레임워크 미감지" : intro.kind}
      </Badge>
      {intro.has_compose ? <Badge tone="accent">docker compose</Badge> : null}
      <span className="text-fg-subtle">자신감 {conf}%</span>
      {intro.sources.length > 0 ? (
        <span className="text-fg-subtle">
          출처: {intro.sources.join(", ")}
        </span>
      ) : null}
    </div>
  );
}

function NotesList({ notes }: { notes: string[] }) {
  return (
    <details className="rounded-md border border-border bg-bg p-3 text-[12px]">
      <summary className="cursor-pointer text-fg-muted">
        무엇을 찾았는지 보기 ({notes.length})
      </summary>
      <ul className="mt-2 space-y-1 text-fg-muted">
        {notes.map((n, i) => (
          <li key={i} className="flex items-start gap-1.5">
            <CheckCircle2 className="mt-[2px] h-3 w-3 shrink-0 text-accent" />
            <span>{n}</span>
          </li>
        ))}
      </ul>
    </details>
  );
}

function DevSection({
  intro,
  overrides,
  setOverrides,
}: {
  intro: IntrospectResponse;
  overrides: ApplyIntrospectionInput;
  setOverrides: (v: ApplyIntrospectionInput) => void;
}) {
  const enabled = overrides.create_dev_service !== false;
  return (
    <section className="rounded-md border border-border bg-bg-elevated p-3">
      <header className="mb-2 flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5 text-[12px] font-semibold text-fg">
          <Server className="h-3.5 w-3.5 text-fg-muted" />
          개발 서버 (Dev)
        </div>
        <label className="flex items-center gap-1.5 text-[11px] text-fg-muted">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) =>
              setOverrides({
                ...overrides,
                create_dev_service: e.currentTarget.checked,
              })
            }
          />
          시작하기
        </label>
      </header>
      <Row
        label="명령"
        value={overrides.dev_command ?? intro.dev_command ?? ""}
        onChange={(v) => setOverrides({ ...overrides, dev_command: v })}
        disabled={!enabled}
      />
      <Row
        label="포트"
        value={String(overrides.dev_port ?? intro.dev_port ?? "")}
        onChange={(v) =>
          setOverrides({
            ...overrides,
            dev_port: v ? Number.parseInt(v, 10) || null : null,
          })
        }
        disabled={!enabled}
        narrow
      />
      {intro.dev_cwd ? (
        <Row
          label="작업 디렉토리"
          value={overrides.dev_cwd ?? intro.dev_cwd ?? ""}
          onChange={(v) => setOverrides({ ...overrides, dev_cwd: v })}
          disabled={!enabled}
        />
      ) : null}
    </section>
  );
}

function ProdSection({
  intro,
  overrides,
  setOverrides,
}: {
  intro: IntrospectResponse;
  overrides: ApplyIntrospectionInput;
  setOverrides: (v: ApplyIntrospectionInput) => void;
}) {
  const enabled = overrides.create_prod_environment !== false;
  return (
    <section className="rounded-md border border-border bg-bg-elevated p-3">
      <header className="mb-2 flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5 text-[12px] font-semibold text-fg">
          <Rocket className="h-3.5 w-3.5 text-fg-muted" />
          프로덕션 환경 (Prod)
        </div>
        <label className="flex items-center gap-1.5 text-[11px] text-fg-muted">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) =>
              setOverrides({
                ...overrides,
                create_prod_environment: e.currentTarget.checked,
              })
            }
          />
          환경 생성
        </label>
      </header>
      <Row
        label="환경 이름"
        value={overrides.prod_environment_name ?? "prod"}
        onChange={(v) =>
          setOverrides({ ...overrides, prod_environment_name: v })
        }
        disabled={!enabled}
      />
      <Row
        label="compose 파일"
        value={overrides.prod_compose_path ?? intro.prod_compose_path ?? ""}
        onChange={(v) => setOverrides({ ...overrides, prod_compose_path: v })}
        disabled={!enabled}
      />
      <Row
        label="primary 서비스"
        value={overrides.prod_primary_service ?? intro.prod_primary_service ?? ""}
        onChange={(v) =>
          setOverrides({ ...overrides, prod_primary_service: v })
        }
        disabled={!enabled}
      />
      <Row
        label="primary 포트"
        value={String(
          overrides.prod_primary_port ?? intro.prod_primary_port ?? "",
        )}
        onChange={(v) =>
          setOverrides({
            ...overrides,
            prod_primary_port: v ? Number.parseInt(v, 10) || null : null,
          })
        }
        disabled={!enabled}
        narrow
      />
      <label className="mt-1.5 flex items-center gap-1.5 text-[11px] text-fg-muted">
        <input
          type="checkbox"
          checked={
            overrides.prod_build !== undefined
              ? overrides.prod_build === true
              : intro.prod_build_required
          }
          onChange={(e) =>
            setOverrides({ ...overrides, prod_build: e.currentTarget.checked })
          }
          disabled={!enabled}
        />
        매 배포마다 빌드 (`docker compose up --build`)
      </label>
    </section>
  );
}

function EnvFilesNote({ intro }: { intro: IntrospectResponse }) {
  return (
    <section className="rounded-md border border-border bg-bg p-3 text-[11px] text-fg-muted">
      <p className="font-semibold text-fg">.env 파일</p>
      {intro.env_files.length > 0 ? (
        <p className="mt-1">
          이미 있음: {intro.env_files.map((f) => <code key={f} className="mr-1 rounded bg-bg-elevated px-1">{f}</code>)}
        </p>
      ) : null}
      {intro.env_examples.length > 0 ? (
        <p className="mt-1">
          템플릿: {intro.env_examples.map((f) => <code key={f} className="mr-1 rounded bg-bg-elevated px-1">{f}</code>)}
        </p>
      ) : null}
      <p className="mt-1 text-fg-subtle">
        IDE 파일 트리에서 직접 편집하세요. (다음 사이클에서 전용 패널 추가 예정)
      </p>
    </section>
  );
}

function BasePathPatchSection({
  configFile,
  patching,
  patchResult,
  patchErr,
  onPatch,
}: {
  configFile: string | null;
  patching: boolean;
  patchResult: AutoPatchResponse | null;
  patchErr: string | null;
  onPatch: () => void;
}) {
  return (
    <section className="rounded-md border border-accent/40 bg-accent/5 p-3">
      <header className="mb-1.5 flex items-center justify-between gap-2">
        <div className="text-[12px] font-semibold text-accent">
          🛠️ Next.js basePath 자동 패치
        </div>
        <Button
          variant="secondary"
          onClick={onPatch}
          disabled={patching}
        >
          {patching ? (
            <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
          ) : (
            <Sparkles className="mr-1 h-3.5 w-3.5" />
          )}
          패치 실행
        </Button>
      </header>
      <p className="text-[11px] text-fg-muted">
        이 앱은 GAPT path 기반 preview에서 동작하려면 빌드 시
        <code className="mx-1 rounded bg-bg-elevated px-1">NEXT_PUBLIC_BASE_PATH</code>
        가 필요합니다. 워크스페이스 클론의
        <code className="mx-1 rounded bg-bg-elevated px-1">{configFile ?? "next.config.*"}</code>
        와 Dockerfile만 수정합니다 (GitHub repo는 그대로).
      </p>
      {patchErr ? (
        <div className="mt-2 rounded-md border border-danger/40 bg-danger/10 p-2 text-[11px] text-danger">
          {patchErr}
        </div>
      ) : null}
      {patchResult ? (
        <div className="mt-2 space-y-1 text-[11px] text-fg-muted">
          {patchResult.patched_files.length > 0 ? (
            <p>
              <CheckCircle2 className="mr-1 inline h-3 w-3 text-success" />
              패치 적용:{" "}
              {patchResult.patched_files.map((f) => (
                <code key={f} className="mr-1 rounded bg-bg-elevated px-1">{f}</code>
              ))}
            </p>
          ) : null}
          {patchResult.skipped.length > 0 ? (
            <ul className="space-y-0.5">
              {patchResult.skipped.map((s, i) => (
                <li key={i} className="text-fg-subtle">· {s}</li>
              ))}
            </ul>
          ) : null}
          {patchResult.next_steps.length > 0 ? (
            <ul className="mt-1 space-y-0.5 border-t border-border/40 pt-1.5">
              {patchResult.next_steps.map((s, i) => (
                <li key={i} className="text-fg-muted">→ {s}</li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}


function Row({
  label,
  value,
  onChange,
  disabled,
  narrow,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  disabled?: boolean;
  narrow?: boolean;
}) {
  return (
    <label className="mb-1.5 flex items-center gap-2 text-[12px]">
      <span className="w-28 shrink-0 text-fg-muted">{label}</span>
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.currentTarget.value)}
        disabled={disabled}
        className={
          (narrow ? "w-28 " : "flex-1 ") +
          "rounded-md border border-border bg-bg px-2 py-1 font-mono text-[12px] text-fg disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-accent"
        }
      />
    </label>
  );
}
