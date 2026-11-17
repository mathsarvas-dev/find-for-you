import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { ApiError } from "@/lib/profile";
import { listJobs, type Company, type JobItem, type JobPage } from "@/lib/sources";
import { JOBS_PAGE_SIZE, JobsList } from "./JobsList";

vi.mock("@/lib/sources", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/sources")>()),
  listJobs: vi.fn(),
}));

const list = vi.mocked(listJobs);
const NOW = Date.parse("2026-11-12T12:00:00Z");

const companies: Company[] = [
  {
    id: 1,
    source: "greenhouse",
    identifier: "gitlab",
    name: "GitLab",
    country: null,
    active: true,
    last_run_at: "2026-11-12T11:00:00Z",
    last_success_at: "2026-11-12T11:00:00Z",
    next_run_at: "2026-11-12T17:00:00Z",
    last_error: null,
    consecutive_failures: 0,
    open_jobs: 2,
    origin: "manual",
    collect_mode: "funnel",
    stub_jobs: 0,
  },
  {
    id: 2,
    source: "greenhouse",
    identifier: "nubank",
    name: "Nubank",
    country: "BR",
    active: true,
    last_run_at: "2026-11-12T11:00:00Z",
    last_success_at: "2026-11-12T11:00:00Z",
    next_run_at: "2026-11-12T17:00:00Z",
    last_error: null,
    consecutive_failures: 0,
    open_jobs: 0,
    origin: "manual",
    collect_mode: "funnel",
    stub_jobs: 0,
  },
];

function job(id: number, extra: Partial<JobItem> = {}): JobItem {
  return {
    id,
    company_id: 1,
    company_name: "GitLab",
    title: `Vaga ${id}`,
    url: `https://boards.greenhouse.io/gitlab/jobs/${id}`,
    location_text: "Remote, Americas",
    country: null,
    city: null,
    work_model: "remote",
    seniority: "senior",
    language: "en",
    department: "Engineering",
    salary_known: false,
    status: "open",
    first_seen_at: "2026-11-12T11:00:00Z",
    posted_at: "2026-11-12T09:00:00Z",
    origin: "ats",
    source: "greenhouse",
    attribution: null,
    ...extra,
  };
}

const page = (items: JobItem[], total = items.length): JobPage => ({ total, items });

async function tick(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

async function renderList(refreshKey = 0) {
  const view = render(<JobsList companies={companies} now={NOW} refreshKey={refreshKey} />);
  await tick(0);
  return view;
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  list.mockReset();
});

const lastQuery = () => list.mock.calls[list.mock.calls.length - 1][0];

describe("JobsList", () => {
  it("carregando: esqueleto com aria-busy", () => {
    list.mockReturnValue(new Promise(() => {}));
    render(<JobsList companies={companies} now={NOW} refreshKey={0} />);
    expect(screen.getByLabelText("Carregando vagas")).toHaveAttribute("aria-busy", "true");
  });

  it("primeira página: abertas, sem empresa, limite padrão; colunas e total", async () => {
    list.mockResolvedValue(page([job(1), job(2, { work_model: "hybrid", location_text: "São Paulo" })], 2));
    await renderList();
    expect(list).toHaveBeenCalledWith({
      status: "open",
      companyId: null,
      limit: JOBS_PAGE_SIZE,
      offset: 0,
      q: "",
    });
    expect(screen.getByText("2 vagas")).toBeInTheDocument();
    const rows = screen.getAllByRole("row").slice(1);
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByText("Vaga 1")).toBeInTheDocument();
    expect(within(rows[0]).getByText("GitLab")).toBeInTheDocument();
    expect(within(rows[0]).getByText("remoto")).toBeInTheDocument();
    expect(within(rows[0]).getByText("há 3 h")).toBeInTheDocument();
    expect(within(rows[1]).getByText("híbrido")).toBeInTheDocument();
    expect(within(rows[1]).getByText("São Paulo")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Mais vagas" })).toBeNull();
  });

  it("título hostil aparece como texto e não vira HTML", async () => {
    const hostile = "<img src=x onerror=alert(1)>";
    list.mockResolvedValue(page([job(1, { title: hostile, location_text: "<b>Lisboa</b>" })]));
    const { container } = await renderList();
    expect(screen.getByText(hostile)).toBeInTheDocument();
    expect(screen.getByText("<b>Lisboa</b>")).toBeInTheDocument();
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("b")).toBeNull();
  });

  it("link seguro só para http(s); javascript: não vira link", async () => {
    list.mockResolvedValue(
      page([
        job(1, { title: "Boa" }),
        job(2, { title: "Ruim", url: "javascript:alert(1)" }),
      ]),
    );
    await renderList();
    const links = screen.getAllByRole("link", { name: /Abrir vaga/ });
    expect(links).toHaveLength(1);
    expect(links[0]).toHaveAccessibleName("Abrir vaga — Boa");
    expect(links[0]).toHaveAttribute("href", "https://boards.greenhouse.io/gitlab/jobs/1");
    expect(links[0]).toHaveAttribute("target", "_blank");
    expect(links[0]).toHaveAttribute("rel", "noopener noreferrer");
    expect(document.querySelector('a[href^="javascript"]')).toBeNull();
    expect(screen.getByText("link indisponível")).toBeInTheDocument();
  });

  it("Mais vagas: offset += limite, acrescenta e some quando tudo carregou", async () => {
    const first = Array.from({ length: JOBS_PAGE_SIZE }, (_, i) => job(i + 1));
    list.mockResolvedValueOnce(page(first, JOBS_PAGE_SIZE + 2));
    await renderList();
    expect(screen.getByText(`${JOBS_PAGE_SIZE + 2} vagas`)).toBeInTheDocument();
    list.mockResolvedValueOnce(page([job(901), job(902)], JOBS_PAGE_SIZE + 2));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Mais vagas" }));
    });
    expect(lastQuery()).toMatchObject({ offset: JOBS_PAGE_SIZE, limit: JOBS_PAGE_SIZE });
    expect(screen.getAllByRole("row")).toHaveLength(JOBS_PAGE_SIZE + 3);
    expect(screen.getByText("Vaga 902")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Mais vagas" })).toBeNull();
  });

  it("filtros de empresa e status refazem a busca do início", async () => {
    list.mockResolvedValue(page([job(1)]));
    await renderList();
    await act(async () => {
      fireEvent.change(screen.getByLabelText("Empresa"), { target: { value: "2" } });
    });
    expect(lastQuery()).toMatchObject({ companyId: 2, offset: 0, status: "open" });
    await act(async () => {
      fireEvent.change(screen.getByLabelText("Situação"), { target: { value: "closed" } });
    });
    expect(lastQuery()).toMatchObject({ companyId: 2, status: "closed" });
    expect(screen.getByRole("option", { name: "Todas as empresas" })).toBeInTheDocument();
  });

  it("busca com debounce de 300 ms e limite de 100 caracteres", async () => {
    list.mockResolvedValue(page([job(1)]));
    await renderList();
    expect(list).toHaveBeenCalledTimes(1);
    const box = screen.getByLabelText("Buscar no título");
    expect(box).toHaveAttribute("maxLength", "100");
    fireEvent.change(box, { target: { value: "r" } });
    await tick(100);
    fireEvent.change(box, { target: { value: "re" } });
    await tick(100);
    fireEvent.change(box, { target: { value: "react" } });
    await tick(299);
    expect(list).toHaveBeenCalledTimes(1);
    await tick(1);
    expect(list).toHaveBeenCalledTimes(2);
    expect(lastQuery()).toMatchObject({ q: "react", offset: 0 });
  });

  it("ignora respostas fora de ordem quando os filtros mudam rápido", async () => {
    let resolveOld!: (p: JobPage) => void;
    list.mockResolvedValueOnce(page([job(1)]));
    await renderList();
    list.mockReturnValueOnce(new Promise((r) => (resolveOld = r)));
    list.mockResolvedValueOnce(page([job(2, { title: "Da Nubank", company_name: "Nubank" })]));
    await act(async () => {
      fireEvent.change(screen.getByLabelText("Empresa"), { target: { value: "1" } });
    });
    await act(async () => {
      fireEvent.change(screen.getByLabelText("Empresa"), { target: { value: "2" } });
    });
    expect(screen.getByText("Da Nubank")).toBeInTheDocument();
    await act(async () => resolveOld(page([job(3, { title: "Antiga" })])));
    expect(screen.queryByText("Antiga")).toBeNull();
    expect(screen.getByText("Da Nubank")).toBeInTheDocument();
  });

  it("vazio sem filtros: explica como começar", async () => {
    list.mockResolvedValue(page([]));
    await renderList();
    expect(
      screen.getByText("Nenhuma vaga ainda — cadastre uma empresa e aguarde a primeira coleta"),
    ).toBeInTheDocument();
  });

  it("vazio com filtro: diz que nada bate com os filtros", async () => {
    list.mockResolvedValue(page([]));
    await renderList();
    await act(async () => {
      fireEvent.change(screen.getByLabelText("Situação"), { target: { value: "closed" } });
    });
    expect(screen.getByText(/Nenhuma vaga com esses filtros/)).toBeInTheDocument();
  });

  it("erro com Tentar de novo", async () => {
    list.mockRejectedValueOnce(new ApiError(null, "Não consegui falar com a API."));
    await renderList();
    expect(screen.getByRole("alert")).toHaveTextContent("Não consegui falar com a API.");
    list.mockResolvedValueOnce(page([job(1)]));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Tentar de novo" }));
    });
    expect(screen.getByText("Vaga 1")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("refreshKey novo recarrega a primeira página", async () => {
    list.mockResolvedValue(page([job(1)]));
    const { rerender } = await renderList(0);
    rerender(<JobsList companies={companies} now={NOW} refreshKey={1} />);
    await tick(0);
    expect(list).toHaveBeenCalledTimes(2);
    expect(lastQuery()).toMatchObject({ offset: 0 });
  });
});

describe("JobsList (crédito de feed)", () => {
  it("vaga de feed mostra 'via <fonte>' linkando a atribuição", async () => {
    list.mockResolvedValue(
      page([
        job(1, {
          origin: "feed",
          source: "remotive",
          company_name: "Acme",
          attribution: { label: "Remotive", url: "https://remotive.com/" },
        }),
      ], 1),
    );
    render(<JobsList companies={companies} now={NOW} refreshKey={0} />);
    await act(async () => {});
    const credit = screen.getByRole("link", { name: "via Remotive" });
    expect(credit).toHaveAttribute("target", "_blank");
    expect(credit.getAttribute("rel")).toContain("noopener");
    expect(credit).toHaveAttribute("href", expect.stringContaining("remotive.com"));
  });

  it("vaga de ATS não mostra crédito", async () => {
    list.mockResolvedValue(page([job(1)], 1));
    render(<JobsList companies={companies} now={NOW} refreshKey={0} />);
    await act(async () => {});
    expect(screen.queryByText(/^via /)).toBeNull();
  });
});
