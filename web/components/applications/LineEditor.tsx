"use client";

import { useState, type KeyboardEvent } from "react";

type Props = {
  initial: string;
  busy: boolean;
  error: string | null;
  onSave: (text: string) => void;
  onCancel: () => void;
};

/** Edição de uma linha do currículo: textarea, Salvar e Cancelar (Escape cancela). */
export function LineEditor({ initial, busy, error, onSave, onCancel }: Props) {
  const [text, setText] = useState(initial);

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Escape") {
      e.preventDefault();
      onCancel();
    }
  }

  return (
    <form
      className="line-editor"
      onSubmit={(e) => {
        e.preventDefault();
        onSave(text.trim());
      }}
    >
      <label className="line-editor__label">
        Texto da linha
        <textarea
          className="line-editor__text"
          value={text}
          maxLength={600}
          rows={3}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
        />
      </label>
      {error ? (
        <p role="alert" className="line-editor__error">
          {error}
        </p>
      ) : null}
      <div className="actions">
        <button type="submit" className="button" disabled={busy || text.trim() === ""}>
          {busy ? "Salvando…" : "Salvar"}
        </button>
        <button type="button" className="button button--secondary" onClick={onCancel}>
          Cancelar
        </button>
      </div>
    </form>
  );
}
