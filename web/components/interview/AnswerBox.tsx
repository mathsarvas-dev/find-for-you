"use client";

import { useEffect, useId, useRef, useState } from "react";
import { ApiError } from "@/lib/profile";

/** Mesmo limite de MAX_ANSWER_CHARS no servidor. */
export const MAX_ANSWER_CHARS = 4000;

type AnswerBoxProps = {
  disabled?: boolean;
  /** Rejeita com ApiError para manter o rascunho e mostrar o motivo. */
  onSubmit: (text: string) => Promise<void> | void;
  onSkip: () => Promise<void> | void;
};

const message = (err: unknown) =>
  err instanceof ApiError ? err.message : "Não consegui enviar. Tente de novo.";

export function AnswerBox({ disabled = false, onSubmit, onSkip }: AnswerBoxProps) {
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [skipping, setSkipping] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inFlight = useRef(false);
  const mounted = useRef(true);
  const fieldId = useId();
  const hintId = useId();
  const countId = useId();

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const over = text.length > MAX_ANSWER_CHARS;
  const busy = disabled || sending || skipping;
  const canSend = !busy && !over && text.trim() !== "";

  async function run(kind: "send" | "skip") {
    if (inFlight.current) return;
    if (kind === "send" && !canSend) return;
    inFlight.current = true;
    setError(null);
    (kind === "send" ? setSending : setSkipping)(true);
    try {
      if (kind === "send") {
        await onSubmit(text.trim());
        if (mounted.current) setText("");
      } else {
        await onSkip();
      }
    } catch (err) {
      if (mounted.current) setError(message(err));
    } finally {
      inFlight.current = false;
      if (mounted.current) (kind === "send" ? setSending : setSkipping)(false);
    }
  }

  return (
    <form
      className="answer"
      onSubmit={(e) => {
        e.preventDefault();
        void run("send");
      }}
    >
      <div className="field">
        <label className="field__label" htmlFor={fieldId}>
          Sua resposta
        </label>
        <textarea
          id={fieldId}
          className="input answer__input"
          rows={5}
          value={text}
          aria-describedby={`${hintId} ${countId}`}
          aria-invalid={over || undefined}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
              e.preventDefault();
              void run("send");
            }
          }}
        />
        <div className="answer__meta">
          <p className="field__hint" id={hintId}>
            Responda com o que você lembra; pode ser curto. <kbd>Ctrl</kbd>+<kbd>Enter</kbd> envia.
          </p>
          <span
            id={countId}
            className="mono answer__count"
            data-over={over || undefined}
          >
            {text.length} / {MAX_ANSWER_CHARS}
          </span>
        </div>
        {over && (
          <p className="field__error">
            A resposta passou do limite de {MAX_ANSWER_CHARS} caracteres. Encurte um pouco.
          </p>
        )}
        {error && (
          <p className="field__error" role="alert">
            {error}
          </p>
        )}
      </div>
      <div className="actions">
        <button
          type="button"
          className="button button--secondary"
          disabled={busy}
          onClick={() => void run("skip")}
        >
          Pular
        </button>
        <button type="submit" className="button" disabled={!canSend} aria-busy={sending || undefined}>
          Enviar resposta
        </button>
      </div>
    </form>
  );
}
