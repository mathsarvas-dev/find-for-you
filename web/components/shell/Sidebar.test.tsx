import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { emptyOverview, type Overview } from "@/lib/overview";
import { NAV_ITEMS, Sidebar, isActive, pendingFor } from "./Sidebar";

function nav() {
  return screen.getByRole("navigation", { name: "Principal" });
}

function current() {
  return within(nav())
    .getAllByRole("link")
    .filter((a) => a.getAttribute("aria-current") === "page")
    .map((a) => a.textContent);
}

function withOverview(patch: (o: Overview) => void): Overview {
  const o = emptyOverview();
  patch(o);
  return o;
}

describe("NAV_ITEMS", () => {
  it("lista os itens na ordem, com Configurações separado no fim", () => {
    expect(NAV_ITEMS.map((i) => [i.label, i.href])).toEqual([
      ["Início", "/"],
      ["Perfil", "/onboarding"],
      ["Entrevista", "/onboarding/interview"],
      ["Fontes", "/sources"],
      ["Vagas", "/jobs"],
      ["Configurações", "/settings"],
    ]);
    expect(NAV_ITEMS.filter((i) => i.footer).map((i) => i.label)).toEqual(["Configurações"]);
  });
});

describe("isActive", () => {
  it.each([
    ["/", "/", true],
    ["/", "/jobs", false],
    ["/onboarding", "/onboarding", true],
    ["/onboarding", "/onboarding/interview", false],
    ["/onboarding/interview", "/onboarding/interview", true],
    ["/jobs", "/jobs/42", true],
    ["/jobs", "/jobsx", false],
    ["/settings", "/settings", true],
  ])("%s em %s → %s", (href, path, expected) => {
    expect(isActive(href, path)).toBe(expected);
  });
});

describe("Sidebar", () => {
  it.each([
    ["/", "Início"],
    ["/onboarding", "Perfil"],
    ["/onboarding/interview", "Entrevista"],
    ["/sources", "Fontes"],
    ["/jobs", "Vagas"],
    ["/settings", "Configurações"],
  ])("marca o item ativo em %s com aria-current", (path, label) => {
    render(<Sidebar pathname={path} overview={null} apiStatus="loading" />);
    expect(current()).toEqual([label]);
  });

  it("não mostra pendências antes do overview carregar", () => {
    const { container } = render(<Sidebar pathname="/" overview={null} apiStatus="down" />);
    expect(container.querySelector(".nav-item__dot")).toBeNull();
    expect(screen.queryByText(/não confirmado/)).toBeNull();
  });

  it("mostra pendências com texto acessível derivadas do overview", () => {
    const o = withOverview((x) => {
      x.jobs.open = 12;
    });
    render(<Sidebar pathname="/" overview={o} apiStatus="ok" />);
    const perfil = within(nav()).getByRole("link", { name: /Perfil/ });
    expect(perfil).toHaveAccessibleName("Perfil (perfil não confirmado)");
    expect(within(nav()).getByRole("link", { name: /Fontes/ })).toHaveAccessibleName(
      "Fontes (nenhuma empresa ativa)",
    );
    expect(within(nav()).getByRole("link", { name: /Vagas/ })).toHaveAccessibleName(
      "Vagas (nenhuma vaga avaliada)",
    );
    expect(within(nav()).getByRole("link", { name: /Início/ })).toHaveAccessibleName("Início");
  });

  it("sem pendência quando tudo está em dia", () => {
    const o = withOverview((x) => {
      x.profile.confirmed = true;
      x.companies.total = 3;
      x.companies.active = 1;
      x.jobs.open = 5;
      x.matches.scored = 2;
    });
    const { container } = render(<Sidebar pathname="/" overview={o} apiStatus="ok" />);
    expect(container.querySelector(".nav-item__dot")).toBeNull();
  });

  it("Fontes só com empresas pausadas fica pendente (igual à trilha)", () => {
    const o = withOverview((x) => {
      x.companies.total = 2;
    });
    expect(pendingFor("/sources", o)).toBe("nenhuma empresa ativa");
  });

  it("Vagas sem vagas abertas não fica pendente", () => {
    expect(pendingFor("/jobs", emptyOverview())).toBeNull();
  });

  it.each([
    ["ok", "ok"],
    ["down", "sem resposta da API"],
    ["loading", "verificando"],
  ] as const)("rodapé Sistema com estado %s", (status, text) => {
    render(<Sidebar pathname="/" overview={null} apiStatus={status} />);
    const link = screen.getByRole("link", { name: /Sistema/ });
    expect(link).toHaveAttribute("href", "/settings#sistema");
    expect(link).toHaveTextContent(text);
  });
});
