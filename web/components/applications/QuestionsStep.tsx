"use client";

import { useEffect, useRef, useState } from "react";
import {
  draftQuestion,
  fetchQuestions,
  pasteQuestions,
  updateQuestion,
  type ApplicationDetail,
  type QuestionOut,
} from "@/lib/applications";
import { ApiError } from "@/lib/profile";

type Props = {
  detail: ApplicationDetail;
  onChanged: () => void;
};

/** Chave do banco sugerida a partir do enunciado (a pessoa pode editar). */
export function suggestedBankKey(label: string): string {
  const slug = label
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 64);
  return slug || "resposta";
}

function messageOf(e: unknown): string {
  return e instanceof ApiError || e instanceof Error ? e.message : "Algo deu errado. Tente de novo.";
}

function QuestionItem({
  applicationId,
  q,
  onChanged,
}: {
  applicationId: number;
  q: QuestionOut;
  onChanged: () => void;
}) {
  const [answer, setAnswer] = useState(q.answer ?? "");
  const [bankKey, setBankKey] = useState(suggestedBankKey(q.label));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);

  async function run(action: () => Promise<unknown>, done: string) {
    setBusy(true);
    setError(null);
    setSaved(null);
    try {
      await action();
      setSaved(done);
      onChanged();
    } catch (e) {
      setError(messageOf(e));
    } finally {
      setBusy(false);
    }
  }

  const queued = q.task?.state === "queued";
  const failed = q.task?.state === "failed";

  return (
    <article className="question" data-sensitive={q.sensitive ? "true" : "false"}>
      <p className="question__label">{q.label}</p>
      <p className="muted question__tags">
        {q.required ? "Obrigatória · " : ""}
        {q.sensitive ? "Responda você" : "Pode ser rascunhada"}
      </p>

      {q.sensitive && q.bank_suggestion ? (
        <p className="question__suggestion">
          Sugestão do seu banco: {q.bank_suggestion.value}{" "}
          <button
            type="button"
            className="link-button"
            onClick={() => setAnswer(q.bank_suggestion?.value ?? "")}
          >
            Usar sugestão
          </button>
        </p>
      ) : null}

      {q.kind === "file" ? (
        <p className="muted">Envie o arquivo você mesmo no site da vaga.</p>
      ) : null}

      {!q.sensitive && q.kind !== "file" ? (
        <div className="actions">
          <button
            type="button"
            className="button button--secondary"
            disabled={busy || queued}
            onClick={() => run(() => draftQuestion(applicationId, q.id), "Rascunho pedido.")}
          >
            {queued ? "Rascunhando…" : "Rascunhar"}
          </button>
        </div>
      ) : null}
      {failed && q.task ? (
        <p role="alert" className="application-error">
          {q.task.error}
        </p>
      ) : null}

      <label className="question__answer">
        Sua resposta
        <textarea value={answer} maxLength={5000} rows={3} onChange={(e) => setAnswer(e.target.value)} />
      </label>
      <div className="actions">
        <button
          type="button"
          className="button button--secondary"
          disabled={busy || answer.trim() === ""}
          onClick={() => run(() => updateQuestion(applicationId, q.id, { answer }), "Resposta salva.")}
        >
          Salvar resposta
        </button>
        <button
          type="button"
          className="button button--secondary"
          disabled={busy || answer.trim() === ""}
          onClick={() => run(() => updateQuestion(applicationId, q.id, { answer, final: true }), "Resposta final.")}
        >
          Marcar como final
        </button>
      </div>
      <label className="question__bank">
        Chave no banco de respostas
        <input value={bankKey} maxLength={64} onChange={(e) => setBankKey(e.target.value)} />
      </label>
      <button
        type="button"
        className="link-button"
        disabled={busy || answer.trim() === "" || bankKey.trim() === ""}
        onClick={() =>
          run(() => updateQuestion(applicationId, q.id, { save_to_bank: bankKey.trim() }), "Salvo no banco.")
        }
      >
        Salvar no banco
      </button>

      {saved ? <p role="status">{saved}</p> : null}
      {error ? (
        <p role="alert" className="application-error">
          {error}
        </p>
      ) : null}
    </article>
  );
}

/** Passo "Perguntas": as da vaga (Greenhouse), ou as coladas; rascunhos e respostas finais. */
export function QuestionsStep({ detail, onChanged }: Props) {
  const [pasteText, setPasteText] = useState("");
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [pasteError, setPasteError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const attempted = useRef(false);
  const hasQuestions = detail.questions.length > 0;

  // one automatic fetch per mount when the source may have questions; a refusal falls back to paste
  useEffect(() => {
    if (hasQuestions || attempted.current) return;
    attempted.current = true;
    let cancelled = false;
    fetchQuestions(detail.id)
      .then(() => {
        if (!cancelled) onChanged();
      })
      .catch((e: unknown) => {
        if (!cancelled) setFetchError(messageOf(e));
      });
    return () => {
      cancelled = true;
    };
  }, [detail.id, hasQuestions, onChanged]);

  async function paste() {
    setBusy(true);
    setPasteError(null);
    try {
      await pasteQuestions(detail.id, pasteText);
      setPasteText("");
      onChanged();
    } catch (e) {
      setPasteError(messageOf(e));
    } finally {
      setBusy(false);
    }
  }

  if (!hasQuestions) {
    return (
      <section className="application-step" aria-labelledby="questions-title">
        <h2 id="questions-title" className="application-step__title">
          Perguntas da vaga
        </h2>
        {fetchError ? <p className="muted">{fetchError}</p> : <p className="muted">Buscando as perguntas…</p>}
        <label className="question__paste">
          Cole as perguntas (uma por parágrafo)
          <textarea value={pasteText} rows={6} maxLength={20000} onChange={(e) => setPasteText(e.target.value)} />
        </label>
        <div className="actions">
          <button type="button" className="button" disabled={busy || pasteText.trim() === ""} onClick={paste}>
            Usar estas perguntas
          </button>
        </div>
        {pasteError ? (
          <p role="alert" className="application-error">
            {pasteError}
          </p>
        ) : null}
      </section>
    );
  }

  return (
    <section className="application-step" aria-labelledby="questions-title">
      <h2 id="questions-title" className="application-step__title">
        Perguntas da vaga
      </h2>
      {detail.questions.map((q) => (
        <QuestionItem key={q.id} applicationId={detail.id} q={q} onChanged={onChanged} />
      ))}
    </section>
  );
}
