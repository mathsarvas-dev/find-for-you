import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { UserMenu, initials } from "./UserMenu";

function button() {
  return screen.getByRole("button", { name: "Menu do perfil" });
}

function open() {
  fireEvent.click(button());
  return screen.getByRole("menu");
}

describe("initials", () => {
  it.each([
    ["Ana Souza", "AS"],
    ["  maria  da silva ", "MS"],
    ["Élio", "É"],
    ["<img onerror>", "IO"],
    ["", "V"],
    [null, "V"],
    ["!!! ???", "V"],
  ])("%j → %s", (name, expected) => {
    expect(initials(name)).toBe(expected);
  });

  it("nunca quebra com valor que não é texto", () => {
    expect(initials(42 as unknown as string)).toBe("V");
    expect(initials({} as unknown as string)).toBe("V");
  });
});

describe("UserMenu", () => {
  it("sem perfil mostra Você e Perfil não confirmado", () => {
    render(<UserMenu name={null} email={null} confirmed={false} />);
    expect(button()).toHaveTextContent("V");
    expect(button()).toHaveAttribute("aria-expanded", "false");
    open();
    expect(screen.getByText("Você")).toBeInTheDocument();
    expect(screen.getByText("Perfil não confirmado")).toBeInTheDocument();
  });

  it("nome e e-mail fazem parte da semântica do menu", () => {
    render(<UserMenu name="Ana Souza" email="ana@example.com" confirmed />);
    const menu = open();
    expect(menu).toHaveAccessibleName("Ana Souza");
    expect(menu).toHaveAccessibleDescription("ana@example.com");
  });

  it("valores que não são texto não derrubam o menu", () => {
    render(<UserMenu name={42 as unknown as string} email={[] as unknown as string} confirmed />);
    expect(button()).toHaveTextContent("V");
    open();
    expect(screen.getByText("Você")).toBeInTheDocument();
  });

  it("Escape com o foco de volta no botão também fecha", () => {
    render(<UserMenu name={null} email={null} confirmed={false} />);
    open();
    button().focus();
    fireEvent.keyDown(button(), { key: "Escape" });
    expect(screen.queryByRole("menu")).toBeNull();
    expect(button()).toHaveFocus();
  });

  it("perfil confirmado mostra nome e e-mail e as iniciais", () => {
    render(<UserMenu name="Ana Souza" email="ana@example.com" confirmed />);
    expect(button()).toHaveTextContent("AS");
    open();
    expect(screen.getByText("Ana Souza")).toBeInTheDocument();
    expect(screen.getByText("ana@example.com")).toBeInTheDocument();
  });

  it("itens Meu perfil e Configurações", () => {
    render(<UserMenu name={null} email={null} confirmed={false} />);
    open();
    expect(screen.getByRole("menuitem", { name: "Meu perfil" })).toHaveAttribute("href", "/onboarding");
    expect(screen.getByRole("menuitem", { name: "Configurações" })).toHaveAttribute("href", "/settings");
  });

  it("abre com foco no primeiro item; setas navegam; Escape fecha e devolve o foco", () => {
    render(<UserMenu name={null} email={null} confirmed={false} />);
    const menu = open();
    expect(button()).toHaveAttribute("aria-expanded", "true");
    const [first, second] = screen.getAllByRole("menuitem");
    expect(first).toHaveFocus();
    fireEvent.keyDown(menu, { key: "ArrowDown" });
    expect(second).toHaveFocus();
    fireEvent.keyDown(menu, { key: "ArrowDown" });
    expect(first).toHaveFocus();
    fireEvent.keyDown(menu, { key: "ArrowUp" });
    expect(second).toHaveFocus();
    fireEvent.keyDown(menu, { key: "Home" });
    expect(first).toHaveFocus();
    fireEvent.keyDown(menu, { key: "End" });
    expect(second).toHaveFocus();
    fireEvent.keyDown(menu, { key: "Escape" });
    expect(screen.queryByRole("menu")).toBeNull();
    expect(button()).toHaveFocus();
    expect(button()).toHaveAttribute("aria-expanded", "false");
  });

  it("seta para baixo no botão abre o menu", () => {
    render(<UserMenu name={null} email={null} confirmed={false} />);
    fireEvent.keyDown(button(), { key: "ArrowDown" });
    expect(screen.getByRole("menu")).toBeInTheDocument();
    expect(screen.getAllByRole("menuitem")[0]).toHaveFocus();
  });

  it("os itens são alcançáveis por Tab (sem tabindex negativo)", () => {
    render(<UserMenu name={null} email={null} confirmed={false} />);
    open();
    for (const item of screen.getAllByRole("menuitem")) {
      expect(item).not.toHaveAttribute("tabindex", "-1");
    }
  });

  it("fecha quando o foco sai do menu por Tab", () => {
    render(
      <>
        <UserMenu name={null} email={null} confirmed={false} />
        <button type="button">depois</button>
      </>,
    );
    const menu = open();
    const outside = screen.getByRole("button", { name: "depois" });
    fireEvent.focusOut(screen.getAllByRole("menuitem")[1], { relatedTarget: outside });
    expect(menu).not.toBeInTheDocument();
  });

  it("clique fora fecha e devolve o foco ao botão", () => {
    render(
      <>
        <UserMenu name={null} email={null} confirmed={false} />
        <p>fora</p>
      </>,
    );
    open();
    fireEvent.click(screen.getByText("fora"));
    expect(screen.queryByRole("menu")).toBeNull();
    expect(button()).toHaveFocus();
  });

  it("clique no próprio botão alterna", () => {
    render(<UserMenu name={null} email={null} confirmed={false} />);
    open();
    fireEvent.click(button());
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("nome hostil vira texto, truncado por CSS", () => {
    const hostile = "<img src=x onerror=alert(1)>";
    const { container } = render(<UserMenu name={hostile} email={"x".repeat(5000)} confirmed />);
    open();
    expect(container.querySelector("img")).toBeNull();
    const name = screen.getByText(hostile);
    expect(name).toHaveClass("user-menu__name");
    expect(screen.getByText("x".repeat(5000))).toHaveClass("user-menu__email");
  });

  it("nome de 5000 caracteres fica em elemento truncável", () => {
    const long = "A".repeat(5000);
    render(<UserMenu name={long} email="" confirmed />);
    open();
    expect(screen.getByText(long)).toHaveClass("user-menu__name");
    expect(button()).toHaveTextContent(/^A$/);
  });
});
