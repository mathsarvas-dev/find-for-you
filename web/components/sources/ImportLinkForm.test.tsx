import { afterEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { importLink, NeedsTextError } from "@/lib/importLink";
import { ApiError } from "@/lib/profile";
import { ImportLinkForm } from "./ImportLinkForm";

vi.mock("@/lib/importLink", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/importLink")>()),
  importLink: vi.fn(),
}));

const imp = vi.mocked(importLink);

afterEach(() => imp.mockReset());

const URL_VALUE = "https://jobs.example.com/vagas/42";

const urlField = () => screen.getByLabelText("Endereço da vaga");
const submit = () => screen.getByRole("button", { name: "Importar vaga" });

async function send(value = URL_VALUE) {
  fireEvent.change(urlField(), { target: { value } });
  await act(async () => {
    fireEvent.click(submit());
  });
}

describe("ImportLinkForm", () => {
  it("botão desabilitado com o campo vazio", () => {
    render(<ImportLinkForm />);
    expect(submit()).toBeDisabled();
  });

  it("sem dados na página: revela título e descrição e reenvia com eles", async () => {
    imp
      .mockRejectedValueOnce(new NeedsTextError("precisa de texto", "no_jsonld"))
      .mockResolvedValueOnce({ job_id: 9, created: true });
    render(<ImportLinkForm />);
    await send();
    expect(
      screen.getByText("Não consegui ler esta vaga automaticamente. Cole o título e a descrição."),
    ).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Título da vaga"), { target: { value: "Engenheiro" } });
    fireEvent.change(screen.getByLabelText("Descrição da vaga"), { target: { value: "Python e SQL" } });
    await act(async () => {
      fireEvent.click(submit());
    });
    expect(imp).toHaveBeenLastCalledWith({ url: URL_VALUE, title: "Engenheiro", description: "Python e SQL" });
    expect(screen.getByRole("status")).toHaveTextContent("Vaga importada.");
    expect(screen.getByRole("link", { name: "Ver vagas" })).toHaveAttribute("href", "/jobs");
  });

  it("texto colado nunca vira HTML", async () => {
    imp.mockRejectedValueOnce(new NeedsTextError("precisa de texto", "robots"));
    imp.mockResolvedValueOnce({ job_id: 9, created: true });
    const { container } = render(<ImportLinkForm />);
    await send();
    fireEvent.change(screen.getByLabelText("Título da vaga"), {
      target: { value: "<img src=x onerror=alert(1)>" },
    });
    fireEvent.change(screen.getByLabelText("Descrição da vaga"), { target: { value: "<script>x()</script>" } });
    await act(async () => {
      fireEvent.click(submit());
    });
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
  });

  it("vaga já importada: mensagem própria e link para as vagas", async () => {
    imp.mockResolvedValue({ job_id: 9, created: false });
    render(<ImportLinkForm />);
    await send();
    expect(screen.getByRole("status")).toHaveTextContent("Essa vaga já estava importada.");
    expect(screen.getByRole("link", { name: "Ver vagas" })).toHaveAttribute("href", "/jobs");
  });

  it("erro comum da API aparece como texto", async () => {
    imp.mockRejectedValue(new ApiError(422, "Endereço não permitido."));
    render(<ImportLinkForm />);
    await send("http://127.0.0.1/vaga");
    expect(screen.getByRole("alert")).toHaveTextContent("Endereço não permitido.");
  });

  it("desabilita o envio enquanto importa (sem envio duplo)", async () => {
    let resolve!: (v: { job_id: number; created: boolean }) => void;
    imp.mockReturnValue(new Promise((r) => (resolve = r)));
    render(<ImportLinkForm />);
    fireEvent.change(urlField(), { target: { value: URL_VALUE } });
    fireEvent.click(submit());
    fireEvent.click(submit());
    expect(imp).toHaveBeenCalledTimes(1);
    expect(submit()).toBeDisabled();
    await act(async () => resolve({ job_id: 1, created: true }));
  });
});
