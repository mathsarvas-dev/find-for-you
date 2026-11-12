import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { ApiError } from "@/lib/profile";
import { addCompany, listCompanies, listJobs, type Company } from "@/lib/sources";
import SourcesPage from "./page";

vi.mock("@/lib/sources", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/sources")>()),
  listCompanies: vi.fn(),
  listJobs: vi.fn(),
  addCompany: vi.fn(),
  collectNow: vi.fn(),
  patchCompany: vi.fn(),
}));

const m = {
  companies: vi.mocked(listCompanies),
  jobs: vi.mocked(listJobs),
  add: vi.mocked(addCompany),
};

const NOW = Date.parse("2026-11-12T12:00:00Z");

const collected: Company = {
  id: 1,
  source: "greenhouse",
  identifier: "gitlab",
  name: "GitLab",
  country: null,
  active: true,
  last_run_at: "2026-11-12T11:55:00Z",
  last_success_at: "2026-11-12T11:55:00Z",
  next_run_at: "2026-11-12T17:55:00Z",
  last_error: null,
  consecutive_failures: 0,
  open_jobs: 3,
};

const pending: Company = {
  ...collected,
  id: 2,
  identifier: "nubank",
  name: "Nubank",
  last_run_at: null,
  last_success_at: null,
  next_run_at: "2026-11-12T11:59:00Z",
  open_jobs: 0,
};

async function tick(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

async function renderPage() {
  const view = render(<SourcesPage />);
  await tick(0);
  return view;
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(NOW);
  m.jobs.mockResolvedValue({ total: 0, items: [] });
});

afterEach(() => {
  vi.useRealTimers();
  Object.values(m).forEach((fn) => fn.mockReset());
});

describe("SourcesPage", () => {
  it("carregando: esqueleto enquanto busca as empresas", () => {
    m.companies.mockReturnValue(new Promise(() => {}));
    render(<SourcesPage />);
    expect(screen.getByLabelText("Carregando empresas")).toHaveAttribute("aria-busy", "true");
  });

  it("vazio: explica como cadastrar a primeira empresa", async () => {
    m.companies.mockResolvedValue([]);
    await renderPage();
    expect(screen.getByText(/Nenhuma empresa cadastrada ainda/)).toBeInTheDocument();
    expect(
      screen.getByText("Nenhuma vaga ainda — cadastre uma empresa e aguarde a primeira coleta"),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Token ou URL do quadro Greenhouse")).toBeInTheDocument();
  });

  it("carrega empresas e a primeira página de vagas", async () => {
    m.companies.mockResolvedValue([collected]);
    await renderPage();
    expect(screen.getByText("GitLab", { selector: ".company__name" })).toBeInTheDocument();
    expect(screen.getByText("última coleta: há 5 min")).toBeInTheDocument();
    expect(m.jobs).toHaveBeenCalledTimes(1);
    expect(m.jobs).toHaveBeenCalledWith(expect.objectContaining({ offset: 0, status: "open" }));
  });

  it("sem coleta pendente: não consulta de novo", async () => {
    m.companies.mockResolvedValue([collected]);
    await renderPage();
    await tick(60_000);
    expect(m.companies).toHaveBeenCalledTimes(1);
  });

  it("com coleta pendente: consulta a cada 10 s e para quando nada mais está pendente", async () => {
    m.companies
      .mockResolvedValueOnce([collected, pending])
      .mockResolvedValueOnce([collected, pending])
      .mockResolvedValue([
        collected,
        { ...pending, last_run_at: "2026-11-12T12:00:15Z", next_run_at: "2026-11-12T18:00:15Z", open_jobs: 5 },
      ]);
    await renderPage();
    expect(screen.getByText(/coleta pendente/)).toBeInTheDocument();
    await tick(9_999);
    expect(m.companies).toHaveBeenCalledTimes(1);
    await tick(1);
    expect(m.companies).toHaveBeenCalledTimes(2);
    expect(m.jobs).toHaveBeenCalledTimes(1);
    await tick(10_000);
    expect(m.companies).toHaveBeenCalledTimes(3);
    // A coleta terminou: as vagas são recarregadas e o polling para.
    expect(m.jobs).toHaveBeenCalledTimes(2);
    expect(screen.getByText("5 vagas abertas")).toBeInTheDocument();
    await tick(60_000);
    expect(m.companies).toHaveBeenCalledTimes(3);
  });

  it("desmontar limpa os timers e não consulta mais", async () => {
    m.companies.mockResolvedValue([pending]);
    const { unmount } = await renderPage();
    unmount();
    expect(vi.getTimerCount()).toBe(0);
    await tick(60_000);
    expect(m.companies).toHaveBeenCalledTimes(1);
  });

  it("desmontar com resposta em voo não atualiza estado (sem avisos)", async () => {
    m.companies.mockResolvedValueOnce([pending]);
    const { unmount } = await renderPage();
    let resolve!: (c: Company[]) => void;
    m.companies.mockReturnValueOnce(new Promise((r) => (resolve = r)));
    await tick(10_000);
    unmount();
    const errors = vi.spyOn(console, "error");
    await act(async () => resolve([pending]));
    expect(errors).not.toHaveBeenCalled();
    errors.mockRestore();
    await tick(30_000);
    expect(m.companies).toHaveBeenCalledTimes(2);
  });

  it("5 min com coleta pendente: avisa que o worker pode estar parado", async () => {
    m.companies.mockResolvedValue([pending]);
    await renderPage();
    // 90 s pendente é normal com o worker saudável (fila de empresas, vetorização)
    await tick(90_000);
    expect(screen.queryByText(/docker compose logs worker/)).toBeNull();
    await tick(200_000);
    expect(screen.queryByText(/docker compose logs worker/)).toBeNull();
    await tick(10_000);
    expect(screen.getByText(/A coleta ainda não começou/)).toBeInTheDocument();
    expect(screen.getByText("docker compose logs worker")).toBeInTheDocument();
  });

  it("erro de rede no polling: para, avisa e retoma com Tentar de novo", async () => {
    m.companies
      .mockResolvedValueOnce([pending])
      .mockRejectedValueOnce(new ApiError(null, "Não consegui falar com a API."))
      .mockResolvedValue([pending]);
    await renderPage();
    await tick(10_000);
    expect(screen.getByRole("alert")).toHaveTextContent("Não consegui falar com a API.");
    expect(m.companies).toHaveBeenCalledTimes(2);
    await tick(60_000);
    expect(m.companies).toHaveBeenCalledTimes(2);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Tentar de novo" }));
    });
    expect(m.companies).toHaveBeenCalledTimes(3);
    expect(screen.queryByRole("alert")).toBeNull();
    await tick(10_000);
    expect(m.companies).toHaveBeenCalledTimes(4);
  });

  it("erro na carga inicial com Tentar de novo", async () => {
    m.companies.mockRejectedValueOnce(new ApiError(null, "Não consegui falar com a API em x."));
    await renderPage();
    expect(screen.getByRole("alert")).toHaveTextContent("Não consegui falar com a API em x.");
    m.companies.mockResolvedValueOnce([collected]);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Tentar de novo" }));
    });
    expect(screen.getByText("GitLab", { selector: ".company__name" })).toBeInTheDocument();
  });

  it("empresa nova recarrega a lista e começa o polling", async () => {
    m.companies.mockResolvedValueOnce([collected]).mockResolvedValue([collected, pending]);
    m.add.mockResolvedValue(pending);
    await renderPage();
    fireEvent.change(screen.getByLabelText("Token ou URL do quadro Greenhouse"), {
      target: { value: "nubank" },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /Cadastrar empresa/ }));
    });
    expect(m.companies).toHaveBeenCalledTimes(2);
    expect(screen.getByText("Nubank", { selector: ".company__name" })).toBeInTheDocument();
    // Ainda sem coleta: a lista de vagas não precisa recarregar; o filtro já tem a empresa.
    expect(m.jobs).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("option", { name: "Nubank" })).toBeInTheDocument();
    await tick(10_000);
    expect(m.companies).toHaveBeenCalledTimes(3);
  });
});
