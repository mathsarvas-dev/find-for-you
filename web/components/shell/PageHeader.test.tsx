import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { PageHeader, PageTitleContext } from "./PageHeader";

describe("PageHeader", () => {
  it("renderiza o h1, a descrição, as ações e a trilha", () => {
    render(
      <PageHeader
        title="Fontes"
        description="Empresas que o Rumo vigia."
        actions={<button type="button">Adicionar empresa</button>}
        stepper={<ol aria-label="Passos" />}
      />,
    );
    expect(screen.getByRole("heading", { level: 1, name: "Fontes" })).toBeInTheDocument();
    expect(screen.getByText("Empresas que o Rumo vigia.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Adicionar empresa" }).closest(".page-header__actions")).not.toBeNull();
    expect(screen.getByRole("list", { name: "Passos" })).toBeInTheDocument();
  });

  it("sem descrição nem ações não renderiza os contêineres", () => {
    const { container } = render(<PageHeader title="Vagas" />);
    expect(container.querySelector(".page-header__actions")).toBeNull();
    expect(container.querySelector("p")).toBeNull();
  });

  it("publica o título no contexto e limpa ao desmontar", () => {
    const set = vi.fn();
    const { unmount } = render(
      <PageTitleContext.Provider value={set}>
        <PageHeader title="Vagas" />
      </PageTitleContext.Provider>,
    );
    expect(set).toHaveBeenLastCalledWith("Vagas");
    unmount();
    expect(set).toHaveBeenLastCalledWith(null);
  });
});
