import { afterEach, describe, expect, it, vi } from "vitest";
import { followUpState, formatDay, getHighlights, getPipeline, getReport, patchPipeline } from "./pipeline";

function mockFetch(body: unknown) {
  const fn = vi.fn<typeof fetch>(
    async () => new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } }),
  );
  vi.stubGlobal("fetch", fn);
  return fn;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("pipeline client", () => {
  it("reads the board, the report and the highlights", async () => {
    const fn = mockFetch({ items: [], today: "2026-11-16" });
    await getPipeline();
    await getReport();
    await getHighlights();
    expect(fn.mock.calls.map((c) => String(c[0]))).toEqual([
      "http://localhost:8000/pipeline",
      "http://localhost:8000/pipeline/report",
      "http://localhost:8000/highlights",
    ]);
  });

  it("patches with JSON, keeping an explicit null", async () => {
    const fn = mockFetch({ id: 4 });
    await patchPipeline(4, { follow_up_at: null, stage: "offer" });
    const [url, init] = fn.mock.calls[0];
    expect(url).toBe("http://localhost:8000/pipeline/4");
    expect(init?.method).toBe("PATCH");
    expect(JSON.parse(String(init?.body))).toEqual({ follow_up_at: null, stage: "offer" });
  });
});

describe("follow-up helpers", () => {
  it("compares the day as text", () => {
    expect(followUpState("2026-11-15", "2026-11-16")).toBe("overdue");
    expect(followUpState("2026-11-16", "2026-11-16")).toBe("today");
    expect(followUpState("2026-11-17", "2026-11-16")).toBe("upcoming");
  });

  it("formats dd/mm/aaaa without time zones", () => {
    expect(formatDay("2026-12-01")).toBe("01/12/2026");
  });
});
