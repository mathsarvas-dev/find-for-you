import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ApplicationDetail } from "@/lib/applications";
import { applyApplication, unapplyApplication } from "@/lib/applications";
import { KitStep } from "./KitStep";

const overviewState = vi.hoisted(() => ({ provisional: false }));

vi.mock("@/components/shell/OverviewContext", () => ({
  useRefreshOverview: () => () => {},
  useOverview: () => ({
    overview: { profile: { provisional: overviewState.provisional } },
    status: "ok",
    refresh: async () => {},
  }),
}));

vi.mock("@/lib/applications", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/applications")>()),
  applyApplication: vi.fn(),
  unapplyApplication: vi.fn(),
  downloadArtifact: vi.fn(),
}));

const applyMock = vi.mocked(applyApplication);
const unapplyMock = vi.mocked(unapplyApplication);

function detail(over: Partial<ApplicationDetail> = {}): ApplicationDetail {
  return {
    id: 7, state: "ready", error: null, locale: "pt", template: "us", referees: null,
    profile_version: 1, current_profile_version: 1, warnings: [],
    job: { id: 9, title: "Data Engineer", company_name: "Globex", url: "https://jobs.example.test/1", status: "open" },
    requirements: { must: [], nice: [], keywords: [] }, coverage: null,
    resume: { version: 1, status: "valid", lines: [] }, cover_letter: null,
    questions: [
      {
        id: 1, position: 1, source: "pasted", label: "Por que nós?", kind: "text", options: null,
        required: false, sensitive: false, answer: "Porque gosto do time.", answer_state: "final",
        bank_key: null, bank_suggestion: null, task: null,
      },
    ],
    tasks: [], can_generate: { allowed: true, reason: null }, applied_at: null,
    ...over,
  };
}

beforeEach(() => {
  overviewState.provisional = false;
  applyMock.mockReset();
  unapplyMock.mockReset();
});

describe("KitStep", () => {
  it("'Apliquei' marks the application and offers to undo it", async () => {
    applyMock.mockResolvedValue({
      id: 7, job_id: 9, state: "applied", error: null, locale: "pt", template: "us",
      referees: null, profile_version: 1, applied_at: "2026-11-16T12:00:00Z",
    });
    const onChanged = vi.fn();
    render(<KitStep detail={detail()} onChanged={onChanged} />);
    fireEvent.click(screen.getByRole("button", { name: "Apliquei" }));
    expect(await screen.findByRole("button", { name: "Desfazer" })).toBeInTheDocument();
    expect(applyMock).toHaveBeenCalledWith(7);
    expect(onChanged).toHaveBeenCalled();
  });

  it("a provisional profile cannot be marked as applied, and the reason is shown", () => {
    overviewState.provisional = true;
    render(<KitStep detail={detail()} onChanged={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Apliquei" })).toBeDisabled();
    expect(screen.getByText(/texto colado, sem extração/)).toBeInTheDocument();
  });

  it("a javascript: posting link never becomes an anchor", () => {
    const { container } = render(
      <KitStep
        detail={detail({ job: { id: 9, title: "x", company_name: "y", url: "javascript:alert(1)", status: "open" } })}
        onChanged={vi.fn()}
      />,
    );
    expect(screen.getByText("Link indisponível")).toBeInTheDocument();
    expect(container.querySelector('a[href^="javascript"]')).toBeNull();
  });

  it("shows the final answers read-only and says nothing is sent", () => {
    render(<KitStep detail={detail()} onChanged={vi.fn()} />);
    expect(screen.getByRole("textbox", { name: "Resposta: Por que nós?" })).toHaveValue("Porque gosto do time.");
    expect(screen.getByText(/o Rumo não envia nada/)).toBeInTheDocument();
  });

  it("'Desfazer' calls unapply and returns to ready", async () => {
    unapplyMock.mockResolvedValue({
      id: 7, job_id: 9, state: "ready", error: null, locale: "pt", template: "us",
      referees: null, profile_version: 1, applied_at: null,
    });
    render(<KitStep detail={detail({ state: "applied", applied_at: "2026-11-16T12:00:00Z" })} onChanged={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Desfazer" }));
    await waitFor(() => expect(unapplyMock).toHaveBeenCalledWith(7));
    expect(await screen.findByRole("button", { name: "Apliquei" })).toBeInTheDocument();
  });
});
