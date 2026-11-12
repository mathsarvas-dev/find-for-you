import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { ApiError, emptyProfile, getProfile, type ProfileState } from "@/lib/profile";
import {
  answerInterview,
  finishInterview,
  getInterview,
  resolveSuggestion,
  retryInterview,
  skipQuestion,
  startInterview,
  type Interview,
  type InterviewState,
} from "@/lib/interview";
import InterviewPage from "./page";

vi.mock("@/lib/profile", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/profile")>()),
  getProfile: vi.fn(),
}));

vi.mock("@/lib/interview", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/interview")>()),
  startInterview: vi.fn(),
  getInterview: vi.fn(),
  answerInterview: vi.fn(),
  skipQuestion: vi.fn(),
  finishInterview: vi.fn(),
  retryInterview: vi.fn(),
  resolveSuggestion: vi.fn(),
}));

const m = {
  getProfile: vi.mocked(getProfile),
  start: vi.mocked(startInterview),
  get: vi.mocked(getInterview),
  answer: vi.mocked(answerInterview),
  skip: vi.mocked(skipQuestion),
  finish: vi.mocked(finishInterview),
  retry: vi.mocked(retryInterview),
  resolve: vi.mocked(resolveSuggestion),
};

const stored: ProfileState = {
  version: 1,
  chunks_total: 4,
  chunks_embedded: 4,
  profile: emptyProfile(),
  narrative: null,
};

const Q1 = "Na Transportes Ribeiro, quantas rotas você planejava por semana?";
const Q2 = "Que ferramenta você usava para os relatórios de frota?";

function interview(state: InterviewState, extra: Partial<Interview> = {}): Interview {
  const base: Interview = {
    id: 4,
    state,
    error: null,
    progress: { asked: 0, max: 12 },
    messages: [],
    suggestions: [],
  };
  if (state === "awaiting_answer") {
    base.progress = { asked: 1, max: 12 };
    base.messages = [{ id: 1, role: "assistant", kind: "question", content: Q1, gap_id: "g1" }];
  }
  return { ...base, ...extra };
}

const answered = (state: InterviewState, extra: Partial<Interview> = {}): Interview =>
  interview(state, {
    progress: { asked: 1, max: 12 },
    messages: [
      { id: 1, role: "assistant", kind: "question", content: Q1, gap_id: "g1" },
      { id: 2, role: "user", kind: "answer", content: "Umas 40 rotas.", gap_id: "g1" },
    ],
    ...extra,
  });

const pendingSuggestion = {
  id: 9,
  message_id: 2,
  gap_id: "g1",
  op: "add_note" as const,
  payload: { op: "add_note" as const, text: "Planejava cerca de 40 rotas por semana" },
  status: "pending" as const,
  applied_version: null,
  before: "—",
  after: "Planejava cerca de 40 rotas por semana",
};

async function tick(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  m.getProfile.mockResolvedValue(stored);
  m.start.mockResolvedValue({ id: 4, state: "generating" });
});

afterEach(() => {
  vi.useRealTimers();
  Object.values(m).forEach((fn) => fn.mockReset());
});

describe("InterviewPage", () => {
  it("carregando: mostra esqueleto enquanto verifica o perfil", async () => {
    m.getProfile.mockReturnValue(new Promise(() => {}));
    render(<InterviewPage />);
    expect(screen.getByLabelText("Carregando entrevista")).toHaveAttribute("aria-busy", "true");
  });

  it("sem perfil: estado vazio com link para o onboarding e sem iniciar entrevista", async () => {
    m.get.mockResolvedValue(null);
    m.getProfile.mockResolvedValue(null);
    render(<InterviewPage />);
    const link = await screen.findByRole("link", { name: "Ir para o perfil" });
    expect(link).toHaveAttribute("href", "/onboarding");
    expect(screen.getByText("Confirme seu perfil primeiro.")).toBeInTheDocument();
    expect(m.start).not.toHaveBeenCalled();
  });

  it("POST /interview com 409 também cai no estado vazio", async () => {
    m.get.mockResolvedValue(null);
    m.start.mockRejectedValue(new ApiError(409, "Confirme seu perfil antes de iniciar a entrevista."));
    render(<InterviewPage />);
    expect(await screen.findByRole("link", { name: "Ir para o perfil" })).toBeInTheDocument();
  });

  it("API fora do ar: erro com Tentar de novo, sem polling", async () => {
    m.get
      .mockRejectedValueOnce(new ApiError(null, "Não consegui falar com a API em http://localhost:8000."))
      .mockResolvedValue(interview("awaiting_answer"));
    render(<InterviewPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Não consegui falar com a API");
    await tick(10_000);
    expect(m.get).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Tentar de novo" }));
    expect(await screen.findByText(Q1)).toBeInTheDocument();
  });

  it("polling a cada 2 s em generating/working e para em awaiting_answer", async () => {
    m.get
      .mockResolvedValueOnce(interview("generating"))
      .mockResolvedValueOnce(interview("working"))
      .mockResolvedValue(interview("awaiting_answer"));
    render(<InterviewPage />);
    expect(await screen.findByText(/Preparando a primeira pergunta/)).toBeInTheDocument();
    // Entrevista ativa: retoma sem POST /interview.
    expect(m.start).not.toHaveBeenCalled();
    expect(m.get).toHaveBeenCalledTimes(1);
    await tick(2000);
    expect(m.get).toHaveBeenCalledTimes(2);
    await tick(2000);
    expect(await screen.findByText(Q1)).toBeInTheDocument();
    expect(screen.getByText("pergunta 1 de ~12")).toBeInTheDocument();
    expect(screen.getByLabelText("Sua resposta")).toBeInTheDocument();
    const calls = m.get.mock.calls.length;
    await tick(10_000);
    expect(m.get).toHaveBeenCalledTimes(calls);
  });

  it("polling para em done e mostra o encerramento com link para ver o perfil", async () => {
    m.get.mockResolvedValueOnce(answered("generating")).mockResolvedValue(
      answered("done", {
        messages: [
          ...answered("done").messages,
          {
            id: 3,
            role: "assistant",
            kind: "info",
            content: "Terminamos por aqui. Revise as sugestões pendentes e confirme o que fizer sentido.",
            gap_id: null,
          },
        ],
      }),
    );
    render(<InterviewPage />);
    await screen.findByText(/Analisando sua resposta/);
    await tick(2000);
    expect(await screen.findByText(/Terminamos por aqui/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Ver perfil" })).toHaveAttribute("href", "/onboarding");
    expect(screen.queryByLabelText("Sua resposta")).not.toBeInTheDocument();
    const calls = m.get.mock.calls.length;
    await tick(10_000);
    expect(m.get).toHaveBeenCalledTimes(calls);
  });

  it("desmontar limpa os timers: nenhuma chamada nova depois", async () => {
    m.get.mockResolvedValue(interview("generating"));
    const { unmount } = render(<InterviewPage />);
    await screen.findByText(/Preparando a primeira pergunta/);
    await tick(2000);
    expect(m.get).toHaveBeenCalledTimes(2);
    unmount();
    await tick(20_000);
    expect(m.get).toHaveBeenCalledTimes(2);
  });

  it("30 s em generating: avisa sobre worker parado ou GROQ_API_KEY", async () => {
    m.get.mockResolvedValue(interview("generating"));
    render(<InterviewPage />);
    await screen.findByText(/Preparando a primeira pergunta/);
    await tick(20_000);
    expect(screen.queryByText("A pergunta ainda não saiu.")).not.toBeInTheDocument();
    await tick(11_000);
    const notice = screen.getByRole("status");
    expect(notice).toHaveTextContent("A pergunta ainda não saiu.");
    expect(notice).toHaveTextContent("docker compose logs worker");
    expect(notice).toHaveTextContent("GROQ_API_KEY");
  });

  it("erro de rede no polling: mostra erro e para até Tentar de novo", async () => {
    m.get
      .mockResolvedValueOnce(interview("generating"))
      .mockRejectedValueOnce(new ApiError(null, "Não consegui falar com a API em http://localhost:8000."))
      .mockResolvedValue(interview("awaiting_answer"));
    render(<InterviewPage />);
    await screen.findByText(/Preparando a primeira pergunta/);
    await tick(2000);
    expect(await screen.findByRole("alert")).toHaveTextContent("Não consegui falar com a API");
    await tick(10_000);
    expect(m.get).toHaveBeenCalledTimes(2);
    fireEvent.click(screen.getByRole("button", { name: "Tentar de novo" }));
    await tick(2000);
    expect(await screen.findByText(Q1)).toBeInTheDocument();
  });

  it("estado error: mostra a mensagem e Tentar de novo chama retry e volta a consultar", async () => {
    m.get
      .mockResolvedValueOnce(interview("error", { error: "O modelo não respondeu. Tente de novo." }))
      .mockResolvedValueOnce(interview("generating"))
      .mockResolvedValue(interview("awaiting_answer"));
    m.retry.mockResolvedValue({ state: "generating" });
    render(<InterviewPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("O modelo não respondeu. Tente de novo.");
    fireEvent.click(screen.getByRole("button", { name: "Tentar de novo" }));
    await vi.waitFor(() => expect(m.retry).toHaveBeenCalledWith(4));
    expect(await screen.findByText(/Preparando a primeira pergunta/)).toBeInTheDocument();
    await tick(2000);
    expect(await screen.findByText(Q1)).toBeInTheDocument();
  });

  it("responder envia o texto, consulta de novo e foca a nova pergunta", async () => {
    m.get
      .mockResolvedValueOnce(interview("awaiting_answer"))
      .mockResolvedValueOnce(answered("generating"))
      .mockResolvedValue(
        answered("awaiting_answer", {
          progress: { asked: 2, max: 12 },
          messages: [
            ...answered("generating").messages,
            { id: 3, role: "assistant", kind: "question", content: Q2, gap_id: "g2" },
          ],
        }),
      );
    m.answer.mockResolvedValue({ state: "generating" });
    render(<InterviewPage />);
    fireEvent.change(await screen.findByLabelText("Sua resposta"), { target: { value: "Umas 40 rotas." } });
    fireEvent.click(screen.getByRole("button", { name: "Enviar resposta" }));
    await vi.waitFor(() => expect(m.answer).toHaveBeenCalledWith(4, "Umas 40 rotas."));
    expect(await screen.findByText(/Analisando sua resposta/)).toBeInTheDocument();
    await tick(2000);
    const question = await screen.findByText(Q2);
    expect(screen.getByText("pergunta 2 de ~12")).toBeInTheDocument();
    const region = question.closest("[tabindex='-1']");
    expect(region).not.toBeNull();
    expect(region).toHaveFocus();
    expect(region).not.toHaveAttribute("aria-live");
  });

  it("Pular chama skip", async () => {
    m.get.mockResolvedValueOnce(interview("awaiting_answer")).mockResolvedValue(interview("generating"));
    m.skip.mockResolvedValue({ state: "generating" });
    render(<InterviewPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Pular" }));
    await vi.waitFor(() => expect(m.skip).toHaveBeenCalledWith(4));
    expect(await screen.findByText(/Preparando a primeira pergunta/)).toBeInTheDocument();
  });

  it("Encerrar entrevista pede confirmação em dois passos", async () => {
    m.get.mockResolvedValueOnce(interview("awaiting_answer")).mockResolvedValue(interview("done"));
    m.finish.mockResolvedValue({ state: "done" });
    render(<InterviewPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Encerrar entrevista" }));
    expect(m.finish).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Cancelar" }));
    expect(screen.queryByRole("button", { name: "Sim, encerrar" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Encerrar entrevista" }));
    fireEvent.click(screen.getByRole("button", { name: "Sim, encerrar" }));
    await vi.waitFor(() => expect(m.finish).toHaveBeenCalledWith(4));
    expect(await screen.findByRole("link", { name: "Ver perfil" })).toBeInTheDocument();
  });

  it("aceitar sugestão resolve e recarrega a entrevista", async () => {
    m.get
      .mockResolvedValueOnce(answered("awaiting_answer", { suggestions: [pendingSuggestion] }))
      .mockResolvedValue(
        answered("awaiting_answer", { suggestions: [{ ...pendingSuggestion, status: "accepted", applied_version: 2 }] }),
      );
    m.resolve.mockResolvedValue({ status: "accepted", applied_version: 2 });
    render(<InterviewPage />);
    const card = await screen.findByRole("article");
    fireEvent.click(within(card).getByRole("button", { name: "Aceitar" }));
    await vi.waitFor(() => expect(m.resolve).toHaveBeenCalledWith(9, "accept", undefined));
    expect(await screen.findByText("Aceita")).toBeInTheDocument();
    expect(m.get).toHaveBeenCalledTimes(2);
  });

  it("409 do resolve mostra que o item mudou", async () => {
    m.get.mockResolvedValue(answered("awaiting_answer", { suggestions: [pendingSuggestion] }));
    m.resolve.mockRejectedValueOnce(new ApiError(409, "O item mudou; descarte esta sugestão"));
    render(<InterviewPage />);
    const card = await screen.findByRole("article");
    fireEvent.click(within(card).getByRole("button", { name: "Aceitar" }));
    expect(await within(card).findByRole("alert")).toHaveTextContent("O item mudou; descarte esta sugestão");
    expect(within(card).getByRole("button", { name: "Descartar" })).toBeEnabled();
  });
  it("primeira vez (nenhuma entrevista): confere o perfil e faz POST /interview uma vez", async () => {
    m.get.mockResolvedValueOnce(null).mockResolvedValue(interview("generating"));
    render(<InterviewPage />);
    expect(await screen.findByText(/Preparando a primeira pergunta/)).toBeInTheDocument();
    expect(m.getProfile).toHaveBeenCalledTimes(1);
    expect(m.start).toHaveBeenCalledTimes(1);
    await tick(4000);
    expect(m.start).toHaveBeenCalledTimes(1);
  });

  it("entrevista encerrada: mostra o encerramento sem criar outra; Nova entrevista inicia uma", async () => {
    m.get
      .mockResolvedValueOnce(answered("done", { suggestions: [pendingSuggestion] }))
      .mockResolvedValue(interview("generating"));
    m.start.mockResolvedValue({ id: 5, state: "generating" });
    render(<InterviewPage />);
    expect(await screen.findByText("Entrevista encerrada")).toBeInTheDocument();
    expect(screen.getByText(/Ainda há 1 sugestão pendente/)).toBeInTheDocument();
    expect(m.start).not.toHaveBeenCalled();
    expect(m.getProfile).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Nova entrevista" }));
    await vi.waitFor(() => expect(m.start).toHaveBeenCalledTimes(1));
    expect(await screen.findByText(/Preparando a primeira pergunta/)).toBeInTheDocument();
  });

  it("sugestão pendente de entrevista encerrada continua resolvível", async () => {
    m.get
      .mockResolvedValueOnce(answered("done", { suggestions: [pendingSuggestion] }))
      .mockResolvedValue(
        answered("done", { suggestions: [{ ...pendingSuggestion, status: "rejected" }] }),
      );
    m.resolve.mockResolvedValue({ status: "rejected", applied_version: null });
    render(<InterviewPage />);
    const card = await screen.findByRole("article");
    fireEvent.click(within(card).getByRole("button", { name: "Descartar" }));
    await vi.waitFor(() => expect(m.resolve).toHaveBeenCalledWith(9, "reject", undefined));
    expect(await screen.findByText("Descartada")).toBeInTheDocument();
    expect(m.start).not.toHaveBeenCalled();
  });

  it("409 do resolve com a sugestão já resolvida: recarrega e mostra o status real", async () => {
    m.get
      .mockResolvedValueOnce(answered("awaiting_answer", { suggestions: [pendingSuggestion] }))
      .mockResolvedValue(
        answered("awaiting_answer", {
          suggestions: [{ ...pendingSuggestion, status: "accepted", applied_version: 2 }],
        }),
      );
    m.resolve.mockRejectedValueOnce(new ApiError(409, "O item mudou; descarte esta sugestão"));
    render(<InterviewPage />);
    const card = await screen.findByRole("article");
    fireEvent.click(within(card).getByRole("button", { name: "Aceitar" }));
    expect(await screen.findByText("Aceita")).toBeInTheDocument();
    expect(screen.queryByText("O item mudou; descarte esta sugestão")).not.toBeInTheDocument();
    expect(m.get).toHaveBeenCalledTimes(2);
  });

  it("Encerrar fica bloqueado enquanto a resposta é enviada", async () => {
    m.get.mockResolvedValue(interview("awaiting_answer"));
    m.answer.mockReturnValue(new Promise(() => {}));
    render(<InterviewPage />);
    fireEvent.change(await screen.findByLabelText("Sua resposta"), { target: { value: "Umas 40." } });
    expect(screen.getByRole("button", { name: "Encerrar entrevista" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Enviar resposta" }));
    await vi.waitFor(() =>
      expect(screen.getByRole("button", { name: "Encerrar entrevista" })).toBeDisabled(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Encerrar entrevista" }));
    expect(screen.queryByRole("button", { name: "Sim, encerrar" })).not.toBeInTheDocument();
    expect(m.finish).not.toHaveBeenCalled();
  });

  it("Tentar de novo (estado error) fica bloqueado enquanto uma sugestão é resolvida", async () => {
    m.get.mockResolvedValue(answered("error", { error: "Falhou.", suggestions: [pendingSuggestion] }));
    m.resolve.mockReturnValue(new Promise(() => {}));
    render(<InterviewPage />);
    const card = await screen.findByRole("article");
    const retry = screen.getByRole("button", { name: "Tentar de novo" });
    expect(retry).toBeEnabled();
    fireEvent.click(within(card).getByRole("button", { name: "Aceitar" }));
    await vi.waitFor(() => expect(retry).toBeDisabled());
    fireEvent.click(retry);
    expect(m.retry).not.toHaveBeenCalled();
  });
});
