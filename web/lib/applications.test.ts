import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./profile";
import { prepareApplication } from "./applications";

function jsonResponse(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function mockFetch(impl: (url: string, init?: RequestInit) => Promise<Response>) {
  const fn = vi.fn(impl);
  vi.stubGlobal("fetch", fn);
  return fn;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("prepareApplication", () => {
  it("asks the worker to generate only a draft", async () => {
    const fetchMock = mockFetch(async (url, init) => {
      if (init?.method === "POST" && url.endsWith("/applications")) {
        return jsonResponse(201, { id: 5, job_id: 9, state: "draft" });
      }
      return jsonResponse(202, { id: 5, state: "generating" });
    });
    await expect(prepareApplication(9)).resolves.toBe(5);
    expect(fetchMock.mock.calls.map((c) => c[0])).toEqual([
      "http://localhost:8000/applications",
      "http://localhost:8000/applications/5/generate",
    ]);
  });

  it("does not generate again when the application is already ready", async () => {
    const fetchMock = mockFetch(async () => jsonResponse(200, { id: 5, job_id: 9, state: "ready" }));
    await expect(prepareApplication(9)).resolves.toBe(5);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("surfaces the server message from a 429 as ApiError", async () => {
    mockFetch(async (url) => {
      if (url.endsWith("/generate")) {
        return jsonResponse(429, { detail: "Limite diário de 5 candidaturas geradas atingido. Tente de novo amanhã." });
      }
      return jsonResponse(200, { id: 5, job_id: 9, state: "draft" });
    });
    const err = await prepareApplication(9).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(429);
    expect((err as ApiError).message).toMatch(/Limite diário/);
  });
});

describe("downloadArtifact", () => {
  it("turns a 503 into an ApiError with the server message", async () => {
    mockFetch(async () => jsonResponse(503, { detail: "Não foi possível gerar o PDF agora. Use a versão em texto." }));
    const { downloadArtifact } = await import("./applications");
    const err = await downloadArtifact(7, "resume", "pdf").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(503);
    expect((err as ApiError).message).toMatch(/Use a versão em texto/);
  });

  it("builds the URLs of the resume and the letter", async () => {
    const { artifactUrl } = await import("./applications");
    expect(artifactUrl(7, "resume", "pdf")).toBe("http://localhost:8000/applications/7/resume.pdf");
    expect(artifactUrl(7, "cover_letter", "txt")).toBe("http://localhost:8000/applications/7/cover-letter.txt");
  });
});
