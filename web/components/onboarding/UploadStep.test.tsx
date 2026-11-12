import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { ApiError, MAX_UPLOAD_BYTES, uploadResume } from "@/lib/profile";
import { UploadStep } from "./UploadStep";

vi.mock("@/lib/profile", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/profile")>()),
  uploadResume: vi.fn(),
}));

const upload = vi.mocked(uploadResume);

function choose(file: File) {
  fireEvent.change(screen.getByLabelText(/Arquivo do currículo/), { target: { files: [file] } });
}

const pdf = (size = 10, name = "curriculo-mariana.pdf") =>
  new File([new Uint8Array(size)], name, { type: "application/pdf" });

afterEach(() => upload.mockReset());

describe("UploadStep", () => {
  it("rejeita arquivo maior que 5 MB com mensagem e não envia", () => {
    render(<UploadStep onUploaded={vi.fn()} />);
    choose(pdf(MAX_UPLOAD_BYTES + 200_000));
    expect(screen.getByRole("alert")).toHaveTextContent("O arquivo tem 5,2 MB; o limite é 5 MB.");
    expect(screen.getByRole("button", { name: "Enviar currículo" })).toBeDisabled();
    expect(upload).not.toHaveBeenCalled();
  });

  it("rejeita tipo errado com mensagem", () => {
    render(<UploadStep onUploaded={vi.fn()} />);
    choose(new File(["x"], "foto.png", { type: "image/png" }));
    expect(screen.getByRole("alert")).toHaveTextContent("“foto.png” não é PDF nem DOCX.");
    expect(screen.getByRole("button", { name: "Enviar currículo" })).toBeDisabled();
  });

  it("antes de escolher, explica o que fazer e o botão fica desabilitado", () => {
    render(<UploadStep onUploaded={vi.fn()} />);
    expect(screen.getByText(/Nenhum arquivo escolhido/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Enviar currículo" })).toBeDisabled();
  });

  it("mostra o arquivo escolhido, o estado enviando e chama onUploaded", async () => {
    let resolve!: (v: { id: number }) => void;
    upload.mockReturnValue(new Promise((r) => (resolve = r)));
    const onUploaded = vi.fn();
    render(<UploadStep onUploaded={onUploaded} />);
    const file = pdf(2048);
    choose(file);
    expect(screen.getByText("curriculo-mariana.pdf")).toBeInTheDocument();
    expect(screen.getByText("2 KB")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Enviar currículo" }));
    const sending = screen.getByRole("button", { name: /Enviando/ });
    expect(sending).toBeDisabled();
    fireEvent.click(sending);
    expect(upload).toHaveBeenCalledTimes(1);

    resolve({ id: 42 });
    await vi.waitFor(() => expect(onUploaded).toHaveBeenCalledWith({ id: 42 }, file));
  });

  it("mostra o erro do servidor e permite tentar de novo", async () => {
    upload.mockRejectedValueOnce(new ApiError(422, "Não foi possível ler o arquivo PDF (corrompido?)."));
    render(<UploadStep onUploaded={vi.fn()} />);
    choose(pdf());
    fireEvent.click(screen.getByRole("button", { name: "Enviar currículo" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Não foi possível ler o arquivo PDF (corrompido?).",
    );
    expect(screen.getByRole("button", { name: "Enviar currículo" })).toBeEnabled();
  });
});
