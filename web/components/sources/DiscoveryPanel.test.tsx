import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { ApiError } from "@/lib/profile";
import {
  getDiscovery,
  listCandidates,
  putDiscovery,
  type Candidate,
  type DiscoveryState,
} from "@/lib/discovery";
import { DiscoveryPanel } from "./DiscoveryPanel";

vi.mock("@/lib/discovery", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/discovery")>()),
  getDiscovery: vi.fn(),
  putDiscovery: vi.fn(),
  listCandidates: vi.fn(),
}));

const m = {
  get: vi.mocked(getDiscovery),
  put: vi.mocked(putDiscovery),
  list: vi.mocked(listCandidates),
};

const base: DiscoveryState = {
  enabled: true,
  target_companies: ["Nubank", "Stone"],
  auto_companies: 3,
  max_auto: 30,
  pending: 0,
  found: 12,
};

const candidate = (i: number, over: Partial<Candidate> = {}): Candidate => ({
  token: `empresa${i}`,
  name_hint: `Empresa ${i}`,
  origin_kind: "catalog",
  state: "pending",
  attempts: 0,
  last_error: null,
  ...over,
});

async function tick(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

async function renderPanel(onCompaniesChanged = vi.fn()) {
  const view = render(<DiscoveryPanel onCompaniesChanged={onCompaniesChanged} />);
  await tick(0);
  return { ...view, onCompaniesChanged };
}

const textarea = () => screen.getByLabelText("Empresas que você quer acompanhar") as HTMLTextAreaElement;
const toggle = () => screen.getByRole("switch", { name: "Descobrir empresas automaticamente" });
const saveButton = () => screen.getByRole("button", { name: /^Salv/ });

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  Object.values(m).forEach((fn) => fn.mockReset());
});

describe("DiscoveryPanel", () => {
  it("carrega interruptor, contadores e a lista de empresas", async () => {
    m.get.mockResolvedValue({ ...base, pending: 34 });
    await renderPanel();
    expect(toggle()).toBeChecked();
    expect(screen.getByText("12 empresas encontradas · 34 na fila")).toBeInTheDocument();
    expect(screen.queryByText(/em validação/)).toBeNull();
    expect(textarea().value).toBe("Nubank\nStone");
    expect(screen.getByText(/3 de 30/)).toBeInTheDocument();
  });

  it("erro na carga com Tentar de novo", async () => {
    m.get.mockRejectedValueOnce(new ApiError(null, "Não consegui falar com a API em x."));
    await renderPanel();
    expect(screen.getByRole("alert")).toHaveTextContent("Não consegui falar com a API em x.");
    m.get.mockResolvedValueOnce(base);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Tentar de novo" }));
    });
    expect(toggle()).toBeChecked();
  });

  it("interruptor salva na hora, com a lista já salva (não o rascunho)", async () => {
    m.get.mockResolvedValue(base);
    m.put.mockResolvedValue({ ...base, enabled: false });
    await renderPanel();
    fireEvent.change(textarea(), { target: { value: "Rascunho não salvo" } });
    await act(async () => {
      fireEvent.click(toggle());
    });
    expect(m.put).toHaveBeenCalledTimes(1);
    expect(m.put).toHaveBeenCalledWith({ enabled: false, target_companies: ["Nubank", "Stone"] });
    expect(toggle()).not.toBeChecked();
    // O rascunho continua no campo.
    expect(textarea().value).toBe("Rascunho não salvo");
  });

  it("interruptor volta atrás quando o servidor recusa", async () => {
    m.get.mockResolvedValue(base);
    let reject!: (e: unknown) => void;
    m.put.mockReturnValue(new Promise((_, r) => (reject = r)));
    await renderPanel();
    fireEvent.click(toggle());
    // Otimista enquanto salva, e sem cliques duplicados.
    expect(toggle()).not.toBeChecked();
    expect(toggle()).toBeDisabled();
    await act(async () => reject(new ApiError(503, "Banco indisponível.")));
    expect(toggle()).toBeChecked();
    expect(screen.getByRole("alert")).toHaveTextContent("Banco indisponível.");
  });

  it("Salvar manda a lista sem repetidos e mostra salvo", async () => {
    m.get.mockResolvedValue(base);
    let resolve!: (s: DiscoveryState) => void;
    m.put.mockReturnValue(new Promise((r) => (resolve = r)));
    await renderPanel();
    fireEvent.change(textarea(), { target: { value: "Nubank\n nubank \n\nGitLab" } });
    fireEvent.click(saveButton());
    expect(m.put).toHaveBeenCalledWith({ enabled: true, target_companies: ["Nubank", "GitLab"] });
    expect(saveButton()).toHaveTextContent("Salvando…");
    expect(saveButton()).toBeDisabled();
    await act(async () => resolve({ ...base, target_companies: ["Nubank", "GitLab"] }));
    expect(screen.getByRole("status")).toHaveTextContent("Lista salva.");
    expect(textarea().value).toBe("Nubank\nGitLab");
    // Editar de novo limpa o "salvo".
    fireEvent.change(textarea(), { target: { value: "Nubank" } });
    expect(screen.queryByText("Lista salva.")).toBeNull();
  });

  it("Salvar com erro do servidor mostra o detail", async () => {
    m.get.mockResolvedValue(base);
    m.put.mockRejectedValue(new ApiError(422, "Nome de empresa contém caracteres inválidos."));
    await renderPanel();
    await act(async () => {
      fireEvent.click(saveButton());
    });
    expect(screen.getByRole("alert")).toHaveTextContent("Nome de empresa contém caracteres inválidos.");
    expect(saveButton()).toBeEnabled();
  });

  it("mais de 50 nomes: mensagem clara e nada é enviado", async () => {
    m.get.mockResolvedValue(base);
    await renderPanel();
    const names = Array.from({ length: 52 }, (_, i) => `Empresa ${i}`).join("\n");
    fireEvent.change(textarea(), { target: { value: names } });
    fireEvent.click(saveButton());
    expect(m.put).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent("São 52 empresas; o limite é 50. Remova 2.");
    expect(textarea()).toHaveAttribute("aria-invalid", "true");
  });

  it("nome com mais de 80 caracteres: mensagem clara e nada é enviado", async () => {
    m.get.mockResolvedValue(base);
    await renderPanel();
    fireEvent.change(textarea(), { target: { value: `Nubank\n${"X".repeat(90)}` } });
    fireEvent.click(saveButton());
    expect(m.put).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent(/tem 90 caracteres; o limite é 80/);
  });

  it("sem pendentes: não consulta de novo", async () => {
    m.get.mockResolvedValue(base);
    await renderPanel();
    await tick(60_000);
    expect(m.get).toHaveBeenCalledTimes(1);
  });

  it("com pendentes: consulta a cada 10 s e para quando a fila esvazia", async () => {
    m.get
      .mockResolvedValueOnce({ ...base, pending: 5 })
      .mockResolvedValueOnce({ ...base, pending: 2, found: 13 })
      .mockResolvedValue({ ...base, pending: 0, found: 15 });
    await renderPanel();
    await tick(9_999);
    expect(m.get).toHaveBeenCalledTimes(1);
    await tick(1);
    expect(m.get).toHaveBeenCalledTimes(2);
    expect(screen.getByText("13 empresas encontradas · 2 na fila")).toBeInTheDocument();
    await tick(10_000);
    expect(m.get).toHaveBeenCalledTimes(3);
    expect(screen.getByText("15 empresas encontradas · 0 na fila")).toBeInTheDocument();
    await tick(60_000);
    expect(m.get).toHaveBeenCalledTimes(3);
  });

  it("polling não apaga o rascunho do campo", async () => {
    m.get
      .mockResolvedValueOnce({ ...base, pending: 5 })
      .mockResolvedValue({ ...base, pending: 4, target_companies: ["Outra"] });
    await renderPanel();
    fireEvent.change(textarea(), { target: { value: "Digitando" } });
    await tick(10_000);
    expect(textarea().value).toBe("Digitando");
  });

  it("desmontar limpa o timer e não consulta mais", async () => {
    m.get.mockResolvedValue({ ...base, pending: 5 });
    const { unmount } = await renderPanel();
    unmount();
    expect(vi.getTimerCount()).toBe(0);
    await tick(60_000);
    expect(m.get).toHaveBeenCalledTimes(1);
  });

  it("desmontar com consulta em voo não atualiza estado (sem avisos)", async () => {
    m.get.mockResolvedValueOnce({ ...base, pending: 5 });
    const { unmount } = await renderPanel();
    let resolve!: (s: DiscoveryState) => void;
    m.get.mockReturnValueOnce(new Promise((r) => (resolve = r)));
    await tick(10_000);
    unmount();
    const errors = vi.spyOn(console, "error");
    await act(async () => resolve({ ...base, pending: 5 }));
    expect(errors).not.toHaveBeenCalled();
    errors.mockRestore();
  });

  it("resposta antiga do polling não desfaz o interruptor salvo depois", async () => {
    m.get.mockResolvedValueOnce({ ...base, pending: 5 });
    await renderPanel();
    let resolveGet!: (s: DiscoveryState) => void;
    m.get.mockReturnValueOnce(new Promise((r) => (resolveGet = r)));
    await tick(10_000);
    expect(m.get).toHaveBeenCalledTimes(2);
    m.put.mockResolvedValue({ ...base, enabled: false, pending: 5 });
    await act(async () => {
      fireEvent.click(toggle());
    });
    expect(toggle()).not.toBeChecked();
    // A consulta começou antes do PUT: chega depois, com o estado velho.
    await act(async () => resolveGet({ ...base, enabled: true, pending: 5, found: 99 }));
    expect(toggle()).not.toBeChecked();
    expect(screen.queryByText(/99 empresas/)).toBeNull();
  });

  it("resposta antiga do polling não desfaz o que o Salvar gravou (polling continua)", async () => {
    m.get.mockResolvedValueOnce({ ...base, pending: 5 });
    await renderPanel();
    let resolveGet!: (s: DiscoveryState) => void;
    m.get.mockReturnValueOnce(new Promise((r) => (resolveGet = r)));
    await tick(10_000);
    m.put.mockResolvedValue({ ...base, target_companies: ["GitLab"], pending: 6, found: 20 });
    fireEvent.change(textarea(), { target: { value: "GitLab" } });
    await act(async () => {
      fireEvent.click(saveButton());
    });
    expect(screen.getByText("20 empresas encontradas · 6 na fila")).toBeInTheDocument();
    await act(async () => resolveGet({ ...base, pending: 5, found: 99 }));
    expect(screen.getByText("20 empresas encontradas · 6 na fila")).toBeInTheDocument();
    // O polling segue normalmente depois.
    m.get.mockResolvedValue({ ...base, target_companies: ["GitLab"], pending: 4, found: 21 });
    await tick(10_000);
    expect(screen.getByText("21 empresas encontradas · 4 na fila")).toBeInTheDocument();
  });

  it("desligada ou no teto: não consulta, mesmo com fila", async () => {
    m.get.mockResolvedValueOnce({ ...base, enabled: false, pending: 5 });
    const { unmount } = await renderPanel();
    await tick(30_000);
    expect(m.get).toHaveBeenCalledTimes(1);
    unmount();
    m.get.mockResolvedValueOnce({ ...base, pending: 5, auto_companies: 30, max_auto: 30 });
    await renderPanel();
    expect(screen.getByText(/Limite de 30 empresas automáticas atingido/)).toBeInTheDocument();
    await tick(30_000);
    expect(m.get).toHaveBeenCalledTimes(2);
  });

  it("refreshKey novo: relê /discovery uma vez e o polling volta quando sai do teto", async () => {
    m.get
      .mockResolvedValueOnce({ ...base, pending: 5, auto_companies: 30, max_auto: 30 })
      .mockResolvedValue({ ...base, pending: 5, auto_companies: 29, max_auto: 30 });
    const onCompaniesChanged = vi.fn();
    const view = render(<DiscoveryPanel refreshKey={30} onCompaniesChanged={onCompaniesChanged} />);
    await tick(0);
    expect(screen.getByText(/Limite de 30 empresas automáticas atingido/)).toBeInTheDocument();
    await tick(30_000);
    expect(m.get).toHaveBeenCalledTimes(1);
    view.rerender(<DiscoveryPanel refreshKey={29} onCompaniesChanged={onCompaniesChanged} />);
    await tick(0);
    expect(m.get).toHaveBeenCalledTimes(2);
    expect(screen.queryByText(/Limite de 30 empresas automáticas atingido/)).toBeNull();
    await tick(10_000);
    expect(m.get).toHaveBeenCalledTimes(3);
  });

  it("mesmo refreshKey num novo render não relê", async () => {
    m.get.mockResolvedValue(base);
    const view = render(<DiscoveryPanel refreshKey={1} />);
    await tick(0);
    view.rerender(<DiscoveryPanel refreshKey={1} />);
    await tick(0);
    expect(m.get).toHaveBeenCalledTimes(1);
  });

  it("consulta que falha depois de um Salvar começar não para o polling em silêncio", async () => {
    m.get.mockResolvedValueOnce({ ...base, pending: 5 });
    await renderPanel();
    let rejectGet!: (e: unknown) => void;
    m.get.mockReturnValueOnce(new Promise((_, r) => (rejectGet = r)));
    await tick(10_000);
    expect(m.get).toHaveBeenCalledTimes(2);
    m.put.mockResolvedValue({ ...base, pending: 5 });
    await act(async () => {
      fireEvent.click(saveButton());
    });
    await act(async () => rejectGet(new ApiError(null, "Sem rede.")));
    m.get.mockResolvedValue({ ...base, pending: 4 });
    await tick(10_000);
    expect(m.get).toHaveBeenCalledTimes(3);
    expect(screen.getByText("12 empresas encontradas · 4 na fila")).toBeInTheDocument();
  });

  it("Retomar fica desabilitado enquanto o interruptor salva", async () => {
    m.get
      .mockResolvedValueOnce({ ...base, pending: 5 })
      .mockRejectedValueOnce(new ApiError(null, "Sem rede."));
    await renderPanel();
    await tick(10_000);
    const resume = screen.getByRole("button", { name: "Retomar" });
    expect(resume).toBeEnabled();
    let resolvePut!: (s: DiscoveryState) => void;
    m.put.mockReturnValue(new Promise((r) => (resolvePut = r)));
    fireEvent.click(toggle());
    expect(resume).toBeDisabled();
    fireEvent.click(resume);
    expect(m.get).toHaveBeenCalledTimes(2);
    await act(async () => resolvePut({ ...base, enabled: false, pending: 5 }));
    expect(toggle()).not.toBeChecked();
    expect(screen.getByRole("button", { name: "Retomar" })).toBeEnabled();
  });

  it("o que foi digitado durante o Salvar não é apagado pela resposta", async () => {
    m.get.mockResolvedValue(base);
    let resolve!: (s: DiscoveryState) => void;
    m.put.mockReturnValue(new Promise((r) => (resolve = r)));
    await renderPanel();
    fireEvent.change(textarea(), { target: { value: "nubank" } });
    fireEvent.click(saveButton());
    fireEvent.change(textarea(), { target: { value: "nubank\nGitLab" } });
    await act(async () => resolve({ ...base, target_companies: ["Nubank"] }));
    expect(textarea().value).toBe("nubank\nGitLab");
    expect(screen.queryByText("Lista salva.")).toBeNull();
  });

  it("a região de status fica no DOM mesmo vazia (para ser anunciada)", async () => {
    m.get.mockResolvedValue(base);
    await renderPanel();
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent("");
    expect(status).toHaveAttribute("aria-live", "polite");
  });

  it("empresas automáticas mudaram: avisa a página para reler a lista", async () => {
    m.get
      .mockResolvedValueOnce({ ...base, pending: 5, auto_companies: 3 })
      .mockResolvedValueOnce({ ...base, pending: 4, auto_companies: 3 })
      .mockResolvedValue({ ...base, pending: 3, auto_companies: 4 });
    const { onCompaniesChanged } = await renderPanel();
    expect(onCompaniesChanged).not.toHaveBeenCalled();
    await tick(10_000);
    expect(onCompaniesChanged).not.toHaveBeenCalled();
    await tick(10_000);
    expect(onCompaniesChanged).toHaveBeenCalledTimes(1);
  });

  describe("Ver candidatos", () => {
    it("só busca ao abrir, mostra token e estado, e pagina de 50 em 50", async () => {
      m.get.mockResolvedValue(base);
      const first = Array.from({ length: 50 }, (_, i) => candidate(i));
      m.list
        .mockResolvedValueOnce({ total: 51, items: first })
        .mockResolvedValueOnce({ total: 51, items: [candidate(50, { state: "valid" })] });
      await renderPanel();
      expect(m.list).not.toHaveBeenCalled();
      const link = screen.getByRole("button", { name: "Ver candidatos" });
      expect(link).toHaveAttribute("aria-expanded", "false");
      await act(async () => {
        fireEvent.click(link);
      });
      expect(m.list).toHaveBeenCalledWith({ limit: 50, offset: 0 });
      expect(screen.getByRole("button", { name: "Esconder candidatos" })).toHaveAttribute(
        "aria-expanded",
        "true",
      );
      const list = screen.getByRole("list", { name: "Candidatos" });
      expect(within(list).getAllByRole("listitem")).toHaveLength(50);
      expect(within(list).getByText("empresa0")).toBeInTheDocument();
      expect(within(list).getAllByText("na fila")).toHaveLength(50);
      expect(screen.getByText("50 de 51")).toBeInTheDocument();
      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: "Carregar mais" }));
      });
      expect(m.list).toHaveBeenLastCalledWith({ limit: 50, offset: 50 });
      expect(within(list).getAllByRole("listitem")).toHaveLength(51);
      expect(within(list).getByText("adicionada")).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Carregar mais" })).toBeNull();
    });

    it("rótulos em PT-BR para cada estado", async () => {
      m.get.mockResolvedValue(base);
      m.list.mockResolvedValue({
        total: 5,
        items: [
          candidate(1, { state: "pending" }),
          candidate(2, { state: "valid" }),
          candidate(3, { state: "invalid", last_error: "Quadro não encontrado." }),
          candidate(4, { state: "known" }),
          candidate(5, { state: "rejected" }),
        ],
      });
      await renderPanel();
      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: "Ver candidatos" }));
      });
      const list = screen.getByRole("list", { name: "Candidatos" });
      for (const label of ["na fila", "adicionada", "sem vagas ou inexistente", "já existe", "ignorada"]) {
        expect(within(list).getByText(label)).toBeInTheDocument();
      }
    });

    it("texto hostil aparece literal, sem virar HTML", async () => {
      m.get.mockResolvedValue(base);
      const hostile = '<img src=x onerror="alert(1)">';
      m.list.mockResolvedValue({
        total: 1,
        items: [candidate(1, { token: hostile, name_hint: hostile, last_error: hostile })],
      });
      const { container } = await renderPanel();
      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: "Ver candidatos" }));
      });
      expect(container.querySelector("img")).toBeNull();
      expect(screen.getAllByText(hostile).length).toBeGreaterThanOrEqual(2);
    });

    it("erro ao listar mostra o detail e permite tentar de novo", async () => {
      m.get.mockResolvedValue(base);
      m.list.mockRejectedValueOnce(new ApiError(500, "Falhou ao listar.")).mockResolvedValue({
        total: 0,
        items: [],
      });
      await renderPanel();
      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: "Ver candidatos" }));
      });
      expect(screen.getByRole("alert")).toHaveTextContent("Falhou ao listar.");
      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: "Tentar de novo" }));
      });
      expect(screen.getByText(/Nenhum candidato ainda/)).toBeInTheDocument();
    });
  });
});
