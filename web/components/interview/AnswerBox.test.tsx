import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { ApiError } from "@/lib/profile";
import { AnswerBox, MAX_ANSWER_CHARS } from "./AnswerBox";

const field = () => screen.getByLabelText("Sua resposta");
const send = () => screen.getByRole("button", { name: "Enviar resposta" });

describe("AnswerBox", () => {
  it("campo com rótulo; vazio ou só espaços deixa o envio desabilitado", () => {
    render(<AnswerBox onSubmit={vi.fn()} onSkip={vi.fn()} />);
    expect(send()).toBeDisabled();
    fireEvent.change(field(), { target: { value: "   " } });
    expect(send()).toBeDisabled();
    fireEvent.change(field(), { target: { value: "Uns 300 pedidos por dia" } });
    expect(send()).toBeEnabled();
  });

  it("contador até 4000 e bloqueio acima do limite", () => {
    render(<AnswerBox onSubmit={vi.fn()} onSkip={vi.fn()} />);
    expect(MAX_ANSWER_CHARS).toBe(4000);
    expect(screen.getByText("0 / 4000")).toBeInTheDocument();
    fireEvent.change(field(), { target: { value: "abc" } });
    expect(screen.getByText("3 / 4000")).toBeInTheDocument();
    fireEvent.change(field(), { target: { value: "a".repeat(4001) } });
    expect(screen.getByText("4001 / 4000")).toBeInTheDocument();
    expect(screen.getByText(/passou do limite/)).toBeInTheDocument();
    expect(send()).toBeDisabled();
  });

  it("Ctrl+Enter e Cmd+Enter enviam; Enter sozinho não", async () => {
    const onSubmit = vi.fn(async () => {});
    render(<AnswerBox onSubmit={onSubmit} onSkip={vi.fn()} />);
    fireEvent.change(field(), { target: { value: "Resposta" } });
    fireEvent.keyDown(field(), { key: "Enter" });
    expect(onSubmit).not.toHaveBeenCalled();
    fireEvent.keyDown(field(), { key: "Enter", ctrlKey: true });
    expect(onSubmit).toHaveBeenCalledWith("Resposta");
    await vi.waitFor(() => expect(field()).toHaveValue(""));
    fireEvent.change(field(), { target: { value: "Outra" } });
    fireEvent.keyDown(field(), { key: "Enter", metaKey: true });
    expect(onSubmit).toHaveBeenLastCalledWith("Outra");
    await vi.waitFor(() => expect(field()).toHaveValue(""));
  });

  it("Ctrl+Enter com campo vazio não envia", () => {
    const onSubmit = vi.fn(async () => {});
    render(<AnswerBox onSubmit={onSubmit} onSkip={vi.fn()} />);
    fireEvent.keyDown(field(), { key: "Enter", ctrlKey: true });
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("desabilita enquanto envia (sem envio duplo) e limpa no sucesso", async () => {
    let finish: () => void = () => {};
    const onSubmit = vi.fn(() => new Promise<void>((r) => (finish = r)));
    render(<AnswerBox onSubmit={onSubmit} onSkip={vi.fn()} />);
    fireEvent.change(field(), { target: { value: "Resposta" } });
    fireEvent.click(send());
    fireEvent.click(send());
    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(send()).toBeDisabled();
    finish();
    await vi.waitFor(() => expect(field()).toHaveValue(""));
  });

  it("falha mantém o rascunho e mostra o erro", async () => {
    const onSubmit = vi.fn().mockRejectedValueOnce(new ApiError(409, "Não há pergunta aguardando resposta."));
    render(<AnswerBox onSubmit={onSubmit} onSkip={vi.fn()} />);
    fireEvent.change(field(), { target: { value: "Meu rascunho" } });
    fireEvent.click(send());
    expect(await screen.findByRole("alert")).toHaveTextContent("Não há pergunta aguardando resposta.");
    expect(field()).toHaveValue("Meu rascunho");
    expect(send()).toBeEnabled();
  });

  it("Pular chama onSkip", async () => {
    const onSkip = vi.fn(async () => {});
    render(<AnswerBox onSubmit={vi.fn()} onSkip={onSkip} />);
    fireEvent.click(screen.getByRole("button", { name: "Pular" }));
    expect(onSkip).toHaveBeenCalledTimes(1);
    await vi.waitFor(() => expect(screen.getByRole("button", { name: "Pular" })).toBeEnabled());
  });

  it("disabled desabilita tudo", () => {
    render(<AnswerBox disabled onSubmit={vi.fn()} onSkip={vi.fn()} />);
    fireEvent.change(field(), { target: { value: "Resposta" } });
    expect(send()).toBeDisabled();
    expect(screen.getByRole("button", { name: "Pular" })).toBeDisabled();
  });
});
