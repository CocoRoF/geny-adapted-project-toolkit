import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { I18nProvider } from "@/app/providers/I18nProvider";
import { PreviewPanel } from "@/ide/PreviewPanel";

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  window.localStorage.clear();
});

function renderPanel() {
  return render(
    <I18nProvider>
      <PreviewPanel workspaceId="ws1" />
    </I18nProvider>,
  );
}

describe("<PreviewPanel />", () => {
  it("renders an empty-state until the URL is set", () => {
    renderPanel();
    expect(screen.getByText(/Set a preview URL|URL 을 설정/)).toBeInTheDocument();
    expect(screen.queryByTestId("preview-iframe")).not.toBeInTheDocument();
  });

  it("renders an iframe pointed at the URL once provided", () => {
    renderPanel();
    fireEvent.change(screen.getByLabelText(/URL/), {
      target: { value: "http://localhost:3000" },
    });
    const frame = screen.getByTestId<HTMLIFrameElement>("preview-iframe");
    expect(frame.src).toContain("http://localhost:3000");
  });

  it("persists the URL per workspace in LocalStorage", () => {
    renderPanel();
    fireEvent.change(screen.getByLabelText(/URL/), {
      target: { value: "http://localhost:5173" },
    });
    expect(window.localStorage.getItem("gapt.ide.preview.ws1")).toBe("http://localhost:5173");
  });

  it("switches the iframe width when a device chip is clicked", () => {
    renderPanel();
    fireEvent.change(screen.getByLabelText(/URL/), {
      target: { value: "http://localhost:3000" },
    });
    fireEvent.click(screen.getByRole("radio", { name: /Phone|폰/ }));
    const frame = screen.getByTestId<HTMLIFrameElement>("preview-iframe");
    expect(frame.style.width).toBe("390px");
  });
});
