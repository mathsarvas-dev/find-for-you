import { afterEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { parseFilters, type ParsedQuery } from "@/lib/chatFilters";
import { ApiError } from "@/lib/profile";
import { parsedQuery } from "./fixtures";
import { ChatBar } from "./ChatBar";

vi.mock("@/lib/chatFilters", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/chatFilters")>()),
  parseFilters: vi.fn(),
}));

const parse = vi.mocked(parseFilters);

afterEach(() => {
  parse.mockReset();
});

const input = () => screen.getByRole("textbox", { name: "Descreva o que procura" });
const region = () => screen.queryByRole("region", { name: "Proposta de filtros" });

async function ask(text: string) {
  fireEvent.change(input(), { target: { value: text } });
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Aplicar" }));
  });
}

function renderBar(onApply: (p: ParsedQuery) => Promise<void> = vi.fn(async () => {})) {
  render(<ChatBar onApply={onApply} />);
  return onApply;
}

describe("ChatBar", () => {
  it("campo rotulado com placeholder e limite de 300", () => {
    renderBar();
    expect(input()).toHaveAttribute("placeholder", "Descreva o que procura…");
    expect(input()).toHaveAttribute("maxLength", "300");
  });

  it("frase vira proposta com chips e notas; nada é aplicado antes de confirmar", async () => {
    const proposal = parsedQuery(
      { work_models: ["remote"], exclude_seniorities: ["intern"] },
      { query_text: "dados", notes: ["ignorei 'ótimo salário' (sem valor)"] },
    );
    parse.mockResolvedValue(proposal);
    const onApply = renderBar();
    await ask("  remoto, sem estágio, algo com dados ");
    expect(parse).toHaveBeenCalledWith("remoto, sem estágio, algo com dados");
    const r = region()!;
    expect(within(r).getByText("remoto")).toBeInTheDocument();
    expect(within(r).getByText("sem estágio")).toBeInTheDocument();
    expect(within(r).getByText("gosto: dados")).toBeInTheDocument();
    expect(within(r).getByText("ignorei 'ótimo salário' (sem valor)")).toBeInTheDocument();
    expect(onApply).not.toHaveBeenCalled();
  });

  it("Aplicar chama onApply com a proposta e limpa a região", async () => {
    const proposal = parsedQuery({ countries: ["PT"] });
    parse.mockResolvedValue(proposal);
    const onApply = renderBar();
    await ask("Portugal");
    await act(async () => {
      fireEvent.click(within(region()!).getByRole("button", { name: "Aplicar" }));
    });
    expect(onApply).toHaveBeenCalledTimes(1);
    expect(onApply).toHaveBeenCalledWith(proposal);
    expect(region()).toBeNull();
    expect(input()).toHaveValue("");
  });

  it("Aplicar que falha mantém a proposta", async () => {
    parse.mockResolvedValue(parsedQuery({ countries: ["PT"] }));
    const onApply = vi.fn(async () => {
      throw new Error("falhou");
    });
    renderBar(onApply);
    await ask("Portugal");
    await act(async () => {
      fireEvent.click(within(region()!).getByRole("button", { name: "Aplicar" }));
    });
    expect(onApply).toHaveBeenCalledTimes(1);
    expect(region()).not.toBeNull();
  });

  it("Descartar limpa sem chamar onApply", async () => {
    parse.mockResolvedValue(parsedQuery({ countries: ["PT"] }));
    const onApply = renderBar();
    await ask("Portugal");
    fireEvent.click(within(region()!).getByRole("button", { name: "Descartar" }));
    expect(region()).toBeNull();
    expect(onApply).not.toHaveBeenCalled();
  });

  it("proposta vazia mostra a nota e não deixa aplicar", async () => {
    parse.mockResolvedValue(parsedQuery({}, { notes: ["não encontrei filtros nessa frase"] }));
    renderBar();
    await ask("oi");
    const r = region()!;
    expect(within(r).getByText("não encontrei filtros nessa frase")).toBeInTheDocument();
    expect(within(r).getByRole("button", { name: "Aplicar" })).toBeDisabled();
  });

  it("enquanto interpreta, o botão fica ocupado", async () => {
    let resolve!: (p: ParsedQuery) => void;
    parse.mockReturnValue(new Promise((r) => (resolve = r)));
    renderBar();
    fireEvent.change(input(), { target: { value: "remoto" } });
    fireEvent.click(screen.getByRole("button", { name: "Aplicar" }));
    const button = screen.getByRole("button", { name: "Aplicar" });
    expect(button).toHaveAttribute("aria-busy", "true");
    expect(button).toBeDisabled();
    await act(async () => resolve(parsedQuery({ work_models: ["remote"] })));
    expect(screen.getAllByRole("button", { name: "Aplicar" })[0]).not.toHaveAttribute("aria-busy");
  });

  it("frase vazia não chama a API", async () => {
    renderBar();
    expect(screen.getByRole("button", { name: "Aplicar" })).toBeDisabled();
    fireEvent.change(input(), { target: { value: "   " } });
    fireEvent.submit(input().closest("form")!);
    expect(parse).not.toHaveBeenCalled();
  });

  it("503 mostra a mensagem; Tentar de novo reenvia a mesma frase", async () => {
    parse.mockRejectedValueOnce(new ApiError(503, "O interpretador está ocupado; tente em instantes."));
    renderBar();
    await ask("remoto na Europa");
    expect(screen.getByRole("alert")).toHaveTextContent("O interpretador está ocupado; tente em instantes.");
    // Mesmo que a pessoa mude o campo, "Tentar de novo" reenvia a frase que falhou.
    fireEvent.change(input(), { target: { value: "outra coisa" } });
    parse.mockResolvedValueOnce(parsedQuery({ work_models: ["remote"] }));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Tentar de novo" }));
    });
    expect(parse).toHaveBeenCalledTimes(2);
    expect(parse).toHaveBeenLastCalledWith("remoto na Europa");
    expect(screen.queryByRole("alert")).toBeNull();
    expect(region()).not.toBeNull();
  });

  it("422, 429 e rede mostram a mensagem da API", async () => {
    renderBar();
    for (const err of [
      new ApiError(422, "Não consegui transformar essa frase em filtros."),
      new ApiError(429, "Você atingiu o limite de 20 frases por hora."),
      new ApiError(null, "Não consegui falar com a API em http://x."),
    ]) {
      parse.mockRejectedValueOnce(err);
      await ask("remoto");
      expect(screen.getByRole("alert")).toHaveTextContent(err.message);
      expect(screen.getByRole("button", { name: "Tentar de novo" })).toBeInTheDocument();
    }
  });

  it("erro inesperado mostra uma mensagem genérica", async () => {
    parse.mockRejectedValueOnce(new TypeError("boom"));
    renderBar();
    await ask("remoto");
    expect(screen.getByRole("alert")).toHaveTextContent(/Algo deu errado/);
  });

  it("disabled desliga o campo e o botão", () => {
    render(<ChatBar onApply={vi.fn()} disabled />);
    expect(input()).toBeDisabled();
    expect(screen.getByRole("button", { name: "Aplicar" })).toBeDisabled();
  });

  it("nota e chip hostis aparecem como texto, nunca como HTML", async () => {
    const hostile = "<img src=x onerror=alert(1)>";
    parse.mockResolvedValue(parsedQuery({ title_exclude: [hostile] }, { notes: [hostile], query_text: hostile }));
    const { container } = render(<ChatBar onApply={vi.fn()} />);
    await ask("qualquer");
    const r = region()!;
    expect(within(r).getByText(hostile)).toBeInTheDocument();
    expect(within(r).getByText(`sem ${hostile}`)).toBeInTheDocument();
    expect(within(r).getByText(`gosto: ${hostile}`)).toBeInTheDocument();
    expect(container.querySelector("img")).toBeNull();
  });
});
