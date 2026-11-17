import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { parseTab, SourcesTabs } from "./SourcesTabs";

describe("SourcesTabs", () => {
  it("quatro abas, a selecionada marcada e única no tab order", () => {
    render(<SourcesTabs tab="feeds" onChange={() => {}} />);
    expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual([
      "Empresas",
      "Feeds",
      "Portais",
      "Importar por link",
    ]);
    expect(screen.getByRole("tab", { name: "Feeds" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "Feeds" })).toHaveAttribute("tabindex", "0");
    expect(screen.getByRole("tab", { name: "Empresas" })).toHaveAttribute("tabindex", "-1");
  });

  it("clique chama onChange com o id da aba", () => {
    const onChange = vi.fn();
    render(<SourcesTabs tab="empresas" onChange={onChange} />);
    fireEvent.click(screen.getByRole("tab", { name: "Portais" }));
    expect(onChange).toHaveBeenCalledWith("portais");
  });

  it("setas trocam de aba com volta ao início; Home e End vão às pontas", () => {
    const onChange = vi.fn();
    render(<SourcesTabs tab="empresas" onChange={onChange} />);
    const first = screen.getByRole("tab", { name: "Empresas" });
    fireEvent.keyDown(first, { key: "ArrowRight" });
    expect(onChange).toHaveBeenLastCalledWith("feeds");
    expect(screen.getByRole("tab", { name: "Feeds" })).toHaveFocus();
    fireEvent.keyDown(first, { key: "ArrowLeft" });
    expect(onChange).toHaveBeenLastCalledWith("link");
    fireEvent.keyDown(first, { key: "End" });
    expect(onChange).toHaveBeenLastCalledWith("link");
    fireEvent.keyDown(first, { key: "Home" });
    expect(onChange).toHaveBeenLastCalledWith("empresas");
  });

  it("parseTab: id conhecido passa; ausente ou desconhecido cai em empresas", () => {
    expect(parseTab("portais")).toBe("portais");
    expect(parseTab("link")).toBe("link");
    expect(parseTab("nada")).toBe("empresas");
    expect(parseTab(null)).toBe("empresas");
  });
});
