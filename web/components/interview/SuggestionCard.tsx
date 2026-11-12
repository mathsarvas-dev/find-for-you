"use client";

import { useEffect, useId, useRef, useState } from "react";
import { ApiError } from "@/lib/profile";
import {
  editableFields,
  OP_LABELS,
  type ResolveAction,
  type Suggestion,
  type SuggestionPayload,
} from "@/lib/interview";

type SuggestionCardProps = {
  suggestion: Suggestion;
  /** Rejeita com ApiError (409: item mudou; 422: o validador recusou o ajuste). */
  onResolve: (action: ResolveAction, payload?: SuggestionPayload) => Promise<void> | void;
};

const STATUS_LABEL = { accepted: "Aceita", adjusted: "Ajustada", rejected: "Descartada" } as const;

type Problem = { conflict: boolean; message: string };

/**
 * Sugestão de alteração do perfil, como diff: o que está hoje e o que entra.
 * Os rótulos dizem qual é qual (a cor só reforça).
 */
export function SuggestionCard({ suggestion, onResolve }: SuggestionCardProps) {
  const { op, payload, status, before, after } = suggestion;
  const titleId = useId();
  const fieldId = useId();
  const fields = editableFields(op);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<Problem | null>(null);
  // Trava síncrona: dois cliques no mesmo quadro não geram dois envios.
  const inFlight = useRef(false);
  const mounted = useRef(true);
  const formRef = useRef<HTMLFormElement>(null);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  useEffect(() => {
    if (editing) formRef.current?.querySelector<HTMLElement>("input, textarea")?.focus();
  }, [editing]);

  async function send(action: ResolveAction, edited?: SuggestionPayload) {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setProblem(null);
    try {
      await (edited === undefined ? onResolve(action) : onResolve(action, edited));
      // Sucesso: a página recarrega e o cartão passa a mostrar o status.
    } catch (err) {
      inFlight.current = false;
      if (!mounted.current) return;
      setBusy(false);
      const conflict = err instanceof ApiError && err.status === 409;
      // 409 no ajuste: sai da edição; o item mudou e só resta descartar.
      if (conflict) setEditing(false);
      setProblem({
        conflict,
        message:
          err instanceof ApiError ? err.message : "Não consegui salvar. Tente de novo.",
      });
    }
  }

  if (status !== "pending") {
    return (
      <article className="suggestion" data-status={status} aria-labelledby={titleId}>
        <div className="suggestion__head">
          <p id={titleId} className="suggestion__title">
            <span className="suggestion__kind">Sugestão</span> <span>{OP_LABELS[op]}</span>
          </p>
          <span className="suggestion__status">{STATUS_LABEL[status]}</span>
        </div>
        <p className="suggestion__resolved">{after}</p>
      </article>
    );
  }

  function startEditing() {
    const initial: Record<string, string> = {};
    for (const f of fields) initial[f.key] = (payload[f.key] as string | undefined) ?? "";
    setDraft(initial);
    setProblem(null);
    setEditing(true);
  }

  const draftValid = fields.every(
    (f) => f.key === "start" || f.key === "end" || (draft[f.key] ?? "").trim() !== "",
  );

  return (
    <article className="suggestion" data-status="pending" aria-labelledby={titleId}>
      <div className="suggestion__head">
        <p id={titleId} className="suggestion__title">
          <span className="suggestion__kind">Sugestão</span> <span>{OP_LABELS[op]}</span>
        </p>
      </div>

      <dl className="diff">
        <div className="diff__row" data-side="before">
          <dt>Hoje no perfil</dt>
          <dd>{before}</dd>
        </div>
        <div className="diff__row" data-side="after">
          <dt>Proposta</dt>
          <dd>{after}</dd>
        </div>
      </dl>

      {editing && (
        <form
          ref={formRef}
          className="suggestion__edit"
          onSubmit={(e) => {
            e.preventDefault();
            if (draftValid) void send("adjust", { ...payload, ...draft });
          }}
        >
          <div className={fields.length > 1 ? "grid" : undefined}>
            {fields.map((f) => {
              const id = `${fieldId}-${f.key}`;
              const common = {
                id,
                className: "input",
                value: draft[f.key] ?? "",
                disabled: busy,
                onChange: (e: { target: { value: string } }) =>
                  setDraft((d) => ({ ...d, [f.key]: e.target.value })),
              };
              return (
                <div className="field" key={f.key}>
                  <label className="field__label" htmlFor={id}>
                    {f.label}
                  </label>
                  {f.multiline ? <textarea rows={3} {...common} /> : <input type="text" {...common} />}
                </div>
              );
            })}
          </div>
          <p className="field__hint">
            Ajuste a redação; números e fatos precisam estar na sua resposta.
          </p>
          {problem && (
            <p className="field__error" role="alert">
              {problem.message}
            </p>
          )}
          <div className="actions">
            <button
              type="button"
              className="button button--secondary"
              disabled={busy}
              onClick={() => {
                setEditing(false);
                setProblem(null);
              }}
            >
              Cancelar
            </button>
            <button type="submit" className="button" disabled={busy || !draftValid}>
              Salvar ajuste
            </button>
          </div>
        </form>
      )}

      {!editing && problem && (
        <p className="field__error suggestion__error" role="alert">
          {problem.message}
        </p>
      )}

      {!editing && (
        <div className="actions">
          <button
            type="button"
            className="button button--secondary"
            disabled={busy}
            onClick={() => void send("reject")}
          >
            Descartar
          </button>
          {!problem?.conflict && (
            <>
              <button
                type="button"
                className="button button--secondary"
                disabled={busy}
                onClick={startEditing}
              >
                Ajustar
              </button>
              <button
                type="button"
                className="button"
                disabled={busy}
                onClick={() => void send("accept")}
              >
                Aceitar
              </button>
            </>
          )}
        </div>
      )}
    </article>
  );
}
