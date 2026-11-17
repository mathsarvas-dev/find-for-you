import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ApplicationDetail } from "@/lib/applications";
import { getApplication, generateApplication } from "@/lib/applications";
import { ApiError } from "@/lib/profile";
import CandidaturaPage from "./page";

vi.mock("next/navigation", () => ({ useParams: () => ({ id: "7" }) }));
vi.mock("@/components/shell/OverviewContext", () => ({ useRefreshOverview: () => () => {} }));
vi.mock("@/lib/applications", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/applications")>()),
  getApplication: vi.fn(),
  generateApplication: vi.fn(),
}));

const HOSTILE = '<img src=x onerror="alert(1)"> Airflow';

function detail(over: Partial<ApplicationDetail> = {}): ApplicationDetail {
  return {
    id: 7, state: "ready", error: null, locale: "en", template: "us", referees: null,
    profile_version: 1, current_profile_version: 1, warnings: [],
    job: { id: 9, title: "Data Engineer", company_name: "Globex", url: "https://example.test", status: "open" },
    requirements: { must: [{ id: "m1", text: HOSTILE }], nice: [], keywords: [] },
    coverage: { must: { covered: 0, total: 1 }, nice: { covered: 0, total: 0 }, items: [] },
    resume: null, cover_letter: null, questions: [], tasks: [],
    can_generate: { allowed: true, reason: null }, applied_at: null,
    ...over,
  };
}

const getMock = vi.mocked(getApplication);
const genMock = vi.mocked(generateApplication);

beforeEach(() => {
  getMock.mockReset();
  genMock.mockReset();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("candidatura page", () => {
  it("renders the reading step with hostile text as plain text", async () => {
    getMock.mockResolvedValue(detail());
    const { container } = render(<CandidaturaPage />);
    expect(await screen.findByText(HOSTILE)).toBeInTheDocument();
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText(/0 de 1 obrigatórios/)).toBeInTheDocument();
  });

  it("shows the server reason and a regenerate button on error", async () => {
    getMock.mockResolvedValue(detail({
      state: "error",
      error: "A IA escreveu algo que não está no seu perfil. Gere de novo ou complete o perfil.",
      can_generate: { allowed: false, reason: "Seu perfil ainda está sendo processado." },
    }));
    render(<CandidaturaPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("A IA escreveu algo");
    const button = screen.getByRole("button", { name: "Gerar de novo" });
    expect(button).toBeDisabled();
    expect(screen.getByText("Seu perfil ainda está sendo processado.")).toBeInTheDocument();
  });

  it("shows the profile-changed warning with a regenerate button", async () => {
    getMock.mockResolvedValue(detail({ warnings: ["profile_changed"] }));
    render(<CandidaturaPage />);
    expect(await screen.findByText("O perfil mudou desde esta versão.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Gerar de novo" })).toBeEnabled();
  });

  it("keeps polling while generating and stops once ready", async () => {
    // capture the 3 s poll timer and fire it by hand: deterministic, no real waiting
    const realSetInterval = globalThis.setInterval;
    const realClearInterval = globalThis.clearInterval;
    const polls: Array<() => void> = [];
    const cleared: unknown[] = [];
    vi.spyOn(globalThis, "setInterval").mockImplementation(((fn: () => void, delay?: number, ...rest: unknown[]) => {
      if (delay === 3000) {
        polls.push(fn);
        return polls.length as unknown as ReturnType<typeof setInterval>;
      }
      return realSetInterval(fn, delay, ...(rest as []));
    }) as typeof setInterval);
    vi.spyOn(globalThis, "clearInterval").mockImplementation(((id?: unknown) => {
      if (typeof id === "number" && id > 0 && polls.length >= id) cleared.push(id);
      else realClearInterval(id as ReturnType<typeof setInterval>);
    }) as typeof clearInterval);

    getMock
      .mockResolvedValueOnce(detail({ state: "generating", requirements: null, coverage: null }))
      .mockResolvedValue(detail({ state: "ready" }));
    render(<CandidaturaPage />);
    await screen.findByText(/Gerando o currículo/);
    expect(polls).toHaveLength(1);

    await act(async () => {
      polls[0]();
    });
    expect(await screen.findByText(HOSTILE)).toBeInTheDocument();
    expect(getMock).toHaveBeenCalledTimes(2);
    expect(cleared).toHaveLength(1); // the poll is cleared once the application is ready
  });

  it("shows a retry button when loading fails", async () => {
    getMock.mockRejectedValueOnce(new ApiError(null, "Não consegui falar com a API."));
    getMock.mockResolvedValueOnce(detail());
    render(<CandidaturaPage />);
    expect(await screen.findByText("Não consegui falar com a API.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Tentar de novo" }));
    expect(await screen.findByText(HOSTILE)).toBeInTheDocument();
  });
});
