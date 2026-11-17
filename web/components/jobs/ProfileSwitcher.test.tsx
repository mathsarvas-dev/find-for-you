import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ApiError } from "@/lib/profile";
import type { NamedProfile } from "@/lib/searchProfiles";
import { filters } from "./fixtures";
import { ProfileSwitcher } from "./ProfileSwitcher";

const api = vi.hoisted(() => ({
  list: vi.fn(),
  create: vi.fn(),
  activate: vi.fn(),
  rename: vi.fn(),
  remove: vi.fn(),
}));
vi.mock("@/lib/searchProfiles", async (orig) => ({
  ...(await orig<typeof import("@/lib/searchProfiles")>()),
  listSearchProfiles: api.list,
  createSearchProfile: api.create,
  activateSearchProfile: api.activate,
  renameSearchProfile: api.rename,
  deleteSearchProfile: api.remove,
}));

const prof = (id: number, name: string, active = false): NamedProfile => ({
  id, name, active, filters: filters(), query_text: null, updated_at: "2026-11-17T00:00:00Z",
});

beforeEach(() => {
  api.list.mockResolvedValue([prof(1, "default", true), prof(2, "Remoto Europa")]);
});
afterEach(() => {
  cleanup();
  Object.values(api).forEach((f) => f.mockReset());
});

const setup = async () => {
  const onSwitched = vi.fn();
  render(<ProfileSwitcher onSwitched={onSwitched} />);
  const select = await screen.findByRole("combobox", { name: "Perfil de busca" });
  return { onSwitched, select };
};

describe("ProfileSwitcher", () => {
  it("lists the profiles with the active one selected", async () => {
    const { select } = await setup();
    expect(select).toHaveValue("1");
    expect(screen.getAllByRole("option").map((o) => o.textContent)).toEqual(["default", "Remoto Europa"]);
  });

  it("switching activates and tells the page to reload", async () => {
    api.activate.mockResolvedValue(prof(2, "Remoto Europa", true));
    const { select, onSwitched } = await setup();
    api.list.mockResolvedValue([prof(1, "default"), prof(2, "Remoto Europa", true)]);
    fireEvent.change(select, { target: { value: "2" } });
    await waitFor(() => expect(onSwitched).toHaveBeenCalled());
    expect(api.activate).toHaveBeenCalledWith(2);
    expect(select).toHaveValue("2");
  });

  it("shows the API message when switching fails and keeps the old selection", async () => {
    api.activate.mockRejectedValue(new ApiError(500, "Falhou."));
    const { select, onSwitched } = await setup();
    fireEvent.change(select, { target: { value: "2" } });
    expect(await screen.findByRole("alert")).toHaveTextContent("Falhou.");
    expect(select).toHaveValue("1");
    expect(onSwitched).not.toHaveBeenCalled();
  });

  it("creates a profile from the current one and makes it active", async () => {
    api.create.mockResolvedValue(prof(3, "Brasil CLT", true));
    api.list.mockResolvedValueOnce([prof(1, "default", true)]);
    api.list.mockResolvedValueOnce([prof(1, "default"), prof(3, "Brasil CLT", true)]);
    const { onSwitched } = await setup();
    fireEvent.click(screen.getByRole("button", { name: "Novo perfil" }));
    const input = screen.getByLabelText("Nome do perfil");
    expect(input).toHaveFocus();
    fireEvent.change(input, { target: { value: "  Brasil CLT " } });
    fireEvent.click(screen.getByRole("button", { name: "Criar" }));
    await waitFor(() => expect(api.create).toHaveBeenCalledWith("Brasil CLT", true));
    expect(await screen.findByRole("status")).toHaveTextContent("Perfil “Brasil CLT” criado");
    expect(screen.getByRole("combobox", { name: "Perfil de busca" })).toHaveValue("3");
    expect(onSwitched).not.toHaveBeenCalled(); // same filters: nothing to reload
  });

  it("blocks an invalid name and surfaces a duplicate-name error", async () => {
    api.create.mockRejectedValue(new ApiError(409, "Já existe um perfil de busca com esse nome."));
    await setup();
    fireEvent.click(screen.getByRole("button", { name: "Novo perfil" }));
    fireEvent.change(screen.getByLabelText("Nome do perfil"), { target: { value: "   " } });
    expect(screen.getByRole("button", { name: "Criar" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Nome do perfil"), { target: { value: "default" } });
    fireEvent.click(screen.getByRole("button", { name: "Criar" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Já existe");
    expect(screen.getByLabelText("Nome do perfil")).toBeInTheDocument(); // form stays open
  });

  it("renames a profile from the manage list", async () => {
    api.rename.mockResolvedValue(prof(1, "Principal", true));
    await setup();
    fireEvent.click(screen.getByRole("button", { name: "Gerenciar perfis" }));
    fireEvent.click(screen.getByRole("button", { name: "Renomear default" }));
    const input = screen.getByLabelText("Novo nome de default");
    expect(input).toHaveValue("default");
    expect(input).toHaveFocus();
    fireEvent.change(input, { target: { value: "Principal" } });
    api.list.mockResolvedValue([prof(1, "Principal", true), prof(2, "Remoto Europa")]);
    fireEvent.click(screen.getByRole("button", { name: "Salvar nome" }));
    await waitFor(() => expect(api.rename).toHaveBeenCalledWith(1, "Principal"));
    expect(await screen.findByRole("option", { name: "Principal" })).toBeInTheDocument();
  });

  it("cannot delete the active profile; deletes an inactive one after confirming", async () => {
    api.remove.mockResolvedValue(undefined);
    await setup();
    fireEvent.click(screen.getByRole("button", { name: "Gerenciar perfis" }));
    expect(screen.getByRole("button", { name: "Excluir default" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Excluir Remoto Europa" }));
    expect(screen.getByText(/Excluir o perfil “Remoto Europa”\?/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Cancelar" }));
    expect(api.remove).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Excluir Remoto Europa" }));
    api.list.mockResolvedValue([prof(1, "default", true)]);
    fireEvent.click(screen.getByRole("button", { name: "Confirmar exclusão" }));
    await waitFor(() => expect(api.remove).toHaveBeenCalledWith(2));
    await waitFor(() => expect(screen.queryByRole("option", { name: "Remoto Europa" })).toBeNull());
  });

  it("with no saved profile only offers to create one", async () => {
    api.list.mockResolvedValue([]);
    const { select } = await setup();
    expect(select).toBeDisabled();
    expect(screen.getByRole("option")).toHaveTextContent("Padrão");
    expect(screen.getByRole("button", { name: "Novo perfil" })).toBeEnabled();
  });
});
