import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { StatusRow } from "./StatusRow";

describe("StatusRow", () => {
  it("mostra rótulo e estado ok como texto", () => {
    render(<StatusRow label="API" state="ok" />);
    const row = screen.getByRole("listitem");
    expect(row).toHaveTextContent("API");
    expect(row).toHaveTextContent("Funcionando");
    expect(row).toHaveAttribute("data-state", "ok");
  });

  it("mostra rótulo e estado down como texto", () => {
    render(<StatusRow label="Banco de dados" state="down" />);
    const row = screen.getByRole("listitem");
    expect(row).toHaveTextContent("Banco de dados");
    expect(row).toHaveTextContent("Fora do ar");
    expect(row).toHaveAttribute("data-state", "down");
  });

  it("mostra rótulo e estado loading como texto, com esqueleto e aria-busy", () => {
    render(<StatusRow label="API" state="loading" />);
    const row = screen.getByRole("listitem");
    expect(row).toHaveTextContent("API");
    expect(row).toHaveTextContent("Verificando");
    expect(row).toHaveAttribute("aria-busy", "true");
  });

  it("mostra o detalhe técnico quando informado", () => {
    render(<StatusRow label="API" state="ok" detail="GET /health · 200" />);
    expect(screen.getByText("GET /health · 200")).toBeInTheDocument();
  });
});
