import { describe, expect, it } from "vitest";
import { interpretHealth, interpretWorker } from "./health";

const at = new Date("2026-11-11T12:00:00Z");
const url = "http://localhost:8000";

describe("interpretHealth", () => {
  it("200 com db up: tudo ok", () => {
    const r = interpretHealth(url, { kind: "response", status: 200, body: { status: "ok", db: "up" }, ms: 12 }, at);
    expect([r.api, r.db, r.problem]).toEqual(["ok", "ok", null]);
  });

  it("503 com db down: API ok, banco fora", () => {
    const r = interpretHealth(url, { kind: "response", status: 503, body: { status: "degraded", db: "down" }, ms: 40 }, at);
    expect([r.api, r.db, r.problem]).toEqual(["ok", "down", "db-down"]);
  });

  it("sem resposta: API fora, banco sem dados", () => {
    const r = interpretHealth(url, { kind: "no-response", reason: "network" }, at);
    expect([r.api, r.db, r.problem]).toEqual(["down", "unknown", "api-unreachable"]);
    expect(r.apiDetail).toBe("sem conexão");
  });

  it("estouro de tempo: detalhe diz quanto esperou", () => {
    const r = interpretHealth(url, { kind: "no-response", reason: "timeout" }, at);
    expect([r.api, r.problem]).toEqual(["down", "api-unreachable"]);
    expect(r.apiDetail).toBe("sem resposta em 3 s");
  });

  it("resposta inesperada: API tratada como fora", () => {
    const r = interpretHealth(url, { kind: "response", status: 500, body: null, ms: 5 }, at);
    expect([r.api, r.db, r.problem, r.httpStatus]).toEqual(["down", "unknown", "api-unexpected", 500]);
  });
});

describe("interpretWorker", () => {
  it("heartbeat recente: ok", () => {
    const r = interpretWorker({ kind: "response", status: 200, body: { status: "up", seen_at: "2026-11-11T11:59:50Z" }, ms: 9 }, at);
    expect(r.state).toBe("ok");
  });

  it("heartbeat antigo: down com minutos desde o último sinal", () => {
    const r = interpretWorker({ kind: "response", status: 200, body: { status: "down", seen_at: "2026-11-11T11:55:00Z" }, ms: 9 }, at);
    expect([r.state, r.detail]).toEqual(["down", "último sinal há 5 min"]);
  });

  it("nunca visto: down", () => {
    const r = interpretWorker({ kind: "response", status: 200, body: { status: "down", seen_at: null }, ms: 9 }, at);
    expect([r.state, r.detail]).toEqual(["down", "nenhum sinal registrado"]);
  });

  it("API fora do ar: unknown", () => {
    const r = interpretWorker({ kind: "no-response", reason: "network" }, at);
    expect(r.state).toBe("unknown");
  });

  it("resposta inesperada: unknown", () => {
    const r = interpretWorker({ kind: "response", status: 500, body: null, ms: 4 }, at);
    expect(r.state).toBe("unknown");
  });
});
