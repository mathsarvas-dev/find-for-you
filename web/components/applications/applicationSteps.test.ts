import { describe, expect, it } from "vitest";
import type { ApplicationDetail } from "@/lib/applications";
import { applicationSteps } from "./applicationSteps";

function detail(over: Partial<ApplicationDetail> = {}): ApplicationDetail {
  return {
    id: 1, state: "ready", error: null, locale: "en", template: "us", referees: null,
    profile_version: 1, current_profile_version: 1, warnings: [],
    job: { id: 9, title: "Data Engineer", company_name: "Globex", url: "https://example.test", status: "open" },
    requirements: { must: [], nice: [], keywords: [] },
    coverage: null, resume: null, cover_letter: null, questions: [], tasks: [],
    can_generate: { allowed: true, reason: null }, applied_at: null,
    ...over,
  };
}

describe("applicationSteps", () => {
  it("marks the reading done once requirements exist, and the current view", () => {
    const steps = applicationSteps(detail(), "resume");
    expect(steps.map((s) => s.id)).toEqual(["read", "resume", "questions", "kit"]);
    expect(steps[0].state).toBe("done");
    expect(steps[1].state).toBe("current");
    expect(steps[3].state).toBe("upcoming");
  });

  it("is done for resume only with a valid document", () => {
    const valid = applicationSteps(detail({ resume: { version: 1, status: "valid", lines: [] } }), "read");
    expect(valid[1].state).toBe("done");
    const fix = applicationSteps(detail({ resume: { version: 1, status: "needs_fix", lines: [] } }), "read");
    expect(fix[1].state).toBe("upcoming");
  });

  it("is done for the kit only when applied", () => {
    expect(applicationSteps(detail({ state: "applied" }), "kit")[3].state).toBe("done");
    expect(applicationSteps(detail({ state: "ready" }), "kit")[3].state).toBe("current");
  });

  it("shows the reading as pending without a detail", () => {
    const steps = applicationSteps(null, "read");
    expect(steps[0].state).toBe("current");
    expect(steps[0].detail).toBe("Aguardando a leitura da vaga");
  });
});
