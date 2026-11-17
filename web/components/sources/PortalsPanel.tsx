"use client";

import { useEffect, useId, useRef, useState } from "react";
import { listPortals, portalStatusLabel, setPortalEnabled, type Portal, type PortalStatus } from "@/lib/portals";
import { ApiError } from "@/lib/profile";
import { safeJobUrl } from "@/lib/sources";

/** Aviso antes de ligar um portal condicional: apoio à decisão, não parecer jurídico. */
export const ENABLE_WARNING =
  "Os termos deste portal não foram totalmente verificados. Ligar pode violar regras do site. A coleta respeita o robots.txt e para se o site recusar o acesso. Isto é apoio à decisão, não parecer jurídico.";

const MARK: Record<PortalStatus, string> = { allowed: "✓", conditional: "!", blocked: "×" };

const message = (err: unknown) =>
  err instanceof ApiError ? err.message : "Não consegui atualizar o portal. Tente de novo.";

/** "AAAA-MM-DD" ou ISO com hora → "DD/MM/AAAA" (sem passar pelo fuso). */
export function brDate(value: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(value);
  return m ? `${m[3]}/${m[2]}/${m[1]}` : value;
}

function PortalCard({ portal, onChange }: { portal: Portal; onChange: (next: Portal) => void }) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inFlight = useRef(false);
  const mounted = useRef(true);
  const warnId = useId();
  const statusId = useId();

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  async function set(enabled: boolean) {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError(null);
    try {
      const next = await setPortalEnabled(portal.id, enabled);
      if (mounted.current) {
        setConfirming(false);
        onChange(next);
      }
    } catch (err) {
      if (mounted.current) setError(message(err));
    } finally {
      inFlight.current = false;
      if (mounted.current) setBusy(false);
    }
  }

  const links = portal.evidence_urls.map((u) => safeJobUrl(u)).filter((u): u is string => u !== null);

  return (
    <li className="portal" data-status={portal.status}>
      <div className="portal__head">
        <h3 className="portal__name">{portal.name}</h3>
        <p className="portal__status" id={statusId}>
          <span aria-hidden="true">{MARK[portal.status] ?? MARK.blocked}</span>{" "}
          <strong>{portalStatusLabel(portal.status)}</strong>
        </p>
      </div>
      <p className="muted">Verificado em {brDate(portal.checked_at)}</p>
      <p>{portal.evidence_summary}</p>
      {links.length > 0 && (
        <ul className="portal__links">
          {links.map((u) => (
            <li key={u}>
              <a href={u} target="_blank" rel="noopener noreferrer">
                {u}
              </a>
            </li>
          ))}
        </ul>
      )}

      {portal.status === "blocked" && <p>Só importar por link.</p>}

      {portal.blocked_by_site && (
        <p className="notice notice--warn" role="status">
          {`O site recusou nosso acesso em ${brDate(portal.blocked_at ?? "")}; coleta suspensa.`}
        </p>
      )}

      {portal.can_toggle && (
        <div className="portal__toggle">
          <label>
            <input
              type="checkbox"
              role="switch"
              checked={portal.enabled}
              disabled={busy}
              aria-label={`${portal.enabled ? "Desligar" : "Ligar"} ${portal.name}`}
              aria-describedby={statusId}
              onChange={() => {
                if (portal.enabled) void set(false);
                else setConfirming(true);
              }}
            />{" "}
            {portal.enabled ? "Ligado" : "Desligado"}
          </label>
        </div>
      )}

      {confirming && (
        <div className="notice notice--warn" role="alertdialog" aria-labelledby={warnId} aria-modal="false">
          <p id={warnId}>{ENABLE_WARNING}</p>
          <div className="actions">
            <button type="button" className="button" disabled={busy} onClick={() => void set(true)}>
              Ligar mesmo assim
            </button>
            <button type="button" className="button button--secondary" onClick={() => setConfirming(false)}>
              Cancelar
            </button>
          </div>
        </div>
      )}

      {error && (
        <p className="field__error" role="alert">
          {error}
        </p>
      )}
    </li>
  );
}

/** Portais de vagas com o status de cada um, a evidência e o interruptor (só condicionais). */
export function PortalsPanel() {
  const [portals, setPortals] = useState<Portal[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    listPortals()
      .then((list) => {
        if (mounted.current) setPortals(list);
      })
      .catch((err) => {
        if (mounted.current) setLoadError(message(err));
      });
    return () => {
      mounted.current = false;
    };
  }, []);

  return (
    <section className="panel" aria-labelledby="portais-title">
      <div className="panel__head">
        <h2 id="portais-title">Portais de vagas</h2>
      </div>
      <div className="panel__body stack">
        <p className="muted">
          Portais têm termos de uso próprios. Só os condicionais podem ser ligados, e a coleta respeita o
          robots.txt.
        </p>
        {loadError && (
          <div className="notice" role="alert">
            <strong>Não deu para carregar os portais.</strong>
            <p>{loadError}</p>
          </div>
        )}
        {!portals && !loadError && <p className="muted">Carregando portais…</p>}
        {portals && (
          <ul className="portal-list">
            {portals.map((p) => (
              <PortalCard
                key={p.id}
                portal={p}
                onChange={(next) =>
                  setPortals((prev) => (prev ? prev.map((x) => (x.id === next.id ? next : x)) : prev))
                }
              />
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
