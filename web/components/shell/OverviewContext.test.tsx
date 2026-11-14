import { describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { OverviewContext, useRefreshOverview, type OverviewState } from "./OverviewContext";

function Probe() {
  const refresh = useRefreshOverview();
  return (
    <button type="button" onClick={refresh}>
      Atualizar
    </button>
  );
}

function renderWith(refresh: OverviewState["refresh"]) {
  render(
    <OverviewContext.Provider value={{ overview: null, status: "ok", refresh }}>
      <Probe />
    </OverviewContext.Provider>,
  );
}

describe("useRefreshOverview", () => {
  it("chama o refresh da casca uma vez por uso", () => {
    const refresh = vi.fn(async () => {});
    renderWith(refresh);
    fireEvent.click(screen.getByRole("button", { name: "Atualizar" }));
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it("ignora falhas, síncronas ou assíncronas", async () => {
    const rejecting = vi.fn(() => Promise.reject(new Error("rede")));
    renderWith(rejecting);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Atualizar" }));
    });
    expect(rejecting).toHaveBeenCalledTimes(1);
  });

  it("fora da casca não faz nada e não quebra", () => {
    render(<Probe />);
    expect(() => fireEvent.click(screen.getByRole("button", { name: "Atualizar" }))).not.toThrow();
  });
});
