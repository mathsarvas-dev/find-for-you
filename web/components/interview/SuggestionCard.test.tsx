import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { ApiError } from "@/lib/profile";
import type { Suggestion } from "@/lib/interview";
import { SuggestionCard } from "./SuggestionCard";

const sug = (extra: Partial<Suggestion> = {}): Suggestion => ({
  id: 7,
  message_id: 2,
  gap_id: "achievement_without_number:e1",
  op: "append_achievement",
  payload: { op: "append_achievement", target_ref: "e1", text: "Reduzi o tempo de fechamento em 30%" },
  status: "pending",
  applied_version: null,
  before: "Nenhuma conquista registrada",
  after: "Reduzi o tempo de fechamento em 30%",
  ...extra,
});

describe("SuggestionCard", () => {
  it("mostra antes e depois com rótulos de texto", () => {
    render(<SuggestionCard suggestion={sug()} onResolve={vi.fn()} />);
    const card = screen.getByRole("article");
    expect(within(card).getByText("Hoje no perfil")).toBeInTheDocument();
    expect(within(card).getByText("Nenhuma conquista registrada")).toBeInTheDocument();
    expect(within(card).getByText("Proposta")).toBeInTheDocument();
    expect(within(card).getByText("Reduzi o tempo de fechamento em 30%")).toBeInTheDocument();
    expect(within(card).getByText("Nova conquista")).toBeInTheDocument();
  });

  it("aceitar chama onResolve('accept') uma vez mesmo com duplo clique", async () => {
    const onResolve = vi.fn(() => new Promise<void>(() => {}));
    render(<SuggestionCard suggestion={sug()} onResolve={onResolve} />);
    const accept = screen.getByRole("button", { name: "Aceitar" });
    fireEvent.click(accept);
    fireEvent.click(accept);
    expect(onResolve).toHaveBeenCalledTimes(1);
    expect(onResolve).toHaveBeenCalledWith("accept");
    expect(screen.getByRole("button", { name: "Descartar" })).toBeDisabled();
  });

  it("descartar chama onResolve('reject')", async () => {
    const onResolve = vi.fn(async () => {});
    render(<SuggestionCard suggestion={sug()} onResolve={onResolve} />);
    fireEvent.click(screen.getByRole("button", { name: "Descartar" }));
    expect(onResolve).toHaveBeenCalledWith("reject");
    await vi.waitFor(() => expect(screen.getByRole("button", { name: "Aceitar" })).toBeDisabled());
  });

  it("ajustar abre edição do texto e envia o payload editado", async () => {
    const onResolve = vi.fn(async () => {});
    render(<SuggestionCard suggestion={sug()} onResolve={onResolve} />);
    fireEvent.click(screen.getByRole("button", { name: "Ajustar" }));
    const field = screen.getByLabelText("Texto da conquista");
    expect(field).toHaveValue("Reduzi o tempo de fechamento em 30%");
    fireEvent.change(field, { target: { value: "Reduzi o fechamento mensal em 30%" } });
    fireEvent.click(screen.getByRole("button", { name: "Salvar ajuste" }));
    expect(onResolve).toHaveBeenCalledWith("adjust", {
      op: "append_achievement",
      target_ref: "e1",
      text: "Reduzi o fechamento mensal em 30%",
    });
    await vi.waitFor(() => expect(screen.getByRole("button", { name: "Salvar ajuste" })).toBeDisabled());
  });

  it("ajustar datas edita início e fim", async () => {
    const onResolve = vi.fn(async () => {});
    const s = sug({
      op: "set_experience_dates",
      payload: { op: "set_experience_dates", target_ref: "e1", start: "2020-01", end: "" },
      before: "— – —",
      after: "2020-01 – —",
    });
    render(<SuggestionCard suggestion={s} onResolve={onResolve} />);
    fireEvent.click(screen.getByRole("button", { name: "Ajustar" }));
    fireEvent.change(screen.getByLabelText("Fim"), { target: { value: "2022-06" } });
    fireEvent.click(screen.getByRole("button", { name: "Salvar ajuste" }));
    expect(onResolve).toHaveBeenCalledWith("adjust", {
      op: "set_experience_dates",
      target_ref: "e1",
      start: "2020-01",
      end: "2022-06",
    });
    await vi.waitFor(() => expect(screen.getByRole("button", { name: "Salvar ajuste" })).toBeDisabled());
  });

  it("422 no ajuste mostra o detail do servidor e deixa corrigir", async () => {
    const onResolve = vi
      .fn()
      .mockRejectedValueOnce(new ApiError(422, "O número 45% não aparece na sua resposta."));
    render(<SuggestionCard suggestion={sug()} onResolve={onResolve} />);
    fireEvent.click(screen.getByRole("button", { name: "Ajustar" }));
    fireEvent.change(screen.getByLabelText("Texto da conquista"), { target: { value: "Reduzi 45%" } });
    fireEvent.click(screen.getByRole("button", { name: "Salvar ajuste" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("O número 45% não aparece na sua resposta.");
    expect(screen.getByLabelText("Texto da conquista")).toHaveValue("Reduzi 45%");
    expect(screen.getByRole("button", { name: "Salvar ajuste" })).toBeEnabled();
  });

  it("409 mostra que o item mudou e oferece só Descartar", async () => {
    const onResolve = vi
      .fn()
      .mockRejectedValueOnce(new ApiError(409, "O item mudou; descarte esta sugestão"))
      .mockResolvedValueOnce(undefined);
    render(<SuggestionCard suggestion={sug()} onResolve={onResolve} />);
    fireEvent.click(screen.getByRole("button", { name: "Aceitar" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("O item mudou; descarte esta sugestão");
    expect(screen.queryByRole("button", { name: "Aceitar" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Ajustar" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Descartar" }));
    expect(onResolve).toHaveBeenLastCalledWith("reject");
    await vi.waitFor(() => expect(onResolve).toHaveBeenCalledTimes(2));
  });

  it.each([
    ["accepted", "Aceita"],
    ["adjusted", "Ajustada"],
    ["rejected", "Descartada"],
  ] as const)("resolvida (%s) mostra o status sem botões", (status, label) => {
    render(<SuggestionCard suggestion={sug({ status })} onResolve={vi.fn()} />);
    expect(screen.getByText(label)).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
  it("409 durante o ajuste: sai da edição e ainda oferece Descartar", async () => {
    const onResolve = vi
      .fn()
      .mockRejectedValueOnce(new ApiError(409, "O item mudou; descarte esta sugestão"))
      .mockResolvedValueOnce(undefined);
    render(<SuggestionCard suggestion={sug()} onResolve={onResolve} />);
    fireEvent.click(screen.getByRole("button", { name: "Ajustar" }));
    fireEvent.change(screen.getByLabelText("Texto da conquista"), { target: { value: "Reduzi em 30%" } });
    fireEvent.click(screen.getByRole("button", { name: "Salvar ajuste" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("O item mudou; descarte esta sugestão");
    expect(screen.queryByRole("button", { name: "Salvar ajuste" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Ajustar" })).not.toBeInTheDocument();
    const discard = screen.getByRole("button", { name: "Descartar" });
    expect(discard).toBeEnabled();
    fireEvent.click(discard);
    expect(onResolve).toHaveBeenLastCalledWith("reject");
    await vi.waitFor(() => expect(onResolve).toHaveBeenCalledTimes(2));
  });
});
