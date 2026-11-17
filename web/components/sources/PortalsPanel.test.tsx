import { afterEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { listPortals, setPortalEnabled, type Portal } from "@/lib/portals";
import { PortalsPanel } from "./PortalsPanel";

vi.mock("@/lib/portals", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/portals")>()),
  listPortals: vi.fn(),
  setPortalEnabled: vi.fn(),
}));

const list = vi.mocked(listPortals);
const toggle = vi.mocked(setPortalEnabled);

const WARNING =
  "Os termos deste portal não foram totalmente verificados. Ligar pode violar regras do site. A coleta respeita o robots.txt e para se o site recusar o acesso. Isto é apoio à decisão, não parecer jurídico.";

const base: Portal = {
  id: "vagas",
  name: "Vagas.com",
  kind: "portal",
  status: "conditional",
  checked_at: "2026-11-14",
  evidence_urls: ["https://www.vagas.com.br/termos"],
  evidence_summary: "Termos citam coleta automatizada sem autorização.",
  enabled: false,
  blocked_by_site: false,
  blocked_at: null,
  can_toggle: true,
};

afterEach(() => {
  list.mockReset();
  toggle.mockReset();
});

async function renderPanel(portals: Portal[]) {
  list.mockResolvedValue(portals);
  render(<PortalsPanel />);
  await act(async () => {});
}

describe("PortalsPanel", () => {
  it("status em texto, data da verificação e resumo da evidência", async () => {
    await renderPanel([base]);
    expect(screen.getByText("Condicional")).toBeInTheDocument();
    expect(screen.getByText("Verificado em 14/11/2026")).toBeInTheDocument();
    expect(screen.getByText("Termos citam coleta automatizada sem autorização.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /termos/ })).toHaveAttribute("target", "_blank");
  });

  it("evidência é texto: tags aparecem literais", async () => {
    await renderPanel([{ ...base, evidence_summary: "<b>negrito</b>" }]);
    expect(screen.getByText("<b>negrito</b>")).toBeInTheDocument();
  });

  it("bloqueado: só importar por link e sem interruptor", async () => {
    await renderPanel([
      { ...base, id: "indeed", name: "Indeed", status: "blocked", can_toggle: false, evidence_urls: [] },
    ]);
    expect(screen.getByText("Bloqueado")).toBeInTheDocument();
    expect(screen.getByText("Só importar por link.")).toBeInTheDocument();
    expect(screen.queryByRole("switch")).toBeNull();
  });

  it("permitido sem interruptor", async () => {
    await renderPanel([{ ...base, status: "allowed", can_toggle: false, enabled: true }]);
    expect(screen.getByText("Permitido")).toBeInTheDocument();
    expect(screen.queryByRole("switch")).toBeNull();
  });

  it("recusado pelo site mostra a data e que a coleta está suspensa", async () => {
    await renderPanel([{ ...base, enabled: true, blocked_by_site: true, blocked_at: "2026-11-10T09:00:00Z" }]);
    expect(screen.getByText("O site recusou nosso acesso em 10/11/2026; coleta suspensa.")).toBeInTheDocument();
  });

  it("ligar exige confirmação com o aviso; nada é enviado antes", async () => {
    await renderPanel([base]);
    fireEvent.click(screen.getByRole("switch", { name: "Ligar Vagas.com" }));
    const dialog = screen.getByRole("alertdialog");
    expect(within(dialog).getByText(WARNING)).toBeInTheDocument();
    expect(toggle).not.toHaveBeenCalled();
  });

  it("cancelar fecha o aviso sem ligar", async () => {
    await renderPanel([base]);
    fireEvent.click(screen.getByRole("switch", { name: "Ligar Vagas.com" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancelar" }));
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(toggle).not.toHaveBeenCalled();
  });

  it("ligar mesmo assim envia enabled true e mostra o novo estado", async () => {
    toggle.mockResolvedValue({ ...base, enabled: true });
    await renderPanel([base]);
    fireEvent.click(screen.getByRole("switch", { name: "Ligar Vagas.com" }));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Ligar mesmo assim" }));
    });
    expect(toggle).toHaveBeenCalledWith("vagas", true);
    expect(screen.getByRole("switch", { name: "Desligar Vagas.com" })).toBeChecked();
  });

  it("desligar não pede confirmação", async () => {
    toggle.mockResolvedValue({ ...base, enabled: false });
    await renderPanel([{ ...base, enabled: true }]);
    await act(async () => {
      fireEvent.click(screen.getByRole("switch", { name: "Desligar Vagas.com" }));
    });
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(toggle).toHaveBeenCalledWith("vagas", false);
  });

  it("erro da API aparece como texto", async () => {
    toggle.mockRejectedValue(new Error("falhou"));
    await renderPanel([base]);
    fireEvent.click(screen.getByRole("switch", { name: "Ligar Vagas.com" }));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Ligar mesmo assim" }));
    });
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });
});
