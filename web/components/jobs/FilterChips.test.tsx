import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import type { SearchFilters } from "@/lib/matching";
import { filters } from "./fixtures";
import { FilterChips } from "./FilterChips";

function renderChips(
  f: SearchFilters = filters(),
  { queryText = null, queryPending = false }: { queryText?: string | null; queryPending?: boolean } = {},
) {
  const onRemove = vi.fn();
  const onEditQuery = vi.fn();
  const view = render(
    <FilterChips
      filters={f}
      queryText={queryText}
      queryPending={queryPending}
      onRemove={onRemove}
      onEditQuery={onEditQuery}
    />,
  );
  return { ...view, onRemove, onEditQuery };
}

describe("FilterChips", () => {
  it("um chip por valor; remover chama onRemove com o chip certo", () => {
    const { onRemove } = renderChips(filters({ countries: ["BR", "PT"], work_models: ["remote"] }));
    expect(screen.getAllByRole("listitem")).toHaveLength(3);
    fireEvent.click(screen.getByRole("button", { name: "Remover filtro PT" }));
    expect(onRemove).toHaveBeenCalledTimes(1);
    expect(onRemove).toHaveBeenCalledWith(
      expect.objectContaining({ field: "countries", value: "PT", kind: "include" }),
    );
  });

  it("inclusão e exclusão têm classes próprias; a exclusão mostra 'sem' e um ícone decorativo", () => {
    const { container } = renderChips(
      filters({ work_models: ["remote"], exclude_seniorities: ["intern"], title_exclude: ["plantão"] }),
    );
    const include = screen.getByText("remoto").closest("li")!;
    expect(include).toHaveClass("chip--include");
    const exclude = screen.getByText("sem estágio").closest("li")!;
    expect(exclude).toHaveClass("chip--exclude");
    expect(exclude).toHaveTextContent(/sem estágio/);
    expect(exclude.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
    expect(screen.getByText("sem plantão")).toBeVisible();
    expect(screen.getByRole("button", { name: "Remover filtro sem estágio" })).toBeInTheDocument();
    // Ícones dentro dos botões também são decorativos.
    container.querySelectorAll("svg").forEach((svg) => expect(svg).toHaveAttribute("aria-hidden", "true"));
  });

  it("sem filtros nem consulta não mostra nada", () => {
    const { container } = renderChips();
    expect(container).toBeEmptyDOMElement();
  });

  it("editar a consulta: Enter salva o texto novo", () => {
    const { onEditQuery } = renderChips(filters(), { queryText: "dados" });
    fireEvent.click(screen.getByRole("button", { name: /^gosto: dados/ }));
    const input = screen.getByRole("textbox", { name: "Editar consulta" });
    expect(input).toHaveValue("dados");
    expect(input).toHaveFocus();
    fireEvent.change(input, { target: { value: "  produto " } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onEditQuery).toHaveBeenCalledWith("produto");
    expect(screen.queryByRole("textbox", { name: "Editar consulta" })).toBeNull();
  });

  it("editar a consulta: Escape cancela sem chamar", () => {
    const { onEditQuery } = renderChips(filters(), { queryText: "dados" });
    fireEvent.click(screen.getByRole("button", { name: /^gosto: dados/ }));
    const input = screen.getByRole("textbox", { name: "Editar consulta" });
    fireEvent.change(input, { target: { value: "produto" } });
    fireEvent.keyDown(input, { key: "Escape" });
    expect(onEditQuery).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /^gosto: dados/ })).toHaveFocus();
  });

  it("editar a consulta: vazio remove", () => {
    const { onEditQuery } = renderChips(filters(), { queryText: "dados" });
    fireEvent.click(screen.getByRole("button", { name: /^gosto: dados/ }));
    const input = screen.getByRole("textbox", { name: "Editar consulta" });
    fireEvent.change(input, { target: { value: "   " } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onEditQuery).toHaveBeenCalledWith(null);
  });

  it("editar a consulta sem mudar o texto não salva", () => {
    const { onEditQuery } = renderChips(filters(), { queryText: "dados" });
    fireEvent.click(screen.getByRole("button", { name: /^gosto: dados/ }));
    fireEvent.keyDown(screen.getByRole("textbox", { name: "Editar consulta" }), { key: "Enter" });
    expect(onEditQuery).not.toHaveBeenCalled();
  });

  it("selo 'consulta semântica pendente'", () => {
    renderChips(filters(), { queryText: "dados", queryPending: true });
    expect(screen.getByText("consulta semântica pendente")).toBeInTheDocument();
  });

  it("sem pendência não há selo", () => {
    renderChips(filters(), { queryText: "dados" });
    expect(screen.queryByText("consulta semântica pendente")).toBeNull();
  });

  it("rótulo hostil aparece como texto, nunca como HTML", () => {
    const hostile = "<img src=x onerror=alert(1)>";
    const { container } = renderChips(filters({ title_exclude: [hostile] }), { queryText: hostile });
    expect(screen.getByText(`sem ${hostile}`)).toBeInTheDocument();
    expect(screen.getByText(`gosto: ${hostile}`)).toBeInTheDocument();
    expect(container.querySelector("img")).toBeNull();
  });
});
