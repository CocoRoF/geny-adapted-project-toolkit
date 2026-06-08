import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { I18nProvider } from "@/app/providers/I18nProvider";
import { PreviewTabContent } from "@/ide/PreviewTabContent";

function renderTab(initialUrl = "") {
  return render(
    <I18nProvider>
      <PreviewTabContent initialUrl={initialUrl} />
    </I18nProvider>,
  );
}

describe("<PreviewTabContent />", () => {
  it("renders an empty-state when initialUrl is blank", () => {
    renderTab("");
    expect(screen.getByText(/Set a preview URL|URL 을 설정/)).toBeInTheDocument();
    expect(screen.queryByTestId("preview-iframe")).not.toBeInTheDocument();
  });

  it("renders an iframe at the initialUrl", () => {
    renderTab("http://localhost:3000");
    const frame = screen.getByTestId<HTMLIFrameElement>("preview-iframe");
    expect(frame.src).toContain("http://localhost:3000");
  });

  it("navigates to a different URL when the user edits the address bar", () => {
    renderTab("http://localhost:3000");
    const input = screen.getByLabelText(/URL/) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "http://localhost:5173" } });
    fireEvent.blur(input);
    const frame = screen.getByTestId<HTMLIFrameElement>("preview-iframe");
    expect(frame.src).toContain("http://localhost:5173");
  });

  it("switches the iframe width when a device chip is clicked", () => {
    renderTab("http://localhost:3000");
    fireEvent.click(screen.getByRole("radio", { name: /Phone|폰/ }));
    const frame = screen.getByTestId<HTMLIFrameElement>("preview-iframe");
    expect(frame.style.width).toBe("390px");
  });
});
