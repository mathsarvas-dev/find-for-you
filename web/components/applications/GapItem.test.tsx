import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import type { CoverageItem } from "@/lib/applications";
import { resolveGap } from "@/lib/applications";
import { GapItem } from "./GapItem";

vi.mock("@/lib/applications", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/applications")>()),
  resolveGap: vi.fn(),
}));

const resolveMock = vi.mocked(resolveGap);
const ITEM: CoverageItem = { id: "m2", kind: "must", text: "Terraform", covered: false, line_ids: [], decision: null };

beforeEach(() => {
  resolveMock.mockReset();
});

describe("GapItem", () => {
  it("'Tenho isso' sends the answer and shows the new profile version", async () => {
    resolveMock.mockResolvedValue({ coverage: null, profile_version: 3, regenerated: true });
    const onResolved = vi.fn();
    render(<GapItem applicationId={7} item={ITEM} onResolved={onResolved} />);
    fireEvent.click(screen.getByRole("button", { name: "Tenho isso" }));
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "Terraform" } });
    fireEvent.click(screen.getByRole("button", { name: "Adicionar ao perfil" }));
    expect(await screen.findByText("Perfil atualizado (versão 3). Gerando de novo…")).toBeInTheDocument();
    expect(resolveMock).toHaveBeenCalledWith(7, "m2", "have", "Terraform");
    expect(onResolved).toHaveBeenCalled();
  });

  it("'Não tenho' only records the gap and never touches the profile", async () => {
    resolveMock.mockResolvedValue({ coverage: null, profile_version: null, regenerated: false });
    render(<GapItem applicationId={7} item={ITEM} onResolved={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Não tenho" }));
    expect(await screen.findByText("Lacuna conhecida: não vai para o currículo.")).toBeInTheDocument();
    expect(resolveMock).toHaveBeenCalledTimes(1);
    expect(resolveMock.mock.calls[0]).toEqual([7, "m2", "lack"]);
  });

  it("shows the requirement text as plain text", () => {
    render(<GapItem applicationId={7} item={{ ...ITEM, text: "<b>x</b>" }} onResolved={vi.fn()} />);
    expect(screen.getByText(/<b>x<\/b>/)).toBeInTheDocument();
  });
});
