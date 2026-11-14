import { afterEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { renderToString } from "react-dom/server";
import { OverviewContext, type OverviewState } from "@/components/shell/OverviewContext";
import { PageTitleContext } from "@/components/shell/PageHeader";
import { ApiError } from "@/lib/profile";
import { emptyOverview } from "@/lib/overview";
import { getDiscovery, putDiscovery, type DiscoveryState } from "@/lib/discovery";
import SettingsPage from "./page";
import { metadata } from "./layout";

// O painel de saúde é assíncrono e de servidor (testado em SystemSection.test.tsx).
vi.mock("@/components/HealthPanel", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/components/HealthPanel")>()),
  HealthPanel: () => <section aria-label="Painel de saúde (stub)" />,
}));

vi.mock("@/lib/discovery", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/discovery")>()),
  getDiscovery: vi.fn(),
  putDiscovery: vi.fn(),
  listCandidates: vi.fn(),
}));

const discovery: DiscoveryState = {
  enabled: true,
  target_companies: ["Nubank"],
  auto_companies: 2,
  max_auto: 30,
  pending: 0,
  found: 2,
};

afterEach(() => vi.clearAllMocks());

function profiled() {
  const o = emptyOverview();
  o.profile = { ...o.profile, confirmed: true, version: 2, name: "Ana Souza", email: "ana@example.com" };
  return o;
}

async function renderPage(value: Partial<OverviewState> = {}, setTitle = vi.fn()) {
  const state: OverviewState = { overview: profiled(), status: "ok", refresh: vi.fn(async () => {}), ...value };
  const view = render(
    <PageTitleContext.Provider value={setTitle}>
      <OverviewContext.Provider value={state}>
        <SettingsPage />
      </OverviewContext.Provider>
    </PageTitleContext.Provider>,
  );
  await act(async () => {});
  return { ...view, state, setTitle };
}

describe("Configurações (/settings)", () => {
  it("metadata do título", () => {
    expect(metadata.title).toBe("Configurações · Rumo");
  });

  it("pré-render fora da casca: esqueleto da conta, carregando a descoberta, sem fetch", () => {
    const html = renderToString(<SettingsPage />);
    expect(html).toContain("Configurações");
    expect(html).toContain("Carregando o perfil…");
    expect(html).toContain("Buscando as preferências de descoberta…");
    expect(html).not.toContain("Você");
    expect(getDiscovery).not.toHaveBeenCalled();
  });

  it("um h1 Configurações, título do topo e as três seções com âncoras", async () => {
    vi.mocked(getDiscovery).mockResolvedValue(discovery);
    const { container, setTitle } = await renderPage();
    const h1s = screen.getAllByRole("heading", { level: 1 });
    expect(h1s).toHaveLength(1);
    expect(h1s[0]).toHaveTextContent("Configurações");
    expect(setTitle).toHaveBeenCalledWith("Configurações");
    for (const id of ["conta", "descoberta", "sistema"]) {
      expect(container.querySelectorAll(`#${id}`)).toHaveLength(1);
    }
    expect(screen.getByRole("heading", { level: 2, name: "Conta e perfil" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "Descoberta automática" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Painel de saúde (stub)" })).toBeInTheDocument();
    // Ordem: conta, descoberta, sistema.
    const order = ["conta", "descoberta", "sistema"].map((id) => container.querySelector(`#${id}`)!);
    expect(order[0].compareDocumentPosition(order[1]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(order[1].compareDocumentPosition(order[2]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("navegação da página aponta para as âncoras", async () => {
    vi.mocked(getDiscovery).mockResolvedValue(discovery);
    await renderPage();
    const nav = screen.getByRole("navigation", { name: "Seções de Configurações" });
    const links = within(nav).getAllByRole("link");
    expect(links.map((a) => [a.textContent, a.getAttribute("href")])).toEqual([
      ["Conta e perfil", "#conta"],
      ["Descoberta automática", "#descoberta"],
      ["Estado do sistema", "#sistema"],
    ]);
  });

  it("monta o painel de descoberta funcionando sem /sources", async () => {
    vi.mocked(getDiscovery).mockResolvedValue(discovery);
    await renderPage();
    expect(getDiscovery).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("switch", { name: "Descobrir empresas automaticamente" })).toBeChecked();
    expect(screen.getByLabelText("Empresas que você quer acompanhar")).toHaveValue("Nubank");
    expect(screen.getByText("2 empresas encontradas · 0 na fila")).toBeInTheDocument();
  });

  it("descoberta que muda as empresas automáticas atualiza o overview da casca", async () => {
    vi.mocked(getDiscovery).mockResolvedValue(discovery);
    vi.mocked(putDiscovery).mockResolvedValue({ ...discovery, enabled: false, auto_companies: 3 });
    const { state } = await renderPage();
    fireEvent.click(screen.getByRole("switch", { name: "Descobrir empresas automaticamente" }));
    await act(async () => {});
    expect(putDiscovery).toHaveBeenCalledWith({ enabled: false, target_companies: ["Nubank"] });
    expect(state.refresh).toHaveBeenCalledTimes(1);
  });

  it("API fora: conta no padrão, descoberta no próprio erro, página inteira", async () => {
    vi.mocked(getDiscovery).mockRejectedValue(new ApiError(null, "Não consegui falar com a API."));
    await renderPage({ overview: null, status: "down" });
    expect(screen.getByText("Você")).toBeInTheDocument();
    expect(screen.getByText("Perfil não confirmado")).toBeInTheDocument();
    const alerts = screen.getAllByRole("alert");
    expect(alerts.some((a) => a.textContent?.includes("Não deu para carregar a descoberta automática."))).toBe(
      true,
    );
    expect(screen.getByRole("button", { name: "Tentar de novo" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Painel de saúde (stub)" })).toBeInTheDocument();
  });
});
