import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MatchList } from "./MatchList";
import { matchItem } from "./fixtures";

describe("MatchList", () => {
  it("lista ordenada, na ordem recebida, com a selecionada marcada", () => {
    render(
      <MatchList
        items={[matchItem(1, { ai_score: 90, state: "ok" }), matchItem(2), matchItem(3)]}
        selectedId={2}
        onSelect={vi.fn()}
      />,
    );
    const list = screen.getByRole("list", { name: "Vagas ordenadas por nota" });
    const buttons = screen.getAllByRole("button");
    expect(list.tagName).toBe("OL");
    expect(buttons).toHaveLength(3);
    expect(buttons[0]).toHaveAccessibleName(/Vaga 1/);
    expect(buttons[1]).toHaveAttribute("aria-current", "true");
    expect(buttons[0]).not.toHaveAttribute("aria-current");
  });
});
