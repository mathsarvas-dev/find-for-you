import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { Stepper } from "./Stepper";
import type { Step } from "./computeSteps";

const steps: Step[] = [
  { id: "a", title: "Montar o perfil", detail: "Perfil na versão 2", state: "done", reviewHref: "/onboarding" },
  {
    id: "b",
    title: "Entrevista rápida",
    detail: "Opcional",
    state: "optional",
    cta: { label: "Fazer a entrevista", href: "/onboarding/interview" },
  },
  {
    id: "c",
    title: "Fontes de vagas",
    detail: "Nenhuma empresa ativa",
    state: "current",
    cta: { label: "Adicionar empresas", href: "/sources" },
  },
  { id: "d", title: "Vagas coletadas", detail: "Nenhuma vaga", state: "upcoming" },
  { id: "e", title: "Candidatar-se", detail: "Em breve", state: "blocked", blockedReason: "Chega no próximo marco." },
];

function items() {
  return within(screen.getByRole("list")).getAllByRole("listitem");
}

describe("Stepper", () => {
  it("renderiza uma <ol> com os passos na ordem", () => {
    const { container } = render(<Stepper steps={steps} orientation="vertical" />);
    expect(container.querySelector("ol.stepper")).not.toBeNull();
    expect(items().map((li) => li.textContent)).toEqual([
      expect.stringContaining("Montar o perfil"),
      expect.stringContaining("Entrevista rápida"),
      expect.stringContaining("Fontes de vagas"),
      expect.stringContaining("Vagas coletadas"),
      expect.stringContaining("Candidatar-se"),
    ]);
  });

  it("um único aria-current=step, no passo atual", () => {
    const { container } = render(<Stepper steps={steps} orientation="vertical" />);
    const marked = container.querySelectorAll('[aria-current="step"]');
    expect(marked).toHaveLength(1);
    expect(marked[0]).toHaveTextContent("Fontes de vagas");
  });

  it("sem passo atual, nenhum aria-current", () => {
    const allDone = steps.map((s) => ({ ...s, state: "done" as const, cta: undefined, reviewHref: "/x" }));
    const { container } = render(<Stepper steps={allDone} orientation="vertical" />);
    expect(container.querySelectorAll("[aria-current]")).toHaveLength(0);
  });

  it("texto oculto de estado, não só cor", () => {
    render(<Stepper steps={steps} orientation="vertical" />);
    const [done, optional, current, upcoming, blocked] = items();
    expect(within(done).getByText(/concluído/)).toHaveClass("sr-only");
    expect(within(optional).getByText(/opcional/)).toHaveClass("sr-only");
    expect(within(current).getByText(/passo atual/)).toHaveClass("sr-only");
    expect(within(blocked).getByText(/bloqueado/)).toHaveClass("sr-only");
    expect(upcoming.querySelector(".sr-only")).toBeNull();
  });

  it("bolinha e linha conectora são decorativas", () => {
    const { container } = render(<Stepper steps={steps} orientation="vertical" />);
    const markers = container.querySelectorAll(".stepper__marker");
    expect(markers).toHaveLength(steps.length);
    markers.forEach((m) => expect(m).toHaveAttribute("aria-hidden", "true"));
    expect(markers[0]).toHaveTextContent("✓");
    expect(markers[2]).toHaveTextContent("3");
  });

  it("botão primário só no passo atual", () => {
    const { container } = render(<Stepper steps={steps} orientation="vertical" />);
    const cta = screen.getByRole("link", { name: "Adicionar empresas" });
    expect(cta).toHaveAttribute("href", "/sources");
    expect(cta).toHaveClass("button");
    const buttons = container.querySelectorAll("a.button");
    expect(buttons).toHaveLength(1);
    expect(buttons[0].closest("li")).toHaveAttribute("aria-current", "step");
  });

  it("passo opcional mostra o CTA como link de texto secundário, mesmo sem ser o atual", () => {
    render(<Stepper steps={steps} orientation="vertical" />);
    const link = within(items()[1]).getByRole("link", { name: "Fazer a entrevista" });
    expect(link).toHaveAttribute("href", "/onboarding/interview");
    expect(link).not.toHaveClass("button");
    expect(link).toHaveClass("stepper__secondary");
  });

  it("futuros e bloqueados não têm links; total = CTA + opcional + Revisar", () => {
    const withCtas = steps.map((s) =>
      s.state === "upcoming" || s.state === "blocked" ? { ...s, cta: { label: "Nunca", href: "/x" } } : s,
    );
    render(<Stepper steps={withCtas} orientation="vertical" />);
    expect(screen.queryByRole("link", { name: "Nunca" })).toBeNull();
    expect(screen.getAllByRole("link")).toHaveLength(3);
  });

  it("'Revisar' nos concluídos, com o nome do passo para leitor de tela", () => {
    render(<Stepper steps={steps} orientation="vertical" />);
    const review = screen.getByRole("link", { name: /Revisar/ });
    expect(review).toHaveAttribute("href", "/onboarding");
    expect(review).toHaveAccessibleName("Revisar Montar o perfil");
  });

  it("bloqueado explica o porquê", () => {
    render(<Stepper steps={steps} orientation="vertical" />);
    expect(within(items()[4]).getByText("Chega no próximo marco.")).toBeInTheDocument();
  });

  it("vertical vs horizontal", () => {
    const { container, rerender } = render(<Stepper steps={steps} orientation="vertical" />);
    expect(container.querySelector(".stepper")).toHaveClass("stepper--vertical");
    expect(container.querySelector(".stepper__compact")).toBeNull();
    rerender(<Stepper steps={steps} orientation="horizontal" />);
    expect(container.querySelector(".stepper")).toHaveClass("stepper--horizontal");
    expect(container.querySelector(".stepper")).not.toHaveClass("stepper--vertical");
  });

  it("horizontal traz o texto compacto 'Passo N de M · título' (mostrado por CSS)", () => {
    const { container } = render(<Stepper steps={steps} orientation="horizontal" />);
    const compact = container.querySelector(".stepper__compact");
    expect(compact).toHaveTextContent("Passo 3 de 5 · Fontes de vagas");
  });

  it("horizontal mantém a <ol> com o CTA atual e o link opcional ao lado da linha compacta", () => {
    const { container } = render(<Stepper steps={steps} orientation="horizontal" />);
    expect(container.querySelector(".stepper__compact")).not.toBeNull();
    const list = container.querySelector("ol.stepper--horizontal") as HTMLElement;
    expect(list).not.toBeNull();
    const current = list.querySelector('[aria-current="step"]') as HTMLElement;
    expect(current).toHaveClass("stepper__step--current");
    const cta = within(current).getByRole("link", { name: "Adicionar empresas" });
    expect(cta).toHaveClass("button");
    expect(cta).toHaveAttribute("href", "/sources");
    const optional = within(list.querySelector(".stepper__step--optional") as HTMLElement)
      .getByRole("link", { name: "Fazer a entrevista" });
    expect(optional).toHaveClass("stepper__secondary");
    expect(optional).not.toHaveClass("button");
  });

  it("compactLabel substitui o texto compacto", () => {
    const { container } = render(
      <Stepper steps={steps} orientation="horizontal" compactLabel="Etapa final" />,
    );
    expect(container.querySelector(".stepper__compact")).toHaveTextContent("Etapa final");
  });

  it("texto hostil em detail aparece literal, nunca vira HTML", () => {
    const hostile = '<img src=x onerror="alert(1)">';
    const { container } = render(
      <Stepper
        steps={[{ id: "x", title: "<b>t</b>", detail: hostile, state: "current" }]}
        orientation="vertical"
      />,
    );
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("b")).toBeNull();
    expect(screen.getByText(hostile)).toBeInTheDocument();
  });
});
