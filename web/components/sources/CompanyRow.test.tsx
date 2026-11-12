import { afterEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { ApiError } from "@/lib/profile";
import { collectNow, patchCompany, type Company } from "@/lib/sources";
import { CompanyRow } from "./CompanyRow";

vi.mock("@/lib/sources", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/sources")>()),
  collectNow: vi.fn(),
  patchCompany: vi.fn(),
}));

const collect = vi.mocked(collectNow);
const patch = vi.mocked(patchCompany);

afterEach(() => {
  collect.mockReset();
  patch.mockReset();
});

const NOW = Date.parse("2026-11-12T12:00:00Z");

const healthy: Company = {
  id: 1,
  source: "greenhouse",
  identifier: "gitlab",
  name: "GitLab",
  country: "US",
  active: true,
  last_run_at: "2026-11-12T11:55:00Z",
  last_success_at: "2026-11-12T11:55:00Z",
  next_run_at: "2026-11-12T17:55:00Z",
  last_error: null,
  consecutive_failures: 0,
  open_jobs: 42,
};

function renderRow(company: Company, onChange = vi.fn(), now = NOW) {
  render(
    <ul>
      <CompanyRow company={company} now={now} onChange={onChange} />
    </ul>,
  );
  return onChange;
}

describe("CompanyRow", () => {
  it("mostra nome, token, país, estado, vagas e última coleta", () => {
    renderRow(healthy);
    expect(screen.getByText("GitLab")).toBeInTheDocument();
    expect(screen.getByText("gitlab")).toHaveClass("mono");
    expect(screen.getByText("US")).toBeInTheDocument();
    expect(screen.getByText("ativa")).toBeInTheDocument();
    expect(screen.getByText("42 vagas abertas")).toBeInTheDocument();
    expect(screen.getByText("última coleta: há 5 min")).toBeInTheDocument();
    expect(screen.queryByText(/Falha na coleta/)).toBeNull();
  });

  it("1 vaga no singular; nunca coletada e pendente", () => {
    renderRow({ ...healthy, open_jobs: 1, last_run_at: null, next_run_at: "2026-11-12T11:59:00Z" });
    expect(screen.getByText("1 vaga aberta")).toBeInTheDocument();
    expect(screen.getByText(/ainda não coletada/)).toBeInTheDocument();
    expect(screen.getByText(/coleta pendente/)).toBeInTheDocument();
  });

  it("antes de montar o relógio (now = 0) não mostra tempo relativo", () => {
    renderRow(healthy, vi.fn(), 0);
    expect(screen.queryByText(/há 5 min/)).toBeNull();
  });

  it("erro legível com o número de falhas seguidas", () => {
    renderRow({
      ...healthy,
      last_error: "Quadro não encontrado no Greenhouse.",
      consecutive_failures: 3,
    });
    expect(
      screen.getByText("Falha na coleta (3 seguidas): Quadro não encontrado no Greenhouse."),
    ).toBeInTheDocument();
  });

  it("pausada: estado em texto e coleta desabilitada", () => {
    renderRow({ ...healthy, active: false });
    expect(screen.getByText("pausada")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Coletar agora — GitLab" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Ativar — GitLab" })).toBeEnabled();
  });

  it("Coletar agora chama a API uma vez e repassa a empresa atualizada", async () => {
    let resolve!: (c: Company) => void;
    collect.mockReturnValue(new Promise((r) => (resolve = r)));
    const onChange = renderRow(healthy);
    const button = screen.getByRole("button", { name: "Coletar agora — GitLab" });
    fireEvent.click(button);
    fireEvent.click(button);
    expect(collect).toHaveBeenCalledTimes(1);
    expect(collect).toHaveBeenCalledWith(1);
    expect(button).toBeDisabled();
    const updated = { ...healthy, next_run_at: "2026-11-12T12:00:00Z" };
    await act(async () => resolve(updated));
    expect(onChange).toHaveBeenCalledWith(updated);
    expect(button).toBeEnabled();
  });

  it("Coletar agora com 409 mostra a mensagem do servidor", async () => {
    collect.mockRejectedValue(new ApiError(409, "Empresa inativa."));
    const onChange = renderRow(healthy);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Coletar agora — GitLab" }));
    });
    expect(screen.getByRole("alert")).toHaveTextContent("Empresa inativa.");
    expect(onChange).not.toHaveBeenCalled();
  });

  it("Pausar alterna para inativa via PATCH", async () => {
    const paused = { ...healthy, active: false };
    patch.mockResolvedValue(paused);
    const onChange = renderRow(healthy);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Pausar — GitLab" }));
    });
    expect(patch).toHaveBeenCalledWith(1, { active: false });
    expect(onChange).toHaveBeenCalledWith(paused);
  });

  it("Ativar alterna para ativa via PATCH", async () => {
    patch.mockResolvedValue(healthy);
    const onChange = renderRow({ ...healthy, active: false });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Ativar — GitLab" }));
    });
    expect(patch).toHaveBeenCalledWith(1, { active: true });
    expect(onChange).toHaveBeenCalledWith(healthy);
  });
});
