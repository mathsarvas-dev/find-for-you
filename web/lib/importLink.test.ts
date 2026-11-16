import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./profile";
import { importLink, NeedsTextError } from "./importLink";

function jsonResponse(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("importLink", () => {
  it("201 é vaga nova; envia só a URL quando não há texto", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(201, { job_id: 9, created: true }));
    vi.stubGlobal("fetch", fetchMock);
    expect(await importLink({ url: "https://example.test/vaga" })).toEqual({
      job_id: 9,
      created: true,
    });
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toMatch(/\/jobs\/import-link$/);
    expect(JSON.parse(String(init.body))).toEqual({ url: "https://example.test/vaga" });
  });

  it("200 é vaga que já estava importada", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(200, { job_id: 9, created: false })));
    expect(await importLink({ url: "https://example.test/vaga" })).toEqual({
      job_id: 9,
      created: false,
    });
  });

  it("422 needs_text vira NeedsTextError com o motivo", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(422, {
      detail: {
        code: "needs_text",
        reason: "no_jobposting",
        message: "Não consegui ler esta vaga automaticamente. Cole o título e a descrição.",
      },
    })));
    const err = await importLink({ url: "https://example.test/vaga" }).catch((e) => e);
    expect(err).toBeInstanceOf(NeedsTextError);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.reason).toBe("no_jobposting");
    expect(err.message).toBe("Não consegui ler esta vaga automaticamente. Cole o título e a descrição.");
  });

  it("envia título e descrição juntos quando colados", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(201, { job_id: 1, created: true }));
    vi.stubGlobal("fetch", fetchMock);
    await importLink({ url: "https://example.test/v", title: "Dev", description: "Texto" });
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(JSON.parse(String(init.body))).toEqual({
      url: "https://example.test/v", title: "Dev", description: "Texto",
    });
  });

  it("outros erros mostram o detail em texto", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(429, {
      detail: "Muitas importações em pouco tempo. Tente de novo mais tarde.",
    })));
    await expect(importLink({ url: "https://example.test/v" })).rejects.toMatchObject({
      status: 429,
      message: "Muitas importações em pouco tempo. Tente de novo mais tarde.",
    });
  });

  it("falha de rede vira ApiError sem status", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => {
      throw new TypeError("offline");
    }));
    await expect(importLink({ url: "https://example.test/v" })).rejects.toMatchObject({
      status: null,
    });
  });
});
