import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import {
  ApiError,
  confirmProfile,
  emptyProfile,
  getImport,
  getProfile,
  uploadResume,
  type ImportState,
  type ProfileState,
} from "@/lib/profile";
import OnboardingPage from "./page";

vi.mock("@/lib/profile", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/profile")>()),
  uploadResume: vi.fn(),
  getImport: vi.fn(),
  confirmProfile: vi.fn(),
  getProfile: vi.fn(),
}));

const m = {
  upload: vi.mocked(uploadResume),
  getImport: vi.mocked(getImport),
  confirm: vi.mocked(confirmProfile),
  getProfile: vi.mocked(getProfile),
};

const extracted = () => {
  const p = emptyProfile();
  p.basics.name = "Mariana Costa";
  p.skills = [{ id: "s1", name: "SQL", level: null }];
  return p;
};

const importState = (status: ImportState["status"], extra: Partial<ImportState> = {}): ImportState => ({
  id: 9,
  status,
  error: null,
  truncated: false,
  profile: status === "ready" ? extracted() : null,
  ...extra,
});

const stored = (embedded: number, total = 4): ProfileState => ({
  version: 1,
  chunks_total: total,
  chunks_embedded: embedded,
  profile: extracted(),
  narrative: "Quero remoto.",
});

async function tick(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

async function uploadFile() {
  const input = await screen.findByLabelText(/Arquivo do currículo/);
  const file = new File(["%PDF-1.4"], "curriculo.pdf", { type: "application/pdf" });
  fireEvent.change(input, { target: { files: [file] } });
  fireEvent.click(screen.getByRole("button", { name: "Enviar currículo" }));
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  m.upload.mockResolvedValue({ id: 9 });
});

afterEach(() => {
  vi.useRealTimers();
  Object.values(m).forEach((fn) => fn.mockReset());
});

describe("OnboardingPage", () => {
  it("sem perfil: começa no passo 1 com narrativa e upload", async () => {
    m.getProfile.mockResolvedValue(null);
    render(<OnboardingPage />);
    expect(await screen.findByLabelText(/Arquivo do currículo/)).toBeInTheDocument();
    expect(screen.getByLabelText(/Conte com suas palavras/)).toBeInTheDocument();
    expect(screen.getByText("Passo 1 de 3")).toBeInTheDocument();
  });

  it("erro de rede ao carregar: diz o que houve e permite tentar de novo", async () => {
    m.getProfile
      .mockRejectedValueOnce(new ApiError(null, "Não consegui falar com a API em http://localhost:8000."))
      .mockResolvedValueOnce(null);
    render(<OnboardingPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Não consegui falar com a API");
    fireEvent.click(screen.getByRole("button", { name: "Tentar de novo" }));
    expect(await screen.findByLabelText(/Arquivo do currículo/)).toBeInTheDocument();
  });

  it("perfil existente: oferece ver o atual ou enviar outro currículo", async () => {
    m.getProfile.mockResolvedValue(stored(4));
    render(<OnboardingPage />);
    expect(await screen.findByRole("button", { name: "Ver perfil atual" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Continuar para a entrevista" })).toHaveAttribute(
      "href",
      "/onboarding/interview",
    );
    fireEvent.click(screen.getByRole("button", { name: "Enviar outro currículo" }));
    expect(await screen.findByLabelText(/Arquivo do currículo/)).toBeInTheDocument();
  });

  it("polling de getImport a cada 2 s para em ready e abre a revisão", async () => {
    m.getProfile.mockResolvedValue(null);
    m.getImport
      .mockResolvedValueOnce(importState("pending"))
      .mockResolvedValueOnce(importState("extracting"))
      .mockResolvedValue(importState("ready"));
    render(<OnboardingPage />);
    await uploadFile();
    expect(await screen.findByText("Passo 2 de 3")).toBeInTheDocument();
    await vi.waitFor(() => expect(m.getImport).toHaveBeenCalledTimes(1));
    await tick(2000);
    await vi.waitFor(() => expect(m.getImport).toHaveBeenCalledTimes(2));
    expect(await screen.findByText(/Extraindo os dados/)).toBeInTheDocument();
    await tick(2000);
    expect(await screen.findByLabelText("Nome completo")).toHaveValue("Mariana Costa");
    expect(m.getImport).toHaveBeenCalledTimes(3);
    await tick(6000);
    expect(m.getImport).toHaveBeenCalledTimes(3);
  });

  it("import ainda pending após 30 s: avisa sobre worker parado ou chave do Groq", async () => {
    m.getProfile.mockResolvedValue(null);
    m.getImport.mockResolvedValue(importState("pending"));
    render(<OnboardingPage />);
    await uploadFile();
    expect(await screen.findByText("Passo 2 de 3")).toBeInTheDocument();
    await tick(10_000);
    expect(screen.queryByText("A leitura não começou.")).not.toBeInTheDocument();
    await tick(21_000);
    const notice = await screen.findByRole("status");
    expect(notice).toHaveTextContent("A leitura não começou.");
    expect(notice).toHaveTextContent("docker compose logs worker");
    expect(notice).toHaveTextContent("GROQ_API_KEY");
  });

  it("import com status error: mostra o erro legível e para", async () => {
    m.getProfile.mockResolvedValue(null);
    m.getImport.mockResolvedValue(
      importState("error", { error: "O modelo não devolveu um perfil válido. Tente de novo." }),
    );
    render(<OnboardingPage />);
    await uploadFile();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "O modelo não devolveu um perfil válido. Tente de novo.",
    );
    await tick(6000);
    expect(m.getImport).toHaveBeenCalledTimes(1);
    // tentar de novo reenvia o mesmo arquivo
    m.getImport.mockResolvedValue(importState("ready"));
    fireEvent.click(screen.getByRole("button", { name: "Tentar de novo" }));
    expect(await screen.findByLabelText("Nome completo")).toBeInTheDocument();
    expect(m.upload).toHaveBeenCalledTimes(2);
  });

  it("erro de rede durante o polling: mostra o erro e retoma o mesmo import", async () => {
    m.getProfile.mockResolvedValue(null);
    m.getImport
      .mockRejectedValueOnce(new ApiError(null, "Não consegui falar com a API em http://localhost:8000."))
      .mockResolvedValue(importState("ready"));
    render(<OnboardingPage />);
    await uploadFile();
    expect(await screen.findByRole("alert")).toHaveTextContent("Não consegui falar com a API");
    fireEvent.click(screen.getByRole("button", { name: "Tentar de novo" }));
    expect(await screen.findByLabelText("Nome completo")).toBeInTheDocument();
    expect(m.upload).toHaveBeenCalledTimes(1);
    expect(m.getImport).toHaveBeenLastCalledWith(9);
  });

  it("confirmar mostra a vetorização e para de consultar ao concluir", async () => {
    m.getProfile.mockResolvedValueOnce(null);
    m.getImport.mockResolvedValue(importState("ready"));
    m.confirm.mockResolvedValue({ version: 1, chunks_total: 4, chunks_embedded: 0 });
    render(<OnboardingPage />);
    await uploadFile();
    fireEvent.click(await screen.findByRole("button", { name: "Confirmar perfil" }));
    expect(await screen.findByText("0 de 4")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Continuar para a entrevista" })).toHaveAttribute(
      "href",
      "/onboarding/interview",
    );

    m.getProfile.mockResolvedValueOnce(stored(2)).mockResolvedValue(stored(4));
    await tick(2000);
    expect(await screen.findByText("2 de 4")).toBeInTheDocument();
    await tick(2000);
    expect(await screen.findByText(/Perfil pronto/)).toBeInTheDocument();
    const calls = m.getProfile.mock.calls.length;
    await tick(10000);
    expect(m.getProfile.mock.calls.length).toBe(calls);
  });

  it("vetorização lenta: avisa que a primeira vez pode levar alguns minutos", async () => {
    m.getProfile.mockResolvedValueOnce(null);
    m.getImport.mockResolvedValue(importState("ready"));
    m.confirm.mockResolvedValue({ version: 1, chunks_total: 4, chunks_embedded: 0 });
    render(<OnboardingPage />);
    await uploadFile();
    fireEvent.click(await screen.findByRole("button", { name: "Confirmar perfil" }));
    m.getProfile.mockResolvedValue(stored(0));
    expect(await screen.findByText("0 de 4")).toBeInTheDocument();
    expect(screen.queryByText(/pode levar alguns minutos/)).not.toBeInTheDocument();
    await tick(30000);
    expect(await screen.findByText(/pode levar alguns minutos/)).toBeInTheDocument();
  });
});
