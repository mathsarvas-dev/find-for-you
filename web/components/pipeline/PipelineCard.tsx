"use client";

import Link from "next/link";
import { useId, useState } from "react";
import {
  CLOSED_STAGES,
  MAX_NOTES,
  STAGES,
  STAGE_LABEL,
  followUpState,
  formatDay,
  type PipelineItem,
  type PipelinePatch,
  type Stage,
} from "@/lib/pipeline";

type Props = {
  item: PipelineItem;
  today: string;
  /** Grava a mudança; rejeita com Error de mensagem em PT-BR. */
  onPatch: (id: number, patch: PipelinePatch) => Promise<void>;
};

/** Texto do lembrete: o estado vai escrito, não só na cor. */
export function followUpText(item: PipelineItem, today: string): string | null {
  if (!item.follow_up_at) return null;
  if (CLOSED_STAGES.includes(item.stage)) return `Follow-up de ${formatDay(item.follow_up_at)} (encerrada)`;
  const s = followUpState(item.follow_up_at, today);
  if (s === "overdue") return `Follow-up atrasado desde ${formatDay(item.follow_up_at)}`;
  if (s === "today") return "Follow-up hoje";
  return `Follow-up em ${formatDay(item.follow_up_at)}`;
}

function messageOf(e: unknown): string {
  return e instanceof Error ? e.message : "Não foi possível salvar agora.";
}

export function PipelineCard({ item, today, onPatch }: Props) {
  const uid = useId();
  const [notes, setNotes] = useState(item.notes ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedNotes, setSavedNotes] = useState(false);
  const followUp = followUpText(item, today);
  const due = item.follow_up_at !== null && !CLOSED_STAGES.includes(item.stage) && item.follow_up_at <= today;

  async function save(patch: PipelinePatch, after?: () => void) {
    setBusy(true);
    setError(null);
    try {
      await onPatch(item.id, patch);
      after?.();
    } catch (e) {
      setError(messageOf(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="pipeline-card" data-due={due ? "true" : undefined}>
      <Link className="pipeline-card__title" href={`/candidaturas/${item.id}`}>
        {item.job_title}
      </Link>
      <p className="pipeline-card__meta">
        {item.company_name}
        {item.ai_score !== null ? ` · Nota ${item.ai_score}` : ""}
      </p>
      {followUp ? <p className="pipeline-card__followup">{followUp}</p> : null}
      <div className="pipeline-card__field">
        <label htmlFor={`${uid}-stage`}>Mover {item.job_title} para</label>
        <select
          id={`${uid}-stage`}
          value={item.stage}
          disabled={busy}
          onChange={(e) => void save({ stage: e.target.value as Stage })}
        >
          {STAGES.map((s) => (
            <option key={s} value={s}>
              {STAGE_LABEL[s]}
            </option>
          ))}
        </select>
      </div>
      <details className="pipeline-card__details">
        <summary>Follow-up e anotações{item.notes ? " (com anotação)" : ""}</summary>
        <div className="pipeline-card__field">
          <label htmlFor={`${uid}-date`}>Data do follow-up</label>
          <input
            id={`${uid}-date`}
            type="date"
            value={item.follow_up_at ?? ""}
            disabled={busy}
            onChange={(e) => void save({ follow_up_at: e.target.value || null })}
          />
        </div>
        <div className="pipeline-card__field">
          <label htmlFor={`${uid}-notes`}>Anotações</label>
          <textarea
            id={`${uid}-notes`}
            rows={3}
            maxLength={MAX_NOTES}
            value={notes}
            onChange={(e) => {
              setNotes(e.target.value);
              setSavedNotes(false);
            }}
          />
          <button
            type="button"
            className="button button--secondary"
            disabled={busy || notes === (item.notes ?? "")}
            onClick={() => void save({ notes }, () => setSavedNotes(true))}
          >
            Salvar anotações
          </button>
          {savedNotes ? <span role="status">Anotações salvas.</span> : null}
        </div>
      </details>
      {error ? (
        <p role="alert" className="pipeline-card__error">
          {error}
        </p>
      ) : null}
    </li>
  );
}
