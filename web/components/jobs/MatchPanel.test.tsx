import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { MatchPanel, type PanelState } from "./MatchPanel";
import { matchDetail } from "./fixtures";

const NOW = Date.parse("2026-11-12T12:00:00Z");

function renderPanel(state: PanelState, chunksUsed = 3) {
  const onClose = vi.fn();
  const onRetry = vi.fn();
  const view = render(
    <MatchPanel state={state} chunksUsed={chunksUsed} now={NOW} onClose={onClose} onRetry={onRetry} />,
  );
  return { ...view, onClose, onRetry };
}

describe("MatchPanel", () => {
  it("sem seleção: explica como escolher e os atalhos", () => {
    renderPanel({ name: "empty" });
    expect(screen.getByText(/Escolha uma vaga na lista/)).toBeInTheDocument();
  });

  it("justificativa, pontos fortes e lacunas como texto", () => {
    const { container } = renderPanel({
      name: "ready",
      detail: matchDetail(1, {
        title: "<script>x</script>",
        rationale: "<img src=x onerror=alert(1)> Python e Airflow",
        strengths: ["<b>Python</b>", "Airflow"],
        gaps: ["Spark"],
      }),
    });
    expect(screen.getByRole("heading", { name: "<script>x</script>" })).toBeInTheDocument();
    expect(screen.getByText("<img src=x onerror=alert(1)> Python e Airflow")).toBeInTheDocument();
    expect(screen.getByText("<b>Python</b>")).toBeInTheDocument();
    expect(container.querySelector("img, b, script")).toBeNull();
    const strengths = screen.getByRole("list", { name: "Pontos fortes" });
    expect(strengths.querySelectorAll("li")).toHaveLength(2);
    expect(screen.getByRole("list", { name: "Lacunas" })).toHaveTextContent("Spark");
    expect(screen.getByText("84")).toBeInTheDocument();
    expect(screen.getByText(/3 trechos do perfil/)).toBeInTheDocument();
  });

  it("link só http(s): javascript: não vira <a>", () => {
    renderPanel({ name: "ready", detail: matchDetail(1, { url: "javascript:alert(1)" }) });
    expect(screen.queryByRole("link", { name: /Abrir vaga/ })).toBeNull();
    expect(document.querySelector('a[href^="javascript"]')).toBeNull();
    expect(screen.getByText("link indisponível")).toBeInTheDocument();
  });

  it("link http(s) abre em nova aba sem opener", () => {
    renderPanel({ name: "ready", detail: matchDetail(2) });
    const link = screen.getByRole("link", { name: /Abrir vaga/ });
    expect(link).toHaveAttribute("href", "https://boards.greenhouse.io/gitlab/jobs/2");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(link).toHaveAttribute("target", "_blank");
  });

  it("pendente: diz que está na fila; erro: mensagem em português", () => {
    const { rerender } = renderPanel({
      name: "ready",
      detail: matchDetail(1, { state: "pendente", ai_score: null, rationale: null, strengths: null, gaps: null }),
    });
    expect(screen.getByText(/Na fila da IA/)).toBeInTheDocument();
    expect(screen.getByText("pré-seleção")).toBeInTheDocument();
    rerender(
      <MatchPanel
        state={{
          name: "ready",
          detail: matchDetail(1, { state: "erro", ai_score: null, rationale: null, error: "MatchInvalid" }),
        }}
        chunksUsed={3}
        now={NOW}
        onClose={vi.fn()}
        onRetry={vi.fn()}
      />,
    );
    expect(screen.getByText("Não foi possível avaliar esta vaga.")).toBeInTheDocument();
    // never the raw exception class name, nor a hardcoded attempt count
    expect(screen.queryByText(/MatchInvalid/)).not.toBeInTheDocument();
    expect(screen.queryByText(/tentativas/)).not.toBeInTheDocument();
  });

  it("nunca avaliada: explica como pedir a avaliação", () => {
    renderPanel({
      name: "ready",
      detail: matchDetail(1, { state: null, ai_score: null, rationale: null, strengths: null, gaps: null }),
    });
    expect(screen.getByText(/Ainda sem avaliação da IA/)).toBeInTheDocument();
  });

  it("erro ao carregar: Tentar de novo; Fechar chama onClose", () => {
    const { onRetry, onClose } = renderPanel({ name: "error", message: "Vaga não encontrada." });
    expect(screen.getByRole("alert")).toHaveTextContent("Vaga não encontrada.");
    fireEvent.click(screen.getByRole("button", { name: "Tentar de novo" }));
    expect(onRetry).toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Fechar detalhes" }));
    expect(onClose).toHaveBeenCalled();
  });
});

describe("MatchPanel candidatura", () => {
  it("o botão Preparar candidatura chama onPrepare e some sem a prop", () => {
    const onPrepare = vi.fn();
    const { unmount } = render(
      <MatchPanel
        state={{ name: "ready", detail: matchDetail(3) }}
        chunksUsed={1}
        now={NOW}
        onClose={vi.fn()}
        onRetry={vi.fn()}
        onPrepare={onPrepare}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Preparar candidatura" }));
    expect(onPrepare).toHaveBeenCalledTimes(1);
    unmount();
    renderPanel({ name: "ready", detail: matchDetail(4) });
    expect(screen.queryByRole("button", { name: "Preparar candidatura" })).toBeNull();
  });
});
