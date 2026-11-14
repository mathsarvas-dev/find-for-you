import { afterEach, describe, expect, it, vi } from "vitest";
import { lazy, Suspense, useState } from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useRouter } from "next/navigation";
import { RecheckButton } from "./RecheckButton";
import { OverviewContext } from "@/components/shell/OverviewContext";

vi.mock("next/navigation", () => ({ useRouter: vi.fn() }));

afterEach(() => vi.clearAllMocks());

/**
 * Simula o router.refresh() do App Router: uma atualização de estado que suspende até o
 * novo payload chegar (`promise`). Dentro de uma transição, a tela antiga fica e o botão
 * mostra o estado pendente.
 */
function setup(overviewRefresh: () => Promise<void> = async () => {}) {
  let resolve!: () => void;
  const promise = new Promise<void>((r) => (resolve = r));
  const refresh = vi.fn();

  const Waiting = lazy(() => promise.then(() => ({ default: () => null })));

  function Harness() {
    const [refreshed, setRefreshed] = useState(false);
    refresh.mockImplementation(() => setRefreshed(true));
    vi.mocked(useRouter).mockReturnValue({ refresh } as unknown as ReturnType<typeof useRouter>);
    return (
      <Suspense fallback={<p>fallback</p>}>
        {refreshed && <Waiting />}
        <RecheckButton />
      </Suspense>
    );
  }

  render(
    <OverviewContext.Provider value={{ overview: null, status: "down", refresh: overviewRefresh }}>
      <Harness />
    </OverviewContext.Provider>,
  );
  return { refresh, resolve };
}

const button = () => screen.getByRole("button", { name: /Verific/ });

describe("RecheckButton", () => {
  it("parado: Verificar de novo, habilitado, sem anúncio", () => {
    setup();
    expect(button()).toHaveTextContent("Verificar de novo");
    expect(button()).toBeEnabled();
    expect(screen.getByRole("status")).toBeEmptyDOMElement();
  });

  it("clique chama router.refresh(), mostra Verificando… desabilitado e volta ao terminar", async () => {
    const { refresh, resolve } = setup();
    fireEvent.click(button());
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(button()).toHaveTextContent("Verificando…");
    expect(button()).toBeDisabled();
    expect(button()).toHaveAttribute("aria-busy", "true");
    expect(screen.getByRole("status")).toHaveTextContent("Verificando a API e o banco…");
    expect(screen.queryByText("fallback")).toBeNull();

    await act(async () => resolve());

    await waitFor(() => expect(button()).toHaveTextContent("Verificar de novo"));
    expect(button()).toBeEnabled();
    expect(button()).not.toHaveAttribute("aria-busy");
    expect(screen.getByRole("status")).toBeEmptyDOMElement();
  });

  it("clique durante a verificação não dispara outra", () => {
    const { refresh } = setup();
    fireEvent.click(button());
    fireEvent.click(button());
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it("clique também atualiza o overview da casca (uma vez)", () => {
    const overviewRefresh = vi.fn(async () => {});
    setup(overviewRefresh);
    fireEvent.click(button());
    fireEvent.click(button());
    expect(overviewRefresh).toHaveBeenCalledTimes(1);
  });
});
