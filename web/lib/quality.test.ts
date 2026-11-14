import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./profile";
import { deleteLabel, getReport, getSample, newSample, putLabel } from "./quality";

function jsonResponse(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}
function mockFetch(impl: (url: string, init?: RequestInit) => Promise<Response>) {
  const fn = vi.fn(impl);
  vi.stubGlobal("fetch", fn);
  return fn;
}
afterEach(() => vi.unstubAllGlobals());

describe("quality client", () => {
  it("getSample e newSample", async () => {
    const body = { items: [], labeled_total: 0 };
    const fn = mockFetch(async () => jsonResponse(200, body));
    await expect(getSample()).resolves.toEqual(body);
    await expect(newSample()).resolves.toEqual(body);
    expect(fn.mock.calls[0][0]).toBe("http://localhost:8000/quality/sample");
    expect(fn.mock.calls[1][0]).toBe("http://localhost:8000/quality/sample/new");
    expect(fn.mock.calls[1][1]?.method).toBe("POST");
  });

  it("putLabel faz PUT com {label}", async () => {
    const fn = mockFetch(async () => jsonResponse(200, { job_id: 7, label: "good" }));
    await expect(putLabel(7, "good")).resolves.toEqual({ job_id: 7, label: "good" });
    const [url, init] = fn.mock.calls[0];
    expect(url).toBe("http://localhost:8000/quality/labels/7");
    expect(init?.method).toBe("PUT");
    expect(JSON.parse(String(init?.body))).toEqual({ label: "good" });
  });

  it("deleteLabel usa DELETE e aceita 204", async () => {
    const fn = mockFetch(async () => new Response(null, { status: 204 }));
    await expect(deleteLabel(7)).resolves.toBeUndefined();
    expect(fn.mock.calls[0][0]).toBe("http://localhost:8000/quality/labels/7");
    expect(fn.mock.calls[0][1]?.method).toBe("DELETE");
  });

  it("getReport propaga a mensagem do servidor", async () => {
    mockFetch(async () => jsonResponse(409, { detail: "Sem perfil." }));
    const err = await getReport().catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.message).toBe("Sem perfil.");
  });
});
