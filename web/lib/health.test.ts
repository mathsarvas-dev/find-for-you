import { describe, expect, it } from "vitest";
import { interpretHealth } from "./health";

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
