"use client";

import { useState } from "react";
import {
  downloadArtifact,
  editLine,
  patchApplication,
  type Template,
  type ApplicationDetail,
  type CoverageItem,
  type Counts,
  type DocLine,
  type DocumentOut,
} from "@/lib/applications";
import { ApiError } from "@/lib/profile";
import { LineEditor } from "./LineEditor";
import { TemplatePicker } from "./TemplatePicker";

const BLOCKED_REASON = "Corrija as linhas marcadas antes de baixar.";

type Props = {
  detail: ApplicationDetail;
  /** Chamado depois de salvar uma linha: a página recarrega a candidatura. */
  onChanged: () => void;
};

const SECTION_LABEL: Record<string, string> = {
  contact: "Contato",
  summary: "Resumo",
  skills: "Habilidades",
  languages: "Idiomas",
  certifications: "Certificações",
  education: "Formação",
  letter: "Carta",
};

function countText(c: Counts): string {
  return `${c.covered} de ${c.total}`;
}

/** Nome do item de origem, resolvido pelas linhas de fato do mesmo documento. */
function originLabel(line: DocLine, lines: DocLine[]): string {
  if (line.origin === "user") return "Editado por você";
  if (line.origin === "fact") return "Do seu perfil";
  const ref = line.parent ?? line.source_refs[0];
  const header = lines.find(
    (l) =>
      l.origin === "fact" &&
      l.source_refs.includes(ref ?? "") &&
      (l.section === "experience" || l.section === "project" || l.section === "education"),
  );
  return header ? `Do perfil: ${header.text}` : "Do seu perfil";
}

function uncovered(items: CoverageItem[]): CoverageItem[] {
  return items.filter((i) => !i.covered);
}

/** Passo "Currículo": cobertura, linhas com a origem e edição validada. */
export function ResumeStep({ detail, onChanged }: Props) {
  // the document returned by the last save, valid only for the detail it was saved on:
  // a fresh detail from the server (new object) replaces it
  const [saved, setSaved] = useState<{ base: ApplicationDetail; doc: DocumentOut } | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [settingsError, setSettingsError] = useState<string | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const [referees, setReferees] = useState(detail.referees ?? "");

  const doc = saved && saved.base === detail ? saved.doc : detail.resume;
  const cov = detail.coverage;

  if (doc === null) {
    return <p className="muted">O currículo ainda não foi gerado.</p>;
  }
  const lines = doc.lines;
  const needsFix = doc.status === "needs_fix";
  const gaps = cov ? uncovered(cov.items) : [];

  async function changeTemplate(template: Template) {
    setBusy(true);
    setSettingsError(null);
    try {
      await patchApplication(detail.id, { template });
      onChanged();
    } catch (e) {
      setSettingsError(e instanceof ApiError ? e.message : "Não foi possível trocar o modelo agora.");
    } finally {
      setBusy(false);
    }
  }

  async function saveReferees() {
    setBusy(true);
    setSettingsError(null);
    try {
      await patchApplication(detail.id, { referees });
      onChanged();
    } catch (e) {
      setSettingsError(e instanceof ApiError ? e.message : "Não foi possível salvar as referências agora.");
    } finally {
      setBusy(false);
    }
  }

  async function download(fmt: "pdf" | "txt") {
    setDownloadError(null);
    try {
      await downloadArtifact(detail.id, "resume", fmt);
    } catch (e) {
      setDownloadError(e instanceof ApiError ? e.message : "Não foi possível baixar agora.");
    }
  }

  async function save(line: DocLine, text: string) {
    setBusy(true);
    setError(null);
    try {
      const next = await editLine(detail.id, line.id, text);
      setSaved({ base: detail, doc: next });
      setEditingId(null);
      onChanged();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Não foi possível salvar a linha agora.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="application-step" aria-labelledby="resume-title">
      <h2 id="resume-title" className="application-step__title">
        Currículo
      </h2>
      {cov ? (
        <p className="application-coverage">
          {countText(cov.must)} obrigatórios · {countText(cov.nice)} desejáveis
          <span className="muted"> (contagem aproximada)</span>
        </p>
      ) : null}

      {needsFix ? (
        <div role="status" className="application-warning">
          Corrija as linhas marcadas para liberar o PDF.
        </div>
      ) : null}

      <div className="application-settings">
        <TemplatePicker value={detail.template} disabled={busy} onChange={changeTemplate} />
        {settingsError ? (
          <p role="alert" className="application-error">
            {settingsError}
          </p>
        ) : null}
        {detail.template === "au" ? (
          <div className="application-referees">
            <label>
              Referências (digitadas por você)
              <textarea
                value={referees}
                maxLength={1000}
                rows={2}
                onChange={(e) => setReferees(e.target.value)}
              />
            </label>
            <button type="button" className="button button--secondary" disabled={busy} onClick={saveReferees}>
              Salvar referências
            </button>
          </div>
        ) : null}
      </div>

      <div className="application-downloads">
        <button
          type="button"
          className="button"
          disabled={needsFix}
          title={needsFix ? BLOCKED_REASON : undefined}
          onClick={() => download("pdf")}
        >
          Baixar PDF
        </button>
        <button
          type="button"
          className="button button--secondary"
          disabled={needsFix}
          title={needsFix ? BLOCKED_REASON : undefined}
          onClick={() => download("txt")}
        >
          Baixar texto
        </button>
        {needsFix ? <p className="muted">{BLOCKED_REASON}</p> : null}
        {downloadError ? (
          <p role="alert" className="application-error">
            {downloadError}
          </p>
        ) : null}
      </div>

      <ol className="application-lines">
        {lines.map((line) => {
          const editing = editingId === line.id;
          const header = line.section === "experience" || line.section === "project";
          return (
            <li key={line.id} className="application-line" data-section={line.section}>
              <span className="application-line__origin muted">{originLabel(line, lines)}</span>
              {header ? (
                <p className="application-line__header">{line.text}</p>
              ) : (
                <p className="application-line__text">
                  {!header && SECTION_LABEL[line.section] && line.section !== "bullet" ? (
                    <span className="application-line__section">{SECTION_LABEL[line.section]}: </span>
                  ) : null}
                  {line.text}
                </p>
              )}

              {line.violations.length > 0 ? (
                <ul role="alert" className="application-line__violations">
                  {line.violations.map((v) => (
                    <li key={v}>{v}</li>
                  ))}
                </ul>
              ) : null}

              {line.origin === "fact" ? (
                <p className="muted application-line__hint">Para mudar este dado, edite o perfil.</p>
              ) : editing ? (
                <LineEditor
                  initial={line.text}
                  busy={busy}
                  error={error}
                  onSave={(text) => save(line, text)}
                  onCancel={() => {
                    setEditingId(null);
                    setError(null);
                  }}
                />
              ) : (
                <button
                  type="button"
                  className="link-button"
                  onClick={() => {
                    setEditingId(line.id);
                    setError(null);
                  }}
                >
                  Editar
                </button>
              )}
            </li>
          );
        })}
      </ol>

      {gaps.length > 0 ? (
        <section className="application-gaps" aria-labelledby="gaps-title">
          <h3 id="gaps-title">Não encontrei no seu perfil</h3>
          <ul className="application-list">
            {gaps.map((g) => (
              <li key={g.id}>{g.text}</li>
            ))}
          </ul>
        </section>
      ) : null}
    </section>
  );
}
