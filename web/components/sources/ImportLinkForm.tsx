"use client";

import Link from "next/link";
import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { importLink, NeedsTextError } from "@/lib/importLink";
import { ApiError } from "@/lib/profile";

const NEEDS_TEXT = "Não consegui ler esta vaga automaticamente. Cole o título e a descrição.";

const message = (err: unknown) =>
  err instanceof ApiError ? err.message : "Algo deu errado ao falar com a API. Tente de novo.";

/**
 * Importa uma vaga pelo endereço. Quando a página não traz os dados, pede título e
 * descrição colados e reenvia. O texto colado é tratado como texto, nunca como HTML.
 */
export function ImportLinkForm() {
  const [url, setUrl] = useState("");
  const [needsText, setNeedsText] = useState(false);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<{ created: boolean } | null>(null);
  const inFlight = useRef(false);
  const mounted = useRef(true);
  const urlId = useId();
  const titleId = useId();
  const descriptionId = useId();

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  function changeUrl(value: string) {
    setUrl(value);
    // Dados colados para outro endereço não valem para este.
    setNeedsText(false);
    setTitle("");
    setDescription("");
    setResult(null);
    setError(null);
  }

  async function send(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const value = url.trim();
    if (inFlight.current || value === "") return;
    if (needsText && (title.trim() === "" || description.trim() === "")) {
      setError("Preencha o título e a descrição da vaga.");
      return;
    }
    inFlight.current = true;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const res = await importLink(
        needsText ? { url: value, title: title.trim(), description: description.trim() } : { url: value },
      );
      if (!mounted.current) return;
      setResult({ created: res.created });
      setUrl("");
      setNeedsText(false);
      setTitle("");
      setDescription("");
    } catch (err) {
      if (!mounted.current) return;
      if (err instanceof NeedsTextError) {
        setNeedsText(true);
      } else {
        setError(message(err));
      }
    } finally {
      inFlight.current = false;
      if (mounted.current) setBusy(false);
    }
  }

  return (
    <section className="panel" aria-labelledby="importar-title">
      <div className="panel__head">
        <h2 id="importar-title">Importar uma vaga por link</h2>
      </div>
      <div className="panel__body stack">
        <p className="muted">
          Cole o endereço de uma vaga de qualquer site. Se a página não trouxer os dados, você cola o título
          e a descrição. A vaga entra na lista de vagas.
        </p>

        <form className="import-link" noValidate onSubmit={(e) => void send(e)}>
          <div className="field">
            <label className="field__label" htmlFor={urlId}>
              Endereço da vaga
            </label>
            <input
              id={urlId}
              className="input"
              type="text"
              inputMode="url"
              autoComplete="off"
              spellCheck={false}
              maxLength={2048}
              value={url}
              onChange={(e) => changeUrl(e.target.value)}
            />
          </div>

          {needsText && (
            <div className="notice notice--warn import-link__fallback">
              <p>{NEEDS_TEXT}</p>
              <div className="field">
                <label className="field__label" htmlFor={titleId}>
                  Título da vaga
                </label>
                <input
                  id={titleId}
                  className="input"
                  type="text"
                  maxLength={300}
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                />
              </div>
              <div className="field">
                <label className="field__label" htmlFor={descriptionId}>
                  Descrição da vaga
                </label>
                <textarea
                  id={descriptionId}
                  className="input"
                  rows={6}
                  maxLength={20000}
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                />
              </div>
            </div>
          )}

          {error && (
            <p className="field__error" role="alert">
              {error}
            </p>
          )}

          {result && (
            <p className="import-link__done" role="status">
              {result.created ? "Vaga importada." : "Essa vaga já estava importada."}{" "}
              <Link href="/jobs">Ver vagas</Link>
            </p>
          )}

          <div className="add-company__foot">
            <span />
            <button type="submit" className="button" disabled={busy || url.trim() === ""} aria-busy={busy || undefined}>
              Importar vaga
            </button>
          </div>
        </form>
      </div>
    </section>
  );
}
