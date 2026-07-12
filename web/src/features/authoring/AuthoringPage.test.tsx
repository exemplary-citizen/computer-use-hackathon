import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AuthoringPage } from "./AuthoringPage";

const manifest = {
  id: "6c58a06a-1136-41f3-93f8-7ca258fa7b12",
  slug: "update-crm-lead",
  name: "Update CRM lead",
  status: "review_required",
  sources: [],
  current_version: 1,
  approved_version: null,
  created_at: "2026-07-11T12:00:00Z",
  updated_at: "2026-07-11T12:00:00Z",
};

afterEach(() => vi.restoreAllMocks());

describe("AuthoringPage", () => {
  it("shows generated artifacts, validation failures, and source conflicts", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      const body = url.endsWith("/automations")
        ? [manifest]
        : {
            manifest,
            version: {
              version: 1,
              validation_passed: false,
              approval: null,
              tools: [],
              conflicts: [
                {
                  id: "a112d327-88cc-4d89-a5f4-c5db61603188",
                  severity: "blocking",
                  description: "The video and SOP disagree on the owner field.",
                  resolution: null,
                },
              ],
            },
            artifacts: { "SOP.md": "# Update lead" },
            validation: {
              valid: false,
              errors: [
                {
                  code: "blocking_conflict",
                  message: "Resolve every material source conflict before approval",
                  artifact: "review.json",
                },
              ],
              warnings: [],
            },
            failure: null,
            progress: null,
          };
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });

    render(<AuthoringPage />);
    fireEvent.click(await screen.findByRole("button", { name: /Update CRM lead/ }));

    expect(await screen.findByRole("heading", { name: "Source conflicts" })).toBeInTheDocument();
    expect(screen.getByText("The video and SOP disagree on the owner field.")).toBeInTheDocument();
    expect(screen.getByLabelText("Edit SOP.md")).toHaveValue("# Update lead");
    expect(screen.getByRole("button", { name: "Approve version" })).toBeDisabled();
  });

  it("requires the provider disclosure in the creation form", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("[]", { status: 200, headers: { "Content-Type": "application/json" } }),
    );
    render(<AuthoringPage />);
    await waitFor(() => expect(screen.getByText("No automations yet")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "New automation" }));

    expect(screen.getByRole("checkbox")).toBeRequired();
    expect(screen.getByText(/uploaded video and normalized evidence\/SOP text/)).toBeInTheDocument();
  });
});
