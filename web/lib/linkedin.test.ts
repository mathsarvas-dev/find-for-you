import { afterEach, describe, expect, it, vi } from "vitest";
import { getOptimization, optimizeLinkedin, rejectedLabel } from "./linkedin";

function mockFetch(body: unknown, status = 200) {
  const fn = vi.fn<typeof fetch>(
    async () => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }),
  );
  vi.stubGlobal("fetch", fn);
  return fn;
}

afterEach(() => vi.unstubAllGlobals());

describe("linkedin client", () => {
  it("reads the saved optimization (null when none)", async () => {
    mockFetch({ optimization: null });
    expect(await getOptimization()).toBeNull();
  });

  it("posts the language and unwraps the result", async () => {
    const fn = mockFetch({ optimization: { profile_version: 2 } });
    expect(await optimizeLinkedin("en")).toEqual({ profile_version: 2 });
    const [url, init] = fn.mock.calls[0];
    expect(url).toBe("http://localhost:8000/linkedin/optimization");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({ language: "en" });
  });

  it("uses a specific message for the hourly limit", async () => {
    mockFetch({}, 429);
    await expect(optimizeLinkedin("pt")).rejects.toThrow("5 otimizações");
  });

  it("names rejected sections", () => {
    expect(rejectedLabel("about")).toBe("Sobre");
    expect(rejectedLabel("experience:abc")).toBe("Experiência");
    expect(rejectedLabel("x")).toBe("x");
  });
});
