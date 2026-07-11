import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { App } from "./App";

describe("App", () => {
  it("keeps authoring and execution in separate surfaces", () => {
    render(<App />);

    expect(screen.getByRole("heading", { name: "Teach and review automations" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Run" }));
    expect(screen.getByRole("heading", { name: "Run an approved automation" })).toBeInTheDocument();
  });
});
