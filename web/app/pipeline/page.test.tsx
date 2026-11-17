import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { getPipeline, getReport, patchPipeline, type PipelineItem } from "@/lib/pipeline";
import { ApiError } from "@/lib/profile";
import PipelinePage from "./page";

vi.mock("@/lib/pipeline", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/pipeline")>()),
  getPipeline: vi.fn(),
  getReport: vi.fn(),
  patchPipeline: vi.fn(),
}));

const board = vi.mocked(getPipeline);
const report = vi.mocked(getReport);
const patch = vi.mocked(patchPipeline);

const card: PipelineItem = {
  id: 4, job_id: 9, job_title: "Data Engineer", company_name: "Acme", stage: "saved", state: "draft",
  ai_score: null, applied_at: null, follow_up_at: null, notes: null,
};
const emptyReport = {
  found: 10, saved: 1, applied: 0, answered: 0, response_rate: null,
  by_country: [], by_source: [], by_score_band: [],
};

beforeEach(() => {
  board.mockReset();
  report.mockReset();
  patch.mockReset();
  report.mockResolvedValue(emptyReport);
});

describe("pipeline page", () => {
  it("shows the board and moves a card, refreshing the report", async () => {
    board.mockResolvedValue({ items: [card], today: "2026-11-16" });
    patch.mockResolvedValue({ ...card, stage: "applied" });
    render(<PipelinePage />);
    const select = await screen.findByLabelText("Mover Data Engineer para");
    fireEvent.change(select, { target: { value: "applied" } });
    await waitFor(() => expect(patch).toHaveBeenCalledWith(4, { stage: "applied" }));
    await waitFor(() => expect(report).toHaveBeenCalledTimes(2));
    expect(await screen.findByLabelText("Mover Data Engineer para")).toHaveValue("applied");
  });

  it("switches to the report", async () => {
    board.mockResolvedValue({ items: [card], today: "2026-11-16" });
    render(<PipelinePage />);
    fireEvent.click(await screen.findByRole("button", { name: "Relatório" }));
    expect(screen.getByText("Vagas encontradas")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Relatório" })).toHaveAttribute("aria-pressed", "true");
  });

  it("the empty pipeline explains the s shortcut", async () => {
    board.mockResolvedValue({ items: [], today: "2026-11-16" });
    render(<PipelinePage />);
    expect(await screen.findByText(/aperte/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Ver vagas" })).toHaveAttribute("href", "/jobs");
  });

  it("an error offers a retry", async () => {
    board.mockRejectedValueOnce(new ApiError(null, "Não consegui falar com a API."));
    board.mockResolvedValueOnce({ items: [], today: "2026-11-16" });
    render(<PipelinePage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Não consegui falar com a API.");
    fireEvent.click(screen.getByRole("button", { name: "Tentar de novo" }));
    await waitFor(() => expect(screen.getByRole("link", { name: "Ver vagas" })).toBeInTheDocument());
  });
});
