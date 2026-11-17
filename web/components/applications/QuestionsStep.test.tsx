import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ApplicationDetail, QuestionOut } from "@/lib/applications";
import { draftQuestion, fetchQuestions, pasteQuestions, updateQuestion } from "@/lib/applications";
import { ApiError } from "@/lib/profile";
import { QuestionsStep, suggestedBankKey } from "./QuestionsStep";

vi.mock("@/lib/applications", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/applications")>()),
  fetchQuestions: vi.fn(),
  pasteQuestions: vi.fn(),
  updateQuestion: vi.fn(),
  draftQuestion: vi.fn(),
}));

const HOSTILE = '<img src=x onerror="alert(1)"> Por que nós?';

function question(over: Partial<QuestionOut> = {}): QuestionOut {
  return {
    id: 1, position: 1, source: "pasted", label: "Por que nós?", kind: "text", options: null,
    required: false, sensitive: false, answer: null, answer_state: "empty", bank_key: null,
    bank_suggestion: null, task: null, ...over,
  };
}

function detail(questions: QuestionOut[]): ApplicationDetail {
  return {
    id: 7, state: "ready", error: null, locale: "pt", template: "us", referees: null,
    profile_version: 1, current_profile_version: 1, warnings: [],
    job: { id: 9, title: "Data Engineer", company_name: "Globex", url: "https://example.test", status: "open" },
    requirements: { must: [], nice: [], keywords: [] }, coverage: null,
    resume: { version: 1, status: "valid", lines: [] }, cover_letter: null,
    questions, tasks: [], can_generate: { allowed: true, reason: null }, applied_at: null,
  };
}

const fetchMock = vi.mocked(fetchQuestions);
const pasteMock = vi.mocked(pasteQuestions);
const updateMock = vi.mocked(updateQuestion);
const draftMock = vi.mocked(draftQuestion);

beforeEach(() => {
  fetchMock.mockReset();
  pasteMock.mockReset();
  updateMock.mockReset();
  draftMock.mockReset();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("QuestionsStep", () => {
  it("falls back to pasting when the job does not give its questions", async () => {
    fetchMock.mockRejectedValue(new ApiError(409, "Esta vaga não informa as perguntas. Cole-as aqui."));
    render(<QuestionsStep detail={detail([])} onChanged={vi.fn()} />);
    expect(await screen.findByText("Esta vaga não informa as perguntas. Cole-as aqui.")).toBeInTheDocument();
    expect(screen.getByLabelText(/Cole as perguntas/)).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("a sensitive question has no draft button and shows the bank suggestion", () => {
    const sensitive = question({
      label: "Qual é a sua pretensão salarial?", sensitive: true,
      bank_suggestion: { key: "salary_expectation", value: "R$ 8.000" },
    });
    render(<QuestionsStep detail={detail([sensitive])} onChanged={vi.fn()} />);
    expect(screen.getByText("Responda você")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Rascunhar" })).toBeNull();
    expect(screen.getByText(/Sugestão do seu banco: R\$ 8.000/)).toBeInTheDocument();
  });

  it("a failed draft shows its reason", () => {
    const failed = question({ task: { state: "failed", error: "Não consegui rascunhar sem inventar. Escreva você." } });
    render(<QuestionsStep detail={detail([failed])} onChanged={vi.fn()} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Não consegui rascunhar sem inventar");
  });

  it("'Marcar como final' sends final: true with the answer", async () => {
    updateMock.mockResolvedValue(question({ answer_state: "final", answer: "Resposta" }));
    render(<QuestionsStep detail={detail([question()])} onChanged={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("Sua resposta"), { target: { value: "Resposta" } });
    fireEvent.click(screen.getByRole("button", { name: "Marcar como final" }));
    await waitFor(() => expect(updateMock).toHaveBeenCalledWith(7, 1, { answer: "Resposta", final: true }));
  });

  it("shows hostile question text as plain text", () => {
    const { container } = render(<QuestionsStep detail={detail([question({ label: HOSTILE })])} onChanged={vi.fn()} />);
    expect(screen.getByText(HOSTILE)).toBeInTheDocument();
    expect(container.querySelector("img")).toBeNull();
  });

  it("suggests a bank key from the label", () => {
    expect(suggestedBankKey("Por que nós? (versão 2)")).toBe("por_que_nos_versao_2");
    expect(suggestedBankKey("???")).toBe("resposta");
  });
});
