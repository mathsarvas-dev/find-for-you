"use client";

import { useEffect, useId, useRef, useState } from "react";
import { ApiError } from "@/lib/profile";
import {
  collectNow,
  isPending,
  patchCompany,
  relativeTime,
  sourceLabel,
  type Company,
} from "@/lib/sources";

type CompanyRowProps = {
  company: Company;
  /** Relógio do cliente; 0 antes de montar (sem tempo relativo no prerender). */
  now: number;
  onChange: (company: Company) => void;
};

const message = (err: unknown) =>
  err instanceof ApiError ? err.message : "Algo deu errado ao falar com a API. Tente de novo.";

function failureText(c: Company): string | null {
  if (!c.last_error || c.consecutive_failures <= 0) return null;
  const n = c.consecutive_failures;
  return `Falha na coleta (${n} ${n === 1 ? "seguida" : "seguidas"}): ${c.last_error}`;
}

export function CompanyRow({ company: c, now, onChange }: CompanyRowProps) {
  const [busy, setBusy] = useState<"collect" | "toggle" | "mode" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const inFlight = useRef(false);
  const mounted = useRef(true);
  const modeHintId = useId();

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  async function run(kind: "collect" | "toggle" | "mode") {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(kind);
    setError(null);
    try {
      const next =
        kind === "collect"
          ? await collectNow(c.id)
          : kind === "toggle"
            ? await patchCompany(c.id, { active: !c.active })
            : await patchCompany(c.id, { collect_mode: c.collect_mode === "all" ? "funnel" : "all" });
      if (mounted.current) onChange(next);
    } catch (err) {
      if (mounted.current) setError(message(err));
    } finally {
      inFlight.current = false;
      if (mounted.current) setBusy(null);
    }
  }

  const failure = failureText(c);
  const pending = now > 0 && isPending(c, now);
  const lastRun =
    c.last_run_at === null
      ? "ainda não coletada"
      : now > 0
        ? `última coleta: ${relativeTime(c.last_run_at, now)}`
        : "última coleta: —";

  return (
    <li className="company" data-active={c.active || undefined}>
      <div className="company__main">
        <p className="company__title">
          <span className="company__name">{c.name}</span>{" "}
          <span className="mono company__token">{c.identifier}</span>
          {c.origin === "auto" && (
            <span className="chip chip--auto" title="Adicionada pela descoberta automática">
              automática
            </span>
          )}
        </p>
        <p className="company__meta">
          <span className="chip" data-state={c.active ? "on" : "off"}>
            {c.active ? "ativa" : "pausada"}
          </span>
          {c.country && <span className="mono">{c.country}</span>}
          <span className="chip">{sourceLabel(c.source)}</span>
          <span className="company__jobs">
            {`${c.open_jobs} ${c.open_jobs === 1 ? "vaga aberta" : "vagas abertas"}`}
            {c.stub_jobs > 0 && ` · ${c.stub_jobs} fora dos seus filtros`}
          </span>
        </p>
        <p className="company__run">
          <span>{lastRun}</span>
          {pending && <span className="company__pending">{"· coleta pendente"}</span>}
        </p>
        <label className="company__mode">
          <input
            type="checkbox"
            role="checkbox"
            checked={c.collect_mode === "all"}
            disabled={busy !== null}
            aria-describedby={`${modeHintId}`}
            onChange={() => void run("mode")}
          />{" "}
          Coletar todas as vagas desta empresa
        </label>
        <p className="field__hint" id={modeHintId}>
          Ignora seus filtros só para esta empresa.
        </p>
        {failure && <p className="company__failure">{failure}</p>}
        {error && (
          <p className="field__error company__error" role="alert">
            {error}
          </p>
        )}
      </div>
      <div className="company__actions">
        <button
          type="button"
          className="button button--secondary"
          disabled={busy !== null || !c.active}
          aria-busy={busy === "collect" || undefined}
          title={c.active ? undefined : "Ative a empresa para coletar"}
          aria-label={`Coletar agora — ${c.name}`}
          onClick={() => void run("collect")}
        >
          Coletar agora
        </button>
        <button
          type="button"
          className="link-button"
          disabled={busy !== null}
          aria-busy={busy === "toggle" || undefined}
          aria-label={`${c.active ? "Pausar" : "Ativar"} — ${c.name}`}
          onClick={() => void run("toggle")}
        >
          {c.active ? "Pausar" : "Ativar"}
        </button>
      </div>
    </li>
  );
}
