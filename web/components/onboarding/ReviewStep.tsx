"use client";

import { useId, useRef, useState, type ReactNode } from "react";
import type {
  Achievement,
  Basics,
  CanonicalProfile,
  Education,
  Experience,
  Language,
  Skill,
} from "@/lib/profile";

/* Cada item ganha uma chave só da interface (_k) para o React; ela sai antes de enviar.
 * Itens novos vão com id "" e o backend preenche o id. */
type Keyed<T> = T & { _k: string };
type ExpDraft = Keyed<Omit<Experience, "achievements">> & { achievements: Keyed<Achievement>[] };

type Draft = {
  basics: Omit<Basics, "links">;
  links: string;
  summary: string;
  experiences: ExpDraft[];
  education: Keyed<Education>[];
  skills: Keyed<Skill>[];
  languages: Keyed<Language>[];
  narrative: string;
};

let seq = 0;
const newKey = () => `k${++seq}`;
const keyed = <T,>(item: T): Keyed<T> => ({ ...item, _k: newKey() });
function unkeyed<T>(item: Keyed<T>): T {
  const copy: Partial<Keyed<T>> = { ...item };
  delete copy._k;
  return copy as T;
}

function toDraft(profile: CanonicalProfile, narrative: string): Draft {
  const { links, ...basics } = profile.basics;
  return {
    basics,
    links: links.join("\n"),
    summary: profile.summary,
    experiences: profile.experiences.map((e) => ({
      ...keyed(e),
      achievements: e.achievements.map(keyed),
    })),
    education: profile.education.map(keyed),
    skills: profile.skills.map(keyed),
    languages: profile.languages.map(keyed),
    narrative,
  };
}

function toProfile(draft: Draft, original: CanonicalProfile): CanonicalProfile {
  return {
    basics: {
      ...draft.basics,
      links: draft.links
        .split("\n")
        .map((l) => l.trim())
        .filter(Boolean),
    },
    summary: draft.summary,
    experiences: draft.experiences.map((e) => ({
      ...unkeyed(e),
      achievements: e.achievements.map(unkeyed),
    })),
    education: draft.education.map(unkeyed),
    skills: draft.skills.map(unkeyed),
    languages: draft.languages.map(unkeyed),
    // Ainda sem edição na revisão: seguem como vieram.
    certifications: original.certifications,
    projects: original.projects,
  };
}

const updateAt = <T,>(list: T[], i: number, patch: Partial<T>): T[] =>
  list.map((item, j) => (j === i ? { ...item, ...patch } : item));
const removeAt = <T,>(list: T[], i: number): T[] => list.filter((_, j) => j !== i);

const blankExperience = (): ExpDraft => ({
  _k: newKey(),
  id: "",
  company: "",
  title: "",
  start: "",
  end: "",
  location: "",
  description: "",
  achievements: [],
});

type ReviewStepProps = {
  profile: CanonicalProfile;
  narrative: string;
  onConfirm: (profile: CanonicalProfile, narrative: string) => Promise<void> | void;
  onBack?: () => void;
};

/** Formulário de revisão do perfil extraído. Nada é salvo até "Confirmar perfil". */
export function ReviewStep({ profile, narrative, onConfirm, onBack }: ReviewStepProps) {
  const [draft, setDraft] = useState(() => toDraft(profile, narrative));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const busy = useRef(false);

  const set = (patch: Partial<Draft>) => setDraft((d) => ({ ...d, ...patch }));
  const setBasics = (patch: Partial<Draft["basics"]>) =>
    setDraft((d) => ({ ...d, basics: { ...d.basics, ...patch } }));
  const setExp = (i: number, patch: Partial<ExpDraft>) =>
    setDraft((d) => ({ ...d, experiences: updateAt(d.experiences, i, patch) }));

  async function confirm() {
    if (busy.current) return;
    busy.current = true;
    setSaving(true);
    setError(null);
    try {
      await onConfirm(toProfile(draft, profile), draft.narrative);
    } catch (err) {
      setError(err instanceof Error && err.message ? err.message : "Não foi possível salvar. Tente de novo.");
      busy.current = false;
      setSaving(false);
    }
  }

  const { basics } = draft;

  return (
    <form className="review" noValidate onSubmit={(e) => e.preventDefault()}>
      <Section title="Dados básicos">
        <div className="grid">
          <Field label="Nome completo" value={basics.name} onChange={(v) => setBasics({ name: v })} />
          <Field label="Título profissional" value={basics.headline} onChange={(v) => setBasics({ headline: v })} />
          <Field label="E-mail" type="email" value={basics.email} onChange={(v) => setBasics({ email: v })} />
          <Field label="Telefone" type="tel" value={basics.phone} onChange={(v) => setBasics({ phone: v })} />
          <Field label="Localização" value={basics.location} onChange={(v) => setBasics({ location: v })} />
          <Field
            label="Links (um por linha)"
            multiline
            rows={2}
            value={draft.links}
            onChange={(v) => set({ links: v })}
          />
        </div>
        <Field
          label="Resumo profissional"
          multiline
          rows={3}
          value={draft.summary}
          onChange={(v) => set({ summary: v })}
        />
      </Section>

      <Section title="Experiências" count={draft.experiences.length}>
        {draft.experiences.length === 0 && (
          <p className="empty">Nenhuma experiência lida do currículo. Adicione as que faltam.</p>
        )}
        {draft.experiences.map((exp, i) => {
          const label = exp.title || exp.company || `Experiência ${i + 1}`;
          return (
            <fieldset className="item" key={exp._k}>
              <legend className="item__title">
                {exp.title || exp.company ? (
                  <>
                    {exp.title || "Sem cargo"}
                    {exp.company && <span className="item__sub"> · {exp.company}</span>}
                  </>
                ) : (
                  `Experiência ${i + 1}`
                )}
              </legend>
              <div className="grid">
                <Field label="Cargo" value={exp.title} onChange={(v) => setExp(i, { title: v })} />
                <Field label="Empresa" value={exp.company} onChange={(v) => setExp(i, { company: v })} />
                <Field label="Início" placeholder="2021-03" value={exp.start} onChange={(v) => setExp(i, { start: v })} />
                <Field label="Fim" placeholder="atual" value={exp.end} onChange={(v) => setExp(i, { end: v })} />
                <Field label="Local" value={exp.location} onChange={(v) => setExp(i, { location: v })} />
              </div>
              <Field
                label="Descrição"
                multiline
                rows={2}
                value={exp.description}
                onChange={(v) => setExp(i, { description: v })}
              />
              <div className="achievements">
                <p className="achievements__title">Conquistas</p>
                {exp.achievements.length === 0 && (
                  <p className="empty">Sem conquistas. Resultados com números ajudam na hora de comparar com vagas.</p>
                )}
                <ol className="achievements__list">
                  {exp.achievements.map((ach, j) => (
                    <li className="row" key={ach._k}>
                      <Field
                        label={`Conquista ${j + 1}`}
                        hideLabel
                        multiline
                        rows={1}
                        value={ach.text}
                        onChange={(v) => setExp(i, { achievements: updateAt(exp.achievements, j, { text: v }) })}
                      />
                      <button
                        type="button"
                        className="link-button link-button--danger"
                        aria-label={`Remover conquista ${j + 1} de ${label}`}
                        onClick={() => setExp(i, { achievements: removeAt(exp.achievements, j) })}
                      >
                        Remover
                      </button>
                    </li>
                  ))}
                </ol>
                <button
                  type="button"
                  className="link-button"
                  aria-label={`Adicionar conquista em ${label}`}
                  onClick={() =>
                    setExp(i, { achievements: [...exp.achievements, { _k: newKey(), id: "", text: "" }] })
                  }
                >
                  + Adicionar conquista
                </button>
              </div>
              <div className="item__foot">
                <button
                  type="button"
                  className="link-button link-button--danger"
                  aria-label={`Remover experiência: ${label}`}
                  onClick={() => set({ experiences: removeAt(draft.experiences, i) })}
                >
                  Remover experiência
                </button>
              </div>
            </fieldset>
          );
        })}
        <button
          type="button"
          className="button button--secondary"
          onClick={() => set({ experiences: [...draft.experiences, blankExperience()] })}
        >
          Adicionar experiência
        </button>
      </Section>

      <Section title="Formação" count={draft.education.length}>
        {draft.education.length === 0 && <p className="empty">Nenhuma formação lida do currículo.</p>}
        {draft.education.map((ed, i) => {
          const upd = (patch: Partial<Keyed<Education>>) => set({ education: updateAt(draft.education, i, patch) });
          return (
            <fieldset className="item" key={ed._k}>
              <legend className="item__title">{ed.institution || `Formação ${i + 1}`}</legend>
              <div className="grid">
                <Field label="Instituição" value={ed.institution} onChange={(v) => upd({ institution: v })} />
                <Field label="Grau" placeholder="Bacharelado" value={ed.degree} onChange={(v) => upd({ degree: v })} />
                <Field label="Área" value={ed.field} onChange={(v) => upd({ field: v })} />
                <Field label="Início" value={ed.start} onChange={(v) => upd({ start: v })} />
                <Field label="Fim" value={ed.end} onChange={(v) => upd({ end: v })} />
              </div>
              <div className="item__foot">
                <button
                  type="button"
                  className="link-button link-button--danger"
                  aria-label={`Remover formação: ${ed.institution || `Formação ${i + 1}`}`}
                  onClick={() => set({ education: removeAt(draft.education, i) })}
                >
                  Remover formação
                </button>
              </div>
            </fieldset>
          );
        })}
        <button
          type="button"
          className="button button--secondary"
          onClick={() =>
            set({
              education: [
                ...draft.education,
                { _k: newKey(), id: "", institution: "", degree: "", field: "", start: "", end: "" },
              ],
            })
          }
        >
          Adicionar formação
        </button>
      </Section>

      <Section title="Habilidades" count={draft.skills.length}>
        {draft.skills.length === 0 && (
          <p className="empty">Nenhuma habilidade lida do currículo. Adicione as que você usa no trabalho.</p>
        )}
        <PairList
          items={draft.skills}
          nameLabel="Habilidade"
          levelLabel="Nível da habilidade"
          levelPlaceholder="ex.: avançado"
          getLevel={(s) => s.level ?? ""}
          onName={(i, v) => set({ skills: updateAt(draft.skills, i, { name: v }) })}
          onLevel={(i, v) => set({ skills: updateAt(draft.skills, i, { level: v || null }) })}
          onRemove={(i) => set({ skills: removeAt(draft.skills, i) })}
          removeLabel={(s) => `Remover habilidade: ${s.name || "sem nome"}`}
        />
        <button
          type="button"
          className="button button--secondary"
          onClick={() => set({ skills: [...draft.skills, { _k: newKey(), id: "", name: "", level: null }] })}
        >
          Adicionar habilidade
        </button>
      </Section>

      <Section title="Idiomas" count={draft.languages.length}>
        {draft.languages.length === 0 && <p className="empty">Nenhum idioma lido do currículo.</p>}
        <PairList
          items={draft.languages}
          nameLabel="Idioma"
          levelLabel="Nível do idioma"
          levelPlaceholder="ex.: fluente"
          getLevel={(l) => l.level}
          onName={(i, v) => set({ languages: updateAt(draft.languages, i, { name: v }) })}
          onLevel={(i, v) => set({ languages: updateAt(draft.languages, i, { level: v }) })}
          onRemove={(i) => set({ languages: removeAt(draft.languages, i) })}
          removeLabel={(l) => `Remover idioma: ${l.name || "sem nome"}`}
        />
        <button
          type="button"
          className="button button--secondary"
          onClick={() => set({ languages: [...draft.languages, { _k: newKey(), id: "", name: "", level: "" }] })}
        >
          Adicionar idioma
        </button>
      </Section>

      <Section title="Sobre você">
        <Field
          label="Sobre você (texto livre)"
          hint="O que você escreveu no passo 1. Entra no perfil como contexto para comparar vagas."
          multiline
          rows={4}
          value={draft.narrative}
          onChange={(v) => set({ narrative: v })}
        />
        {(profile.certifications.length > 0 || profile.projects.length > 0) && (
          <p className="field__hint">
            Certificações ({profile.certifications.length}) e projetos ({profile.projects.length}) lidos do
            currículo são mantidos como vieram.
          </p>
        )}
      </Section>

      <div className="review__actions">
        {error && (
          <p className="field__error" role="alert">
            {error}
          </p>
        )}
        <div className="actions">
          {onBack && (
            <button type="button" className="button button--secondary" onClick={onBack} disabled={saving}>
              Voltar
            </button>
          )}
          <button type="button" className="button" onClick={confirm} disabled={saving}>
            {saving ? "Salvando…" : "Confirmar perfil"}
          </button>
        </div>
      </div>
    </form>
  );
}

function Section({ title, count, children }: { title: string; count?: number; children: ReactNode }) {
  return (
    <fieldset className="panel form-section">
      <legend className="form-section__legend">
        {title}
        {count !== undefined && <span className="mono form-section__count" aria-hidden="true">{count}</span>}
      </legend>
      <div className="form-section__body">{children}</div>
    </fieldset>
  );
}

type FieldProps = {
  label: string;
  value: string;
  onChange: (value: string) => void;
  multiline?: boolean;
  rows?: number;
  hint?: string;
  placeholder?: string;
  type?: string;
  hideLabel?: boolean;
};

function Field({ label, value, onChange, multiline, rows = 3, hint, placeholder, type = "text", hideLabel }: FieldProps) {
  const id = useId();
  const hintId = useId();
  const common = {
    id,
    className: "input",
    value,
    placeholder,
    "aria-describedby": hint ? hintId : undefined,
  };
  return (
    <div className="field">
      <label className={hideLabel ? "sr-only" : "field__label"} htmlFor={id}>
        {label}
      </label>
      {hint && (
        <p className="field__hint" id={hintId}>
          {hint}
        </p>
      )}
      {multiline ? (
        <textarea {...common} rows={rows} onChange={(e) => onChange(e.target.value)} />
      ) : (
        <input {...common} type={type} onChange={(e) => onChange(e.target.value)} />
      )}
    </div>
  );
}

type PairListProps<T extends { _k: string; name: string }> = {
  items: T[];
  nameLabel: string;
  levelLabel: string;
  levelPlaceholder: string;
  getLevel: (item: T) => string;
  onName: (i: number, v: string) => void;
  onLevel: (i: number, v: string) => void;
  onRemove: (i: number) => void;
  removeLabel: (item: T) => string;
};

/** Lista compacta de nome + nível (habilidades, idiomas). */
function PairList<T extends { _k: string; name: string }>(props: PairListProps<T>) {
  const { items, nameLabel, levelLabel, levelPlaceholder, getLevel, onName, onLevel, onRemove, removeLabel } = props;
  if (items.length === 0) return null;
  return (
    <ul className="pairs">
      {items.map((item, i) => (
        <li className="pairs__row" key={item._k}>
          <input
            className="input"
            aria-label={nameLabel}
            value={item.name}
            onChange={(e) => onName(i, e.target.value)}
          />
          <input
            className="input"
            aria-label={levelLabel}
            placeholder={levelPlaceholder}
            value={getLevel(item)}
            onChange={(e) => onLevel(i, e.target.value)}
          />
          <button
            type="button"
            className="link-button link-button--danger"
            aria-label={removeLabel(item)}
            onClick={() => onRemove(i)}
          >
            Remover
          </button>
        </li>
      ))}
    </ul>
  );
}
