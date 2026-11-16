import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./profile";
import { addFeed, deleteFeed, listFeeds, parseKeywords } from "./feeds";

function jsonResponse(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("listFeeds", () => {
  it("lê GET /feeds com feeds e fontes", async () => {
    const body = { feeds: [], sources: [] };
    const fetchMock = vi.fn(async () => jsonResponse(200, body));
    vi.stubGlobal("fetch", fetchMock);
    expect(await listFeeds()).toEqual(body);
    expect(String((fetchMock.mock.calls[0] as unknown as [string])[0])).toMatch(/\/feeds$/);
  });
});

describe("addFeed and deleteFeed", () => {
  it("envia POST /feeds com fonte e parâmetros", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(201, { id: 7 }));
    vi.stubGlobal("fetch", fetchMock);
    await addFeed({ source: "remotive", params: { keywords: ["python"] } });
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toMatch(/\/feeds$/);
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({
      source: "remotive",
      params: { keywords: ["python"] },
    });
  });

  it("mostra a mensagem da API quando o feed é recusado", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(409, { detail: "Esse feed já existe." })));
    await expect(addFeed({ source: "remotive", params: { keywords: [] } })).rejects.toMatchObject({
      status: 409,
      message: "Esse feed já existe.",
    });
  });

  it("DELETE /feeds/{id}", async () => {
    const fetchMock = vi.fn(async () => new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);
    await deleteFeed(7);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toMatch(/\/feeds\/7$/);
    expect(init.method).toBe("DELETE");
  });
});

describe("parseKeywords", () => {
  it("separa por vírgula ou quebra de linha e descarta vazias", () => {
    expect(parseKeywords(" python, dados\n\n react ,")).toEqual({
      ok: true,
      keywords: ["python", "dados", "react"],
    });
  });

  it("aceita até 5 palavras-chave", () => {
    expect(parseKeywords("a,b,c,d,e")).toEqual({ ok: true, keywords: ["a", "b", "c", "d", "e"] });
  });

  it("recusa a sexta palavra-chave", () => {
    expect(parseKeywords("a,b,c,d,e,f")).toEqual({
      ok: false,
      error: "Use no máximo 5 palavras-chave.",
    });
  });

  it("recusa palavra-chave com mais de 60 caracteres", () => {
    expect(parseKeywords("x".repeat(61))).toEqual({
      ok: false,
      error: "Cada palavra-chave pode ter até 60 caracteres.",
    });
    expect(parseKeywords("x".repeat(60))).toEqual({ ok: true, keywords: ["x".repeat(60)] });
  });

  it("texto vazio não gera palavras-chave", () => {
    expect(parseKeywords("  ,  ")).toEqual({ ok: true, keywords: [] });
  });
});

it("ApiError é o erro lançado para falhas de rede já traduzidas", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => {
    throw new TypeError("offline");
  }));
  await expect(listFeeds()).rejects.toBeInstanceOf(ApiError);
});
