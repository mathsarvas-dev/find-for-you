import { afterEach, describe, expect, it, vi } from "vitest";
import { isValidElement, Suspense, type ReactElement } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { checkHealth, type HealthReport } from "@/lib/health";
import { HealthPanel, HealthPanelLoading } from "@/components/HealthPanel";
import { SystemSection } from "./SystemSection";

vi.mock("next/server", () => ({ connection: vi.fn(async () => {}) }));
const { refresh } = vi.hoisted(() => ({ refresh: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh }) }));

vi.mock("@/lib/health", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/health")>()),
  checkHealth: vi.fn(),
}));

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

const down: HealthReport = {
  ...healthy,
  api: "down",
  db: "unknown",
  apiDetail: "sem conexão",
  dbDetail: "depende da API",
  problem: "api-unreachable",
  httpStatus: null,
};

afterEach(() => vi.clearAllMocks());

type El = ReactElement<Record<string, unknown>>;

/** Acha o <Suspense> da seção e o painel que ele envolve. */
function parts() {
  const root = SystemSection() as El;
  const children = ([] as unknown[]).concat(root.props.children as unknown[]);
  const suspense = children.find((c) => isValidElement(c) && c.type === Suspense) as El;
  return { root, suspense, panel: suspense.props.children as El };
}

/** Renderiza a seção como no servidor: o painel assíncrono resolvido no lugar do Suspense. */
async function renderResolved() {
  const { root, suspense, panel } = parts();
  const resolved = await HealthPanel(panel.props);
  const children = ([] as unknown[])
    .concat(root.props.children as unknown[])
    .map((c) => (c === suspense ? resolved : c));
  return render(<div {...root.props}>{children as React.ReactNode}</div>);
}

describe("SystemSection", () => {
  it("é a âncora #sistema e monta o HealthPanel dentro de Suspense com o esqueleto", () => {
    const { root, suspense, panel } = parts();
    expect(root.props.id).toBe("sistema");
    expect(suspense.type).toBe(Suspense);
    expect((suspense.props.fallback as El).type).toBe(HealthPanelLoading);
    expect(panel.type).toBe(HealthPanel);
  });

  it("esqueleto: título da seção e API/banco em verificação", () => {
    const { suspense } = parts();
    render(suspense.props.fallback as El);
    expect(screen.getByRole("heading", { level: 2, name: "Estado do sistema" })).toBeInTheDocument();
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
  });

  it("API ok: estados, sem aviso; Verificar de novo refaz a checagem com router.refresh()", async () => {
    vi.mocked(checkHealth).mockResolvedValue(healthy);
    await renderResolved();
    expect(checkHealth).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("heading", { level: 2, name: "Estado do sistema" })).toBeInTheDocument();
    expect(screen.getByText("SELECT 1 ok")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
    // Não é um link só de âncora (em /settings isso não recarregaria nada).
    expect(screen.queryByRole("link", { name: "Verificar de novo" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Verificar de novo" }));
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it("API fora: aviso específico e Verificar de novo", async () => {
    vi.mocked(checkHealth).mockResolvedValue(down);
    await renderResolved();
    expect(screen.getByRole("alert")).toHaveTextContent("A API não respondeu.");
    expect(screen.getByText("sem conexão")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Verificar de novo" })).toBeEnabled();
  });
});
