import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { listApplications } from "@/lib/applications";
import { ApiError } from "@/lib/profile";
import CandidaturasPage from "./page";

vi.mock("@/lib/applications", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/applications")>()),
  listApplications: vi.fn(),
}));

const listMock = vi.mocked(listApplications);

beforeEach(() => {
  listMock.mockReset();
});

describe("candidaturas list", () => {
  it("lists applications with their state and coverage", async () => {
    listMock.mockResolvedValue({
      items: [{
        id: 3, job_id: 9, job_title: "Data Engineer", company_name: "Globex", state: "ready",
        updated_at: "2026-11-16T12:00:00Z", applied_at: null,
        coverage: { must: { covered: 2, total: 3 }, nice: { covered: 0, total: 1 } },
      }],
    });
    render(<CandidaturasPage />);
    expect(await screen.findByRole("link", { name: "Data Engineer · Globex" })).toHaveAttribute("href", "/candidaturas/3");
    expect(screen.getByText(/Pronta para revisar · 2 de 3 obrigatórios/)).toBeInTheDocument();
  });

  it("the empty list sends to the jobs", async () => {
    listMock.mockResolvedValue({ items: [] });
    render(<CandidaturasPage />);
    expect(await screen.findByRole("link", { name: "Ver vagas" })).toHaveAttribute("href", "/jobs");
  });

  it("an error offers a retry", async () => {
    listMock.mockRejectedValueOnce(new ApiError(null, "Não consegui falar com a API."));
    listMock.mockResolvedValueOnce({ items: [] });
    render(<CandidaturasPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Não consegui falar com a API.");
    fireEvent.click(screen.getByRole("button", { name: "Tentar de novo" }));
    await waitFor(() => expect(screen.getByRole("link", { name: "Ver vagas" })).toBeInTheDocument());
  });
});
