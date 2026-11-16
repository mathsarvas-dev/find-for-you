import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { OverviewContext, type OverviewState } from "@/components/shell/OverviewContext";
import { PageTitleContext } from "@/components/shell/PageHeader";
import { emptyOverview, type Overview } from "@/lib/overview";
import { JourneyPanel, greetingFor } from "./JourneyPanel";

function fresh(): Overview {
  return emptyOverview();
}

/** Perfil confirmado, 3 empresas, vagas abertas, nenhuma avaliada. */
function midJourney(): Overview {
  const o = emptyOverview();
  o.profile = { ...o.profile, confirmed: true, version: 2, vectorized: true, name: "Ana Souza" };
  o.companies = { total: 3, active: 3, auto: 1 };
  o.jobs = { open: 42, outside_filters: 0 };
  return o;
}

function allDone(): Overview {
  const o = midJourney();
  o.interview = { state: "done" };
  o.matches = { scored: 12, pending: 3 };
  return o;
}

function renderPanel(state: Partial<OverviewState>, setTitle = vi.fn()) {
  const value: OverviewState = {
    overview: null,
    status: "loading",
    refresh: vi.fn(async () => {}),
    ...state,
  };
  const view = render(
    <PageTitleContext.Provider value={setTitle}>
      <OverviewContext.Provider value={value}>
        <JourneyPanel />
      </OverviewContext.Provider>
    </PageTitleContext.Provider>,
  );
  return { ...view, value, setTitle };
}

function trail() {
  return screen.getByRole("list", { name: "Seu caminho" });
}

function headerActions(container: HTMLElement) {
  return container.querySelector<HTMLElement>(".page-header__actions");
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date(2026, 10, 13, 9, 30));
});

afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
});

describe("greetingFor", () => {
  it.each([
    [0, "Boa noite"],
    [4, "Boa noite"],
    [5, "Bom dia"],
    [11, "Bom dia"],
    [12, "Boa tarde"],
    [17, "Boa tarde"],
    [18, "Boa noite"],
    [23, "Boa noite"],
  ])("%i h → %s", (hour, text) => {
    expect(greetingFor(hour)).toBe(text);
  });
});

describe("JourneyPanel", () => {
  it("h1 Início único e título da barra superior", () => {
    const { setTitle } = renderPanel({ overview: fresh(), status: "ok" });
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Início");
    expect(setTitle).toHaveBeenCalledWith("Início");
  });

  it("carrega a trilha Seu caminho com os 7 passos do overview", () => {
    renderPanel({ overview: midJourney(), status: "ok" });
    expect(screen.getByRole("heading", { level: 2, name: "Seu caminho" })).toBeInTheDocument();
    const steps = within(trail()).getAllByRole("listitem");
    expect(steps).toHaveLength(7);
    expect(steps[3]).toHaveTextContent("3 empresas · 42 vagas abertas");
    const current = steps.find((li) => li.getAttribute("aria-current") === "step");
    expect(current).toHaveTextContent("Avaliar melhores vagas");
  });

  it("usuário novo: CTA do cabeçalho é o do passo atual (Montar meu perfil → /onboarding)", () => {
    const { container } = renderPanel({ overview: fresh(), status: "ok" });
    const actions = headerActions(container)!;
    const cta = within(actions).getByRole("link");
    expect(cta).toHaveTextContent("Montar meu perfil");
    expect(cta).toHaveAttribute("href", "/onboarding");
    expect(cta).toHaveClass("button");
    expect(screen.getByText("Próximo passo: Montar o perfil.")).toBeInTheDocument();
  });

  it("meio do caminho: CTA Avaliar melhores vagas → /jobs", () => {
    const { container } = renderPanel({ overview: midJourney(), status: "ok" });
    const cta = within(headerActions(container)!).getByRole("link");
    expect(cta).toHaveTextContent("Avaliar melhores vagas");
    expect(cta).toHaveAttribute("href", "/jobs");
  });

  it("fontes como passo atual: CTA Adicionar empresas → /sources", () => {
    const o = midJourney();
    o.companies = { total: 0, active: 0, auto: 0 };
    o.jobs = { open: 0, outside_filters: 0 };
    const { container } = renderPanel({ overview: o, status: "ok" });
    const cta = within(headerActions(container)!).getByRole("link");
    expect(cta).toHaveTextContent("Adicionar empresas");
    expect(cta).toHaveAttribute("href", "/sources");
  });

  it("com perfil, o CTA do passo Perfil na trilha fica como computeSteps define", () => {
    const o = midJourney();
    o.profile = { ...o.profile, vectorized: false };
    renderPanel({ overview: o, status: "ok" });
    const steps = within(trail()).getAllByRole("listitem");
    expect(steps[0]).toHaveTextContent("Revisar");
    expect(within(steps[1]).getByRole("link", { name: "Ver vetorização" })).toHaveAttribute(
      "href",
      "/onboarding",
    );
  });

  it("cartões resumo clicáveis com números", () => {
    renderPanel({ overview: midJourney(), status: "ok" });
    const summary = screen.getByRole("region", { name: "Resumo" });
    const links = within(summary).getAllByRole("link");
    expect(links.map((a) => a.getAttribute("href"))).toEqual(["/onboarding", "/sources", "/jobs", "/jobs"]);
    expect(links[0]).toHaveTextContent("Versão 2");
    expect(links[1]).toHaveTextContent("3 empresas ativas");
    expect(links[2]).toHaveTextContent("42 vagas abertas");
    expect(links[3]).toHaveTextContent("0 de 42");
  });

  it("tudo concluído: sem passo atual e CTA Ver vagas avaliadas → /jobs", () => {
    const { container } = renderPanel({ overview: allDone(), status: "ok" });
    expect(within(trail()).getAllByRole("listitem").some((li) => li.hasAttribute("aria-current"))).toBe(false);
    const cta = within(headerActions(container)!).getByRole("link");
    expect(cta).toHaveTextContent("Ver vagas avaliadas");
    expect(cta).toHaveAttribute("href", "/jobs");
    expect(screen.getByText("Trilha em dia: 12 vagas avaliadas.")).toBeInTheDocument();
  });

  it("carregando: esqueleto ocupado, sem trilha, sem CTA e sem erro", () => {
    const { container } = renderPanel({ overview: null, status: "loading" });
    expect(container.querySelector("[aria-busy='true']")).not.toBeNull();
    expect(container.querySelectorAll(".skeleton").length).toBeGreaterThan(0);
    expect(screen.getByText("Carregando seu progresso…")).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "Seu caminho" })).toBeNull();
    expect(headerActions(container)).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Início");
  });

  it("erro de rede: mensagem específica e um único botão Tentar de novo que chama refresh()", async () => {
    let finish: () => void = () => {};
    const refresh = vi.fn(() => new Promise<void>((resolve) => (finish = resolve)));
    const { container } = renderPanel({ overview: null, status: "down", refresh });
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Não consegui carregar o seu progresso.");
    expect(alert).toHaveTextContent("GET /overview");
    expect(within(alert).getByRole("link", { name: "Ver estado do sistema" })).toHaveAttribute(
      "href",
      "/settings#sistema",
    );
    expect(screen.getAllByRole("button")).toHaveLength(1);
    expect(headerActions(container)).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Tentar de novo" }));
    expect(refresh).toHaveBeenCalledTimes(1);
    const busy = screen.getByRole("button", { name: "Tentando…" });
    expect(busy).toBeDisabled();
    fireEvent.click(busy);
    expect(refresh).toHaveBeenCalledTimes(1);

    await act(async () => finish());
    expect(screen.getByRole("button", { name: "Tentar de novo" })).toBeEnabled();
  });

  it("falha depois de já ter dados: mostra os últimos números e um aviso discreto", () => {
    renderPanel({ overview: midJourney(), status: "down" });
    expect(screen.getByRole("list", { name: "Seu caminho" })).toBeInTheDocument();
    const note = screen.getByRole("status");
    expect(note).toHaveTextContent("Não consegui atualizar agora");
    expect(within(note).getByRole("link", { name: "Ver estado do sistema" })).toHaveAttribute(
      "href",
      "/settings#sistema",
    );
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("sem aviso quando a última busca respondeu", () => {
    renderPanel({ overview: midJourney(), status: "ok" });
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("saudação calculada depois de montar, com o primeiro nome", () => {
    renderPanel({ overview: midJourney(), status: "ok" });
    expect(screen.getByText("Bom dia, Ana.")).toBeInTheDocument();
  });

  it("saudação sem nome", () => {
    vi.setSystemTime(new Date(2026, 10, 13, 20, 0));
    renderPanel({ overview: fresh(), status: "ok" });
    expect(screen.getByText("Boa noite.")).toBeInTheDocument();
  });

  it("textos hostis aparecem literais, nunca como HTML", () => {
    const o = midJourney();
    o.profile = { ...o.profile, name: '<img src=x onerror="alert(1)"> Zé' };
    const { container } = renderPanel({ overview: o, status: "ok" });
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText('Bom dia, <img.')).toBeInTheDocument();
  });

  it("não busca nada sozinho: só lê o contexto", () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    renderPanel({ overview: midJourney(), status: "ok" });
    expect(fetchSpy).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
  });
});
