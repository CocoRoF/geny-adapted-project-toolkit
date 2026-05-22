import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import App from "@/app/App";

describe("<App />", () => {
  it("renders the GAPT title", () => {
    render(<App />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
      "GAPT — geny-adapted-project-toolkit",
    );
  });

  it("renders the language switcher with two options", () => {
    render(<App />);
    const select = screen.getByRole("combobox");
    expect(select).toBeInTheDocument();
    const options = screen.getAllByRole("option");
    expect(options).toHaveLength(2);
  });

  it("renders the repository link", () => {
    render(<App />);
    expect(screen.getByRole("link")).toHaveAttribute(
      "href",
      "https://github.com/CocoRoF/geny-adapted-project-toolkit",
    );
  });
});
