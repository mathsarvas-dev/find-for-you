import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { OverviewContext, type OverviewState } from "@/components/shell/OverviewContext";
import { ApiError } from "@/lib/profile";
import {
  DEFAULT_FILTERS,
  getMatch,
  getSearchProfile,
  listMatches,
  putSearchProfile,
  refreshMatches,
  type MatchPage,
  type SearchFilters,
  type SearchProfile,
} from "@/lib/matching";
import { parseFilters } from "@/lib/chatFilters";
import { matchDetail, matchItem, matchPage, parsedQuery } from "@/components/jobs/fixtures";
import JobsPage from "./page";

const { push, prepare, create } = vi.hoisted(() => ({ push: vi.fn(), prepare: vi.fn(), create: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));
vi.mock("@/lib/searchProfiles", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/searchProfiles")>()),
  listSearchProfiles: vi.fn(async () => []),
}));
vi.mock("@/lib/applications", () => ({ prepareApplication: prepare, createApplication: create }));

vi.mock("@/lib/chatFilters", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/chatFilters")>()),
  parseFilters: vi.fn(),
}));

vi.mock("@/lib/matching", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/matching")>()),
  getSearchProfile: vi.fn(),
  putSearchProfile: vi.fn(),
  listMatches: vi.fn(),
  getMatch: vi.fn(),
  refreshMatches: vi.fn(),
}));

const m = {
  profile: vi.mocked(getSearchProfile),
  put: vi.mocked(putSearchProfile),
  list: vi.mocked(listMatches),
  get: vi.mocked(getMatch),
  refresh: vi.mocked(refreshMatches),
  parse: vi.mocked(parseFilters),
};

const sp = (filters: SearchFilters): SearchProfile => ({ filters, query_text: null, query_pending: false });

const NOW = Date.parse("2026-11-12T12:00:00Z");

async function tick(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

async function renderPage() {
  const view = render(<JobsPage />);
  await tick(0);
  return view;
}

async function renderWithOverview() {
  const refresh = vi.fn(async () => {});
  const state: OverviewState = { overview: null, status: "ok", refresh };
  const view = render(
    <OverviewContext.Provider value={state}>
      <JobsPage />
    </OverviewContext.Provider>,
  );
  await tick(0);
  return { ...view, refresh };
}

function currentStep() {
  const list = screen.getByRole("list", { name: "Etapas das vagas" });
  const items = within(list)
    .getAllByRole("listitem")
    .filter((li) => li.getAttribute("aria-current") === "step");
  return items.length === 1 ? items[0] : null;
}

const header = () => document.querySelector<HTMLElement>(".page-header__actions");

const rows = () => screen.getAllByRole("button", { name: /^Vaga \d/ });

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(NOW);
  m.profile.mockResolvedValue(sp(DEFAULT_FILTERS));
  m.put.mockImplementation(async (f, q) => ({ filters: f, query_text: q ?? null, query_pending: false }));
  m.get.mockImplementation(async (id) => matchDetail(id));
});

afterEach(() => {
  vi.useRealTimers();
  Object.values(m).forEach((fn) => fn.mockReset());
});

describe("JobsPage", () => {
  it("carregando: esqueleto com aria-busy", () => {
    m.profile.mockReturnValue(new Promise(() => {}));
    m.list.mockReturnValue(new Promise(() => {}));
    render(<JobsPage />);
    expect(screen.getByLabelText("Carregando vagas")).toHaveAttribute("aria-busy", "true");
  });

  it("lista ordenada com total e nota", async () => {
    m.list.mockResolvedValue(
      matchPage([matchItem(1, { ai_score: 88, state: "ok" }), matchItem(2)], { total: 2 }),
    );
    await renderPage();
    expect(m.list).toHaveBeenCalledWith({ status: "open", limit: 50, offset: 0 });
    expect(screen.getByText("2 vagas")).toBeInTheDocument();
    expect(rows()).toHaveLength(2);
    expect(screen.getByText("88")).toBeInTheDocument();
  });

  describe("estados vazios", () => {
    it("sem perfil confirmado", async () => {
      m.list.mockResolvedValue(matchPage([], { profile_version: null, vectorized: false, chunks_used: 0 }));
      await renderPage();
      expect(screen.getByText("Você ainda não confirmou um perfil.")).toBeInTheDocument();
      expect(screen.getByRole("link", { name: "Montar meu perfil" })).toHaveAttribute("href", "/onboarding");
      expect(screen.getByRole("button", { name: "Avaliar melhores vagas" })).toBeDisabled();
    });

    it("perfil vetorizando", async () => {
      m.list.mockResolvedValue(matchPage([], { profile_version: 2, vectorized: false, chunks_used: 0 }));
      await renderPage();
      expect(screen.getByText("Seu perfil ainda está sendo vetorizado.")).toBeInTheDocument();
      m.list.mockResolvedValue(matchPage([matchItem(1)]));
      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: "Verificar de novo" }));
      });
      expect(rows()).toHaveLength(1);
    });

    it("sem vagas (sem filtros), contando as que faltam vetor", async () => {
      m.list.mockResolvedValue(matchPage([], { missing_embeddings: 4 }));
      await renderPage();
      expect(screen.getByText("Nenhuma vaga ranqueada ainda.")).toBeInTheDocument();
      expect(screen.getByText(/4 vagas coletadas ainda esperam o vetor/)).toBeInTheDocument();
      expect(screen.getByRole("link", { name: "Cadastrar empresas" })).toHaveAttribute("href", "/sources");
    });

    it("sem resultado para os filtros", async () => {
      m.profile.mockResolvedValue(sp({ ...DEFAULT_FILTERS, work_models: ["onsite"] }));
      m.list.mockResolvedValue(matchPage([]));
      await renderPage();
      expect(screen.getByText("Nenhuma vaga passa nesses filtros.")).toBeInTheDocument();
    });

    it("erro de rede com Tentar de novo", async () => {
      m.list.mockRejectedValueOnce(new ApiError(null, "Não consegui falar com a API em http://x."));
      await renderPage();
      expect(screen.getByRole("alert")).toHaveTextContent("Não consegui falar com a API em http://x.");
      m.list.mockResolvedValue(matchPage([matchItem(1)]));
      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: "Tentar de novo" }));
      });
      expect(rows()).toHaveLength(1);
      expect(screen.queryByRole("alert")).toBeNull();
    });
  });

  describe("polling", () => {
    it("consulta a cada 10 s só enquanto houver pendente", async () => {
      m.list.mockResolvedValue(matchPage([matchItem(1, { state: "pendente" })]));
      await renderPage();
      expect(m.list).toHaveBeenCalledTimes(1);
      expect(screen.getByText(/A lista confere a cada 10 segundos/)).toBeInTheDocument();
      await tick(9_999);
      expect(m.list).toHaveBeenCalledTimes(1);
      m.list.mockResolvedValue(matchPage([matchItem(1, { state: "ok", ai_score: 70 })]));
      await tick(1);
      expect(m.list).toHaveBeenCalledTimes(2);
      expect(screen.getByText("70")).toBeInTheDocument();
      await tick(30_000);
      expect(m.list).toHaveBeenCalledTimes(2);
    });

    it("sem pendentes não consulta", async () => {
      m.list.mockResolvedValue(matchPage([matchItem(1)]));
      await renderPage();
      await tick(30_000);
      expect(m.list).toHaveBeenCalledTimes(1);
    });

    it("para ao desmontar", async () => {
      m.list.mockResolvedValue(matchPage([matchItem(1, { state: "pendente" })]));
      const { unmount } = await renderPage();
      unmount();
      await tick(30_000);
      expect(m.list).toHaveBeenCalledTimes(1);
    });
  });

  describe("filtros", () => {
    it("mudar um filtro salva o perfil de busca e recarrega (debounce 300 ms)", async () => {
      m.list.mockResolvedValue(matchPage([matchItem(1)]));
      await renderPage();
      fireEvent.click(screen.getByRole("checkbox", { name: "remoto" }));
      await tick(299);
      expect(m.put).not.toHaveBeenCalled();
      await tick(1);
      expect(m.put).toHaveBeenCalledWith({ ...DEFAULT_FILTERS, work_models: ["remote"] });
      expect(m.list).toHaveBeenCalledTimes(2);
    });

    it("resposta antiga é ignorada", async () => {
      m.list.mockResolvedValueOnce(matchPage([matchItem(1)]));
      await renderPage();
      let resolveOld!: (p: MatchPage) => void;
      m.list.mockReturnValueOnce(new Promise((r) => (resolveOld = r)));
      m.list.mockResolvedValueOnce(matchPage([matchItem(2, {}, { title: "Vaga 2 nova" })]));
      fireEvent.click(screen.getByRole("checkbox", { name: "remoto" }));
      await tick(300);
      fireEvent.click(screen.getByRole("checkbox", { name: "híbrido" }));
      await tick(300);
      expect(screen.getByText("Vaga 2 nova")).toBeInTheDocument();
      await act(async () => resolveOld(matchPage([matchItem(3, {}, { title: "Vaga 3 antiga" })])));
      expect(screen.queryByText("Vaga 3 antiga")).toBeNull();
      expect(screen.getByText("Vaga 2 nova")).toBeInTheDocument();
    });

    it("erro ao salvar o filtro aparece sem apagar a lista", async () => {
      m.list.mockResolvedValue(matchPage([matchItem(1)]));
      await renderPage();
      m.put.mockRejectedValueOnce(new ApiError(422, "Confira os filtros: países como BR ou PT."));
      fireEvent.change(screen.getByLabelText("Países"), { target: { value: "Narnia" } });
      await tick(300);
      expect(screen.getByRole("alert")).toHaveTextContent("Confira os filtros: países como BR ou PT.");
      expect(rows()).toHaveLength(1);
    });
  });

  describe("seleção e atalhos", () => {
    it("clique abre o painel lateral com a avaliação", async () => {
      m.list.mockResolvedValue(matchPage([matchItem(1), matchItem(2)]));
      await renderPage();
      await act(async () => {
        fireEvent.click(rows()[1]);
      });
      expect(m.get).toHaveBeenCalledWith(2);
      expect(screen.getByRole("heading", { name: "Vaga 2" })).toBeInTheDocument();
      expect(screen.getByText(/Python e Airflow/)).toBeInTheDocument();
    });

    it("p prepara a candidatura da vaga selecionada e abre a página dela", async () => {
      m.list.mockResolvedValue(matchPage([matchItem(1), matchItem(2)]));
      prepare.mockResolvedValue(33);
      await renderPage();
      await act(async () => {
        fireEvent.keyDown(document.body, { key: "j" });
      });
      await act(async () => {
        fireEvent.keyDown(document.body, { key: "p" });
      });
      expect(prepare).toHaveBeenCalledWith(1);
      expect(push).toHaveBeenCalledWith("/candidaturas/33");
    });

    it("j/k trocam a seleção; dentro de input não agem", async () => {
      m.list.mockResolvedValue(matchPage([matchItem(1), matchItem(2), matchItem(3)]));
      await renderPage();
      const current = () => rows().findIndex((r) => r.getAttribute("aria-current") === "true");
      expect(current()).toBe(-1);
      await act(async () => {
        fireEvent.keyDown(document.body, { key: "j" });
      });
      expect(current()).toBe(0);
      await act(async () => {
        fireEvent.keyDown(document.body, { key: "j" });
        fireEvent.keyDown(document.body, { key: "j" });
        fireEvent.keyDown(document.body, { key: "j" });
      });
      expect(current()).toBe(2);
      await act(async () => {
        fireEvent.keyDown(document.body, { key: "k" });
      });
      expect(current()).toBe(1);
      await act(async () => {
        fireEvent.keyDown(screen.getByLabelText("Países"), { key: "j" });
        fireEvent.keyDown(document.body, { key: "j", ctrlKey: true });
        fireEvent.keyDown(screen.getByLabelText("Países"), { key: "s" });
      });
      expect(current()).toBe(1);
      expect(m.put).not.toHaveBeenCalled();
      expect(create).not.toHaveBeenCalled();
    });

    it("s salva a vaga selecionada no pipeline e avisa com texto", async () => {
      m.list.mockResolvedValue(matchPage([matchItem(1), matchItem(2)]));
      create.mockResolvedValue({ id: 5 });
      await renderPage();
      await act(async () => {
        fireEvent.keyDown(document.body, { key: "s" });
      });
      expect(create).not.toHaveBeenCalled(); // nada selecionado
      await act(async () => {
        fireEvent.keyDown(document.body, { key: "j" });
      });
      await act(async () => {
        fireEvent.keyDown(document.body, { key: "s" });
      });
      expect(create).toHaveBeenCalledWith(1);
      await tick(0);
      expect(screen.getByText(/Vaga salva no pipeline\./)).toBeInTheDocument();
      expect(screen.getByRole("link", { name: "Ver pipeline" })).toHaveAttribute("href", "/pipeline");
      expect(push).not.toHaveBeenCalled();
    });

    it("erro ao salvar a vaga aparece como alerta", async () => {
      m.list.mockResolvedValue(matchPage([matchItem(1)]));
      create.mockRejectedValue(new ApiError(404, "Vaga não encontrada."));
      await renderPage();
      await act(async () => {
        fireEvent.keyDown(document.body, { key: "j" });
      });
      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: "Salvar no pipeline" }));
      });
      expect(screen.getByRole("alert")).toHaveTextContent("Vaga não encontrada.");
    });

    it("resposta antiga do detalhe é ignorada", async () => {
      m.list.mockResolvedValue(matchPage([matchItem(1), matchItem(2)]));
      await renderPage();
      let resolveOld!: (d: ReturnType<typeof matchDetail>) => void;
      m.get.mockReturnValueOnce(new Promise((r) => (resolveOld = r)));
      await act(async () => {
        fireEvent.click(rows()[0]);
      });
      await act(async () => {
        fireEvent.click(rows()[1]);
      });
      expect(screen.getByRole("heading", { name: "Vaga 2" })).toBeInTheDocument();
      await act(async () => resolveOld(matchDetail(1)));
      expect(screen.getByRole("heading", { name: "Vaga 2" })).toBeInTheDocument();
      expect(screen.queryByRole("heading", { name: "Vaga 1" })).toBeNull();
    });
  });

  describe("correções da revisão", () => {
    it("'salvando…' some mesmo se a lista for recarregada durante o PUT", async () => {
      m.list.mockResolvedValue(matchPage([matchItem(1, { state: "pendente" })]));
      await renderPage();
      m.list.mockRejectedValueOnce(new ApiError(null, "Sem resposta."));
      await tick(10_000);
      expect(screen.getByText("Perdemos contato com a API.")).toBeInTheDocument();
      let resolvePut!: (f: SearchProfile) => void;
      m.put.mockReturnValueOnce(new Promise((r) => (resolvePut = r)));
      fireEvent.click(screen.getByRole("checkbox", { name: "remoto" }));
      await tick(300);
      expect(screen.getByText("salvando…")).toBeInTheDocument();
      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: "Tentar de novo" }));
      });
      await act(async () => resolvePut(sp({ ...DEFAULT_FILTERS, work_models: ["remote"] })));
      expect(screen.queryByText("salvando…")).toBeNull();
      // O último salvamento recarrega a lista com os filtros novos.
      expect(m.list.mock.calls.length).toBeGreaterThanOrEqual(4);
    });

    it("Avaliar fica desativado com edição pendente ou salvamento em curso", async () => {
      m.list.mockResolvedValue(matchPage([matchItem(1)]));
      await renderPage();
      const evaluate = () => screen.getByRole("button", { name: "Avaliar melhores vagas" });
      expect(evaluate()).toBeEnabled();
      let resolvePut!: (f: SearchProfile) => void;
      m.put.mockReturnValueOnce(new Promise((r) => (resolvePut = r)));
      fireEvent.click(screen.getByRole("checkbox", { name: "remoto" }));
      await tick(0);
      expect(evaluate()).toBeDisabled();
      await tick(300);
      expect(evaluate()).toBeDisabled();
      await act(async () => resolvePut(sp({ ...DEFAULT_FILTERS, work_models: ["remote"] })));
      expect(evaluate()).toBeEnabled();
    });

    it("409 de filtros salvos inválidos: restaura os filtros padrão", async () => {
      m.profile.mockRejectedValueOnce(
        new ApiError(409, "Os filtros salvos são inválidos. Salve-os novamente."),
      );
      m.list.mockResolvedValue(matchPage([matchItem(1)]));
      await renderPage();
      expect(screen.getByRole("alert")).toHaveTextContent("Os filtros salvos são inválidos.");
      expect(screen.queryByText(/docker compose up -d api/)).toBeNull();
      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: "Restaurar filtros padrão" }));
      });
      expect(m.put).toHaveBeenCalledWith(DEFAULT_FILTERS);
      await tick(0);
      expect(rows()).toHaveLength(1);
    });

    it("erro de rede mostra a dica do docker; HTTP 500 não", async () => {
      m.list.mockRejectedValueOnce(new ApiError(null, "Não consegui falar com a API."));
      const { unmount } = await renderPage();
      expect(screen.getByText(/docker compose up -d api/)).toBeInTheDocument();
      unmount();
      m.list.mockRejectedValueOnce(new ApiError(500, "A API respondeu com erro (HTTP 500)."));
      await renderPage();
      expect(screen.getByRole("alert")).toHaveTextContent("HTTP 500");
      expect(screen.queryByText(/docker compose up -d api/)).toBeNull();
    });

    it("polling não descarta as linhas carregadas com Mais vagas", async () => {
      const first = Array.from({ length: 50 }, (_, i) => matchItem(i + 1, i === 0 ? { state: "pendente" } : {}));
      const second = Array.from({ length: 50 }, (_, i) => matchItem(i + 51));
      const third = Array.from({ length: 20 }, (_, i) => matchItem(i + 101));
      m.list.mockResolvedValueOnce(matchPage(first, { total: 120 }));
      await renderPage();
      m.list.mockResolvedValueOnce(matchPage(second, { total: 120 }));
      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: "Mais vagas" }));
      });
      m.list.mockResolvedValueOnce(matchPage(third, { total: 120 }));
      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: "Mais vagas" }));
      });
      expect(rows()).toHaveLength(120);
      const polled = [...first, ...second].map((it, i) => (i === 0 ? { ...it, state: "ok", ai_score: 95 } : it));
      m.list.mockResolvedValueOnce(matchPage(polled, { total: 120 }));
      await tick(10_000);
      expect(m.list).toHaveBeenLastCalledWith({ status: "open", limit: 100, offset: 0 });
      expect(rows()).toHaveLength(120);
      expect(screen.getByText("95")).toBeInTheDocument();
      expect(screen.getByText("Vaga 120")).toBeInTheDocument();
    });

    it("Escape fecha o painel de detalhes", async () => {
      m.list.mockResolvedValue(matchPage([matchItem(1), matchItem(2)]));
      await renderPage();
      await act(async () => {
        fireEvent.click(rows()[1]);
      });
      expect(screen.getByRole("heading", { name: "Vaga 2" })).toBeInTheDocument();
      await act(async () => {
        fireEvent.keyDown(document.body, { key: "Escape" });
      });
      expect(screen.queryByRole("heading", { name: "Vaga 2" })).toBeNull();
      expect(rows()[1]).not.toHaveAttribute("aria-current");
      expect(rows()[1]).toHaveFocus();
    });
  });

  describe("Avaliar melhores vagas", () => {
    it("enfileira, recarrega e passa a consultar", async () => {
      m.list.mockResolvedValue(matchPage([matchItem(1)]));
      await renderPage();
      m.refresh.mockResolvedValue({ requested: 30, created: 28, reset: 0, already_ok: 2, profile_version: 3 });
      m.list.mockResolvedValue(matchPage([matchItem(1, { state: "pendente" })]));
      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: "Avaliar melhores vagas" }));
      });
      expect(m.refresh).toHaveBeenCalledTimes(1);
      expect(screen.getByText("28 vagas na fila da IA; 2 já tinham nota.")).toBeInTheDocument();
      expect(m.list).toHaveBeenCalledTimes(2);
      await tick(10_000);
      expect(m.list).toHaveBeenCalledTimes(3);
    });

    it("429 mostra a mensagem do servidor", async () => {
      m.list.mockResolvedValue(matchPage([matchItem(1)]));
      await renderPage();
      m.refresh.mockRejectedValue(
        new ApiError(429, "Aguarde alguns segundos antes de avaliar as vagas novamente."),
      );
      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: "Avaliar melhores vagas" }));
      });
      expect(screen.getByRole("alert")).toHaveTextContent("Aguarde alguns segundos");
    });
  });

  describe("cabeçalho e trilha", () => {
    it("um único h1, também enquanto carrega", () => {
      m.profile.mockReturnValue(new Promise(() => {}));
      m.list.mockReturnValue(new Promise(() => {}));
      render(<JobsPage />);
      expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
      expect(screen.getByRole("heading", { level: 1, name: "Vagas" })).toBeInTheDocument();
    });

    it("Avaliar melhores vagas existe uma vez só, no cabeçalho, e dispara uma requisição por clique", async () => {
      m.list.mockResolvedValue(matchPage([matchItem(1)]));
      await renderPage();
      expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
      const buttons = screen.getAllByRole("button", { name: "Avaliar melhores vagas" });
      expect(buttons).toHaveLength(1);
      expect(buttons[0].closest(".page-header__actions")).not.toBeNull();
      let resolve!: () => void;
      m.refresh.mockReturnValueOnce(
        new Promise((r) => {
          resolve = () => r({ requested: 30, created: 30, reset: 0, already_ok: 0, profile_version: 3 });
        }),
      );
      fireEvent.click(buttons[0]);
      // Enquanto avalia: desativado (mesma regra de antes), um segundo clique não repete.
      expect(buttons[0]).toBeDisabled();
      expect(buttons[0]).toHaveAttribute("aria-busy", "true");
      fireEvent.click(buttons[0]);
      await act(async () => resolve());
      expect(m.refresh).toHaveBeenCalledTimes(1);
      expect(screen.getByText("30 vagas na fila da IA.")).toBeInTheDocument();
    });

    it("Filtros abre o painel de filtros recolhido e foca o botão dele", async () => {
      m.list.mockResolvedValue(matchPage([matchItem(1)]));
      await renderPage();
      const toggle = screen.getByRole("button", { name: /^Filtros ·/ });
      fireEvent.click(toggle);
      expect(toggle).toHaveAttribute("aria-expanded", "false");
      fireEvent.click(within(header()!).getByRole("button", { name: "Filtros" }));
      expect(toggle).toHaveAttribute("aria-expanded", "true");
      expect(toggle).toHaveFocus();
      // Já aberto: continua aberto (não alterna).
      fireEvent.click(within(header()!).getByRole("button", { name: "Filtros" }));
      expect(toggle).toHaveAttribute("aria-expanded", "true");
    });

    it("sem perfil: sem Filtros, trilha bloqueada e um botão para /onboarding", async () => {
      m.list.mockResolvedValue(matchPage([], { profile_version: null, vectorized: false, chunks_used: 0 }));
      await renderPage();
      expect(within(header()!).queryByRole("button", { name: "Filtros" })).toBeNull();
      expect(currentStep()).toBeNull();
      expect(screen.getByRole("list", { name: "Etapas das vagas" })).toHaveTextContent(/Filtrar.*bloqueado/);
      const empty = document.querySelector<HTMLElement>(".matches__empty")!;
      const actions = [...within(empty).queryAllByRole("button"), ...within(empty).queryAllByRole("link")];
      expect(actions).toHaveLength(1);
      expect(actions[0]).toHaveAttribute("href", "/onboarding");
    });

    it("sem vagas: passo Filtrar e um botão para /sources", async () => {
      m.list.mockResolvedValue(matchPage([]));
      await renderPage();
      expect(currentStep()).toHaveTextContent("Filtrar");
      const empty = document.querySelector<HTMLElement>(".matches__empty")!;
      const actions = [...within(empty).queryAllByRole("button"), ...within(empty).queryAllByRole("link")];
      expect(actions).toHaveLength(1);
      expect(actions[0]).toHaveAttribute("href", "/sources");
    });

    it("trilha: Avaliar sem nota da IA, Ler com alguma nota", async () => {
      m.list.mockResolvedValue(matchPage([matchItem(1)]));
      const view = await renderPage();
      expect(currentStep()).toHaveTextContent("Avaliar");
      view.unmount();
      m.list.mockResolvedValue(matchPage([matchItem(1, { ai_score: 88, state: "ok" })]));
      await renderPage();
      expect(currentStep()).toHaveTextContent("Ler");
    });

    it("avaliar atualiza o resumo da casca (e de novo quando a fila esvazia)", async () => {
      m.list.mockResolvedValue(matchPage([matchItem(1)]));
      const { refresh } = await renderWithOverview();
      expect(refresh).not.toHaveBeenCalled();
      m.refresh.mockResolvedValue({ requested: 1, created: 1, reset: 0, already_ok: 0, profile_version: 3 });
      m.list.mockResolvedValue(matchPage([matchItem(1, { state: "pendente" })]));
      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: "Avaliar melhores vagas" }));
      });
      expect(m.refresh).toHaveBeenCalledTimes(1);
      expect(refresh).toHaveBeenCalledTimes(1);
      m.list.mockResolvedValue(matchPage([matchItem(1, { ai_score: 90, state: "ok" })]));
      await tick(10_000);
      expect(refresh).toHaveBeenCalledTimes(2);
    });

    it("avaliar com erro não atualiza o resumo", async () => {
      m.list.mockResolvedValue(matchPage([matchItem(1)]));
      const { refresh } = await renderWithOverview();
      m.refresh.mockRejectedValue(new ApiError(429, "Aguarde alguns segundos."));
      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: "Avaliar melhores vagas" }));
      });
      expect(refresh).not.toHaveBeenCalled();
    });
  });

  describe("chat e chips", () => {
    const chat = () => screen.getByRole("textbox", { name: "Descreva o que procura" });
    const proposal = () => screen.queryByRole("region", { name: "Proposta de filtros" });

    async function askAndApply(text: string) {
      fireEvent.change(chat(), { target: { value: text } });
      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: "Aplicar" }));
      });
      await act(async () => {
        fireEvent.click(within(proposal()!).getByRole("button", { name: "Aplicar" }));
      });
    }

    it("a barra de chat fica acima dos chips, que ficam acima do painel manual", async () => {
      m.profile.mockResolvedValue(sp({ ...DEFAULT_FILTERS, countries: ["BR"] }));
      m.list.mockResolvedValue(matchPage([matchItem(1)]));
      await renderPage();
      const chatEl = chat();
      const chip = screen.getByRole("button", { name: "Remover filtro BR" });
      const panel = screen.getByLabelText("Países");
      expect(chatEl.compareDocumentPosition(chip) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
      expect(chip.compareDocumentPosition(panel) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    });

    it("aplicar a proposta salva uma vez a fusão e o painel manual mostra os países novos", async () => {
      m.profile.mockResolvedValue(sp({ ...DEFAULT_FILTERS, countries: ["BR"] }));
      m.list.mockResolvedValue(matchPage([matchItem(1)]));
      m.parse.mockResolvedValue(
        parsedQuery({ countries: ["PT"], exclude_seniorities: ["intern"] }, { query_text: "dados" }),
      );
      await renderPage();
      await askAndApply("Portugal, sem estágio, algo com dados");
      expect(m.parse).toHaveBeenCalledWith("Portugal, sem estágio, algo com dados");
      expect(m.put).toHaveBeenCalledTimes(1);
      const merged = { ...DEFAULT_FILTERS, countries: ["BR", "PT"], exclude_seniorities: ["intern"] };
      expect(m.put).toHaveBeenCalledWith(merged, "dados");
      expect(screen.getByLabelText("Países")).toHaveValue("BR, PT");
      expect(screen.getByRole("button", { name: "Remover filtro sem estágio" })).toBeInTheDocument();
      expect(screen.getByText("gosto: dados")).toBeInTheDocument();
      expect(proposal()).toBeNull();
      expect(m.list).toHaveBeenCalledTimes(2);
      // Nenhum PUT extra vindo do painel manual.
      await tick(1000);
      expect(m.put).toHaveBeenCalledTimes(1);
    });

    it("edição manual depois do chat parte dos filtros novos (não reverte o chat)", async () => {
      m.list.mockResolvedValue(matchPage([matchItem(1)]));
      m.parse.mockResolvedValue(parsedQuery({ exclude_seniorities: ["intern"], title_exclude: ["plantão"] }));
      await renderPage();
      // Rascunho manual esperando o debounce quando o chat aplica: é descartado.
      fireEvent.change(screen.getByLabelText("Países"), { target: { value: "DE" } });
      await askAndApply("sem estágio, sem plantão");
      expect(screen.getByRole("button", { name: "Avaliar melhores vagas" })).toBeEnabled();
      await tick(1000);
      expect(m.put).toHaveBeenCalledTimes(1);
      fireEvent.click(screen.getByRole("checkbox", { name: "remoto" }));
      await tick(300);
      expect(m.put).toHaveBeenCalledTimes(2);
      expect(m.put).toHaveBeenLastCalledWith({
        ...DEFAULT_FILTERS,
        work_models: ["remote"],
        exclude_seniorities: ["intern"],
        title_exclude: ["plantão"],
      });
    });

    it("remover o chip BR atualiza o campo Países do painel", async () => {
      m.profile.mockResolvedValue(sp({ ...DEFAULT_FILTERS, countries: ["BR", "PT"] }));
      m.list.mockResolvedValue(matchPage([matchItem(1)]));
      await renderPage();
      expect(screen.getByLabelText("Países")).toHaveValue("BR, PT");
      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: "Remover filtro BR" }));
      });
      expect(m.put).toHaveBeenCalledTimes(1);
      expect(m.put).toHaveBeenCalledWith({ ...DEFAULT_FILTERS, countries: ["PT"] }, null);
      expect(screen.getByLabelText("Países")).toHaveValue("PT");
      expect(screen.queryByRole("button", { name: "Remover filtro BR" })).toBeNull();
    });

    it("editar o chip da consulta salva o texto novo pelo mesmo caminho", async () => {
      m.profile.mockResolvedValue({ filters: DEFAULT_FILTERS, query_text: "dados", query_pending: false });
      m.list.mockResolvedValue(matchPage([matchItem(1)], { query_used: true }));
      await renderPage();
      fireEvent.click(screen.getByRole("button", { name: /^gosto: dados/ }));
      const input = screen.getByRole("textbox", { name: "Editar consulta" });
      fireEvent.change(input, { target: { value: "produto" } });
      await act(async () => {
        fireEvent.keyDown(input, { key: "Enter" });
      });
      expect(m.put).toHaveBeenCalledTimes(1);
      expect(m.put).toHaveBeenCalledWith(DEFAULT_FILTERS, "produto");
      expect(screen.getByText("gosto: produto")).toBeInTheDocument();
    });

    it("consulta rejeitada pelo servidor: mostra o motivo e mantém o editor aberto com o texto", async () => {
      m.profile.mockResolvedValue({ filters: DEFAULT_FILTERS, query_text: "dados", query_pending: false });
      m.list.mockResolvedValue(matchPage([matchItem(1)], { query_used: true }));
      m.put.mockRejectedValueOnce(
        new ApiError(422, "A consulta de gosto não pode ter negações; use os filtros de exclusão."),
      );
      await renderPage();
      fireEvent.click(screen.getByRole("button", { name: /^gosto: dados/ }));
      const input = screen.getByRole("textbox", { name: "Editar consulta" });
      fireEvent.change(input, { target: { value: "foco no cliente" } });
      await act(async () => {
        fireEvent.keyDown(input, { key: "Enter" });
      });
      expect(screen.getByRole("alert")).toHaveTextContent("não pode ter negações");
      expect(screen.getByRole("textbox", { name: "Editar consulta" })).toHaveValue("foco no cliente");
    });

    it("falha do parseFilters não salva nada e mantém os chips", async () => {
      m.profile.mockResolvedValue(sp({ ...DEFAULT_FILTERS, countries: ["BR"] }));
      m.list.mockResolvedValue(matchPage([matchItem(1)]));
      m.parse.mockRejectedValue(new ApiError(503, "O interpretador está ocupado; tente em instantes."));
      await renderPage();
      fireEvent.change(chat(), { target: { value: "remoto" } });
      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: "Aplicar" }));
      });
      expect(screen.getByRole("alert")).toHaveTextContent("tente em instantes");
      expect(screen.getByRole("button", { name: "Tentar de novo" })).toBeInTheDocument();
      expect(m.put).not.toHaveBeenCalled();
      expect(screen.getByRole("button", { name: "Remover filtro BR" })).toBeInTheDocument();
      expect(screen.getByLabelText("Países")).toHaveValue("BR");
    });

    it("erro ao salvar a proposta mantém a proposta e mostra o erro", async () => {
      m.list.mockResolvedValue(matchPage([matchItem(1)]));
      m.parse.mockResolvedValue(parsedQuery({ countries: ["PT"] }));
      m.put.mockRejectedValueOnce(new ApiError(422, "Confira os filtros."));
      await renderPage();
      await askAndApply("Portugal");
      expect(screen.getByRole("alert")).toHaveTextContent("Confira os filtros.");
      expect(proposal()).not.toBeNull();
      expect(screen.getByLabelText("Países")).toHaveValue("");
    });

    it("consulta pendente: selo e releitura do perfil até o SERVIDOR dizer que ficou pronta", async () => {
      // query_used fica false para sempre (ex.: peso da consulta 0): não pode prender o polling.
      m.list.mockResolvedValue(matchPage([matchItem(1)]));
      m.parse.mockResolvedValue(parsedQuery({}, { query_text: "dados" }));
      m.put.mockImplementation(async (f, q) => ({ filters: f, query_text: q ?? null, query_pending: true }));
      await renderPage();
      await askAndApply("algo com dados");
      expect(screen.getByText("consulta semântica pendente")).toBeInTheDocument();
      expect(m.profile).toHaveBeenCalledTimes(1);
      expect(m.list).toHaveBeenCalledTimes(2);
      m.profile.mockResolvedValue({ filters: DEFAULT_FILTERS, query_text: "dados", query_pending: true });
      await tick(10_000);
      expect(m.profile).toHaveBeenCalledTimes(2);
      expect(screen.getByText("consulta semântica pendente")).toBeInTheDocument();
      m.profile.mockResolvedValue({ filters: DEFAULT_FILTERS, query_text: "dados", query_pending: false });
      await tick(10_000);
      expect(m.profile).toHaveBeenCalledTimes(3);
      expect(screen.queryByText("consulta semântica pendente")).toBeNull();
      // A lista é relida uma vez para usar a consulta; depois nada mais.
      expect(m.list).toHaveBeenCalledTimes(3);
      await tick(60_000);
      expect(m.profile).toHaveBeenCalledTimes(3);
      expect(m.list).toHaveBeenCalledTimes(3);
    });

    it("query_used na lista também tira o selo, sem polling", async () => {
      m.profile.mockResolvedValue({ filters: DEFAULT_FILTERS, query_text: "dados", query_pending: true });
      m.list.mockResolvedValue(matchPage([matchItem(1)], { query_used: true }));
      await renderPage();
      expect(screen.queryByText("consulta semântica pendente")).toBeNull();
      await tick(60_000);
      expect(m.profile).toHaveBeenCalledTimes(1);
      expect(m.list).toHaveBeenCalledTimes(1);
    });

    it("perfil nunca vetorizado: sem selo e sem polling da consulta", async () => {
      m.profile.mockResolvedValue({ filters: DEFAULT_FILTERS, query_text: "dados", query_pending: true });
      m.list.mockResolvedValue(matchPage([], { profile_version: 2, vectorized: false, chunks_used: 0 }));
      await renderPage();
      expect(screen.getByText("Seu perfil ainda está sendo vetorizado.")).toBeInTheDocument();
      expect(screen.queryByText("consulta semântica pendente")).toBeNull();
      await tick(120_000);
      expect(m.profile).toHaveBeenCalledTimes(1);
      expect(m.list).toHaveBeenCalledTimes(1);
    });

    it("aba oculta pausa os dois pollings; ao voltar, retomam", async () => {
      let state: DocumentVisibilityState = "visible";
      Object.defineProperty(document, "visibilityState", { configurable: true, get: () => state });
      const setVisibility = (next: DocumentVisibilityState) =>
        act(() => {
          state = next;
          document.dispatchEvent(new Event("visibilitychange"));
        });
      try {
        m.profile.mockResolvedValue({ filters: DEFAULT_FILTERS, query_text: "dados", query_pending: true });
        m.list.mockResolvedValue(matchPage([matchItem(1, { state: "pendente" })]));
        const { unmount } = await renderPage();
        setVisibility("hidden");
        await tick(60_000);
        expect(m.list).toHaveBeenCalledTimes(1);
        expect(m.profile).toHaveBeenCalledTimes(1);
        setVisibility("visible");
        await tick(10_000);
        expect(m.list).toHaveBeenCalledTimes(2);
        expect(m.profile).toHaveBeenCalledTimes(2);
        // Sem intervalos duplicados: mais 10 s, uma consulta de cada.
        await tick(10_000);
        expect(m.list).toHaveBeenCalledTimes(3);
        expect(m.profile).toHaveBeenCalledTimes(3);
        unmount();
        await tick(60_000);
        expect(m.list).toHaveBeenCalledTimes(3);
        expect(m.profile).toHaveBeenCalledTimes(3);
      } finally {
        delete (document as { visibilityState?: unknown }).visibilityState;
      }
    });

    describe("ações seguidas não se perdem", () => {
      function deferred<T>() {
        let resolve!: (v: T) => void;
        const promise = new Promise<T>((r) => (resolve = r));
        return { promise, resolve };
      }

      it("duas remoções seguidas valem as duas; a resposta lenta do PUT antigo não volta o BR", async () => {
        m.profile.mockResolvedValue(sp({ ...DEFAULT_FILTERS, countries: ["BR", "PT"] }));
        m.list.mockResolvedValue(matchPage([matchItem(1)]));
        const first = deferred<SearchProfile>();
        m.put.mockReturnValueOnce(first.promise);
        await renderPage();
        fireEvent.click(screen.getByRole("button", { name: "Remover filtro BR" }));
        fireEvent.click(screen.getByRole("button", { name: "Remover filtro PT" }));
        expect(m.put).toHaveBeenCalledTimes(2);
        expect(m.put).toHaveBeenNthCalledWith(1, { ...DEFAULT_FILTERS, countries: ["PT"] }, null);
        expect(m.put).toHaveBeenNthCalledWith(2, { ...DEFAULT_FILTERS, countries: [] }, null);
        await tick(0);
        await act(async () => first.resolve(sp({ ...DEFAULT_FILTERS, countries: ["PT"] })));
        expect(screen.queryByRole("button", { name: "Remover filtro BR" })).toBeNull();
        expect(screen.queryByRole("button", { name: "Remover filtro PT" })).toBeNull();
        expect(screen.getByLabelText("Países")).toHaveValue("");
        expect(screen.queryByText("salvando…")).toBeNull();
      });

      it("× de chip durante o salvamento do painel inclui a edição manual e não é revertido", async () => {
        m.profile.mockResolvedValue(sp({ ...DEFAULT_FILTERS, countries: ["BR"] }));
        m.list.mockResolvedValue(matchPage([matchItem(1)]));
        const manual = deferred<SearchProfile>();
        m.put.mockReturnValueOnce(manual.promise);
        await renderPage();
        fireEvent.click(screen.getByRole("checkbox", { name: "remoto" }));
        await tick(300);
        expect(m.put).toHaveBeenCalledWith({ ...DEFAULT_FILTERS, countries: ["BR"], work_models: ["remote"] });
        await act(async () => {
          fireEvent.click(screen.getByRole("button", { name: "Remover filtro BR" }));
        });
        expect(m.put).toHaveBeenLastCalledWith({ ...DEFAULT_FILTERS, work_models: ["remote"] }, null);
        await act(async () => manual.resolve(sp({ ...DEFAULT_FILTERS, countries: ["BR"], work_models: ["remote"] })));
        expect(screen.queryByRole("button", { name: "Remover filtro BR" })).toBeNull();
        expect(screen.getByLabelText("Países")).toHaveValue("");
        expect(screen.getByRole("checkbox", { name: "remoto" })).toBeChecked();
        await tick(1000);
        expect(m.put).toHaveBeenCalledTimes(2);
      });

      it("× de chip com edição do painel esperando o debounce: o rascunho velho não reverte o chip", async () => {
        m.profile.mockResolvedValue(sp({ ...DEFAULT_FILTERS, countries: ["BR", "PT"] }));
        m.list.mockResolvedValue(matchPage([matchItem(1)]));
        await renderPage();
        fireEvent.change(screen.getByLabelText("Países"), { target: { value: "BR, PT, DE" } });
        await act(async () => {
          fireEvent.click(screen.getByRole("button", { name: "Remover filtro BR" }));
        });
        await tick(1000);
        expect(m.put).toHaveBeenCalledTimes(1);
        expect(m.put).toHaveBeenCalledWith({ ...DEFAULT_FILTERS, countries: ["PT"] }, null);
        expect(screen.getByLabelText("Países")).toHaveValue("PT");
        expect(screen.queryByRole("button", { name: "Remover filtro BR" })).toBeNull();
      });

      it("Aplicar do chat durante um salvamento manual em voo inclui a mudança manual na fusão", async () => {
        m.list.mockResolvedValue(matchPage([matchItem(1)]));
        m.parse.mockResolvedValue(parsedQuery({ countries: ["PT"] }));
        const manual = deferred<SearchProfile>();
        m.put.mockReturnValueOnce(manual.promise);
        await renderPage();
        fireEvent.click(screen.getByRole("checkbox", { name: "remoto" }));
        await tick(300);
        expect(m.put).toHaveBeenCalledTimes(1);
        await askAndApply("Portugal");
        expect(m.put).toHaveBeenCalledTimes(2);
        expect(m.put).toHaveBeenLastCalledWith({ ...DEFAULT_FILTERS, work_models: ["remote"], countries: ["PT"] }, null);
        await act(async () => manual.resolve(sp({ ...DEFAULT_FILTERS, work_models: ["remote"] })));
        expect(screen.getByLabelText("Países")).toHaveValue("PT");
        expect(screen.getByRole("checkbox", { name: "remoto" })).toBeChecked();
        expect(proposal()).toBeNull();
      });

      it("aplicação substituída por um salvamento que falha não limpa a proposta", async () => {
        m.profile.mockResolvedValue(sp({ ...DEFAULT_FILTERS, countries: ["BR"] }));
        m.list.mockResolvedValue(matchPage([matchItem(1)]));
        m.parse.mockResolvedValue(parsedQuery({ countries: ["PT"] }));
        const chatPut = deferred<SearchProfile>();
        m.put.mockReturnValueOnce(chatPut.promise).mockRejectedValueOnce(new ApiError(422, "Confira os filtros."));
        await renderPage();
        await askAndApply("Portugal");
        // Enquanto a proposta grava, o envio de outra frase fica desligado.
        expect(chat()).toBeDisabled();
        await act(async () => {
          fireEvent.click(screen.getByRole("button", { name: "Remover filtro BR" }));
        });
        await act(async () => chatPut.resolve(sp({ ...DEFAULT_FILTERS, countries: ["BR", "PT"] })));
        expect(proposal()).not.toBeNull();
        expect(screen.getByRole("alert")).toHaveTextContent("Confira os filtros.");
        // Volta ao último estado confirmado pelo servidor.
        expect(screen.getByRole("button", { name: "Remover filtro BR" })).toBeInTheDocument();
        expect(screen.getByLabelText("Países")).toHaveValue("BR");
      });
    });

    it("remover o último chip leva o foco ao campo do chat", async () => {
      m.profile.mockResolvedValue(sp({ ...DEFAULT_FILTERS, countries: ["BR"] }));
      m.list.mockResolvedValue(matchPage([matchItem(1)]));
      await renderPage();
      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: "Remover filtro BR" }));
      });
      expect(chat()).toHaveFocus();
    });

    it("Testar qualidade no cabeçalho leva a /jobs/qualidade", async () => {
      m.list.mockResolvedValue(matchPage([matchItem(1)]));
      await renderPage();
      expect(within(header()!).getByRole("link", { name: "Testar qualidade" })).toHaveAttribute(
        "href",
        "/jobs/qualidade",
      );
    });
  });
});
