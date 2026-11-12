import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import type { Message, Suggestion } from "@/lib/interview";
import { ChatThread } from "./ChatThread";

const messages: Message[] = [
  { id: 1, role: "assistant", kind: "question", content: "Quantos pedidos você fechava por mês?", gap_id: "g1" },
  { id: 2, role: "user", kind: "answer", content: "Uns 300.", gap_id: "g1" },
  { id: 3, role: "assistant", kind: "question", content: "Qual era o seu cargo em 2021?", gap_id: "g2" },
  { id: 4, role: "user", kind: "skip", content: "(pulou a pergunta)", gap_id: "g2" },
];

const suggestion: Suggestion = {
  id: 9,
  message_id: 2,
  gap_id: "g1",
  op: "add_note",
  payload: { op: "add_note", text: "Fechava cerca de 300 pedidos por mês" },
  status: "pending",
  applied_version: null,
  before: "—",
  after: "Fechava cerca de 300 pedidos por mês",
};

describe("ChatThread", () => {
  it("mostra perguntas, respostas e pulos em ordem", () => {
    render(<ChatThread messages={messages} suggestions={[]} onResolve={vi.fn()} />);
    const items = within(screen.getByRole("list", { name: "Conversa até aqui" })).getAllByRole("listitem");
    expect(items).toHaveLength(4);
    expect(items[0]).toHaveTextContent("Quantos pedidos você fechava por mês?");
    expect(items[1]).toHaveTextContent("Sua resposta");
    expect(items[1]).toHaveTextContent("Uns 300.");
    expect(items[3]).toHaveTextContent("Pergunta pulada");
  });

  it("sugestão aparece logo abaixo da resposta que a gerou e repassa o id", () => {
    const onResolve = vi.fn(() => new Promise<void>(() => {}));
    render(<ChatThread messages={messages} suggestions={[suggestion]} onResolve={onResolve} />);
    const items = within(screen.getByRole("list", { name: "Conversa até aqui" })).getAllByRole("listitem");
    expect(within(items[1]).getByRole("article")).toHaveTextContent("Fechava cerca de 300 pedidos por mês");
    fireEvent.click(within(items[1]).getByRole("button", { name: "Aceitar" }));
    expect(onResolve).toHaveBeenCalledWith(9, "accept", undefined);
  });
});
