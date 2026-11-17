import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { getOptimization, optimizeLinkedin, type OptimizationOut } from "@/lib/linkedin";
import { ApiError } from "@/lib/profile";
import LinkedinPage from "./page";

vi.mock("@/lib/linkedin", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/linkedin")>()),
  getOptimization: vi.fn(),
  optimizeLinkedin: vi.fn(),
}));

const get = vi.mocked(getOptimization);
const run = vi.mocked(optimizeLinkedin);

const out = (extra: Partial<OptimizationOut> = {}): OptimizationOut => ({
  profile_version: 1,
  language: "pt",
  stale: false,
  created_at: "2026-11-17T10:00:00+00:00",
  result: {
    language: "pt",
    headline: { text: "Engenheira de dados | Airflow", source_refs: ["exp1"] },
    about: [{ text: "Primeiro parágrafo.", source_refs: [] }, { text: "Segundo parágrafo.", source_refs: [] }],
    experiences: [
      { item_id: "exp1", title: "Engenheira de Dados", company: "Acme", bullets: [{ text: "Reduzi o pipeline.", source_refs: [] }] },
    ],
    skills: [{ id: "sk1", name: "Airflow" }, { id: "sk2", name: "SQL" }],
    viewpoints: [
      { key: "recruiter", label: "Recrutador", notes: ["Mostre a área."] },
      { key: "ats", label: "ATS (filtros automáticos)", notes: [] },
      { key: "technical_manager", label: "Gestor técnico", notes: [] },
      { key: "international", label: "Mercado internacional", notes: [] },
    ],
    improvements: [
      { priority: "high", section: "about", text: "Escreva uma abertura.", source_refs: [] },
      { priority: "low", section: "skills", text: "Ordene as habilidades.", source_refs: [] },
    ],
    rejected: [],
  },
  ...extra,
});

beforeEach(() => {
  get.mockReset();
  run.mockReset();
});

describe("linkedin page", () => {
  it("starts with nothing and generates suggestions in the chosen language", async () => {
    get.mockResolvedValue(null);
    run.mockResolvedValue(out({ language: "en" }));
    render(<LinkedinPage />);
    const button = await screen.findByRole("button", { name: "Gerar sugestões" });
    fireEvent.change(screen.getByLabelText("Idioma dos textos"), { target: { value: "en" } });
    fireEvent.click(button);
    await waitFor(() => expect(run).toHaveBeenCalledWith("en"));
    expect(await screen.findByText("Engenheira de dados | Airflow")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Gerar de novo" })).toBeInTheDocument();
  });

  it("shows every section, the four viewpoints and the prioritized list", async () => {
    get.mockResolvedValue(out());
    render(<LinkedinPage />);
    expect(await screen.findByText("Primeiro parágrafo.")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Experiência: Engenheira de Dados na Acme" })).toBeInTheDocument();
    for (const name of ["Recrutador", "ATS (filtros automáticos)", "Gestor técnico", "Mercado internacional"]) {
      expect(screen.getByRole("region", { name })).toBeInTheDocument();
    }
    const items = screen.getAllByRole("listitem").map((li) => li.textContent);
    expect(items.findIndex((t) => t?.includes("Prioridade alta"))).toBeLessThan(
      items.findIndex((t) => t?.includes("Prioridade baixa")),
    );
    expect(within(screen.getByRole("region", { name: "Gestor técnico" })).getByText("Sem observações.")).toBeInTheDocument();
  });

  it("copies the headline and confirms", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    get.mockResolvedValue(out());
    render(<LinkedinPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Copiar título" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("Engenheira de dados | Airflow"));
    expect(await screen.findByText("Copiado.")).toBeInTheDocument();
  });

  it("copies the about paragraphs joined by a blank line", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    get.mockResolvedValue(out());
    render(<LinkedinPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Copiar sobre" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("Primeiro parágrafo.\n\nSegundo parágrafo."));
  });

  it("warns when the profile changed after the analysis", async () => {
    get.mockResolvedValue(out({ stale: true }));
    render(<LinkedinPage />);
    expect(await screen.findByText(/Seu perfil mudou depois desta análise/)).toBeInTheDocument();
  });

  it("lists the sections that were rejected to avoid inventing data", async () => {
    const o = out();
    o.result.about = [];
    o.result.rejected = [{ section: "about", reasons: ["Termo que não está no seu perfil: Kubernetes"] }];
    get.mockResolvedValue(o);
    render(<LinkedinPage />);
    expect(await screen.findByText(/Para não inventar dados/)).toBeInTheDocument();
    expect(screen.getByText(/Kubernetes/)).toBeInTheDocument();
  });

  it("shows the API message for a refused profile and keeps the button usable", async () => {
    get.mockResolvedValue(null);
    run.mockRejectedValue(new ApiError(409, "Seu perfil veio de um texto colado, sem extração."));
    render(<LinkedinPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Gerar sugestões" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("texto colado");
    expect(screen.getByRole("button", { name: "Gerar sugestões" })).toBeEnabled();
  });

  it("a load error offers a retry", async () => {
    get.mockRejectedValueOnce(new ApiError(null, "Não consegui falar com a API."));
    get.mockResolvedValueOnce(null);
    render(<LinkedinPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Não consegui falar com a API.");
    fireEvent.click(screen.getByRole("button", { name: "Tentar de novo" }));
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
  });
});
