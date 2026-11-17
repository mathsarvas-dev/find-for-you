import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { PipelineItem } from "@/lib/pipeline";
import { PipelineCard, followUpText } from "./PipelineCard";

const base: PipelineItem = {
  id: 7, job_id: 3, job_title: "Data Engineer", company_name: "Acme", stage: "applied", state: "applied",
  ai_score: 88, applied_at: "2026-11-10T10:00:00Z", follow_up_at: null, notes: null,
};

function renderCard(item: Partial<PipelineItem> = {}, onPatch = vi.fn(async () => {})) {
  render(
    <ul>
      <PipelineCard item={{ ...base, ...item }} today="2026-11-16" onPatch={onPatch} />
    </ul>,
  );
  return onPatch;
}

describe("PipelineCard", () => {
  it("shows the job, company and score as text and links to the application", () => {
    renderCard();
    expect(screen.getByRole("link", { name: "Data Engineer" })).toHaveAttribute("href", "/candidaturas/7");
    expect(screen.getByText("Acme · Nota 88")).toBeInTheDocument();
  });

  it("moves the card with the labelled select", async () => {
    const onPatch = renderCard();
    fireEvent.change(screen.getByLabelText("Mover Data Engineer para"), { target: { value: "interview" } });
    await waitFor(() => expect(onPatch).toHaveBeenCalledWith(7, { stage: "interview" }));
  });

  it("writes the follow-up state in words", () => {
    expect(followUpText({ ...base, follow_up_at: "2026-11-10" }, "2026-11-16")).toBe(
      "Follow-up atrasado desde 10/11/2026",
    );
    expect(followUpText({ ...base, follow_up_at: "2026-11-16" }, "2026-11-16")).toBe("Follow-up hoje");
    expect(followUpText({ ...base, follow_up_at: "2026-11-20" }, "2026-11-16")).toBe("Follow-up em 20/11/2026");
    expect(followUpText({ ...base, follow_up_at: "2026-11-10", stage: "rejected" }, "2026-11-16")).toMatch(
      /encerrada/,
    );
    expect(followUpText(base, "2026-11-16")).toBeNull();
  });

  it("clears the follow-up date", async () => {
    const onPatch = renderCard({ follow_up_at: "2026-11-20" });
    fireEvent.change(screen.getByLabelText("Data do follow-up"), { target: { value: "" } });
    await waitFor(() => expect(onPatch).toHaveBeenCalledWith(7, { follow_up_at: null }));
  });

  it("saves notes only when changed and confirms", async () => {
    const onPatch = renderCard({ notes: "antiga" });
    const save = screen.getByRole("button", { name: "Salvar anotações" });
    expect(save).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Anotações"), { target: { value: "nova" } });
    fireEvent.click(save);
    await waitFor(() => expect(onPatch).toHaveBeenCalledWith(7, { notes: "nova" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Anotações salvas.");
  });

  it("shows the error when saving fails", async () => {
    renderCard({}, vi.fn(async () => Promise.reject(new Error("Candidatura não encontrada."))));
    fireEvent.change(screen.getByLabelText("Mover Data Engineer para"), { target: { value: "offer" } });
    expect(await screen.findByRole("alert")).toHaveTextContent("Candidatura não encontrada.");
  });
});
