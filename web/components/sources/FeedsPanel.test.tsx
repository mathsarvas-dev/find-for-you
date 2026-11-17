import { afterEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { addFeed, deleteFeed, listFeeds, type Feed, type FeedSourceInfo } from "@/lib/feeds";
import { ApiError } from "@/lib/profile";
import { FeedsPanel } from "./FeedsPanel";

vi.mock("@/lib/feeds", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/feeds")>()),
  listFeeds: vi.fn(),
  addFeed: vi.fn(),
  deleteFeed: vi.fn(),
}));

const list = vi.mocked(listFeeds);
const add = vi.mocked(addFeed);
const remove = vi.mocked(deleteFeed);

const remotiveInfo: FeedSourceInfo = {
  source: "remotive",
  label: "Remotive",
  needs_key: false,
  key_configured: true,
  gate_status: "allowed",
  gate_allowed: true,
  attribution: { label: "Remotive", url: "https://remotive.com" },
};

const feed: Feed = {
  id: 7,
  source: "remotive",
  params: { keywords: ["dados", "python"], country: "BR" },
  active: true,
  last_run_at: null,
  next_run_at: null,
  last_error: null,
  jobs_seen: 12,
  attribution: { label: "Remotive", url: "https://remotive.com" },
};

afterEach(() => {
  list.mockReset();
  add.mockReset();
  remove.mockReset();
  vi.restoreAllMocks();
});

async function renderPanel(feeds: Feed[] = [], sources: FeedSourceInfo[] = [remotiveInfo]) {
  list.mockResolvedValue({ feeds, sources });
  render(<FeedsPanel />);
  await act(async () => {});
}

describe("FeedsPanel", () => {
  it("cada feed mostra o crédito obrigatório linkando a fonte", async () => {
    await renderPanel([feed]);
    const credit = screen.getByRole("link", { name: "Vagas via Remotive" });
    expect(credit).toHaveAttribute("href", expect.stringMatching(/^https:\/\/remotive\.com\/?$/));
    expect(credit).toHaveAttribute("target", "_blank");
    expect(credit.getAttribute("rel")).toContain("noopener");
    expect(credit.getAttribute("rel")).toContain("noreferrer");
    expect(screen.getByText("dados, python")).toBeInTheDocument();
    expect(screen.getByText("12 vagas vistas")).toBeInTheDocument();
  });

  it("erro da última coleta aparece como texto", async () => {
    await renderPanel([{ ...feed, last_error: "Tempo esgotado" }]);
    expect(screen.getByText(/Erro na última coleta: Tempo esgotado/)).toBeInTheDocument();
  });

  it("fonte com chave não configurada aparece desabilitada com o motivo", async () => {
    await renderPanel([], [
      { ...remotiveInfo, source: "adzuna", label: "Adzuna", needs_key: true, key_configured: false, attribution: null },
    ]);
    const option = screen.getByRole("option", { name: /Adzuna/ });
    expect(option).toBeDisabled();
    expect(option).toHaveTextContent("Chave não configurada no .env");
  });

  it("fonte condicional desligada aponta para a aba Portais", async () => {
    await renderPanel([], [
      {
        ...remotiveInfo,
        source: "bundesagentur",
        label: "Bundesagentur",
        gate_status: "conditional",
        gate_allowed: false,
        attribution: null,
      },
    ]);
    const option = screen.getByRole("option", { name: /Bundesagentur/ });
    expect(option).toBeDisabled();
    expect(option).toHaveTextContent("Ligue na aba Portais");
  });

  it("criar feed envia palavras-chave e país em maiúsculas", async () => {
    add.mockResolvedValue(feed);
    await renderPanel();
    fireEvent.change(screen.getByLabelText(/Palavras-chave/), { target: { value: "dados, python" } });
    fireEvent.change(screen.getByLabelText("País (opcional)"), { target: { value: "br" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Adicionar feed" }));
    });
    expect(add).toHaveBeenCalledWith({
      source: "remotive",
      params: { keywords: ["dados", "python"], country: "BR" },
    });
  });

  it("mais de 5 palavras-chave é recusado antes de enviar", async () => {
    await renderPanel();
    fireEvent.change(screen.getByLabelText(/Palavras-chave/), { target: { value: "a, b, c, d, e, f" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Adicionar feed" }));
    });
    expect(add).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent("Use no máximo 5 palavras-chave.");
  });

  it("erro da API no cadastro aparece como texto", async () => {
    add.mockRejectedValue(new ApiError(409, "Esse feed já existe."));
    await renderPanel();
    fireEvent.change(screen.getByLabelText(/Palavras-chave/), { target: { value: "dados" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Adicionar feed" }));
    });
    expect(screen.getByRole("alert")).toHaveTextContent("Esse feed já existe.");
  });

  it("excluir pede confirmação; cancelar não chama a API", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    await renderPanel([feed]);
    fireEvent.click(screen.getByRole("button", { name: "Excluir feed Remotive" }));
    expect(confirm).toHaveBeenCalledWith("Excluir este feed? As vagas já coletadas continuam.");
    expect(remove).not.toHaveBeenCalled();
  });

  it("excluir confirmado chama a API e tira o feed da lista", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    remove.mockResolvedValue(undefined);
    await renderPanel([feed]);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Excluir feed Remotive" }));
    });
    expect(remove).toHaveBeenCalledWith(7);
    expect(screen.queryByRole("link", { name: "Vagas via Remotive" })).toBeNull();
  });
});
