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
  render(<FilterBar initial={initial} revision={0} onChange={onChange} />);
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

  it("erro de validação é anunciado (role=alert)", async () => {
    renderBar();
    fireEvent.change(screen.getByLabelText("Salário mínimo por ano"), { target: { value: "90000" } });
    await tick(300);
    expect(screen.getByRole("alert")).toHaveTextContent("Informe a moeda do salário mínimo");
  });

  it("avisa a página enquanto há edição esperando o debounce", async () => {
    const onEditing = vi.fn();
    render(<FilterBar initial={DEFAULT_FILTERS} revision={0} onChange={vi.fn()} onEditing={onEditing} />);
    fireEvent.click(screen.getByRole("checkbox", { name: "remoto" }));
    expect(onEditing).toHaveBeenLastCalledWith(true);
    await tick(300);
    expect(onEditing).toHaveBeenLastCalledWith(false);
  });

  describe("recolhível", () => {
    afterEach(() => {
      vi.unstubAllGlobals();
    });

    it("no desktop começa aberto, com a contagem de filtros ativos", () => {
      renderBar({ ...DEFAULT_FILTERS, work_models: ["remote"], countries: ["BR"] });
      const toggle = screen.getByRole("button", { name: /Filtros/ });
      expect(toggle).toHaveAttribute("aria-expanded", "true");
      expect(toggle).toHaveTextContent("2 ativos");
      expect(screen.getByRole("checkbox", { name: "remoto" })).toBeVisible();
    });

    it("em tela de até 560 px começa recolhido; o botão abre e fecha", () => {
      vi.stubGlobal(
        "matchMedia",
        vi.fn((q: string) => ({
          matches: q.includes("560"),
          media: q,
          addEventListener: vi.fn(),
          removeEventListener: vi.fn(),
        })),
      );
      renderBar();
      const toggle = screen.getByRole("button", { name: /Filtros/ });
      expect(toggle).toHaveAttribute("aria-expanded", "false");
      expect(toggle).toHaveTextContent("nenhum ativo");
      expect(screen.queryByRole("checkbox", { name: "remoto" })).toBeNull();
      fireEvent.click(toggle);
      expect(toggle).toHaveAttribute("aria-expanded", "true");
      expect(screen.getByRole("checkbox", { name: "remoto" })).toBeInTheDocument();
      fireEvent.click(toggle);
      expect(toggle).toHaveAttribute("aria-expanded", "false");
    });
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

  describe("exclusões e revisão", () => {
    it("mostra as exclusões de país e título salvas", () => {
      renderBar({ ...DEFAULT_FILTERS, exclude_countries: ["US", "CN"], title_exclude: ["plantão"] });
      expect(screen.getByLabelText("Excluir países")).toHaveValue("US, CN");
      expect(screen.getByLabelText("Evitar no título")).toHaveValue("plantão");
    });

    it("editar países mantém todas as exclusões no onChange", async () => {
      const initial: SearchFilters = {
        ...DEFAULT_FILTERS,
        exclude_countries: ["US"],
        exclude_work_models: ["onsite"],
        exclude_seniorities: ["intern"],
        exclude_languages: ["de"],
        exclude_departments: ["Sales"],
        title_exclude: ["plantão"],
      };
      const onChange = renderBar(initial);
      fireEvent.change(screen.getByLabelText("Países"), { target: { value: "br" } });
      await tick(300);
      expect(onChange).toHaveBeenCalledWith({ ...initial, countries: ["BR"] });
    });

    it("os campos novos emitem listas separadas por vírgula", async () => {
      const onChange = renderBar();
      fireEvent.change(screen.getByLabelText("Excluir países"), { target: { value: "us, cn ,US" } });
      fireEvent.change(screen.getByLabelText("Evitar no título"), {
        target: { value: "plantão, Estágio , estágio," },
      });
      await tick(300);
      expect(onChange).toHaveBeenCalledWith({
        ...DEFAULT_FILTERS,
        exclude_countries: ["US", "CN"],
        title_exclude: ["plantão", "Estágio"],
      });
    });

    it("'Evitar no título' guarda no máximo 10 termos", async () => {
      const onChange = renderBar();
      const terms = Array.from({ length: 12 }, (_, i) => `t${i}`);
      fireEvent.change(screen.getByLabelText("Evitar no título"), { target: { value: terms.join(", ") } });
      await tick(300);
      expect(onChange.mock.calls[0][0].title_exclude).toEqual(terms.slice(0, 10));
    });

    it("marcar uma inclusão tira o mesmo valor da exclusão", async () => {
      const onChange = renderBar({
        ...DEFAULT_FILTERS,
        exclude_work_models: ["remote"],
        exclude_seniorities: ["intern", "senior"],
      });
      fireEvent.click(screen.getByRole("checkbox", { name: "remoto" }));
      fireEvent.click(screen.getByRole("checkbox", { name: "sênior" }));
      await tick(300);
      expect(onChange).toHaveBeenCalledWith({
        ...DEFAULT_FILTERS,
        work_models: ["remote"],
        exclude_work_models: [],
        seniorities: ["senior"],
        exclude_seniorities: ["intern"],
      });
    });

    it("incluir um país excluído tira a exclusão (também do campo)", async () => {
      const onChange = renderBar({ ...DEFAULT_FILTERS, exclude_countries: ["BR", "US"] });
      fireEvent.change(screen.getByLabelText("Países"), { target: { value: "br" } });
      await tick(300);
      expect(onChange).toHaveBeenCalledWith({
        ...DEFAULT_FILTERS,
        countries: ["BR"],
        exclude_countries: ["US"],
      });
      expect(screen.getByLabelText("Excluir países")).toHaveValue("US");
      // A limpeza do campo não gera um segundo salvamento.
      await tick(1000);
      expect(onChange).toHaveBeenCalledTimes(1);
    });

    it("revision nova redesenha a partir do initial novo e descarta o rascunho", async () => {
      const onChange = vi.fn();
      const { rerender } = render(<FilterBar initial={DEFAULT_FILTERS} revision={0} onChange={onChange} />);
      fireEvent.change(screen.getByLabelText("Países"), { target: { value: "DE" } });
      await tick(100);
      const next: SearchFilters = {
        ...DEFAULT_FILTERS,
        countries: ["PT"],
        exclude_seniorities: ["intern"],
        title_exclude: ["plantão"],
      };
      rerender(<FilterBar initial={next} revision={1} onChange={onChange} />);
      expect(screen.getByLabelText("Países")).toHaveValue("PT");
      expect(screen.getByLabelText("Evitar no título")).toHaveValue("plantão");
      // O rascunho velho ("DE") foi descartado: nada é salvo sozinho.
      await tick(1000);
      expect(onChange).not.toHaveBeenCalled();
      // Uma edição manual depois disso parte do initial novo (não reverte a mudança do chat).
      fireEvent.click(screen.getByRole("checkbox", { name: "remoto" }));
      await tick(300);
      expect(onChange).toHaveBeenCalledWith({ ...next, work_models: ["remote"] });
    });

    it("initial novo sem revision nova não mexe no rascunho", () => {
      const onChange = vi.fn();
      const { rerender } = render(<FilterBar initial={DEFAULT_FILTERS} revision={0} onChange={onChange} />);
      fireEvent.change(screen.getByLabelText("Países"), { target: { value: "DE" } });
      rerender(<FilterBar initial={{ ...DEFAULT_FILTERS, countries: ["PT"] }} revision={0} onChange={onChange} />);
      expect(screen.getByLabelText("Países")).toHaveValue("DE");
    });
  });
});
