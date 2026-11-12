import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import type { CanonicalProfile } from "@/lib/profile";
import { ReviewStep } from "./ReviewStep";

function sample(): CanonicalProfile {
  return {
    basics: {
      name: "Mariana Costa",
      headline: "Analista de dados",
      email: "mariana.costa@email.com",
      phone: "(31) 99812-4410",
      location: "Belo Horizonte, MG",
      links: ["https://linkedin.com/in/marianacosta"],
    },
    summary: "Seis anos com análise de dados em logística.",
    experiences: [
      {
        id: "e1",
        company: "Transportadora Serra Verde",
        title: "Analista de dados pleno",
        start: "2021-03",
        end: "",
        location: "Contagem, MG",
        description: "Painéis de operação e previsão de demanda.",
        achievements: [
          { id: "a1", text: "Reduzi o custo de frete em 12% com um modelo de rotas." },
          { id: "a2", text: "Automatizei o fechamento mensal em Python." },
        ],
      },
    ],
    education: [
      { id: "ed1", institution: "UFMG", degree: "Bacharelado", field: "Estatística", start: "2012", end: "2016" },
    ],
    skills: [
      { id: "s1", name: "SQL", level: "avançado" },
      { id: "s2", name: "Python", level: null },
    ],
    languages: [{ id: "l1", name: "Inglês", level: "intermediário" }],
    certifications: [{ id: "c1", name: "AWS Cloud Practitioner", issuer: "AWS", date: "2023" }],
    projects: [],
  };
}

function confirm() {
  fireEvent.click(screen.getByRole("button", { name: "Confirmar perfil" }));
}

describe("ReviewStep", () => {
  it("onConfirm recebe o valor editado e a narrativa", async () => {
    const onConfirm = vi.fn().mockResolvedValue(undefined);
    render(<ReviewStep profile={sample()} narrative="Quero vagas remotas." onConfirm={onConfirm} />);
    fireEvent.change(screen.getByLabelText("Nome completo"), { target: { value: "Mariana A. Costa" } });
    fireEvent.change(screen.getByLabelText(/Sobre você/), { target: { value: "Quero vagas híbridas." } });
    confirm();
    expect(onConfirm).toHaveBeenCalledTimes(1);
    const [profile, narrative] = onConfirm.mock.calls[0];
    expect(profile.basics.name).toBe("Mariana A. Costa");
    expect(narrative).toBe("Quero vagas híbridas.");
    // nada da interface vaza para o payload; itens não editáveis são preservados
    expect(profile.experiences[0]).toEqual(sample().experiences[0]);
    expect(profile.certifications).toEqual(sample().certifications);
  });

  it("notes passam intactas pela revisão", () => {
    const onConfirm = vi.fn().mockResolvedValue(undefined);
    const withNotes = { ...sample(), notes: [{ id: "n1", topic: "missing_skills", text: "Uso Docker" }] };
    render(<ReviewStep profile={withNotes} narrative="" onConfirm={onConfirm} />);
    confirm();
    expect(onConfirm.mock.calls[0][0].notes).toEqual(withNotes.notes);
  });

  it("Confirmar desabilita após o primeiro clique (duplo clique envia uma vez)", () => {
    const onConfirm = vi.fn(() => new Promise<void>(() => {}));
    render(<ReviewStep profile={sample()} narrative="" onConfirm={onConfirm} />);
    const button = screen.getByRole("button", { name: "Confirmar perfil" });
    fireEvent.click(button);
    fireEvent.click(button);
    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: /Salvando/ })).toBeDisabled();
  });

  it("se a confirmação falhar, mostra o erro e reabilita o botão", async () => {
    const onConfirm = vi.fn().mockRejectedValue(new Error("Banco de dados indisponível"));
    render(<ReviewStep profile={sample()} narrative="" onConfirm={onConfirm} />);
    confirm();
    expect(await screen.findByRole("alert")).toHaveTextContent("Banco de dados indisponível");
    expect(screen.getByRole("button", { name: "Confirmar perfil" })).toBeEnabled();
  });

  it("remover conquista funciona", () => {
    const onConfirm = vi.fn().mockResolvedValue(undefined);
    render(<ReviewStep profile={sample()} narrative="" onConfirm={onConfirm} />);
    fireEvent.click(screen.getByRole("button", { name: /Remover conquista 1/ }));
    confirm();
    const [profile] = onConfirm.mock.calls[0];
    expect(profile.experiences[0].achievements).toEqual([
      { id: "a2", text: "Automatizei o fechamento mensal em Python." },
    ]);
  });

  it("conquista nova entra com id vazio", () => {
    const onConfirm = vi.fn().mockResolvedValue(undefined);
    render(<ReviewStep profile={sample()} narrative="" onConfirm={onConfirm} />);
    fireEvent.click(screen.getByRole("button", { name: /Adicionar conquista/ }));
    const fields = screen.getAllByLabelText(/^Conquista \d/);
    expect(fields).toHaveLength(3);
    fireEvent.change(fields[2], { target: { value: "Treinei 4 analistas júnior." } });
    confirm();
    const [profile] = onConfirm.mock.calls[0];
    expect(profile.experiences[0].achievements[2]).toEqual({ id: "", text: "Treinei 4 analistas júnior." });
  });

  it("adiciona e remove experiência; nova experiência tem id vazio", () => {
    const onConfirm = vi.fn().mockResolvedValue(undefined);
    render(<ReviewStep profile={sample()} narrative="" onConfirm={onConfirm} />);
    fireEvent.click(screen.getByRole("button", { name: "Adicionar experiência" }));
    const titles = screen.getAllByLabelText("Cargo");
    fireEvent.change(titles[1], { target: { value: "Estagiária de BI" } });
    fireEvent.click(screen.getByRole("button", { name: /Remover experiência: Analista de dados pleno/ }));
    confirm();
    const [profile] = onConfirm.mock.calls[0];
    expect(profile.experiences).toHaveLength(1);
    expect(profile.experiences[0]).toMatchObject({ id: "", title: "Estagiária de BI", achievements: [] });
  });

  it("adiciona e remove habilidades", () => {
    const onConfirm = vi.fn().mockResolvedValue(undefined);
    render(<ReviewStep profile={sample()} narrative="" onConfirm={onConfirm} />);
    const skills = screen.getByRole("group", { name: "Habilidades" });
    fireEvent.click(within(skills).getByRole("button", { name: "Remover habilidade: SQL" }));
    fireEvent.click(within(skills).getByRole("button", { name: "Adicionar habilidade" }));
    const names = within(skills).getAllByLabelText("Habilidade");
    fireEvent.change(names[1], { target: { value: "Power BI" } });
    confirm();
    const [profile] = onConfirm.mock.calls[0];
    expect(profile.skills).toEqual([
      { id: "s2", name: "Python", level: null },
      { id: "", name: "Power BI", level: null },
    ]);
  });

  it("links são editados um por linha", () => {
    const onConfirm = vi.fn().mockResolvedValue(undefined);
    render(<ReviewStep profile={sample()} narrative="" onConfirm={onConfirm} />);
    fireEvent.change(screen.getByLabelText(/Links/), {
      target: { value: "https://linkedin.com/in/marianacosta\n\n https://github.com/mcosta \n" },
    });
    confirm();
    expect(onConfirm.mock.calls[0][0].basics.links).toEqual([
      "https://linkedin.com/in/marianacosta",
      "https://github.com/mcosta",
    ]);
  });

  it("perfil vazio mostra como adicionar itens", () => {
    render(
      <ReviewStep
        profile={{ ...sample(), experiences: [], skills: [] }}
        narrative=""
        onConfirm={vi.fn()}
      />,
    );
    expect(screen.getByText(/Nenhuma experiência lida do currículo/)).toBeInTheDocument();
    expect(screen.getByText(/Nenhuma habilidade lida do currículo/)).toBeInTheDocument();
  });
});
