import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { PipelineItem, Report } from "@/lib/pipeline";
import { PipelineBoard } from "./PipelineBoard";
import { PipelineReport } from "./PipelineReport";

function item(id: number, stage: PipelineItem["stage"], title: string): PipelineItem {
  return {
    id, job_id: id, job_title: title, company_name: "Acme", stage, state: "draft", ai_score: null,
    applied_at: null, follow_up_at: null, notes: null,
  };
}

describe("PipelineBoard", () => {
  it("groups cards by stage with counts and empty columns", () => {
    render(
      <PipelineBoard
        items={[item(1, "saved", "A"), item(2, "saved", "B"), item(3, "offer", "C")]}
        today="2026-11-16"
        onPatch={vi.fn(async () => {})}
      />,
    );
    const saved = screen.getByRole("region", { name: /Salva/ });
    expect(saved).toHaveTextContent("(2)");
    expect(within(saved).getAllByRole("listitem")).toHaveLength(2);
    expect(within(screen.getByRole("region", { name: /Oferta/ })).getByRole("link", { name: "C" })).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: /Recusada/ })).getByText("Nenhuma vaga.")).toBeInTheDocument();
  });
});

describe("PipelineReport", () => {
  const report: Report = {
    found: 120, saved: 2, applied: 4, answered: 1, response_rate: 0.25,
    by_country: [
      { key: "DE", applied: 4, answered: 1, rate: 0.25 },
      { key: "unknown", applied: 1, answered: 0, rate: 0 },
    ],
    by_source: [{ key: "greenhouse", applied: 4, answered: 1, rate: 0.25 }],
    by_score_band: [{ key: "80_100", applied: 3, answered: 1, rate: 1 / 3 }],
  };

  it("shows the totals and the response rate as text", () => {
    render(<PipelineReport report={report} />);
    expect(screen.getByText("Vagas encontradas").nextSibling).toHaveTextContent("120");
    expect(screen.getByText("Taxa de resposta", { selector: "dt" }).nextSibling).toHaveTextContent("25%");
  });

  it("builds a table per group with readable labels", () => {
    render(<PipelineReport report={report} />);
    expect(screen.getByRole("heading", { name: "Por país" })).toBeInTheDocument();
    expect(screen.getByRole("rowheader", { name: "Desconhecido" })).toBeInTheDocument();
    expect(screen.getByRole("rowheader", { name: "Nota 80 a 100" })).toBeInTheDocument();
    expect(screen.getByRole("rowheader", { name: "greenhouse" })).toBeInTheDocument();
  });

  it("shows a dash when nothing was applied", () => {
    render(
      <PipelineReport
        report={{ ...report, applied: 0, answered: 0, response_rate: null, by_country: [], by_source: [], by_score_band: [] }}
      />,
    );
    expect(screen.getByText("Taxa de resposta", { selector: "dt" }).nextSibling).toHaveTextContent("—");
    expect(screen.getAllByText("Sem candidaturas aplicadas ainda.")).toHaveLength(3);
  });
});
