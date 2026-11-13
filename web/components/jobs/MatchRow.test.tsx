import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { MatchRow } from "./MatchRow";
import { matchItem } from "./fixtures";

function renderRow(item = matchItem(1), selected = false, onSelect = vi.fn()) {
  const view = render(
    <ol>
      <MatchRow item={item} selected={selected} onSelect={onSelect} />
    </ol>,
  );
  return { ...view, onSelect };
}

describe("MatchRow", () => {
  it("título hostil aparece literal e não vira HTML", () => {
    const hostile = "<img src=x onerror=alert(1)>";
    const { container } = renderRow(matchItem(1, {}, { title: hostile, company_name: "<b>Acme</b>" }));
    expect(screen.getByText(hostile)).toBeInTheDocument();
    expect(screen.getByText("<b>Acme</b>")).toBeInTheDocument();
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("b")).toBeNull();
  });

  it("marca salário desconhecido de forma discreta", () => {
    renderRow(matchItem(1, { unknown_fields: ["salary", "work_model"] }, { work_model: "unknown" }));
    expect(screen.getByText("salário desconhecido")).toBeInTheDocument();
    expect(screen.getByText("modelo desconhecido")).toBeInTheDocument();
  });

  it("dados da linha: empresa, país, modelo, salário e nota", () => {
    renderRow(
      matchItem(
        2,
        { ai_score: 91, state: "ok", unknown_fields: [] },
        {
          title: "Engenheira de dados",
          work_model: "hybrid",
          country: "PT",
          salary_known: true,
          salary_min: 60000,
          salary_max: 75000,
          salary_currency: "EUR",
          salary_period: "year",
        },
      ),
    );
    expect(screen.getByText("Engenheira de dados")).toBeInTheDocument();
    expect(screen.getByText("GitLab")).toBeInTheDocument();
    expect(screen.getByText("PT")).toBeInTheDocument();
    expect(screen.getByText("híbrido")).toBeInTheDocument();
    expect(screen.getByText("EUR 60.000–75.000/ano")).toBeInTheDocument();
    expect(screen.getByText("91")).toBeInTheDocument();
    expect(screen.queryByText("salário desconhecido")).toBeNull();
  });

  it("estado da avaliação aparece em texto", () => {
    renderRow(matchItem(1, { state: "pendente" }));
    expect(screen.getByText("avaliando…")).toBeInTheDocument();
  });

  it("clique seleciona; selecionada tem aria-current", () => {
    const { onSelect } = renderRow(matchItem(5), true);
    const button = screen.getByRole("button", { name: /Vaga 5/ });
    expect(button).toHaveAttribute("aria-current", "true");
    fireEvent.click(button);
    expect(onSelect).toHaveBeenCalledWith(5);
  });
});
