import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ApplicationDetail, DocLine } from "@/lib/applications";
import { editLine, patchApplication } from "@/lib/applications";
import { ApiError } from "@/lib/profile";
import { ResumeStep } from "./ResumeStep";

vi.mock("@/lib/applications", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/applications")>()),
  editLine: vi.fn(),
  patchApplication: vi.fn(),
}));

const editMock = vi.mocked(editLine);
const HOSTILE = '<img src=x onerror="alert(1)"> Airflow';

function line(over: Partial<DocLine> & { id: string }): DocLine {
  return {
    section: "bullet", text: "", fields: {}, parent: null, source_refs: [], origin: "ai",
    covers: [], violations: [], ...over,
  };
}

const HEADER = line({
  id: "L1", section: "experience", origin: "fact", text: "Engenheira de Dados — Acme",
  fields: { company: "Acme" }, source_refs: ["exp1"],
});
const BULLET = line({
  id: "L2", section: "bullet", origin: "ai", text: "Reduzi o tempo em 40% com Airflow",
  parent: "exp1", source_refs: ["ach1"],
});

function detail(lines: DocLine[], status: "valid" | "needs_fix" = "valid", over: Partial<ApplicationDetail> = {}): ApplicationDetail {
  return {
    id: 7, state: "ready", error: null, locale: "pt", template: "us", referees: null,
    profile_version: 1, current_profile_version: 1, warnings: [],
    job: { id: 9, title: "Data Engineer", company_name: "Globex", url: "https://example.test", status: "open" },
    requirements: { must: [], nice: [], keywords: [] },
    coverage: {
      must: { covered: 1, total: 2 }, nice: { covered: 0, total: 0 },
      items: [{ id: "m2", kind: "must", text: "Terraform", covered: false, line_ids: [], decision: null }],
    },
    resume: { version: 1, status, lines }, cover_letter: null, questions: [], tasks: [],
    can_generate: { allowed: true, reason: null }, applied_at: null,
    ...over,
  };
}

afterEach(() => {
  editMock.mockReset();
});

describe("ResumeStep", () => {
  it("saves a valid edit and tells the page to reload", async () => {
    editMock.mockResolvedValue({ version: 1, status: "valid", lines: [HEADER, { ...BULLET, origin: "user", text: "Cortei o tempo com Airflow" }] });
    const onChanged = vi.fn();
    render(<ResumeStep detail={detail([HEADER, BULLET])} onChanged={onChanged} />);
    fireEvent.click(screen.getByRole("button", { name: "Editar" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Texto da linha" }), {
      target: { value: "Cortei o tempo com Airflow" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Salvar" }));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(editMock).toHaveBeenCalledWith(7, "L2", "Cortei o tempo com Airflow");
    expect(screen.getByText("Cortei o tempo com Airflow")).toBeInTheDocument();
  });

  it("shows the reason and the blocking notice for an invalid edit", async () => {
    const bad = { ...BULLET, origin: "user" as const, text: "Reduzi em 60%", violations: ["Número, data ou valor que não está no item citado: 60%"] };
    editMock.mockResolvedValue({ version: 1, status: "needs_fix", lines: [HEADER, bad] });
    render(<ResumeStep detail={detail([HEADER, BULLET])} onChanged={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Editar" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Texto da linha" }), { target: { value: "Reduzi em 60%" } });
    fireEvent.click(screen.getByRole("button", { name: "Salvar" }));
    expect(await screen.findByText("Número, data ou valor que não está no item citado: 60%")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Corrija as linhas marcadas para liberar o PDF");
  });

  it("gives a fact line no edit button, only the hint to edit the profile", () => {
    render(<ResumeStep detail={detail([HEADER, BULLET])} onChanged={vi.fn()} />);
    expect(screen.getAllByRole("button", { name: "Editar" })).toHaveLength(1);
    expect(screen.getByText(/edite o perfil/)).toBeInTheDocument();
  });

  it("names the source item on a generated line", () => {
    render(<ResumeStep detail={detail([HEADER, BULLET])} onChanged={vi.fn()} />);
    expect(screen.getByText("Do perfil: Engenheira de Dados — Acme")).toBeInTheDocument();
  });

  it("renders hostile line text and reasons as plain text", () => {
    const hostile = { ...BULLET, text: HOSTILE, violations: ["<script>x</script> motivo"] };
    const { container } = render(<ResumeStep detail={detail([HEADER, hostile])} onChanged={vi.fn()} />);
    expect(screen.getByText(HOSTILE)).toBeInTheDocument();
    expect(screen.getByText("<script>x</script> motivo")).toBeInTheDocument();
    expect(container.querySelector("img, script")).toBeNull();
  });

  it("lists the requirements the resume does not cover", () => {
    render(<ResumeStep detail={detail([HEADER, BULLET])} onChanged={vi.fn()} />);
    expect(screen.getByText("Não encontrei no seu perfil")).toBeInTheDocument();
    expect(screen.getByText("Terraform")).toBeInTheDocument();
  });
});

describe("ResumeStep settings and downloads", () => {
  it("changing the template calls patchApplication", async () => {
    const patch = vi.mocked(patchApplication).mockResolvedValue({
      id: 7, job_id: 9, state: "ready", error: null, locale: "pt", template: "de",
      referees: null, profile_version: 1, applied_at: null,
    });
    const onChanged = vi.fn();
    render(<ResumeStep detail={detail([HEADER, BULLET])} onChanged={onChanged} />);
    fireEvent.change(screen.getByRole("combobox", { name: /Modelo do currículo/ }), { target: { value: "de" } });
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(patch).toHaveBeenCalledWith(7, { template: "de" });
    patch.mockRestore();
  });

  it("shows the referees field only for the Australian template", () => {
    const { rerender } = render(<ResumeStep detail={detail([HEADER, BULLET])} onChanged={vi.fn()} />);
    expect(screen.queryByLabelText(/Referências/)).toBeNull();
    rerender(<ResumeStep detail={detail([HEADER, BULLET], "valid", { template: "au" })} onChanged={vi.fn()} />);
    expect(screen.getByLabelText(/Referências/)).toBeInTheDocument();
  });

  it("disables the downloads with the reason while a line needs fixing", () => {
    const bad = { ...BULLET, violations: ["x"] };
    render(<ResumeStep detail={detail([HEADER, bad], "needs_fix")} onChanged={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Baixar PDF" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Baixar texto" })).toBeDisabled();
    expect(screen.getAllByText("Corrija as linhas marcadas antes de baixar.").length).toBeGreaterThan(0);
  });

  it("shows the server message when the PDF is unavailable", async () => {
    const lib = await import("@/lib/applications");
    const dl = vi.spyOn(lib, "downloadArtifact").mockRejectedValue(
      new ApiError(503, "Não foi possível gerar o PDF agora. Use a versão em texto."),
    );
    render(<ResumeStep detail={detail([HEADER, BULLET])} onChanged={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Baixar PDF" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Use a versão em texto");
    dl.mockRestore();
  });
});
