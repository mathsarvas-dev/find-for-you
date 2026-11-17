"use client";

import { useState, type FormEvent } from "react";
import { resolveGap, type CoverageItem, type GapResult } from "@/lib/applications";
import { ApiError } from "@/lib/profile";

const MAX_ANSWER = 200;

type Props = {
  applicationId: number;
  item: CoverageItem;
  /** Chamado depois de resolver: a página recarrega a candidatura. */
  onResolved: () => void;
};

function resultText(r: GapResult): string {
  if (r.profile_version === null) return "Lacuna conhecida: não vai para o currículo.";
  if (r.regenerated) return `Perfil atualizado (versão ${r.profile_version}). Gerando de novo…`;
  return "Perfil atualizado. Gere de novo quando puder.";
}

/** Uma lacuna do currículo: "Tenho isso" (vira item do perfil) ou "Não tenho" (só aviso). */
export function GapItem({ applicationId, item, onResolved }: Props) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const decided = item.decision !== null;

  async function haveIt(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const value = text.trim();
    if (!value) return;
    setBusy(true);
    setError(null);
    try {
      const r = await resolveGap(applicationId, item.id, "have", value);
      setMessage(resultText(r));
      setOpen(false);
      onResolved();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Não foi possível salvar agora.");
    } finally {
      setBusy(false);
    }
  }

  async function dontHaveIt() {
    setBusy(true);
    setError(null);
    try {
      const r = await resolveGap(applicationId, item.id, "lack");
      setMessage(resultText(r));
      onResolved();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Não foi possível salvar agora.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="gap-item">
      <p>Não encontrei no seu perfil: {item.text}</p>
      {item.decision === "lack" && message === null ? (
        <p className="muted">Lacuna conhecida</p>
      ) : null}
      {message ? <p role="status">{message}</p> : null}
      {error ? (
        <p role="alert" className="application-error">
          {error}
        </p>
      ) : null}

      {!decided && message === null ? (
        open ? (
          <form className="gap-item__form" onSubmit={haveIt}>
            <label>
              O que você tem nessa área?
              <input
                type="text"
                value={text}
                maxLength={MAX_ANSWER}
                onChange={(e) => setText(e.target.value)}
              />
            </label>
            <div className="actions">
              <button type="submit" className="button" disabled={busy || text.trim() === ""}>
                Adicionar ao perfil
              </button>
              <button type="button" className="button button--secondary" onClick={() => setOpen(false)}>
                Cancelar
              </button>
            </div>
          </form>
        ) : (
          <div className="actions">
            <button type="button" className="button button--secondary" onClick={() => setOpen(true)}>
              Tenho isso
            </button>
            <button type="button" className="button button--secondary" disabled={busy} onClick={dontHaveIt}>
              Não tenho
            </button>
          </div>
        )
      ) : null}
    </li>
  );
}
