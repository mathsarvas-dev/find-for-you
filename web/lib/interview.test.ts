import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./profile";
import {
  answerInterview,
  editableFields,
  finishInterview,
  getInterview,
  resolveSuggestion,
  retryInterview,
  skipQuestion,
  startInterview,
} from "./interview";

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

const current = {
  id: 4,
  state: "awaiting_answer",
  error: null,
  progress: { asked: 1, max: 12 },
  messages: [{ id: 1, role: "assistant", kind: "question", content: "Quantos pedidos?", gap_id: "g" }],
  suggestions: [],
};

describe("startInterview", () => {
  it("POST /interview devolve id e estado", async () => {
    const fetchMock = mockFetch(async () => jsonResponse(201, { id: 4, state: "generating" }));
    await expect(startInterview()).resolves.toEqual({ id: 4, state: "generating" });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://localhost:8000/interview");
    expect(init?.method).toBe("POST");
  });

  it("409 sem perfil vira ApiError com a mensagem do servidor", async () => {
    mockFetch(async () =>
      jsonResponse(409, { detail: "Confirme seu perfil antes de iniciar a entrevista." }),
    );
    const err = await startInterview().catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(409);
    expect(err.message).toBe("Confirme seu perfil antes de iniciar a entrevista.");
  });
});

describe("getInterview", () => {
  it("404 significa sem entrevista: devolve null", async () => {
    mockFetch(async () => jsonResponse(404, { detail: "Nenhuma entrevista encontrada." }));
    await expect(getInterview()).resolves.toBeNull();
  });

  it("devolve a entrevista atual", async () => {
    const fetchMock = mockFetch(async () => jsonResponse(200, current));
    await expect(getInterview()).resolves.toEqual(current);
    expect(fetchMock.mock.calls[0][0]).toBe("http://localhost:8000/interview/current");
  });

  it("erro de rede vira ApiError sem status", async () => {
    mockFetch(async () => {
      throw new TypeError("Failed to fetch");
    });
    const err = await getInterview().catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBeNull();
  });
});

describe("ações da sessão", () => {
  it("answerInterview envia o texto em JSON", async () => {
    const fetchMock = mockFetch(async () => jsonResponse(202, { state: "generating" }));
    await expect(answerInterview(4, "Uns 300 por dia")).resolves.toEqual({ state: "generating" });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://localhost:8000/interview/4/answer");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(init?.body as string)).toEqual({ text: "Uns 300 por dia" });
  });

  it("422 da resposta repassa o detail", async () => {
    mockFetch(async () => jsonResponse(422, { detail: "A resposta não pode ser vazia." }));
    await expect(answerInterview(4, " ")).rejects.toThrow("A resposta não pode ser vazia.");
  });

  it.each([
    [skipQuestion, "skip"],
    [finishInterview, "finish"],
    [retryInterview, "retry"],
  ] as const)("%o chama POST /interview/{id}/%s", async (fn, path) => {
    const fetchMock = mockFetch(async () => jsonResponse(200, { state: "generating" }));
    await expect(fn(4)).resolves.toEqual({ state: "generating" });
    expect(fetchMock.mock.calls[0][0]).toBe(`http://localhost:8000/interview/4/${path}`);
    expect(fetchMock.mock.calls[0][1]?.method).toBe("POST");
  });
});

describe("resolveSuggestion", () => {
  it("aceitar envia só a ação", async () => {
    const fetchMock = mockFetch(async () => jsonResponse(200, { status: "accepted", applied_version: 3 }));
    await expect(resolveSuggestion(7, "accept")).resolves.toEqual({
      status: "accepted",
      applied_version: 3,
    });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://localhost:8000/interview/suggestions/7/resolve");
    expect(JSON.parse(init?.body as string)).toEqual({ action: "accept" });
  });

  it("ajustar envia a proposta editada", async () => {
    const fetchMock = mockFetch(async () => jsonResponse(200, { status: "adjusted", applied_version: 4 }));
    const payload = { op: "add_note" as const, text: "Prefiro remoto" };
    await resolveSuggestion(7, "adjust", payload);
    expect(JSON.parse(fetchMock.mock.calls[0][1]?.body as string)).toEqual({
      action: "adjust",
      payload,
    });
  });

  it("409 vira a mensagem de item alterado", async () => {
    mockFetch(async () =>
      jsonResponse(409, { detail: "O item do perfil a que esta sugestão se refere mudou ou não existe mais." }),
    );
    const err = await resolveSuggestion(7, "accept").catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(409);
    expect(err.message).toBe("O item mudou; descarte esta sugestão");
  });

  it("422 repassa o detail do validador", async () => {
    mockFetch(async () => jsonResponse(422, { detail: "A sugestão não é válida." }));
    const err = await resolveSuggestion(7, "adjust", { op: "add_note", text: "x" }).catch((e) => e);
    expect(err.status).toBe(422);
    expect(err.message).toBe("A sugestão não é válida.");
  });
});

describe("editableFields", () => {
  it.each([
    ["append_achievement", ["text"]],
    ["set_experience_description", ["text"]],
    ["set_summary", ["text"]],
    ["set_headline", ["text"]],
    ["add_note", ["text"]],
    ["add_skill", ["name"]],
    ["set_preference", ["value"]],
    ["set_experience_dates", ["start", "end"]],
  ] as const)("%s edita %o", (op, fields) => {
    expect(editableFields(op).map((f) => f.key)).toEqual(fields);
  });
});
