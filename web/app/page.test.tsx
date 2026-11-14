import { afterEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { renderToString } from "react-dom/server";
import { AppShell } from "@/components/shell/AppShell";
import { OverviewContext } from "@/components/shell/OverviewContext";
import { ApiError } from "@/lib/profile";
import { emptyOverview, getOverview } from "@/lib/overview";
import HomePage from "./page";

vi.mock("next/navigation", () => ({ usePathname: vi.fn(() => "/") }));

vi.mock("@/lib/overview", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/overview")>()),
  getOverview: vi.fn(),
}));

const getOverviewMock = vi.mocked(getOverview);

afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
});

function midJourney() {
  const o = emptyOverview();
  o.profile = { ...o.profile, confirmed: true, version: 2, vectorized: true, name: "Ana Souza" };
  o.companies = { total: 3, active: 3, auto: 1 };
  o.jobs = { open: 42 };
  return o;
}

function prerender(hour: number, withData: boolean) {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date(2026, 10, 13, hour, 0));
  const value = withData
    ? { overview: midJourney(), status: "ok" as const, refresh: async () => {} }
    : { overview: null, status: "loading" as const, refresh: async () => {} };
  const html = renderToString(
    <OverviewContext.Provider value={value}>
      <HomePage />
    </OverviewContext.Provider>,
  );
  vi.useRealTimers();
  return html;
}

describe("Início (/)", () => {
  it("pré-render não depende da hora: mesmo HTML às 9 h e às 21 h, sem saudação", () => {
    for (const withData of [false, true]) {
      const morning = prerender(9, withData);
      const night = prerender(21, withData);
      expect(morning).toBe(night);
      expect(morning).not.toMatch(/Bom dia|Boa tarde|Boa noite/);
      expect(morning).toContain("Início");
    }
  });

  it("pré-render fora da casca mostra o esqueleto, não o painel de serviços", () => {
    const html = prerender(9, false);
    expect(html).toContain("Carregando seu progresso…");
    expect(html).not.toContain("Serviços");
    expect(html).not.toContain("Estado do sistema");
  });

  it("dentro da casca: uma busca por visita, sem polling; Tentar de novo busca outra vez", async () => {
    vi.useFakeTimers();
    getOverviewMock.mockRejectedValue(new ApiError(null, "Não consegui falar com a API"));
    render(
      <AppShell>
        <HomePage />
      </AppShell>,
    );
    await act(async () => {});
    expect(getOverviewMock).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("alert")).toHaveTextContent("Não consegui carregar o seu progresso.");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5 * 60_000);
    });
    expect(getOverviewMock).toHaveBeenCalledTimes(1);

    getOverviewMock.mockResolvedValue(midJourney());
    fireEvent.click(screen.getByRole("button", { name: "Tentar de novo" }));
    await act(async () => {});
    expect(getOverviewMock).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByRole("list", { name: "Seu caminho" })).toBeInTheDocument();
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
  });

  it("Tentar de novo que falha de novo volta a ficar disponível", async () => {
    getOverviewMock.mockRejectedValue(new ApiError(null, "Não consegui falar com a API"));
    render(
      <AppShell>
        <HomePage />
      </AppShell>,
    );
    await act(async () => {});
    fireEvent.click(screen.getByRole("button", { name: "Tentar de novo" }));
    expect(screen.getByRole("button", { name: "Tentando…" })).toBeDisabled();
    await act(async () => {});
    expect(getOverviewMock).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("button", { name: "Tentar de novo" })).toBeEnabled();
  });
});
