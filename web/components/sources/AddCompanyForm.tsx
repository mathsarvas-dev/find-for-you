"use client";

import { useEffect, useId, useRef, useState } from "react";
import { ApiError } from "@/lib/profile";
import { addCompany, type Company, type NewCompany } from "@/lib/sources";

type AddCompanyFormProps = {
  onAdded: (company: Company) => void;
};

const message = (err: unknown) =>
  err instanceof ApiError ? err.message : "Não consegui cadastrar. Tente de novo.";

/** Cadastro de um quadro do Greenhouse: aceita o token ou a URL colada (o servidor normaliza). */
export function AddCompanyForm({ onAdded }: AddCompanyFormProps) {
  const [identifier, setIdentifier] = useState("");
  const [country, setCountry] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [added, setAdded] = useState<string | null>(null);
  const inFlight = useRef(false);
  const mounted = useRef(true);
  const fieldRef = useRef<HTMLInputElement>(null);
  const fieldId = useId();
  const hintId = useId();
  const errorId = useId();
  const countryId = useId();
  const countryHintId = useId();

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const value = identifier.trim();
  const canSend = !sending && value !== "";

  async function send() {
    if (inFlight.current || value === "") return;
    const cc = country.trim().toUpperCase();
    if (cc && !/^[A-Z]{2}$/.test(cc)) {
      setError("País deve ter 2 letras (ex.: BR, US, DE) ou ficar em branco.");
      setAdded(null);
      return;
    }
    inFlight.current = true;
    setSending(true);
    setError(null);
    setAdded(null);
    const input: NewCompany = { source: "greenhouse", identifier: value };
    if (cc) input.country = cc;
    try {
      const company = await addCompany(input);
      if (!mounted.current) return;
      setIdentifier("");
      setCountry("");
      setAdded(company.name);
      onAdded(company);
    } catch (err) {
      if (!mounted.current) return;
      setError(message(err));
      // Foco volta ao campo para corrigir o token (o campo não é desabilitado no envio).
      fieldRef.current?.focus();
    } finally {
      inFlight.current = false;
      if (mounted.current) setSending(false);
    }
  }

  return (
    <form
      className="add-company"
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        void send();
      }}
    >
      <div className="add-company__fields">
        <div className="field add-company__token">
          <label className="field__label" htmlFor={fieldId}>
            Token ou URL do quadro Greenhouse
          </label>
          <input
            ref={fieldRef}
            id={fieldId}
            className="input"
            type="text"
            inputMode="url"
            autoComplete="off"
            spellCheck={false}
            maxLength={500}
            placeholder="Ex.: gitlab"
            value={identifier}
            aria-describedby={error ? `${hintId} ${errorId}` : hintId}
            aria-invalid={error ? true : undefined}
            onChange={(e) => setIdentifier(e.target.value)}
          />
          <p className="field__hint" id={hintId}>
            O token é a parte final de <span className="mono">boards.greenhouse.io/&lt;token&gt;</span>,
            no endereço da página de vagas da empresa. Pode colar a URL inteira.
          </p>
        </div>
        <div className="field add-company__country">
          <label className="field__label" htmlFor={countryId}>
            País (opcional)
          </label>
          <input
            id={countryId}
            className="input mono"
            type="text"
            autoComplete="off"
            spellCheck={false}
            maxLength={2}
            placeholder="BR"
            value={country}
            aria-describedby={countryHintId}
            onChange={(e) => setCountry(e.target.value.toUpperCase())}
          />
          <p className="field__hint" id={countryHintId}>
            2 letras
          </p>
        </div>
      </div>

      {error && (
        <p className="field__error add-company__msg" id={errorId} role="alert">
          {error}
        </p>
      )}

      <div className="add-company__foot">
        <p className="muted add-company__status" role="status" aria-live="polite">
          {sending
            ? "Conferindo o quadro no Greenhouse. Pode levar alguns segundos."
            : added
              ? `${added} cadastrada. A primeira coleta começa em instantes.`
              : ""}
        </p>
        <button type="submit" className="button" disabled={!canSend} aria-busy={sending || undefined}>
          {sending ? "Validando o quadro…" : "Cadastrar empresa"}
        </button>
      </div>
    </form>
  );
}
