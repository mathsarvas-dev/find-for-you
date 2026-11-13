import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { DEFAULT_FILTERS, type SearchFilters } from "@/lib/matching";
import { FilterBar } from "./FilterBar";

async function tick(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

function renderBar(initial: SearchFilters = DEFAULT_FILTERS) {
  const onChange = vi.fn();
  render(<FilterBar initial={initial} onChange={onChange} />);
  return onChange;
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("FilterBar", () => {
  it("mostra os filtros salvos", () => {
    renderBar({
      ...DEFAULT_FILTERS,
      countries: ["BR", "PT"],
      departments: ["Data"],
      work_models: ["remote"],
      seniorities: ["senior"],
      languages: ["en"],
      salary_min: 120000,
      salary_currency: "BRL",
      include_unknown: false,
    });
    expect(screen.getByLabelText("Países")).toHaveValue("BR, PT");
    expect(screen.getByLabelText("Departamentos")).toHaveValue("Data");
    expect(screen.getByRole("checkbox", { name: "remoto" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "híbrido" })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: "sênior" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "inglês" })).toBeChecked();
    expect(screen.getByLabelText("Salário mínimo por ano")).toHaveValue(120000);
    expect(screen.getByLabelText("Moeda")).toHaveValue("BRL");
    expect(screen.getByRole("checkbox", { name: /Incluir desconhecidos/ })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: /Aceitar vagas sem salário/ })).toBeChecked();
  });

  it("não salva nada ao montar", async () => {
    const onChange = renderBar();
    await tick(1000);
    expect(onChange).not.toHaveBeenCalled();
  });

  it("debounce de 300 ms: várias mudanças seguidas salvam uma vez, com tudo", async () => {
    const onChange = renderBar();
    fireEvent.click(screen.getByRole("checkbox", { name: "remoto" }));
    await tick(100);
    fireEvent.change(screen.getByLabelText("Países"), { target: { value: "br, pt ," } });
    await tick(100);
    fireEvent.click(screen.getByRole("checkbox", { name: "sênior" }));
    await tick(299);
    expect(onChange).not.toHaveBeenCalled();
    await tick(1);
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenCalledWith({
      ...DEFAULT_FILTERS,
      countries: ["BR", "PT"],
      work_models: ["remote"],
      seniorities: ["senior"],
    });
    await tick(1000);
    expect(onChange).toHaveBeenCalledTimes(1);
  });

  it("salário sem moeda: mostra o erro e não salva; com a moeda, salva os dois", async () => {
    const onChange = renderBar();
    fireEvent.change(screen.getByLabelText("Salário mínimo por ano"), { target: { value: "90000" } });
    await tick(300);
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByText("Informe a moeda do salário mínimo (ex.: BRL).")).toBeInTheDocument();
    expect(screen.getByLabelText("Moeda")).toHaveAttribute("aria-invalid", "true");

    fireEvent.change(screen.getByLabelText("Moeda"), { target: { value: "brl" } });
    await tick(300);
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenCalledWith({ ...DEFAULT_FILTERS, salary_min: 90000, salary_currency: "BRL" });
    expect(screen.queryByText(/Informe a moeda/)).toBeNull();
  });

  it("salário negativo é recusado", async () => {
    const onChange = renderBar();
    fireEvent.change(screen.getByLabelText("Moeda"), { target: { value: "EUR" } });
    fireEvent.change(screen.getByLabelText("Salário mínimo por ano"), { target: { value: "-5" } });
    await tick(300);
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByText(/Salário mínimo precisa ser um número/)).toBeInTheDocument();
  });

  it("opções de desconhecido e sem salário", async () => {
    const onChange = renderBar();
    fireEvent.click(screen.getByRole("checkbox", { name: /Incluir desconhecidos/ }));
    fireEvent.click(screen.getByRole("checkbox", { name: /Aceitar vagas sem salário/ }));
    await tick(300);
    expect(onChange).toHaveBeenCalledWith({
      ...DEFAULT_FILTERS,
      include_unknown: false,
      accept_no_salary: false,
    });
  });
});
