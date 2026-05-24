import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { ThemeSwitcher } from "@/app/ThemeSwitcher";
import { I18nProvider } from "@/app/providers/I18nProvider";
import { ThemeProvider } from "@/app/providers/ThemeProvider";

beforeEach(() => {
  window.localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }));
});

afterEach(() => {
  vi.clearAllMocks();
});

function renderSwitcher() {
  return render(
    <I18nProvider>
      <ThemeProvider>
        <ThemeSwitcher />
      </ThemeProvider>
    </I18nProvider>,
  );
}

describe("<ThemeSwitcher />", () => {
  it("defaults to system mode and resolves to light when matchMedia says so", () => {
    renderSwitcher();
    expect(document.documentElement.dataset["theme"]).toBe("light");
    const systemRadio = screen.getByRole("radio", { name: /System|시스템/ });
    expect(systemRadio).toHaveAttribute("aria-checked", "true");
  });

  it("flips the html data-theme when the user picks dark", () => {
    renderSwitcher();
    fireEvent.click(screen.getByRole("radio", { name: /Dark|다크/ }));
    expect(document.documentElement.dataset["theme"]).toBe("dark");
    expect(window.localStorage.getItem("gapt.theme")).toBe("dark");
  });

  it("persists the choice across remounts", () => {
    renderSwitcher();
    fireEvent.click(screen.getByRole("radio", { name: /Light|라이트/ }));
    expect(window.localStorage.getItem("gapt.theme")).toBe("light");
  });
});
