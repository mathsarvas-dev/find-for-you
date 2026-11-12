import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { VectorStatus } from "./VectorStatus";

describe("VectorStatus", () => {
  it("mostra o progresso como texto e barra", () => {
    render(<VectorStatus total={8} embedded={3} />);
    expect(screen.getByText("3 de 8")).toBeInTheDocument();
    const bar = screen.getByRole("progressbar");
    expect(bar).toHaveAttribute("aria-valuenow", "3");
    expect(bar).toHaveAttribute("aria-valuemax", "8");
    expect(screen.queryByText(/Perfil pronto/)).not.toBeInTheDocument();
  });

  it("mostra o estado concluído", () => {
    render(<VectorStatus total={8} embedded={8} />);
    expect(screen.getByText("8 de 8")).toBeInTheDocument();
    expect(screen.getByText(/Perfil pronto/)).toBeInTheDocument();
  });

  it("avisa com honestidade quando está demorando", () => {
    render(<VectorStatus total={8} embedded={0} slow />);
    expect(screen.getByText(/a primeira vez baixa\/carrega o modelo local e pode levar alguns minutos/)).toBeInTheDocument();
  });

  it("não mostra o aviso de demora quando já terminou", () => {
    render(<VectorStatus total={8} embedded={8} slow />);
    expect(screen.queryByText(/pode levar alguns minutos/)).not.toBeInTheDocument();
  });
});
