"use client";

import { useId, useState } from "react";
import { filterChips, parseFilters, type ParsedQuery } from "@/lib/chatFilters";
import { ApiError } from "@/lib/profile";
import { ChipView, QUERY_MAX } from "./FilterChips";

type Props = {
  /** Grava a proposta (fusão + PUT na página). Rejeitar mantém a proposta na tela. */
  onApply: (p: ParsedQuery) => Promise<void>;
  disabled?: boolean;
};

type State =
  | { name: "idle" }
  | { name: "parsing" }
  | { name: "error"; message: string; text: string }
  | { name: "proposal"; proposal: ParsedQuery; applying: boolean };

const message = (err: unknown) =>
  err instanceof ApiError ? err.message : "Algo deu errado ao interpretar a frase. Tente de novo.";

/** Frase → proposta de filtros (nada é salvo antes de "Aplicar"). */
export function ChatBar({ onApply, disabled = false }: Props) {
  const [text, setText] = useState("");
  const [state, setState] = useState<State>({ name: "idle" });
  const id = useId();
  const parsing = state.name === "parsing";

  async function interpret(phrase: string) {
    const clean = phrase.trim();
    if (!clean || parsing) return;
    setState({ name: "parsing" });
    try {
      const proposal = await parseFilters(clean);
      setState({ name: "proposal", proposal, applying: false });
    } catch (err) {
      setState({ name: "error", message: message(err), text: clean });
    }
  }

  async function apply(proposal: ParsedQuery) {
    setState({ name: "proposal", proposal, applying: true });
    try {
      await onApply(proposal);
      setState({ name: "idle" });
      setText("");
    } catch {
      // A página já mostra o erro do salvamento; a proposta fica para tentar de novo.
      setState({ name: "proposal", proposal, applying: false });
    }
  }

  const chips = state.name === "proposal" ? filterChips(state.proposal.filters, state.proposal.query_text) : [];

  return (
    <section className="panel chat" aria-labelledby={`${id}-label`}>
      <form
        className="chat__form"
        onSubmit={(e) => {
          e.preventDefault();
          void interpret(text);
        }}
      >
        <label id={`${id}-label`} className="field__label" htmlFor={`${id}-text`}>
          Descreva o que procura
        </label>
        <div className="chat__row">
          <input
            id={`${id}-text`}
            className="input"
            type="text"
            autoComplete="off"
            maxLength={QUERY_MAX}
            placeholder="Descreva o que procura…"
            value={text}
            disabled={disabled}
            onChange={(e) => setText(e.target.value)}
          />
          <button
            type="submit"
            className="button"
            disabled={disabled || parsing || text.trim() === ""}
            aria-busy={parsing || undefined}
          >
            Aplicar
          </button>
        </div>
        <p className="field__hint">
          Ex.: “remoto na Europa, sem plantão, sênior, algo com dados”. Nada muda até você aplicar a proposta.
        </p>
      </form>

      {state.name === "error" && (
        <div className="notice chat__notice" role="alert">
          <strong>Não deu para interpretar a frase.</strong>
          <p>{state.message}</p>
          <div className="actions">
            <button
              type="button"
              className="button button--secondary"
              onClick={() => void interpret(state.text)}
            >
              Tentar de novo
            </button>
          </div>
        </div>
      )}

      {state.name === "proposal" && (
        <section className="chat__proposal" aria-label="Proposta de filtros">
          <p className="chat__proposal-title">Proposta</p>
          {chips.length > 0 && (
            <ul className="filter-chips__list" aria-label="Filtros propostos">
              {chips.map((chip) => (
                <ChipView key={chip.key} chip={chip} />
              ))}
            </ul>
          )}
          {state.proposal.notes.length > 0 && (
            <ul className="chat__notes">
              {state.proposal.notes.map((note, i) => (
                <li key={i}>{note}</li>
              ))}
            </ul>
          )}
          <div className="chat__actions">
            <button
              type="button"
              className="button"
              disabled={chips.length === 0 || state.applying}
              aria-busy={state.applying || undefined}
              onClick={() => void apply(state.proposal)}
            >
              Aplicar
            </button>
            <button
              type="button"
              className="button button--secondary"
              disabled={state.applying}
              onClick={() => setState({ name: "idle" })}
            >
              Descartar
            </button>
          </div>
        </section>
      )}
    </section>
  );
}
