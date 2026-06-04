/**
 * Phase N.2.6 — "+ 새 프로젝트 → 새로 만들기" wizard.
 *
 * Four-step flow:
 *   1. Identity (display name / slug / repo name / visibility)
 *   2. Preset card grid (5 cards)
 *   3. Preset options (dynamic form from option_schema)
 *   4. Confirm + Create
 *
 * The "Import" sibling lives in `ImportProjectModal.tsx` and is reached
 * via the dropdown's "불러오기" item.
 */

import { useEffect, useMemo, useState } from "react";
import {
  Boxes,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  FileText,
  Layers,
  Loader2,
  Monitor,
  Server,
  Square,
} from "lucide-react";

import { ApiError } from "@/api/client";
import type { ProjectResponse } from "@/api/projects";
import {
  type ScaffoldOption,
  type ScaffoldPreset,
  type ScaffoldRequestPayload,
  createProjectFromScaffold,
  listScaffolds,
} from "@/api/scaffolds";
import { Button } from "@/ui/Button";
import { Field, Input, Select } from "@/ui/Input";
import { Modal } from "@/ui/Modal";
import { cn } from "@/ui/cn";

interface Props {
  open: boolean;
  onClose: () => void;
  onCreated: (project: ProjectResponse) => void;
}

const SLUG_PATTERN = /^[a-z0-9](?:[a-z0-9-]{0,118}[a-z0-9])?$/;
const REPO_NAME_PATTERN = /^[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}$/;

type Step = 0 | 1 | 2 | 3;

function iconFor(name: string) {
  switch (name) {
    case "layers":
      return Layers;
    case "server":
      return Server;
    case "monitor":
      return Monitor;
    case "file-text":
      return FileText;
    case "square":
      return Square;
    default:
      return Boxes;
  }
}

function slugify(input: string): string {
  return input
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9-]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .replace(/-{2,}/g, "-")
    .slice(0, 120);
}

export function NewProjectScaffoldModal({ open, onClose, onCreated }: Props) {
  const [step, setStep] = useState<Step>(0);
  const [presets, setPresets] = useState<ScaffoldPreset[] | null>(null);
  const [presetsError, setPresetsError] = useState<string | null>(null);

  // Step 1 — identity
  const [displayName, setDisplayName] = useState("");
  const [slug, setSlug] = useState("");
  const [slugTouched, setSlugTouched] = useState(false);
  const [repoName, setRepoName] = useState("");
  const [repoNameTouched, setRepoNameTouched] = useState(false);
  const [visibility, setVisibility] = useState<"private" | "public">("private");

  // Step 2 — preset
  const [selectedPresetId, setSelectedPresetId] = useState<string | null>(null);

  // Step 3 — options
  const [options, setOptions] = useState<Record<string, unknown>>({});

  // Step 4 — submit
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Load presets on open.
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setPresetsError(null);
    void listScaffolds()
      .then((res) => {
        if (cancelled) return;
        setPresets(res.presets);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setPresetsError(
          err instanceof ApiError
            ? `${err.code}: ${err.reason}`
            : err instanceof Error
              ? err.message
              : String(err),
        );
      });
    return () => {
      cancelled = true;
    };
  }, [open]);

  // Reset state when modal closes.
  useEffect(() => {
    if (open) return;
    setStep(0);
    setDisplayName("");
    setSlug("");
    setSlugTouched(false);
    setRepoName("");
    setRepoNameTouched(false);
    setVisibility("private");
    setSelectedPresetId(null);
    setOptions({});
    setError(null);
    setSubmitting(false);
  }, [open]);

  // Auto-derive slug + repo_name from display name until the user
  // touches them manually.
  useEffect(() => {
    const derived = slugify(displayName);
    if (!slugTouched) setSlug(derived);
    if (!repoNameTouched) setRepoName(derived);
  }, [displayName, slugTouched, repoNameTouched]);

  const selectedPreset = useMemo(
    () => presets?.find((p) => p.id === selectedPresetId) ?? null,
    [presets, selectedPresetId],
  );

  // When the operator changes the preset, hydrate options with each
  // schema field's default value.
  useEffect(() => {
    if (!selectedPreset) return;
    const next: Record<string, unknown> = {};
    for (const opt of selectedPreset.option_schema) {
      next[opt.id] = opt.default;
    }
    setOptions(next);
  }, [selectedPreset]);

  const slugValid = slug.length > 0 && SLUG_PATTERN.test(slug);
  const repoNameValid =
    repoName.length > 0 && REPO_NAME_PATTERN.test(repoName);
  const step0Valid =
    displayName.trim().length > 0 && slugValid && repoNameValid;
  const step1Valid = selectedPresetId !== null;
  const optionsHaveErrors = false; // dynamic-form validation handled by inputs

  // Skip Step 3 entirely when the preset has no options.
  const optionsStepNeeded =
    (selectedPreset?.option_schema.length ?? 0) > 0;

  function nextStep(): void {
    if (step === 0 && !step0Valid) return;
    if (step === 1 && !step1Valid) return;
    if (step === 1 && !optionsStepNeeded) {
      setStep(3);
      return;
    }
    if (step === 2 && optionsHaveErrors) return;
    setStep((step + 1) as Step);
  }

  function prevStep(): void {
    if (step === 3 && !optionsStepNeeded) {
      setStep(1);
      return;
    }
    setStep(((step - 1) as Step) >= 0 ? ((step - 1) as Step) : 0);
  }

  function submit(): void {
    if (!selectedPreset) return;
    setError(null);
    setSubmitting(true);
    const payload: ScaffoldRequestPayload = {
      slug,
      display_name: displayName,
      repo_name: repoName,
      repo_visibility: visibility,
      preset_id: selectedPreset.id,
      preset_options: options,
    };
    void createProjectFromScaffold(payload)
      .then((res) => onCreated(res.project))
      .catch((err: unknown) => {
        if (err instanceof ApiError) {
          setError(`${err.code}: ${err.reason}`);
        } else {
          setError(err instanceof Error ? err.message : String(err));
        }
      })
      .finally(() => setSubmitting(false));
  }

  const titles = ["식별 정보", "프리셋 선택", "옵션", "확인 + 생성"];
  const title = `새 프로젝트 만들기 — ${titles[step]}`;

  return (
    <Modal
      open={open}
      onClose={() => {
        if (!submitting) onClose();
      }}
      title={title}
      size="lg"
      footer={
        <div className="flex w-full items-center justify-between gap-2">
          <Button variant="ghost" onClick={onClose} disabled={submitting}>
            취소
          </Button>
          <div className="flex items-center gap-2">
            {step > 0 ? (
              <Button variant="outline" onClick={prevStep} disabled={submitting}>
                <ChevronLeft className="h-3.5 w-3.5" />
                이전
              </Button>
            ) : null}
            {step < 3 ? (
              <Button
                variant="primary"
                onClick={nextStep}
                disabled={
                  (step === 0 && !step0Valid) ||
                  (step === 1 && !step1Valid) ||
                  (step === 2 && optionsHaveErrors)
                }
              >
                다음
                <ChevronRight className="h-3.5 w-3.5" />
              </Button>
            ) : (
              <Button
                variant="primary"
                onClick={submit}
                disabled={submitting || !step1Valid}
              >
                {submitting ? (
                  <>
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    만드는 중…
                  </>
                ) : (
                  <>
                    <CheckCircle2 className="h-3.5 w-3.5" />
                    만들기
                  </>
                )}
              </Button>
            )}
          </div>
        </div>
      }
    >
      {/* Step indicator */}
      <ol className="mb-4 flex items-center gap-2 text-[11px] text-fg-subtle">
        {titles.map((label, idx) => (
          <li key={label} className="flex items-center gap-1.5">
            <span
              className={cn(
                "grid h-5 w-5 place-items-center rounded-full border text-[10px] font-semibold",
                idx === step
                  ? "border-accent bg-accent text-bg"
                  : idx < step
                    ? "border-accent text-accent"
                    : "border-border text-fg-subtle",
              )}
            >
              {idx + 1}
            </span>
            <span className={idx === step ? "text-fg font-medium" : ""}>
              {label}
            </span>
            {idx < titles.length - 1 ? (
              <ChevronRight className="h-3 w-3 text-fg-subtle" />
            ) : null}
          </li>
        ))}
      </ol>

      {presetsError ? (
        <p
          role="alert"
          className="mb-4 rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-[12px] text-danger"
        >
          프리셋 로드 실패: {presetsError}
        </p>
      ) : null}

      {step === 0 ? (
        <div className="flex flex-col gap-3.5">
          <Field label="표시 이름" hint="한글 OK. 카드 + 프로젝트 페이지 상단에 표시됩니다.">
            <Input
              type="text"
              value={displayName}
              onChange={(e) => setDisplayName(e.currentTarget.value)}
              maxLength={200}
              placeholder="My Project"
            />
          </Field>
          <Field
            label="슬러그 (GAPT 내부 식별자)"
            hint="소문자, 숫자, 하이픈만. 표시 이름에서 자동 추출됩니다."
            error={
              slug.length > 0 && !slugValid
                ? "소문자/숫자/하이픈만 사용 가능"
                : null
            }
          >
            <Input
              type="text"
              value={slug}
              onChange={(e) => {
                setSlug(e.currentTarget.value);
                setSlugTouched(true);
              }}
              maxLength={120}
              aria-invalid={slug.length > 0 && !slugValid}
              placeholder="my-project"
            />
          </Field>
          <Field
            label="GitHub 레포 이름"
            hint="GAPT 가 이 이름으로 새 레포를 만듭니다. 슬러그와 달라도 됩니다."
            error={
              repoName.length > 0 && !repoNameValid
                ? "영숫자 + - _ . 만 (시작은 영숫자/_)"
                : null
            }
          >
            <Input
              type="text"
              value={repoName}
              onChange={(e) => {
                setRepoName(e.currentTarget.value);
                setRepoNameTouched(true);
              }}
              maxLength={100}
              aria-invalid={repoName.length > 0 && !repoNameValid}
              placeholder="my-project"
            />
          </Field>
          <Field label="공개 범위">
            <Select
              value={visibility}
              onChange={(e) =>
                setVisibility(e.currentTarget.value as "private" | "public")
              }
            >
              <option value="private">private (권장)</option>
              <option value="public">public</option>
            </Select>
          </Field>
        </div>
      ) : null}

      {step === 1 ? (
        <div>
          {!presets ? (
            <div className="flex items-center gap-2 text-[12px] text-fg-muted">
              <Loader2 className="h-3 w-3 animate-spin" /> 프리셋 불러오는 중…
            </div>
          ) : (
            <div className="grid grid-cols-2 gap-2">
              {presets.map((preset) => {
                const Icon = iconFor(preset.icon);
                const isSelected = preset.id === selectedPresetId;
                return (
                  <button
                    type="button"
                    key={preset.id}
                    onClick={() => setSelectedPresetId(preset.id)}
                    className={cn(
                      "flex flex-col items-start gap-1.5 rounded-md border p-3 text-left transition-colors",
                      isSelected
                        ? "border-accent bg-accent/5 ring-2 ring-accent/30"
                        : "border-border bg-bg hover:border-accent/40 hover:bg-bg-subtle",
                    )}
                  >
                    <div className="flex items-center gap-2">
                      <Icon className="h-4 w-4 text-accent" />
                      <span className="font-medium text-[13px] text-fg">
                        {preset.display_name}
                      </span>
                    </div>
                    <p className="text-[11.5px] text-fg-muted">
                      {preset.description}
                    </p>
                    <div className="mt-1 flex flex-wrap gap-1">
                      {preset.stack.map((s) => (
                        <span
                          key={s}
                          className="rounded border border-border bg-bg-subtle px-1.5 py-0.5 font-mono text-[10px] text-fg-muted"
                        >
                          {s}
                        </span>
                      ))}
                    </div>
                  </button>
                );
              })}
            </div>
          )}
        </div>
      ) : null}

      {step === 2 && selectedPreset ? (
        <div className="flex flex-col gap-3.5">
          <p className="text-[12px] text-fg-muted">
            {selectedPreset.display_name} — 옵션을 조정합니다. 기본값으로 두면
            바로 다음 단계로.
          </p>
          {selectedPreset.option_schema.map((opt) => (
            <OptionField
              key={opt.id}
              option={opt}
              value={options[opt.id]}
              onChange={(v) =>
                setOptions((prev) => ({ ...prev, [opt.id]: v }))
              }
            />
          ))}
        </div>
      ) : null}

      {step === 3 && selectedPreset ? (
        <div className="flex flex-col gap-3.5">
          <p className="text-[12px] text-fg-muted">
            아래 내용으로 GitHub 레포를 만들고 스캐폴드를 푸시한 뒤 GAPT 프로젝트로
            등록합니다.
          </p>
          <SummaryRow label="표시 이름" value={displayName} />
          <SummaryRow label="GAPT 슬러그" value={slug} />
          <SummaryRow
            label="GitHub 레포"
            value={`${visibility === "private" ? "🔒" : "🌐"} ${repoName}`}
          />
          <SummaryRow label="프리셋" value={selectedPreset.display_name} />
          {Object.keys(options).length > 0 ? (
            <SummaryRow
              label="옵션"
              value={Object.entries(options)
                .map(([k, v]) => `${k}=${String(v)}`)
                .join(", ")}
            />
          ) : null}

          {error ? (
            <p
              role="alert"
              className="rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-[12px] text-danger"
            >
              {error}
              {error.includes("token") ? (
                <span className="ml-2 text-[11px]">
                  →{" "}
                  <a
                    href="/_gapt/settings"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="underline"
                  >
                    Settings → Credentials
                  </a>{" "}
                  에서 GitHub PAT 등록 후 다시 시도.
                </span>
              ) : null}
            </p>
          ) : null}
        </div>
      ) : null}
    </Modal>
  );
}

function OptionField({
  option,
  value,
  onChange,
}: {
  option: ScaffoldOption;
  value: unknown;
  onChange: (v: unknown) => void;
}) {
  if (option.type === "integer") {
    return (
      <Field label={option.label} hint={option.description}>
        <Input
          type="number"
          value={String(value ?? option.default ?? "")}
          onChange={(e) => onChange(Number.parseInt(e.currentTarget.value, 10))}
          min={option.min}
          max={option.max}
        />
      </Field>
    );
  }
  if (option.type === "string") {
    return (
      <Field label={option.label} hint={option.description}>
        <Input
          type="text"
          value={String(value ?? option.default ?? "")}
          onChange={(e) => onChange(e.currentTarget.value)}
        />
      </Field>
    );
  }
  if (option.type === "enum") {
    return (
      <Field label={option.label} hint={option.description}>
        <Select
          value={String(value ?? option.default ?? "")}
          onChange={(e) => onChange(e.currentTarget.value)}
        >
          {(option.choices ?? []).map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </Select>
      </Field>
    );
  }
  // boolean
  return (
    <Field label={option.label} hint={option.description}>
      <label className="flex h-8 items-center gap-2 rounded-md border border-border bg-surface px-2.5 text-[13px]">
        <input
          type="checkbox"
          checked={Boolean(value ?? option.default)}
          onChange={(e) => onChange(e.currentTarget.checked)}
        />
        {Boolean(value ?? option.default) ? "사용" : "미사용"}
      </label>
    </Field>
  );
}

function SummaryRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline gap-2 border-b border-border pb-2 text-[12.5px]">
      <span className="w-28 shrink-0 text-fg-subtle">{label}</span>
      <span className="font-mono text-fg">{value}</span>
    </div>
  );
}
