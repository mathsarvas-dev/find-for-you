import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { checkHealth, checkWorker, type HealthReport, type WorkerReport } from "@/lib/health";
import { HealthPanel, HealthPanelLoading, PanelHead, ProblemNotice } from "./HealthPanel";

vi.mock("next/server", () => ({ connection: vi.fn(async () => {}) }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: vi.fn() }) }));

vi.mock("@/lib/health", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/health")>()),
  checkHealth: vi.fn(),
  checkWorker: vi.fn(),
}));

const workerUp: WorkerReport = { state: "ok", detail: "sinal recente" };

const healthy: HealthReport = {
  apiUrl: "http://api.test:8000",
  api: "ok",
  db: "ok",
  apiDetail: "GET /health · 200 · 12 ms",
  dbDetail: "SELECT 1 ok",
  problem: null,
  httpStatus: 200,
  checkedAt: new Date(2026, 10, 13, 9, 5, 7),
};

afterEach(() => vi.clearAllMocks());

describe("HealthPanel", () => {
  it("PanelHead mostra o título Serviços e o alvo", () => {
    render(<PanelHead target="GET /health" />);
    expect(screen.getByRole("heading", { level: 2, name: "Serviços" })).toBeInTheDocument();
    expect(screen.getByText("GET /health")).toBeInTheDocument();
  });

  it("carregando: API e banco em verificação", () => {
    render(<HealthPanelLoading />);
    const rows = screen.getAllByRole("listitem");
    expect(rows).toHaveLength(3);
    expect(rows[0]).toHaveTextContent("API");
    expect(rows[1]).toHaveTextContent("Banco de dados");
    expect(rows[2]).toHaveTextContent("Worker");
    for (const row of rows) expect(row).toHaveAttribute("aria-busy", "true");
  });

  it("checa no servidor e mostra estados, hora e Verificar de novo", async () => {
    vi.mocked(checkHealth).mockResolvedValue(healthy);
    vi.mocked(checkWorker).mockResolvedValue(workerUp);
    render(await HealthPanel());
    expect(screen.getByText("http://api.test:8000/health")).toBeInTheDocument();
    const rows = screen.getAllByRole("listitem");
    expect(within(rows[0]).getByText("Funcionando")).toBeInTheDocument();
    expect(within(rows[1]).getByText("SELECT 1 ok")).toBeInTheDocument();
    expect(within(rows[2]).getByText("Funcionando")).toBeInTheDocument();
    expect(screen.getByText("09:05:07")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
    // Botão de cliente que refaz a checagem com router.refresh(), não um link.
    expect(screen.getByRole("button", { name: "Verificar de novo" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Verificar de novo" })).toBeNull();
  });

  it.each([
    ["api-unreachable", "A API não respondeu."],
    ["db-down", "A API está no ar, mas não alcança o banco."],
    ["api-unexpected", "A API respondeu de um jeito inesperado (HTTP 500)."],
  ] as const)("aviso de problema %s", (problem, title) => {
    render(<ProblemNotice report={{ ...healthy, problem, httpStatus: 500 }} />);
    expect(screen.getByRole("alert")).toHaveTextContent(title);
  });

  it("worker parado mostra aviso; worker ok não", async () => {
    vi.mocked(checkHealth).mockResolvedValue(healthy);
    vi.mocked(checkWorker).mockResolvedValue({ state: "down", detail: "último sinal há 5 min" });
    const { unmount } = render(await HealthPanel());
    expect(screen.getByRole("alert")).toHaveTextContent("O worker está parado.");
    unmount();

    vi.mocked(checkWorker).mockResolvedValue(workerUp);
    render(await HealthPanel());
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("sem problema não há aviso", () => {
    const { container } = render(<ProblemNotice report={healthy} />);
    expect(container).toBeEmptyDOMElement();
  });
});
