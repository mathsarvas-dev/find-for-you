import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { OverviewContext, type OverviewState } from "@/components/shell/OverviewContext";
import { emptyOverview, type InterviewState, type Overview } from "@/lib/overview";
import { AccountSection } from "./AccountSection";

function withProfile(over: Partial<Overview["profile"]> = {}, interview: InterviewState = "in_progress") {
  const o = emptyOverview();
  o.profile = {
    ...o.profile,
    confirmed: true,
    version: 3,
    vectorized: true,
    name: "Ana Souza",
    email: "ana@example.com",
    ...over,
  };
  o.interview = { state: interview };
  return o;
}

function renderWith(value: Partial<OverviewState>) {
  const state: OverviewState = { overview: null, status: "ok", refresh: async () => {}, ...value };
  return render(
    <OverviewContext.Provider value={state}>
      <AccountSection />
    </OverviewContext.Provider>,
  );
}

const section = () => screen.getByRole("region", { name: "Conta e perfil" });

describe("AccountSection", () => {
  it("é a seção #conta com título h2", () => {
    renderWith({ overview: withProfile() });
    expect(section()).toHaveAttribute("id", "conta");
    expect(within(section()).getByRole("heading", { level: 2, name: "Conta e perfil" })).toBeInTheDocument();
  });

  it("mostra nome, e-mail e versão do perfil vindos do overview", () => {
    renderWith({ overview: withProfile() });
    expect(screen.getByText("Ana Souza")).toBeInTheDocument();
    expect(screen.getByText("ana@example.com")).toBeInTheDocument();
    expect(screen.getByText("Versão 3 · confirmado")).toBeInTheDocument();
    expect(screen.getByText("Em andamento")).toBeInTheDocument();
  });

  it("Editar perfil leva a /onboarding; Continuar entrevista a /onboarding/interview", () => {
    renderWith({ overview: withProfile() });
    expect(screen.getByRole("link", { name: "Editar perfil" })).toHaveAttribute("href", "/onboarding");
    expect(screen.getByRole("link", { name: "Continuar entrevista" })).toHaveAttribute(
      "href",
      "/onboarding/interview",
    );
  });

  it("entrevista concluída: sem Continuar entrevista", () => {
    renderWith({ overview: withProfile({}, "done") });
    expect(screen.queryByRole("link", { name: "Continuar entrevista" })).toBeNull();
    expect(screen.getByText("Concluída")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Editar perfil" })).toBeInTheDocument();
  });

  it("entrevista não iniciada com perfil: oferece Continuar entrevista", () => {
    renderWith({ overview: withProfile({}, "none") });
    expect(screen.getByRole("link", { name: "Continuar entrevista" })).toBeInTheDocument();
    expect(screen.getByText("Não iniciada")).toBeInTheDocument();
  });

  it("sem perfil: Você, Perfil não confirmado e só Editar perfil", () => {
    renderWith({ overview: emptyOverview() });
    expect(screen.getByText("Você")).toBeInTheDocument();
    expect(screen.getByText("Perfil não confirmado")).toBeInTheDocument();
    expect(screen.getByText("Nenhum perfil ainda")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Continuar entrevista" })).toBeNull();
    expect(screen.getByRole("link", { name: "Editar perfil" })).toHaveAttribute("href", "/onboarding");
  });

  it("perfil confirmado sem nome e sem e-mail: Você e Sem e-mail no perfil", () => {
    renderWith({ overview: withProfile({ name: "  ", email: null }) });
    expect(screen.getByText("Você")).toBeInTheDocument();
    expect(screen.getByText("Sem e-mail no perfil")).toBeInTheDocument();
  });

  it("perfil em rascunho (versão sem confirmação) também oferece a entrevista", () => {
    renderWith({ overview: withProfile({ confirmed: false, version: 1 }, "none") });
    expect(screen.getByText("Você")).toBeInTheDocument();
    expect(screen.getByText("Perfil não confirmado")).toBeInTheDocument();
    expect(screen.getByText("Versão 1 · não confirmado")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Continuar entrevista" })).toBeInTheDocument();
  });

  it("dados hostis aparecem como texto literal, sem virar HTML", () => {
    const name = '<img src=x onerror="alert(1)">';
    const email = "<script>alert(2)</script>@x.com";
    const { container } = renderWith({ overview: withProfile({ name, email }) });
    expect(screen.getByText(name)).toBeInTheDocument();
    expect(screen.getByText(email)).toBeInTheDocument();
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
  });

  it("valores que não são texto caem no padrão sem quebrar", () => {
    renderWith({
      overview: withProfile({ name: 42 as unknown as string, email: {} as unknown as string }),
    });
    expect(screen.getByText("Você")).toBeInTheDocument();
    expect(screen.getByText("Sem e-mail no perfil")).toBeInTheDocument();
  });

  it("nome longo fica inteiro no title e é cortado pelo CSS", () => {
    const long = "Maria ".repeat(40).trim();
    renderWith({ overview: withProfile({ name: long }) });
    const value = screen.getByText(long);
    expect(value).toHaveAttribute("title", long);
    expect(value).toHaveClass("settings__value");
  });

  it("carregando: esqueleto ocupado, sem padrão enganoso nem botões condicionais", () => {
    renderWith({ overview: null, status: "loading" });
    expect(section()).toHaveAttribute("aria-busy", "true");
    expect(screen.queryByText("Você")).toBeNull();
    expect(screen.queryByRole("link", { name: "Continuar entrevista" })).toBeNull();
    expect(screen.getByRole("link", { name: "Editar perfil" })).toBeInTheDocument();
  });

  it("overview falhou: padrão, aviso específico e link para o estado do sistema", () => {
    renderWith({ overview: null, status: "down" });
    expect(screen.getByText("Você")).toBeInTheDocument();
    expect(screen.getByText("Perfil não confirmado")).toBeInTheDocument();
    const note = screen.getByRole("status");
    expect(note).toHaveTextContent("Não consegui ler o seu perfil agora.");
    expect(within(note).getByRole("link", { name: "Ver estado do sistema" })).toHaveAttribute(
      "href",
      "#sistema",
    );
    expect(screen.queryByRole("link", { name: "Continuar entrevista" })).toBeNull();
    expect(screen.getByRole("link", { name: "Editar perfil" })).toBeInTheDocument();
  });

  it("falha depois de ter dados: mantém os últimos dados com aviso", () => {
    renderWith({ overview: withProfile(), status: "down" });
    expect(screen.getByText("Ana Souza")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Não consegui atualizar agora");
  });
});
