import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ApiError } from "@/lib/profile";
import {
  deleteLabel,
  getReport,
  getSample,
  newSample,
  putLabel,
  type SampleItem,
} from "@/lib/quality";
import { qualityReport, sampleItem } from "@/components/quality/fixtures";
import QualityPage from "./page";

vi.mock("@/lib/quality", () => ({
  getSample: vi.fn(),
  newSample: vi.fn(),
  putLabel: vi.fn(),
  deleteLabel: vi.fn(),
  getReport: vi.fn(),
}));

const m = {
  sample: vi.mocked(getSample),
  fresh: vi.mocked(newSample),
  put: vi.mocked(putLabel),
  del: vi.mocked(deleteLabel),
  report: vi.mocked(getReport),
};

const items = (labels: Array<"good" | "bad" | null>): SampleItem[] =>
  labels.map((label, i) => sampleItem({ job_id: i + 1, title: `Vaga ${i + 1}`, label }));

async function renderPage() {
  render(<QualityPage />);
  await screen.findByText(/[0-9]+ de [0-9]+ rotuladas/);
}

beforeEach(() => {
  m.sample.mockResolvedValue({ items: items([null, null, null]), labeled_total: 0 });
  m.report.mockResolvedValue(qualityReport({ status: "insuficiente", missing: ["faltam 18 rótulos"] }));
  m.put.mockImplementation(async (job_id, label) => ({ job_id, label }));
  m.del.mockResolvedValue(undefined);
});

afterEach(() => {
  vi.clearAllMocks();
  vi.restoreAllMocks();
});

describe("QualityPage", () => {
  it("começa na primeira vaga sem rótulo e mostra o contador", async () => {
    m.sample.mockResolvedValue({ items: items(["good", "bad", null, null]), labeled_total: 2 });
    await renderPage();
    expect(screen.getByRole("heading", { name: "Vaga 3" })).toBeInTheDocument();
    expect(screen.getByText("2 de 4 rotuladas")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Voltar às vagas" })).toHaveAttribute("href", "/jobs");
    expect(await screen.findByText("faltam 18 rótulos")).toBeInTheDocument();
  });

  it("Boa grava, avança, atualiza o contador e o relatório", async () => {
    await renderPage();
    await screen.findByText("faltam 18 rótulos");
    m.report.mockClear();
    fireEvent.click(screen.getByRole("button", { name: "Boa" }));
    await screen.findByRole("heading", { name: "Vaga 2" });
    expect(m.put).toHaveBeenCalledWith(1, "good");
    expect(screen.getByText("1 de 3 rotuladas")).toBeInTheDocument();
    await waitFor(() => expect(m.report).toHaveBeenCalledTimes(1));
  });

  it("tecla b grava ruim", async () => {
    await renderPage();
    fireEvent.keyDown(window, { key: "b" });
    await screen.findByRole("heading", { name: "Vaga 2" });
    expect(m.put).toHaveBeenCalledWith(1, "bad");
  });

  it("duplo clique manda um único pedido", async () => {
    let resolve!: () => void;
    m.put.mockImplementation(
      (job_id, label) => new Promise((r) => (resolve = () => r({ job_id, label }))),
    );
    await renderPage();
    const boa = screen.getByRole("button", { name: "Boa" });
    fireEvent.click(boa);
    fireEvent.click(boa);
    fireEvent.keyDown(window, { key: "g" });
    expect(m.put).toHaveBeenCalledTimes(1);
    await act(async () => resolve());
    await screen.findByRole("heading", { name: "Vaga 2" });
  });

  it("Pular avança sem chamada e Anterior volta", async () => {
    await renderPage();
    fireEvent.click(screen.getByRole("button", { name: "Pular" }));
    expect(screen.getByRole("heading", { name: "Vaga 2" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Anterior" }));
    expect(screen.getByRole("heading", { name: "Vaga 1" })).toBeInTheDocument();
    expect(m.put).not.toHaveBeenCalled();
  });

  it("Remover rótulo chama deleteLabel e mantém a vaga", async () => {
    m.sample.mockResolvedValue({ items: items(["good", null]), labeled_total: 1 });
    await renderPage();
    fireEvent.click(screen.getByRole("button", { name: "Anterior" }));
    fireEvent.click(await screen.findByRole("button", { name: "Remover rótulo" }));
    await screen.findByText("0 de 2 rotuladas");
    expect(m.del).toHaveBeenCalledWith(1);
    expect(screen.getByRole("heading", { name: "Vaga 1" })).toBeInTheDocument();
  });

  it("erro ao gravar mostra a mensagem e não avança", async () => {
    m.put.mockRejectedValue(new ApiError(500, "Falhou ao gravar."));
    await renderPage();
    fireEvent.click(screen.getByRole("button", { name: "Boa" }));
    expect(await screen.findByText("Falhou ao gravar.")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Vaga 1" })).toBeInTheDocument();
    expect(screen.getByText("0 de 3 rotuladas")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Boa" })).toBeEnabled();
  });

  it("título hostil aparece como texto", async () => {
    m.sample.mockResolvedValue({
      items: [sampleItem({ title: "<img src=x onerror=alert(1)>", url: null })],
      labeled_total: 0,
    });
    await renderPage();
    expect(screen.getByText("<img src=x onerror=alert(1)>")).toBeInTheDocument();
    expect(document.querySelector("img")).toBeNull();
  });

  it("Nova amostra recarrega a amostra e o relatório", async () => {
    m.report.mockResolvedValue(qualityReport());
    vi.spyOn(window, "confirm").mockReturnValue(true);
    m.fresh.mockResolvedValue({
      items: [sampleItem({ job_id: 9, title: "Vaga nova" })],
      labeled_total: 0,
    });
    await renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Nova amostra" }));
    await screen.findByRole("heading", { name: "Vaga nova" });
    expect(m.fresh).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(m.report).toHaveBeenCalledTimes(2));
  });

  it("409 mostra link para /onboarding", async () => {
    m.sample.mockRejectedValue(new ApiError(409, "Confirme seu perfil primeiro."));
    render(<QualityPage />);
    expect(await screen.findByText("Confirme seu perfil primeiro.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Ir para o perfil" })).toHaveAttribute("href", "/onboarding");
  });

  it("erro de rede oferece Tentar de novo", async () => {
    m.sample.mockRejectedValueOnce(new ApiError(null, "Não consegui falar com a API."));
    render(<QualityPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Tentar de novo" }));
    expect(await screen.findByText(/[0-9]+ de [0-9]+ rotuladas/)).toBeInTheDocument();
  });
});
