import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { usePathname } from "next/navigation";
import { ApiError } from "@/lib/profile";
import { emptyOverview, getOverview } from "@/lib/overview";
import { AppShell } from "./AppShell";
import { PageHeader } from "./PageHeader";
import { titleForPath } from "./Topbar";
import { useOverview } from "./OverviewContext";
import type { Overview } from "@/lib/overview";

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
    expect(within(nav).getAllByRole("link")).toHaveLength(9);
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
    expect(screen.getByRole("link", { name: "Fontes (nenhuma fonte ativa)" })).toBeInTheDocument();
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

function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function named(name: string): Overview {
  const o = emptyOverview();
  o.profile = { ...o.profile, confirmed: true, name, email: `${name.toLowerCase()}@example.com` };
  return o;
}

function Probe() {
  const { overview, status, refresh } = useOverview();
  return (
    <div>
      <span data-testid="probe">{`${status}:${overview?.profile.name ?? "-"}`}</span>
      <button type="button" onClick={refresh}>
        atualizar
      </button>
    </div>
  );
}

describe("AppShell: overview", () => {
  it("refaz a busca a cada navegação e passa um AbortSignal", async () => {
    m.overview.mockResolvedValue(named("Ana"));
    const view = await renderShell();
    expect(m.overview).toHaveBeenCalledTimes(1);
    m.path.mockReturnValue("/jobs");
    view.rerender(<AppShell><main>conteúdo</main></AppShell>);
    await act(async () => {});
    expect(m.overview).toHaveBeenCalledTimes(2);
    expect(m.overview.mock.calls[0][0]).toBeInstanceOf(AbortSignal);
  });

  it("resposta antiga que chega depois é ignorada (e a requisição é abortada)", async () => {
    const first = deferred<Overview>();
    const second = deferred<Overview>();
    m.overview.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    const view = render(<AppShell><Probe /></AppShell>);
    m.path.mockReturnValue("/jobs");
    view.rerender(<AppShell><Probe /></AppShell>);
    const firstSignal = m.overview.mock.calls[0][0] as AbortSignal;
    expect(firstSignal.aborted).toBe(true);
    await act(async () => second.resolve(named("Nova")));
    await act(async () => first.resolve(named("Velha")));
    expect(screen.getByTestId("probe")).toHaveTextContent("ok:Nova");
  });

  it("falha de requisição antiga não marca a API como fora do ar", async () => {
    const first = deferred<Overview>();
    m.overview.mockReturnValueOnce(first.promise).mockResolvedValueOnce(named("Ana"));
    const view = render(<AppShell><Probe /></AppShell>);
    m.path.mockReturnValue("/jobs");
    view.rerender(<AppShell><Probe /></AppShell>);
    await act(async () => {});
    await act(async () => first.reject(new Error("abortada")));
    expect(screen.getByTestId("probe")).toHaveTextContent("ok:Ana");
  });

  it("useOverview().refresh() busca de novo e atualiza a casca", async () => {
    m.overview.mockResolvedValueOnce(named("Ana")).mockResolvedValueOnce(named("Bia"));
    await renderShell(<Probe />);
    expect(screen.getByTestId("probe")).toHaveTextContent("ok:Ana");
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "atualizar" })));
    expect(m.overview).toHaveBeenCalledTimes(2);
    expect(screen.getByTestId("probe")).toHaveTextContent("ok:Bia");
    expect(screen.getByRole("button", { name: "Menu do perfil" })).toHaveTextContent("B");
  });

  it("desmontar aborta a requisição e não atualiza estado", async () => {
    const first = deferred<Overview>();
    m.overview.mockReturnValueOnce(first.promise);
    const view = render(<AppShell><Probe /></AppShell>);
    view.unmount();
    expect((m.overview.mock.calls[0][0] as AbortSignal).aborted).toBe(true);
    await act(async () => first.resolve(named("Ana")));
    expect(errorSpy).not.toHaveBeenCalled();
  });

  it("overview malformado não derruba a casca", async () => {
    const bad = emptyOverview();
    bad.profile = { ...bad.profile, confirmed: true, name: 42 as unknown as string, email: null };
    m.overview.mockResolvedValue(bad);
    await renderShell();
    expect(screen.getByRole("button", { name: "Menu do perfil" })).toHaveTextContent("V");
  });

  it("fora da casca useOverview devolve o estado vazio", () => {
    render(<Probe />);
    expect(screen.getByTestId("probe")).toHaveTextContent("loading:-");
  });
});

describe("AppShell: gaveta fecha sozinha", () => {
  type Listener = (e: { matches: boolean }) => void;
  let listeners: Listener[];

  beforeEach(() => {
    m.overview.mockRejectedValue(new Error("x"));
    listeners = [];
    vi.stubGlobal("matchMedia", (query: string) => ({
      media: query,
      matches: false,
      addEventListener: (_: string, fn: Listener) => listeners.push(fn),
      removeEventListener: (_: string, fn: Listener) => {
        listeners = listeners.filter((l) => l !== fn);
      },
    }));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("trava a rolagem do fundo enquanto aberta e libera ao fechar", async () => {
    await renderShell();
    fireEvent.click(menuButton());
    expect(document.body.style.overflow).toBe("hidden");
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    expect(document.body.style.overflow).toBe("");
  });

  it("fecha em qualquer mudança de rota (ex.: voltar do navegador) e devolve o foco ao botão", async () => {
    const view = await renderShell();
    fireEvent.click(menuButton());
    m.path.mockReturnValue("/sources");
    view.rerender(<AppShell><main>conteúdo</main></AppShell>);
    await act(async () => {});
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.body.style.overflow).toBe("");
    expect(menuButton()).toHaveFocus();
  });

  it("não reabre sozinha ao voltar para a rota em que foi aberta", async () => {
    const view = await renderShell();
    fireEvent.click(menuButton());
    m.path.mockReturnValue("/sources");
    view.rerender(<AppShell><main>conteúdo</main></AppShell>);
    await act(async () => {});
    expect(screen.queryByRole("dialog")).toBeNull();
    const outside = screen.getByText("conteúdo");
    outside.setAttribute("tabindex", "-1");
    outside.focus();
    m.path.mockReturnValue("/");
    view.rerender(<AppShell><main>conteúdo</main></AppShell>);
    await act(async () => {});
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.body.style.overflow).toBe("");
    expect(menuButton()).toHaveAttribute("aria-expanded", "false");
    expect(outside).toHaveFocus();
  });

  it("fecha ao virar desktop e leva o foco ao conteúdo (botão escondido)", async () => {
    const { container } = await renderShell();
    fireEvent.click(menuButton());
    expect(listeners.length).toBeGreaterThan(0);
    menuButton().style.display = "none";
    act(() => listeners.forEach((l) => l({ matches: true })));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.body.style.overflow).toBe("");
    expect(container.querySelector("#conteudo")).toHaveFocus();
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
