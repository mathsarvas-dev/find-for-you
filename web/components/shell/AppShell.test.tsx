import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { usePathname } from "next/navigation";
import { ApiError } from "@/lib/profile";
import { emptyOverview, getOverview } from "@/lib/overview";
import { AppShell } from "./AppShell";
import { PageHeader } from "./PageHeader";
import { titleForPath } from "./Topbar";

vi.mock("next/navigation", () => ({ usePathname: vi.fn(() => "/") }));

vi.mock("@/lib/overview", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/overview")>()),
  getOverview: vi.fn(),
}));

const m = { overview: vi.mocked(getOverview), path: vi.mocked(usePathname) };

async function renderShell(children: React.ReactNode = <main>conteúdo</main>) {
  const view = render(<AppShell>{children}</AppShell>);
  await act(async () => {});
  return view;
}

function menuButton() {
  return screen.getByRole("button", { name: "Abrir navegação" });
}

let errorSpy: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  m.path.mockReturnValue("/");
  errorSpy = vi.spyOn(console, "error");
});

afterEach(() => {
  vi.clearAllMocks();
  errorSpy.mockRestore();
});

describe("AppShell", () => {
  it("com a API fora do ar a casca continua navegável, sem erro", async () => {
    m.overview.mockRejectedValue(new ApiError(null, "Não consegui falar com a API"));
    const { container } = await renderShell();
    expect(screen.getByText("conteúdo")).toBeInTheDocument();
    const nav = screen.getByRole("navigation", { name: "Principal" });
    expect(within(nav).getAllByRole("link")).toHaveLength(6);
    expect(container.querySelector(".nav-item__dot")).toBeNull();
    expect(screen.getByRole("link", { name: /Sistema/ })).toHaveTextContent("sem resposta da API");
    expect(screen.queryByRole("alert")).toBeNull();
    expect(errorSpy).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Menu do perfil" })).toHaveTextContent("V");
  });

  it("com o overview carregado mostra pendências, estado ok e iniciais", async () => {
    const o = emptyOverview();
    o.profile = { ...o.profile, confirmed: true, name: "Ana Souza", email: "ana@example.com" };
    m.overview.mockResolvedValue(o);
    await renderShell();
    expect(screen.getByRole("link", { name: "Fontes (nenhuma empresa cadastrada)" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Sistema/ })).toHaveTextContent("ok");
    expect(screen.getByRole("button", { name: "Menu do perfil" })).toHaveTextContent("AS");
  });

  it("título do topo vem do mapa por rota e não é h1", async () => {
    m.overview.mockRejectedValue(new Error("x"));
    m.path.mockReturnValue("/jobs");
    await renderShell();
    const crumbs = screen.getByRole("navigation", { name: "Caminho" });
    expect(crumbs).toHaveTextContent("Rumo/Vagas");
    expect(screen.queryByRole("heading", { level: 1 })).toBeNull();
  });

  it("PageHeader define o título do topo", async () => {
    m.overview.mockRejectedValue(new Error("x"));
    m.path.mockReturnValue("/jobs");
    await renderShell(<PageHeader title="Melhores vagas" />);
    expect(screen.getByRole("navigation", { name: "Caminho" })).toHaveTextContent("Melhores vagas");
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
  });

  describe("gaveta", () => {
    beforeEach(() => {
      m.overview.mockRejectedValue(new Error("x"));
    });

    it("abre como diálogo modal com foco dentro; Escape fecha e devolve o foco", async () => {
      await renderShell();
      expect(menuButton()).toHaveAttribute("aria-expanded", "false");
      fireEvent.click(menuButton());
      const dialog = screen.getByRole("dialog", { name: "Navegação" });
      expect(dialog).toHaveAttribute("aria-modal", "true");
      expect(menuButton()).toHaveAttribute("aria-expanded", "true");
      expect(dialog.contains(document.activeElement)).toBe(true);
      expect(within(dialog).getAllByRole("link", { name: /Início|Perfil|Entrevista|Fontes|Vagas|Configurações/ }))
        .toHaveLength(6);
      fireEvent.keyDown(dialog, { key: "Escape" });
      expect(screen.queryByRole("dialog")).toBeNull();
      expect(menuButton()).toHaveFocus();
    });

    it("prende o foco com Tab e Shift+Tab", async () => {
      await renderShell();
      fireEvent.click(menuButton());
      const dialog = screen.getByRole("dialog");
      const focusables = dialog.querySelectorAll<HTMLElement>("a[href], button");
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      last.focus();
      fireEvent.keyDown(dialog, { key: "Tab" });
      expect(first).toHaveFocus();
      fireEvent.keyDown(dialog, { key: "Tab", shiftKey: true });
      expect(last).toHaveFocus();
    });

    it("clique no fundo e no botão fechar fecham", async () => {
      const { container } = await renderShell();
      fireEvent.click(menuButton());
      fireEvent.click(container.querySelector(".drawer__overlay")!);
      expect(screen.queryByRole("dialog")).toBeNull();
      fireEvent.click(menuButton());
      fireEvent.click(screen.getByRole("button", { name: "Fechar navegação" }));
      expect(screen.queryByRole("dialog")).toBeNull();
      expect(menuButton()).toHaveFocus();
    });

    it("escolher um item fecha a gaveta", async () => {
      await renderShell();
      fireEvent.click(menuButton());
      fireEvent.click(within(screen.getByRole("dialog")).getByRole("link", { name: "Vagas" }));
      expect(screen.queryByRole("dialog")).toBeNull();
    });
  });
});

describe("titleForPath", () => {
  it.each([
    ["/", "Início"],
    ["/onboarding", "Perfil"],
    ["/onboarding/interview", "Entrevista"],
    ["/sources", "Fontes"],
    ["/jobs", "Vagas"],
    ["/jobs/1", "Vagas"],
    ["/settings", "Configurações"],
    ["/desconhecida", "Rumo"],
  ])("%s → %s", (path, title) => {
    expect(titleForPath(path)).toBe(title);
  });
});
