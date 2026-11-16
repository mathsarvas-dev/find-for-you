import { afterEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { ApiError } from "@/lib/profile";
import { addCompany, type Company } from "@/lib/sources";
import { AddCompanyForm } from "./AddCompanyForm";

vi.mock("@/lib/sources", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/sources")>()),
  addCompany: vi.fn(),
}));

const add = vi.mocked(addCompany);

const created: Company = {
  id: 1,
  source: "greenhouse",
  identifier: "gitlab",
  name: "gitlab",
  country: null,
  active: true,
  last_run_at: null,
  last_success_at: null,
  next_run_at: "2026-11-12T12:00:00Z",
  last_error: null,
  consecutive_failures: 0,
  open_jobs: 0,
  origin: "manual",
  collect_mode: "funnel",
  stub_jobs: 0,
};

afterEach(() => add.mockReset());

const field = () => screen.getByLabelText("Token ou URL do quadro Greenhouse");
const submit = () => screen.getByRole("button", { name: /Cadastrar empresa/ });

describe("AddCompanyForm", () => {
  it("rótulo associado e ajuda de onde achar o token", () => {
    render(<AddCompanyForm onAdded={() => {}} />);
    expect(field()).toHaveAccessibleDescription(/boards\.greenhouse\.io\/<token>/);
    expect(submit()).toBeDisabled();
  });

  it("envia a URL colada como está e limpa ao dar certo", async () => {
    add.mockResolvedValue(created);
    const onAdded = vi.fn();
    render(<AddCompanyForm onAdded={onAdded} />);
    fireEvent.change(field(), { target: { value: " https://boards.greenhouse.io/gitlab " } });
    fireEvent.change(screen.getByLabelText("País (opcional)"), { target: { value: "br" } });
    await act(async () => {
      fireEvent.click(submit());
    });
    expect(add).toHaveBeenCalledWith({
      source: "greenhouse",
      identifier: "https://boards.greenhouse.io/gitlab",
      country: "BR",
    });
    expect(onAdded).toHaveBeenCalledWith(created);
    expect(field()).toHaveValue("");
    expect(screen.getByLabelText("País (opcional)")).toHaveValue("");
    expect(screen.getByRole("status")).toHaveTextContent("gitlab cadastrada");
  });

  it("sem país não manda o campo", async () => {
    add.mockResolvedValue(created);
    render(<AddCompanyForm onAdded={() => {}} />);
    fireEvent.change(field(), { target: { value: "gitlab" } });
    await act(async () => {
      fireEvent.click(submit());
    });
    expect(add).toHaveBeenCalledWith({ source: "greenhouse", identifier: "gitlab" });
  });

  it("desabilita após o primeiro clique (sem envio duplo)", async () => {
    let resolve!: (c: Company) => void;
    add.mockReturnValue(new Promise((r) => (resolve = r)));
    render(<AddCompanyForm onAdded={() => {}} />);
    fireEvent.change(field(), { target: { value: "gitlab" } });
    const button = submit();
    fireEvent.click(button);
    fireEvent.click(button);
    fireEvent.submit(field().closest("form")!);
    expect(add).toHaveBeenCalledTimes(1);
    expect(button).toBeDisabled();
    expect(button).toHaveTextContent("Validando o quadro…");
    await act(async () => resolve(created));
  });

  it.each([
    [409, "Essa empresa já está cadastrada."],
    [422, "Quadro não encontrado no Greenhouse."],
    [503, "Fonte indisponível no momento."],
    [502, "Resposta inesperada da fonte."],
  ])("erro %i: mostra o detail do servidor, mantém o texto e devolve o foco", async (status, detail) => {
    add.mockRejectedValue(new ApiError(status, detail));
    const onAdded = vi.fn();
    render(<AddCompanyForm onAdded={onAdded} />);
    fireEvent.change(field(), { target: { value: "gitlb" } });
    await act(async () => {
      fireEvent.click(submit());
    });
    expect(screen.getByRole("alert")).toHaveTextContent(detail);
    expect(field()).toHaveValue("gitlb");
    expect(field()).toHaveAttribute("aria-invalid", "true");
    expect(field()).toHaveFocus();
    expect(submit()).toBeEnabled();
    expect(onAdded).not.toHaveBeenCalled();
  });

  it("país inválido é recusado antes de enviar", async () => {
    render(<AddCompanyForm onAdded={() => {}} />);
    fireEvent.change(field(), { target: { value: "gitlab" } });
    fireEvent.change(screen.getByLabelText("País (opcional)"), { target: { value: "B1" } });
    await act(async () => {
      fireEvent.click(submit());
    });
    expect(add).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent(/2 letras/);
  });
});
