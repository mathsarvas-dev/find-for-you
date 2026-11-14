import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { emptyOverview, type Overview } from "@/lib/overview";
import { SummaryCards, SummaryCardsLoading } from "./SummaryCards";

function overview(patch: (o: Overview) => void = () => {}): Overview {
  const o = emptyOverview();
  patch(o);
  return o;
}

function card(name: RegExp) {
  return screen.getByRole("link", { name });
}

describe("SummaryCards", () => {
  it("quatro cartões clicáveis, cada um levando à sua página", () => {
    render(<SummaryCards overview={overview()} />);
    expect(screen.getAllByRole("link")).toHaveLength(4);
    expect(card(/^Perfil/)).toHaveAttribute("href", "/onboarding");
    expect(card(/^Fontes/)).toHaveAttribute("href", "/sources");
    expect(card(/^Vagas/)).toHaveAttribute("href", "/jobs");
    expect(card(/^Avaliadas/)).toHaveAttribute("href", "/jobs");
  });

  it("usuário novo: textos de vazio, sem números inventados", () => {
    render(<SummaryCards overview={overview()} />);
    expect(card(/^Perfil/)).toHaveTextContent("Não confirmado");
    expect(card(/^Perfil/)).toHaveTextContent("Envie o currículo e confirme");
    expect(card(/^Fontes/)).toHaveTextContent("0 empresas ativas");
    expect(card(/^Fontes/)).toHaveTextContent("Nenhuma empresa cadastrada");
    expect(card(/^Vagas/)).toHaveTextContent("0 vagas abertas");
    expect(card(/^Vagas/)).toHaveTextContent("Cadastre empresas para coletar");
    expect(card(/^Avaliadas/)).toHaveTextContent("0 de 0");
  });

  it("meio do caminho: versão, empresas ativas/automáticas, abertas e n de N", () => {
    const o = overview((o) => {
      o.profile = { ...o.profile, confirmed: true, version: 2, vectorized: true };
      o.companies = { total: 4, active: 3, auto: 1 };
      o.jobs = { open: 1234 };
      o.matches = { scored: 0, pending: 0 };
    });
    render(<SummaryCards overview={o} />);
    expect(card(/^Perfil/)).toHaveTextContent("Versão 2");
    expect(card(/^Perfil/)).toHaveTextContent("Confirmado · vetores prontos");
    expect(card(/^Fontes/)).toHaveTextContent("3 empresas ativas");
    expect(card(/^Fontes/)).toHaveTextContent("1 automática · 1 pausada");
    expect(card(/^Vagas/)).toHaveTextContent("1.234 vagas abertas");
    expect(card(/^Avaliadas/)).toHaveTextContent("0 de 1.234");
    expect(card(/^Avaliadas/)).toHaveTextContent("Nenhuma vaga avaliada ainda");
  });

  it("singular em PT-BR", () => {
    const o = overview((o) => {
      o.profile = { ...o.profile, confirmed: true, version: 1, vectorized: false };
      o.companies = { total: 1, active: 1, auto: 0 };
      o.jobs = { open: 1 };
      o.matches = { scored: 1, pending: 1 };
    });
    render(<SummaryCards overview={o} />);
    expect(card(/^Perfil/)).toHaveTextContent("Confirmado · vetores em preparo");
    expect(card(/^Fontes/)).toHaveTextContent("1 empresa ativa");
    expect(card(/^Fontes/)).toHaveTextContent("Nenhuma automática");
    expect(card(/^Vagas/)).toHaveTextContent("1 vaga aberta");
    expect(card(/^Avaliadas/)).toHaveTextContent("1 de 1");
    expect(card(/^Avaliadas/)).toHaveTextContent("1 na fila");
  });

  it("plural com várias automáticas e fila", () => {
    const o = overview((o) => {
      o.companies = { total: 5, active: 5, auto: 2 };
      o.jobs = { open: 0 };
      o.matches = { scored: 7, pending: 3 };
    });
    render(<SummaryCards overview={o} />);
    expect(card(/^Fontes/)).toHaveTextContent("2 automáticas");
    expect(card(/^Vagas/)).toHaveTextContent("Aguardando a primeira coleta");
    expect(card(/^Avaliadas/)).toHaveTextContent("3 na fila");
  });

  it("perfil confirmado sem versão conhecida", () => {
    const o = overview((o) => {
      o.profile = { ...o.profile, confirmed: true, version: null, vectorized: true };
    });
    render(<SummaryCards overview={o} />);
    expect(card(/^Perfil/)).toHaveTextContent("Confirmado");
  });

  it("esqueleto: quatro cartões ocupados, sem links", () => {
    const { container } = render(<SummaryCardsLoading />);
    expect(screen.queryAllByRole("link")).toHaveLength(0);
    expect(container.querySelectorAll(".summary-card")).toHaveLength(4);
    expect(container.querySelectorAll(".skeleton").length).toBeGreaterThanOrEqual(4);
  });
});
