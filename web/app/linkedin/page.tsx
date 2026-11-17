"use client";

import { useEffect, useId, useState } from "react";
import { PageHeader } from "@/components/shell/PageHeader";
import {
  LANGUAGE_OPTIONS,
  PRIORITY_LABEL,
  SECTION_LABEL,
  getOptimization,
  optimizeLinkedin,
  rejectedLabel,
  type DraftText,
  type OptimizationOut,
  type OptimizeLanguage,
} from "@/lib/linkedin";
import { ApiError } from "@/lib/profile";

const message = (err: unknown) =>
  err instanceof ApiError ? err.message : "Algo deu errado ao falar com a API. Tente de novo.";

/** Botão que copia um texto; o aviso "Copiado" fica numa região de status. */
function CopyButton({ text, label }: { text: string; label: string }) {
  const [state, setState] = useState<"idle" | "ok" | "fail">("idle");
  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setState("ok");
    } catch {
      setState("fail");
    }
  }
  return (
    <span className="linkedin-copy">
      <button type="button" className="button button--secondary" aria-label={label} onClick={() => void copy()}>
        Copiar
      </button>
      <span role="status" className="muted">
        {state === "ok" ? "Copiado." : state === "fail" ? "Não foi possível copiar. Selecione o texto." : ""}
      </span>
    </span>
  );
}

function Block({ title, copyText, copyLabel, children }: {
  title: string;
  copyText?: string;
  copyLabel?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="panel linkedin-block">
      <div className="panel__head">
        <h2>{title}</h2>
        {copyText ? <CopyButton text={copyText} label={copyLabel ?? `Copiar ${title}`} /> : null}
      </div>
      <div className="panel__body">{children}</div>
    </section>
  );
}

const paragraphs = (items: DraftText[]) => items.map((p) => p.text).join("\n\n");

export default function LinkedinPage() {
  const [data, setData] = useState<OptimizationOut | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [language, setLanguage] = useState<OptimizeLanguage>("pt");
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const langId = useId();

  useEffect(() => {
    let cancelled = false;
    getOptimization()
      .then((saved) => {
        if (cancelled) return;
        setData(saved);
        if (saved) setLanguage(saved.language as OptimizeLanguage);
        setLoadError(null);
        setLoading(false);
      })
      .catch((err) => {
        if (cancelled) return;
        setLoadError(message(err));
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [reloadKey]);

  async function run() {
    setRunning(true);
    setError(null);
    try {
      setData(await optimizeLinkedin(language));
    } catch (err) {
      setError(message(err));
    } finally {
      setRunning(false);
    }
  }

  const r = data?.result;

  return (
    <main className="page page--wide">
      <PageHeader
        title="LinkedIn"
        description="Sugestões para o seu perfil do LinkedIn, escritas a partir do seu perfil confirmado. Não lemos o seu LinkedIn: compare cada rascunho com o que já está publicado e copie só o que fizer sentido."
      />

      <form
        className="linkedin-controls"
        onSubmit={(e) => {
          e.preventDefault();
          void run();
        }}
      >
        <label htmlFor={langId}>Idioma dos textos</label>
        <select
          id={langId}
          className="input linkedin-controls__select"
          value={language}
          disabled={running}
          onChange={(e) => setLanguage(e.target.value as OptimizeLanguage)}
        >
          {LANGUAGE_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
        <button type="submit" className="button" disabled={running || loading}>
          {running ? "Gerando…" : data ? "Gerar de novo" : "Gerar sugestões"}
        </button>
        <span role="status" className="muted">
          {running ? "Isso pode levar até um minuto." : ""}
        </span>
      </form>

      {error && (
        <p role="alert" className="field__error">
          {error}
        </p>
      )}
      {loadError && (
        <div role="alert" className="application-error">
          <p>{loadError}</p>
          <button type="button" className="button button--secondary" onClick={() => setReloadKey((k) => k + 1)}>
            Tentar de novo
          </button>
        </div>
      )}
      {loading && !loadError && <p className="muted">Carregando…</p>}

      {data && r && (
        <div className="stack">
          {data.stale && (
            <p role="status" className="linkedin-stale">
              Seu perfil mudou depois desta análise. Gere de novo para refletir as mudanças.
            </p>
          )}

          <Block title="Melhorias priorizadas">
            {r.improvements.length === 0 ? (
              <p className="muted">Nenhuma melhoria sugerida.</p>
            ) : (
              <ol className="linkedin-list">
                {r.improvements.map((i, n) => (
                  <li key={n}>
                    <strong className={`linkedin-priority linkedin-priority--${i.priority}`}>
                      Prioridade {PRIORITY_LABEL[i.priority].toLowerCase()}
                    </strong>{" "}
                    <span className="muted">({SECTION_LABEL[i.section]})</span> {i.text}
                  </li>
                ))}
              </ol>
            )}
          </Block>

          <Block title="Título (headline)" copyText={r.headline?.text} copyLabel="Copiar título">
            {r.headline ? <p>{r.headline.text}</p> : <p className="muted">Sem rascunho para o título.</p>}
          </Block>

          <Block title="Sobre" copyText={r.about.length ? paragraphs(r.about) : undefined} copyLabel="Copiar sobre">
            {r.about.length ? (
              r.about.map((p, n) => <p key={n}>{p.text}</p>)
            ) : (
              <p className="muted">Sem rascunho para a seção Sobre.</p>
            )}
          </Block>

          {r.experiences.map((e) => (
            <Block
              key={e.item_id}
              title={`Experiência: ${[e.title, e.company].filter(Boolean).join(" na ")}`}
              copyText={e.bullets.map((b) => `• ${b.text}`).join("\n")}
              copyLabel={`Copiar experiência ${e.company || e.title}`}
            >
              <ul className="linkedin-list">
                {e.bullets.map((b, n) => (
                  <li key={n}>{b.text}</li>
                ))}
              </ul>
            </Block>
          ))}

          <Block
            title="Habilidades (ordem sugerida)"
            copyText={r.skills.length ? r.skills.map((s) => s.name).join(", ") : undefined}
            copyLabel="Copiar habilidades"
          >
            {r.skills.length ? (
              <ol className="linkedin-list">
                {r.skills.map((s) => (
                  <li key={s.id}>{s.name}</li>
                ))}
              </ol>
            ) : (
              <p className="muted">Seu perfil não tem habilidades.</p>
            )}
          </Block>

          <Block title="Notas por ponto de vista">
            {r.viewpoints.map((v) => (
              <section key={v.key} aria-label={v.label} className="linkedin-view">
                <h3>{v.label}</h3>
                {v.notes.length ? (
                  <ul className="linkedin-list">
                    {v.notes.map((n, i) => (
                      <li key={i}>{n}</li>
                    ))}
                  </ul>
                ) : (
                  <p className="muted">Sem observações.</p>
                )}
              </section>
            ))}
          </Block>

          {r.rejected.length > 0 && (
            <Block title="Seções que não foram escritas">
              <p>
                Para não inventar dados, estas seções ficaram de fora. Complete o perfil com mais detalhes e gere de
                novo.
              </p>
              <ul className="linkedin-list">
                {r.rejected.map((x, n) => (
                  <li key={n}>
                    <strong>{rejectedLabel(x.section)}:</strong> {x.reasons.join("; ")}
                  </li>
                ))}
              </ul>
            </Block>
          )}
        </div>
      )}
    </main>
  );
}
