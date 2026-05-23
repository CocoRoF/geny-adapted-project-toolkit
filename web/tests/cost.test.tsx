import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { I18nProvider } from "@/app/providers/I18nProvider";
import { CostModal } from "@/chat/CostModal";
import { deriveCostSnapshot, formatMs } from "@/chat/cost-snapshot";
import { GuardRejectedAlert } from "@/chat/GuardRejectedAlert";
import type { SessionStreamEvent } from "@/chat/useSessionStream";

function ev(
  seq: number,
  kind: SessionStreamEvent["kind"],
  data: Record<string, unknown>,
): SessionStreamEvent {
  return { seq, kind, data, ts: new Date(seq * 1000).toISOString() };
}

describe("deriveCostSnapshot", () => {
  it("returns zeros when no cost event has arrived", () => {
    const snapshot = deriveCostSnapshot([ev(1, "text", { chunk: "hi" })]);
    expect(snapshot).toEqual({
      cost_usd: 0,
      input_tokens: 0,
      output_tokens: 0,
      tool_calls: 0,
      tool_duration_ms: 0,
      by_tool: {},
    });
  });

  it("walks back from the tail to pick the latest cost event", () => {
    const snapshot = deriveCostSnapshot([
      ev(1, "cost", { cost_usd: 0.001, input_tokens: 10, output_tokens: 5 }),
      ev(2, "text", { chunk: "noise" }),
      ev(3, "cost", {
        cost_usd: 0.005,
        input_tokens: 50,
        output_tokens: 25,
        tool_calls: 2,
        tool_duration_ms: 1250,
        by_tool: { gapt_read: 1, gapt_grep: 1 },
      }),
      ev(4, "text", { chunk: "more" }),
    ]);
    expect(snapshot.cost_usd).toBeCloseTo(0.005);
    expect(snapshot.input_tokens).toBe(50);
    expect(snapshot.tool_calls).toBe(2);
    expect(snapshot.by_tool).toEqual({ gapt_read: 1, gapt_grep: 1 });
  });

  it("coerces missing numeric fields to 0", () => {
    const snapshot = deriveCostSnapshot([
      ev(1, "cost", { cost_usd: 0.01 }), // only cost_usd
    ]);
    expect(snapshot.cost_usd).toBeCloseTo(0.01);
    expect(snapshot.input_tokens).toBe(0);
    expect(snapshot.tool_calls).toBe(0);
  });
});

describe("formatMs", () => {
  it("renders sub-second values in ms", () => {
    expect(formatMs(340)).toBe("340 ms");
  });
  it("renders >=1s values in seconds with two decimals", () => {
    expect(formatMs(1234)).toBe("1.23 s");
  });
});

describe("<CostModal />", () => {
  it("renders the snapshot totals and lists tools sorted by count", () => {
    render(
      <I18nProvider>
        <CostModal
          snapshot={{
            cost_usd: 0.1234,
            input_tokens: 1500,
            output_tokens: 800,
            tool_calls: 5,
            tool_duration_ms: 4200,
            by_tool: { gapt_read: 3, gapt_grep: 2 },
          }}
          onClose={() => undefined}
        />
      </I18nProvider>,
    );

    expect(screen.getByTestId("cost-modal-total")).toHaveTextContent("$0.1234");
    const items = screen.getByTestId("cost-modal-tools").querySelectorAll("li");
    expect(items[0]?.textContent).toContain("gapt_read");
    expect(items[1]?.textContent).toContain("gapt_grep");
  });

  it("falls back to a no-tools message when by_tool is empty", () => {
    render(
      <I18nProvider>
        <CostModal
          snapshot={{
            cost_usd: 0,
            input_tokens: 0,
            output_tokens: 0,
            tool_calls: 0,
            tool_duration_ms: 0,
            by_tool: {},
          }}
          onClose={() => undefined}
        />
      </I18nProvider>,
    );
    expect(
      screen.getByText(/No tools have been called yet|아직 호출된 도구가 없습니다/),
    ).toBeInTheDocument();
  });
});

describe("<GuardRejectedAlert />", () => {
  it("renders the friendly exec.stage.guard_rejected message and dismiss button", () => {
    const onDismiss = vi.fn();
    render(
      <I18nProvider>
        <GuardRejectedAlert reason="budget exhausted" onDismiss={onDismiss} />
      </I18nProvider>,
    );

    expect(screen.getByTestId("guard-rejected")).toBeInTheDocument();
    expect(
      screen.getByText(/Budget or policy limit reached|예산 또는 정책 한도/),
    ).toBeInTheDocument();
    expect(screen.getByText("budget exhausted")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Dismiss|닫기/ }));
    expect(onDismiss).toHaveBeenCalled();
  });
});
