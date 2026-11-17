import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { getHighlights, type Highlights as HighlightsData } from "@/lib/pipeline";
import { Highlights, followUpsText, newJobsText } from "./Highlights";

vi.mock("@/lib/pipeline", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/pipeline")>()),
  getHighlights: vi.fn(),
}));

const get = vi.mocked(getHighlights);

const data: HighlightsData = {
  min_score: 80, days: 7, new_high_count: 3, follow_ups_due: 2,
  items: [
    { job_id: 1, job_title: "Data Engineer", company_name: "Acme", ai_score: 92 },
    { job_id: 2, job_title: "ML Engineer", company_name: "Globex", ai_score: 85 },
  ],
};

beforeEach(() => get.mockReset());

describe("Highlights", () => {
  it("shows new high scores and follow-ups due, in text with links", async () => {
    get.mockResolvedValue(data);
    render(<Highlights />);
    expect(await screen.findByText(/3 vagas novas com nota 80 ou mais nos últimos 7 dias/)).toBeInTheDocument();
    expect(screen.getByText("Data Engineer · Acme · Nota 92")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Ver vagas" })).toHaveAttribute("href", "/jobs");
    expect(screen.getByText("2 follow-ups para hoje ou atrasados.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Abrir pipeline" })).toHaveAttribute("href", "/pipeline");
    expect(screen.getByText(/nada é enviado para fora/)).toBeInTheDocument();
  });

  it("shows only the part that has something", async () => {
    get.mockResolvedValue({ ...data, new_high_count: 0, items: [] });
    render(<Highlights />);
    expect(await screen.findByText("2 follow-ups para hoje ou atrasados.")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Ver vagas" })).toBeNull();
  });

  it("renders nothing without highlights or when the request fails", async () => {
    get.mockResolvedValueOnce({ ...data, new_high_count: 0, follow_ups_due: 0, items: [] });
    const { container, unmount } = render(<Highlights />);
    await waitFor(() => expect(get).toHaveBeenCalledTimes(1));
    expect(container).toBeEmptyDOMElement();
    unmount();
    get.mockRejectedValueOnce(new Error("sem API"));
    const second = render(<Highlights />);
    await waitFor(() => expect(get).toHaveBeenCalledTimes(2));
    expect(second.container).toBeEmptyDOMElement();
  });

  it("uses the singular", () => {
    expect(newJobsText({ ...data, new_high_count: 1 })).toBe("1 vaga nova com nota 80 ou mais");
    expect(followUpsText(1)).toBe("1 follow-up para hoje ou atrasado");
  });
});
