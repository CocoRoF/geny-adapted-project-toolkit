import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { CommandPalette } from "@/app/CommandPalette";
import { I18nProvider } from "@/app/providers/I18nProvider";
import { PaletteProvider } from "@/app/providers/PaletteProvider";
import { usePalette } from "@/app/providers/palette-context";
import { usePaletteAction } from "@/app/usePaletteAction";

function OpenPaletteButton() {
  const palette = usePalette();
  return (
    <button type="button" onClick={() => palette.open()}>
      open
    </button>
  );
}

function Harness({ run }: { run: () => void }) {
  usePaletteAction({
    id: "test.greet",
    title: "Say hello",
    section: "Tests",
    keywords: ["greeting"],
    run,
  });
  return null;
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.clearAllMocks();
});

function renderPalette(run: () => void) {
  return render(
    <I18nProvider>
      <PaletteProvider>
        <Harness run={run} />
        <OpenPaletteButton />
        <CommandPalette />
      </PaletteProvider>
    </I18nProvider>,
  );
}

describe("<CommandPalette />", () => {
  it("opens via Cmd/Ctrl+K and lists registered actions", async () => {
    renderPalette(() => undefined);

    fireEvent.keyDown(window, { key: "k", ctrlKey: true });

    await waitFor(() => {
      expect(screen.getByTestId("command-palette")).toBeInTheDocument();
    });
    expect(screen.getByText("Say hello")).toBeInTheDocument();
  });

  it("runs the action and closes when an item is selected", async () => {
    const run = vi.fn();
    renderPalette(run);

    fireEvent.click(screen.getByRole("button", { name: "open" }));
    await waitFor(() => expect(screen.getByTestId("command-palette")).toBeInTheDocument());

    fireEvent.click(screen.getByText("Say hello"));

    expect(run).toHaveBeenCalledTimes(1);
    await waitFor(() => {
      expect(screen.queryByTestId("command-palette")).not.toBeInTheDocument();
    });
  });

  it("Esc closes the palette", async () => {
    renderPalette(() => undefined);

    fireEvent.click(screen.getByRole("button", { name: "open" }));
    await waitFor(() => expect(screen.getByTestId("command-palette")).toBeInTheDocument());

    fireEvent.keyDown(window, { key: "Escape" });

    await waitFor(() => {
      expect(screen.queryByTestId("command-palette")).not.toBeInTheDocument();
    });
  });
});
