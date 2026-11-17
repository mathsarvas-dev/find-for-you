import { afterEach, describe, expect, it, vi } from "vitest";
import {
  activateSearchProfile,
  createSearchProfile,
  deleteSearchProfile,
  listSearchProfiles,
  renameSearchProfile,
  validProfileName,
} from "./searchProfiles";

function mockFetch(body: unknown, status = 200) {
  const fn = vi.fn<typeof fetch>(
    async () =>
      status === 204
        ? new Response(null, { status })
        : new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }),
  );
  vi.stubGlobal("fetch", fn);
  return fn;
}

afterEach(() => vi.unstubAllGlobals());

describe("search profiles client", () => {
  it("lists the items", async () => {
    mockFetch({ items: [{ id: 1, name: "default" }] });
    expect(await listSearchProfiles()).toEqual([{ id: 1, name: "default" }]);
  });

  it("creates, activates, renames and deletes with the right verbs", async () => {
    const fn = mockFetch({ id: 3 });
    await createSearchProfile("Remoto Europa", true);
    await activateSearchProfile(3);
    await renameSearchProfile(3, "Novo");
    expect(fn.mock.calls.map((c) => [String(c[0]), c[1]?.method])).toEqual([
      ["http://localhost:8000/search-profiles", "POST"],
      ["http://localhost:8000/search-profiles/3/activate", "POST"],
      ["http://localhost:8000/search-profiles/3", "PATCH"],
    ]);
    expect(JSON.parse(String(fn.mock.calls[0][1]?.body))).toEqual({ name: "Remoto Europa", activate: true });
    const del = mockFetch(null, 204);
    await deleteSearchProfile(3);
    expect(del.mock.calls[0][1]?.method).toBe("DELETE");
  });

  it("validates names like the API", () => {
    expect(validProfileName("  Brasil CLT ")).toBe(true);
    expect(validProfileName("   ")).toBe(false);
    expect(validProfileName("x".repeat(61))).toBe(false);
    expect(validProfileName("a\nb")).toBe(false);
  });
});
