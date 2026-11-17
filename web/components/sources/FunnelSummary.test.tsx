import { afterEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { ApiError } from "@/lib/profile";
import { getFunnel, putFunnel, type FunnelState } from "@/lib/funnel";
import { FunnelSummary } from "./FunnelSummary";

vi.mock("@/lib/funnel", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/funnel")>()),
  getFunnel: vi.fn(),
  putFunnel: vi.fn(),
}));

const get = vi.mocked(getFunnel);
const put = vi.mocked(putFunnel);

const base: FunnelState = { full: 10, stub: 3, gated: 0, floor: null, suggested_floor: 0.51, good_labels: 12 };

afterEach(() => {
  get.mockReset();
  put.mockReset();
});

async function renderWith(state: FunnelState) {
  get.mockResolvedValue(state);
  render(<FunnelSummary />);
  await act(async () => {});
}

describe("FunnelSummary", () => {
  it("conta as vagas fora dos filtros e só sugere o corte, sem ativar", async () => {
    await renderWith(base);
    expect(screen.getByText("3 vagas fora dos seus filtros")).toBeInTheDocument();
    expect(screen.getByText(/Sugestão de corte por similaridade: 0,51/)).toBeInTheDocument();
    expect(put).not.toHaveBeenCalled();
  });

  it("aviso fixo sobre o corte, sempre visível", async () => {
    await renderWith(base);
    expect(screen.getByText(/pode esconder vagas boas/)).toBeInTheDocument();
  });

  it("Ativar corte envia o valor sugerido e mostra o corte ativo", async () => {
    await renderWith(base);
    put.mockResolvedValue({ ...base, floor: 0.51 });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Ativar corte" }));
    });
    expect(put).toHaveBeenCalledWith(0.51);
    expect(screen.getByText("Corte ativo: 0,51")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Ativar corte" })).toBeNull();
  });

  it("Desligar corte envia null", async () => {
    await renderWith({ ...base, floor: 0.6 });
    put.mockResolvedValue({ ...base, floor: null });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Desligar corte" }));
    });
    expect(put).toHaveBeenCalledWith(null);
    expect(screen.queryByText(/Corte ativo/)).toBeNull();
  });

  it("sem sugestão (poucas vagas marcadas) não mostra o botão de ativar", async () => {
    await renderWith({ ...base, suggested_floor: null, good_labels: 2 });
    expect(screen.queryByRole("button", { name: "Ativar corte" })).toBeNull();
  });

  it("erro da API aparece como texto e a sugestão continua disponível", async () => {
    await renderWith(base);
    put.mockRejectedValue(new ApiError(422, "O corte precisa estar entre 0 e 1."));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Ativar corte" }));
    });
    expect(screen.getByRole("alert")).toHaveTextContent("O corte precisa estar entre 0 e 1.");
    expect(screen.getByRole("button", { name: "Ativar corte" })).toBeEnabled();
  });
});
