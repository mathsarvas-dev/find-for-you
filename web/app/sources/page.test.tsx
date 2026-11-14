import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { OverviewContext, type OverviewState } from "@/components/shell/OverviewContext";
import { ApiError } from "@/lib/profile";
import { addCompany, collectNow, listCompanies, listJobs, patchCompany, type Company } from "@/lib/sources";
import { getDiscovery, putDiscovery, type DiscoveryState } from "@/lib/discovery";
import SourcesPage from "./page";

vi.mock("@/lib/sources", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/sources")>()),
  listCompanies: vi.fn(),
  listJobs: vi.fn(),
  addCompany: vi.fn(),
  collectNow: vi.fn(),
  patchCompany: vi.fn(),
}));

vi.mock("@/lib/discovery", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/discovery")>()),
  getDiscovery: vi.fn(),
  putDiscovery: vi.fn(),
  listCandidates: vi.fn(),
}));

const discovery: DiscoveryState = {
  enabled: true,
  target_companies: [],
  auto_companies: 0,
  max_auto: 30,
  pending: 0,
  found: 0,
};

const m = {
  discovery: vi.mocked(getDiscovery),
  companies: vi.mocked(listCompanies),
  jobs: vi.mocked(listJobs),
  add: vi.mocked(addCompany),
  patch: vi.mocked(patchCompany),
  collect: vi.mocked(collectNow),
  putDiscovery: vi.mocked(putDiscovery),
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
  origin: "manual",
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
  origin: "manual",
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

/** Renderiza dentro de uma casca falsa e devolve o refresh() do resumo. */
async function renderWithOverview() {
  const refresh = vi.fn(async () => {});
  const state: OverviewState = { overview: null, status: "ok", refresh };
  const view = render(
    <OverviewContext.Provider value={state}>
      <SourcesPage />
    </OverviewContext.Provider>,
  );
  await tick(0);
  return { ...view, refresh };
}

function currentStep() {
  const list = screen.getByRole("list", { name: "Etapas das fontes" });
  const items = within(list)
    .getAllByRole("listitem")
    .filter((li) => li.getAttribute("aria-current") === "step");
  expect(items).toHaveLength(1);
  return items[0];
}

const header = () => document.querySelector<HTMLElement>(".page-header__actions");

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(NOW);
  m.jobs.mockResolvedValue({ total: 0, items: [] });
  m.discovery.mockResolvedValue(discovery);
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

  it("monta o painel de descoberta automática", async () => {
    m.companies.mockResolvedValue([collected]);
    await renderPage();
    expect(screen.getByRole("heading", { name: "Descoberta automática" })).toBeInTheDocument();
    expect(
      screen.getByRole("switch", { name: "Descobrir empresas automaticamente" }),
    ).toBeChecked();
  });

  it("filtro por origem: todas, manuais e automáticas", async () => {
    const auto: Company = { ...collected, id: 3, identifier: "stone", name: "Stone", origin: "auto" };
    m.companies.mockResolvedValue([collected, auto]);
    await renderPage();
    const names = () =>
      Array.from(document.querySelectorAll(".company__name")).map((n) => n.textContent);
    expect(screen.getByRole("radio", { name: /todas/ })).toBeChecked();
    expect(names()).toEqual(["GitLab", "Stone"]);
    fireEvent.click(screen.getByRole("radio", { name: /automáticas/ }));
    expect(names()).toEqual(["Stone"]);
    fireEvent.click(screen.getByRole("radio", { name: /manuais/ }));
    expect(names()).toEqual(["GitLab"]);
    // O filtro é só da lista: a API não é chamada de novo.
    expect(m.companies).toHaveBeenCalledTimes(1);
  });

  it("filtro sem resultado explica o vazio", async () => {
    m.companies.mockResolvedValue([collected]);
    await renderPage();
    fireEvent.click(screen.getByRole("radio", { name: /automáticas/ }));
    expect(screen.getByText(/Nenhuma empresa automática ainda/)).toBeInTheDocument();
  });

  it("pausar empresa automática no teto: o painel relê e o polling volta", async () => {
    const auto: Company = { ...collected, id: 3, identifier: "stone", name: "Stone", origin: "auto" };
    m.companies
      .mockResolvedValueOnce([collected, auto])
      .mockResolvedValue([collected, { ...auto, active: false }]);
    m.discovery
      .mockResolvedValueOnce({ ...discovery, pending: 5, auto_companies: 1, max_auto: 1 })
      .mockResolvedValue({ ...discovery, pending: 5, auto_companies: 0, max_auto: 1 });
    m.patch.mockResolvedValue({ ...auto, active: false });
    await renderPage();
    expect(screen.getByText(/Limite de 1 empresas automáticas atingido/)).toBeInTheDocument();
    await tick(30_000);
    expect(m.discovery).toHaveBeenCalledTimes(1);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Pausar — Stone" }));
    });
    expect(m.patch).toHaveBeenCalledWith(3, { active: false });
    expect(m.discovery).toHaveBeenCalledTimes(2);
    expect(screen.queryByText(/empresas automáticas atingido/)).toBeNull();
    await tick(10_000);
    expect(m.discovery).toHaveBeenCalledTimes(3);
  });

  it("pausar empresa manual não relê a descoberta", async () => {
    m.companies.mockResolvedValue([collected]);
    m.patch.mockResolvedValue({ ...collected, active: false });
    await renderPage();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Pausar — GitLab" }));
    });
    expect(m.discovery).toHaveBeenCalledTimes(1);
  });

  it("descoberta adicionou empresa: a lista de empresas é relida", async () => {
    m.companies.mockResolvedValue([collected]);
    m.discovery
      .mockResolvedValueOnce({ ...discovery, pending: 3, auto_companies: 0 })
      .mockResolvedValue({ ...discovery, pending: 2, auto_companies: 1, found: 1 });
    await renderPage();
    expect(m.companies).toHaveBeenCalledTimes(1);
    await tick(10_000);
    expect(m.companies).toHaveBeenCalledTimes(2);
  });

  describe("cabeçalho, trilha e resumo da casca", () => {
    it("um único h1, também enquanto carrega", () => {
      m.companies.mockReturnValue(new Promise(() => {}));
      render(<SourcesPage />);
      expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
      expect(screen.getByRole("heading", { level: 1, name: "Fontes" })).toBeInTheDocument();
    });

    it("sem empresas: passo Adicionar e um botão no vazio que leva ao formulário", async () => {
      m.companies.mockResolvedValue([]);
      await renderPage();
      expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
      expect(currentStep()).toHaveTextContent("Adicionar");
      const empty = document.querySelector<HTMLElement>(".sources__empty")!;
      const buttons = within(empty).getAllByRole("button");
      expect(buttons).toHaveLength(1);
      expect(buttons[0]).toHaveTextContent("Adicionar empresa");
      fireEvent.click(buttons[0]);
      expect(screen.getByLabelText("Token ou URL do quadro Greenhouse")).toHaveFocus();
      // Sem empresas não há vagas para ver.
      expect(screen.queryByRole("link", { name: "Ver vagas" })).toBeNull();
    });

    it("ações do cabeçalho: Adicionar empresa foca o formulário e Ver vagas leva a /jobs", async () => {
      m.companies.mockResolvedValue([collected]);
      await renderPage();
      const actions = within(header()!);
      fireEvent.click(actions.getByRole("button", { name: "Adicionar empresa" }));
      expect(screen.getByLabelText("Token ou URL do quadro Greenhouse")).toHaveFocus();
      expect(actions.getByRole("link", { name: "Ver vagas" })).toHaveAttribute("href", "/jobs");
      // Focar não chama a API.
      expect(m.companies).toHaveBeenCalledTimes(1);
      expect(m.add).not.toHaveBeenCalled();
    });

    it("trilha: Coletar enquanto nenhuma empresa foi coletada, Ver vagas depois", async () => {
      m.companies.mockResolvedValueOnce([pending]).mockResolvedValue([collected, { ...pending, id: 9 }]);
      await renderPage();
      expect(currentStep()).toHaveTextContent("Coletar");
      await tick(10_000);
      expect(currentStep()).toHaveTextContent("Ver vagas");
    });

    it("cadastrar empresa atualiza o resumo da casca", async () => {
      m.companies.mockResolvedValueOnce([collected]).mockResolvedValue([collected, pending]);
      m.add.mockResolvedValue(pending);
      const { refresh } = await renderWithOverview();
      expect(refresh).not.toHaveBeenCalled();
      fireEvent.change(screen.getByLabelText("Token ou URL do quadro Greenhouse"), {
        target: { value: "nubank" },
      });
      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: /Cadastrar empresa/ }));
      });
      expect(m.add).toHaveBeenCalledTimes(1);
      expect(refresh).toHaveBeenCalledTimes(1);
    });

    it("pausar e coletar agora atualizam o resumo da casca", async () => {
      m.companies.mockResolvedValue([collected]);
      m.patch.mockResolvedValue({ ...collected, active: false });
      m.collect.mockResolvedValue({ ...collected, next_run_at: "2026-11-12T11:00:00Z" });
      const { refresh } = await renderWithOverview();
      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: "Coletar agora — GitLab" }));
      });
      expect(m.collect).toHaveBeenCalledTimes(1);
      expect(refresh).toHaveBeenCalledTimes(1);
      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: "Pausar — GitLab" }));
      });
      expect(m.patch).toHaveBeenCalledTimes(1);
      expect(refresh).toHaveBeenCalledTimes(2);
    });

    it("salvar a descoberta automática atualiza o resumo da casca", async () => {
      m.companies.mockResolvedValue([collected]);
      m.putDiscovery.mockResolvedValue({ ...discovery, enabled: false });
      const { refresh } = await renderWithOverview();
      await act(async () => {
        fireEvent.click(screen.getByRole("switch", { name: "Descobrir empresas automaticamente" }));
      });
      expect(m.putDiscovery).toHaveBeenCalledTimes(1);
      expect(refresh).toHaveBeenCalledTimes(1);
    });

    it("erro ao cadastrar não atualiza o resumo", async () => {
      m.companies.mockResolvedValue([collected]);
      m.add.mockRejectedValue(new ApiError(404, "Quadro não encontrado."));
      const { refresh } = await renderWithOverview();
      fireEvent.change(screen.getByLabelText("Token ou URL do quadro Greenhouse"), {
        target: { value: "nada" },
      });
      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: /Cadastrar empresa/ }));
      });
      expect(screen.getByRole("alert")).toHaveTextContent("Quadro não encontrado.");
      expect(refresh).not.toHaveBeenCalled();
    });
  });
});
