import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { ApiError } from "@/lib/profile";
import {
  DEFAULT_FILTERS,
  getMatch,
  getSearchProfile,
  listMatches,
  putSearchProfile,
  refreshMatches,
  type MatchPage,
} from "@/lib/matching";
import { matchDetail, matchItem, matchPage } from "@/components/jobs/fixtures";
import JobsPage from "./page";

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
};

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

const rows = () => screen.getAllByRole("button", { name: /^Vaga \d/ });

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(NOW);
  m.profile.mockResolvedValue(DEFAULT_FILTERS);
  m.put.mockImplementation(async (f) => f);
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
      m.profile.mockResolvedValue({ ...DEFAULT_FILTERS, work_models: ["onsite"] });
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

    it("j/k trocam a seleção; dentro de input não agem; s e p não fazem nada", async () => {
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
        fireEvent.keyDown(document.body, { key: "s" });
        fireEvent.keyDown(document.body, { key: "p" });
      });
      expect(current()).toBe(1);
      expect(m.put).not.toHaveBeenCalled();
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
      let resolvePut!: (f: typeof DEFAULT_FILTERS) => void;
      m.put.mockReturnValueOnce(new Promise((r) => (resolvePut = r)));
      fireEvent.click(screen.getByRole("checkbox", { name: "remoto" }));
      await tick(300);
      expect(screen.getByText("salvando…")).toBeInTheDocument();
      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: "Tentar de novo" }));
      });
      await act(async () => resolvePut({ ...DEFAULT_FILTERS, work_models: ["remote"] }));
      expect(screen.queryByText("salvando…")).toBeNull();
      // O último salvamento recarrega a lista com os filtros novos.
      expect(m.list.mock.calls.length).toBeGreaterThanOrEqual(4);
    });

    it("Avaliar fica desativado com edição pendente ou salvamento em curso", async () => {
      m.list.mockResolvedValue(matchPage([matchItem(1)]));
      await renderPage();
      const evaluate = () => screen.getByRole("button", { name: "Avaliar melhores vagas" });
      expect(evaluate()).toBeEnabled();
      let resolvePut!: (f: typeof DEFAULT_FILTERS) => void;
      m.put.mockReturnValueOnce(new Promise((r) => (resolvePut = r)));
      fireEvent.click(screen.getByRole("checkbox", { name: "remoto" }));
      await tick(0);
      expect(evaluate()).toBeDisabled();
      await tick(300);
      expect(evaluate()).toBeDisabled();
      await act(async () => resolvePut({ ...DEFAULT_FILTERS, work_models: ["remote"] }));
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
});
