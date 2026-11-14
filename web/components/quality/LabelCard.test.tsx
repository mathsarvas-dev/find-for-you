import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { LabelCard } from "./LabelCard";
import { sampleItem } from "./fixtures";

type Props = React.ComponentProps<typeof LabelCard>;

function setup(item = sampleItem(), extra: Partial<Props> = {}) {
  const fns = { onLabel: vi.fn(), onSkip: vi.fn(), onPrevious: vi.fn(), onClear: vi.fn() };
  const view = render(<LabelCard item={item} {...fns} {...extra} />);
  return { ...fns, view };
}

describe("LabelCard", () => {
  it("mostra título, empresa, país e trecho", () => {
    setup();
    expect(screen.getByRole("heading", { name: "Engenheira de dados" })).toBeInTheDocument();
    expect(screen.getByText("Acme · BR")).toBeInTheDocument();
    expect(screen.getByText("Pipelines em Python.")).toBeInTheDocument();
  });

  it("botões chamam os callbacks", () => {
    const f = setup();
    fireEvent.click(screen.getByRole("button", { name: "Boa" }));
    fireEvent.click(screen.getByRole("button", { name: "Ruim" }));
    fireEvent.click(screen.getByRole("button", { name: "Pular" }));
    fireEvent.click(screen.getByRole("button", { name: "Anterior" }));
    expect(f.onLabel.mock.calls).toEqual([["good"], ["bad"]]);
    expect(f.onSkip).toHaveBeenCalledTimes(1);
    expect(f.onPrevious).toHaveBeenCalledTimes(1);
  });

  it("Remover rótulo só aparece com a vaga rotulada", () => {
    const a = setup();
    expect(screen.queryByRole("button", { name: "Remover rótulo" })).toBeNull();
    a.view.unmount();
    const f = setup(sampleItem({ label: "good" }));
    fireEvent.click(screen.getByRole("button", { name: "Remover rótulo" }));
    expect(f.onClear).toHaveBeenCalledTimes(1);
  });

  it("atalhos g e b rotulam", () => {
    const f = setup();
    fireEvent.keyDown(window, { key: "g" });
    fireEvent.keyDown(window, { key: "b" });
    expect(f.onLabel.mock.calls).toEqual([["good"], ["bad"]]);
  });

  it("atalhos são ignorados ao digitar ou com modificador", () => {
    const f = setup();
    const input = document.createElement("input");
    const area = document.createElement("textarea");
    const ce = document.createElement("div");
    Object.defineProperty(ce, "isContentEditable", { value: true });
    document.body.append(input, area, ce);
    fireEvent.keyDown(input, { key: "g" });
    fireEvent.keyDown(area, { key: "b" });
    fireEvent.keyDown(ce, { key: "g" });
    fireEvent.keyDown(window, { key: "g", ctrlKey: true });
    fireEvent.keyDown(window, { key: "b", metaKey: true });
    fireEvent.keyDown(window, { key: "b", altKey: true });
    expect(f.onLabel).not.toHaveBeenCalled();
    input.remove();
    area.remove();
    ce.remove();
  });

  it("ocupado: botões e atalhos ficam parados", () => {
    const f = setup(sampleItem(), { busy: true });
    expect(screen.getByRole("button", { name: "Boa" })).toBeDisabled();
    fireEvent.keyDown(window, { key: "g" });
    expect(f.onLabel).not.toHaveBeenCalled();
  });

  it("link só para http/https, com rel seguro", () => {
    const a = setup();
    const link = screen.getByRole("link", { name: "Engenheira de dados" });
    expect(link).toHaveAttribute("href", "https://acme.example/jobs/1");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(link).toHaveAttribute("target", "_blank");
    a.view.unmount();
    for (const url of ["javascript:alert(1)", "data:text/html,x", "ftp://x/y", "nao e url", null]) {
      const v = setup(sampleItem({ url }));
      expect(screen.queryByRole("link")).toBeNull();
      expect(screen.getByText("Engenheira de dados")).toBeInTheDocument();
      v.view.unmount();
    }
  });

  it("texto hostil aparece literal, sem elementos", () => {
    const evil = "<img src=x onerror=alert(1)>";
    const { view } = setup(sampleItem({ title: evil, company_name: evil, snippet: evil, url: null }));
    expect(view.container.querySelector("img")).toBeNull();
    expect(screen.getAllByText(/<img src=x onerror=alert\(1\)>/)).toHaveLength(3);
  });
});
