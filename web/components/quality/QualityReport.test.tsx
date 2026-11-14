import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QualityReport } from "./QualityReport";
import { qualityReport } from "./fixtures";

afterEach(() => vi.restoreAllMocks());

describe("QualityReport", () => {
  it("insuficiente lista o que falta", () => {
    render(
      <QualityReport
        report={qualityReport({
          status: "insuficiente",
          precision_at_10: null,
          precision_vec_at_10: null,
          missing: ["faltam 18 rótulos"],
        })}
        onNewSample={vi.fn()}
      />,
    );
    expect(screen.getByText("faltam 18 rótulos")).toBeInTheDocument();
    expect(screen.queryByText("dentro da meta")).toBeNull();
  });

  it("dentro da meta mostra as duas precisões com vírgula e a amostra", () => {
    render(<QualityReport report={qualityReport()} onNewSample={vi.fn()} />);
    expect(screen.getByText("dentro da meta")).toBeInTheDocument();
    expect(screen.getByText("0,60")).toBeInTheDocument();
    expect(screen.getByText("0,50")).toBeInTheDocument();
    expect(screen.getByText(/40 rotuladas/)).toBeInTheDocument();
  });

  it("abaixo da meta e mal classificadas", () => {
    render(
      <QualityReport
        report={qualityReport({
          status: "abaixo_da_meta",
          precision_at_10: 0.4,
          misranked: [
            { job_id: 1, title: "Vaga A", label: "good", position: 14 },
            { job_id: 2, title: "<b>Vaga B</b>", label: "bad", position: 3 },
          ],
        })}
        onNewSample={vi.fn()}
      />,
    );
    expect(screen.getByText("abaixo da meta (meta 0,6)")).toBeInTheDocument();
    expect(screen.getByText("0,40")).toBeInTheDocument();
    expect(screen.getByText(/Vaga A/)).toBeInTheDocument();
    expect(screen.getByText("#14")).toBeInTheDocument();
    expect(screen.getByText("(boa fora das 10 primeiras)")).toBeInTheDocument();
    expect(screen.getByText(/<b>Vaga B<\/b>/)).toBeInTheDocument();
    expect(screen.getByText("(ruim entre as 10 primeiras)")).toBeInTheDocument();
  });

  it("Nova amostra confirma antes de chamar", async () => {
    const onNew = vi.fn().mockResolvedValue(undefined);
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<QualityReport report={qualityReport()} onNewSample={onNew} />);
    fireEvent.click(screen.getByRole("button", { name: "Nova amostra" }));
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining("rótulos"));
    expect(onNew).not.toHaveBeenCalled();
    confirm.mockReturnValue(true);
    fireEvent.click(screen.getByRole("button", { name: "Nova amostra" }));
    await waitFor(() => expect(onNew).toHaveBeenCalledTimes(1));
  });
});
