import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { ApplicationDetail } from "@/lib/applications";
import { RequirementsStep } from "./RequirementsStep";

const HOSTILE = '<img src=x onerror="alert(1)"> Airflow';

function detail(over: Partial<ApplicationDetail> = {}): ApplicationDetail {
  return {
    id: 1, state: "ready", error: null, locale: "en", template: "us", referees: null,
    profile_version: 1, current_profile_version: 1, warnings: [],
    job: { id: 9, title: "Data Engineer", company_name: "Globex", url: "https://example.test", status: "open" },
    requirements: {
      must: [{ id: "m1", text: HOSTILE }, { id: "m2", text: "Python" }],
      nice: [{ id: "n1", text: "Spark" }],
      keywords: ["dados"],
    },
    coverage: {
      must: { covered: 1, total: 2 },
      nice: { covered: 0, total: 1 },
      items: [],
    },
    resume: null, cover_letter: null, questions: [], tasks: [],
    can_generate: { allowed: true, reason: null }, applied_at: null,
    ...over,
  };
}

describe("RequirementsStep", () => {
  it("shows the coverage as 'n de N' for must and nice", () => {
    render(<RequirementsStep detail={detail()} />);
    expect(screen.getByText(/1 de 2 obrigatórios/)).toBeInTheDocument();
    expect(screen.getByText(/0 de 1 desejáveis/)).toBeInTheDocument();
  });

  it("renders hostile posting text as plain text, never as markup", () => {
    const { container } = render(<RequirementsStep detail={detail()} />);
    expect(screen.getByText(HOSTILE)).toBeInTheDocument();
    expect(container.querySelector("img")).toBeNull();
  });

  it("says the requirements come later when there are none", () => {
    render(<RequirementsStep detail={detail({ requirements: null, coverage: null })} />);
    expect(screen.getByText(/aparecem quando o currículo é gerado/)).toBeInTheDocument();
  });
});
