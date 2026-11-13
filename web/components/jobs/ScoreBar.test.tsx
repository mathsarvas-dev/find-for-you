import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { ScoreBar } from "./ScoreBar";

describe("ScoreBar", () => {
  it("nota da IA: número dominante, barra proporcional, sem legenda de pré-seleção", () => {
    const { container } = render(<ScoreBar aiScore={82} vecScore={0.71} />);
    const root = container.firstElementChild as HTMLElement;
    expect(root).toHaveAttribute("data-source", "ai");
    expect(screen.getByText("82")).toBeInTheDocument();
    expect(screen.queryByText("pré-seleção")).toBeNull();
    expect(screen.getByText("Nota da IA: 82 de 100")).toHaveClass("sr-only");
    const fill = container.querySelector(".score__fill") as HTMLElement;
    expect(fill.style.width).toBe("82%");
  });

  it("sem nota da IA: vetorial em 0–100, cinza, com a legenda pré-seleção", () => {
    const { container } = render(<ScoreBar aiScore={null} vecScore={0.784} />);
    expect(container.firstElementChild).toHaveAttribute("data-source", "vector");
    expect(screen.getByText("78")).toBeInTheDocument();
    expect(screen.getByText("pré-seleção")).toBeInTheDocument();
    expect(screen.getByText("Pré-seleção vetorial: 78 de 100")).toBeInTheDocument();
  });

  it("sem nenhuma nota: traço e texto explicando", () => {
    const { container } = render(<ScoreBar aiScore={null} vecScore={null} />);
    expect(container.firstElementChild).toHaveAttribute("data-source", "none");
    expect(screen.getByText("—")).toBeInTheDocument();
    expect(screen.getByText("Sem nota")).toBeInTheDocument();
  });
});
