import {
  formatSalary,
  languageLabel,
  seniorityLabel,
  type MatchDetail,
} from "@/lib/matching";
import { relativeTime, safeJobUrl, workModelLabel } from "@/lib/sources";
import { ScoreBar } from "./ScoreBar";

export type PanelState =
  | { name: "empty" }
  | { name: "loading" }
  | { name: "error"; message: string }
  | { name: "ready"; detail: MatchDetail };

type MatchPanelProps = {
  state: PanelState;
  /** Trechos do perfil usados na nota vetorial (da listagem). */
  chunksUsed: number;
  /** Relógio do cliente; 0 antes de montar. */
  now: number;
  onClose: () => void;
  onRetry: () => void;
  /** Abre a candidatura da vaga (tecla p ou botão). Sem a prop, o botão não aparece. */
  onPrepare?: () => void;
  preparing?: boolean;
  prepareError?: string | null;
};

/** Painel lateral (não modal) com a avaliação da vaga selecionada. Tudo como texto. */
export function MatchPanel({
  state,
  chunksUsed,
  now,
  onClose,
  onRetry,
  onPrepare,
  preparing = false,
  prepareError = null,
}: MatchPanelProps) {
  if (state.name === "empty") {
    return (
      <div className="match-panel__empty">
        <p>Escolha uma vaga na lista para ver a justificativa da nota, os pontos fortes e as lacunas.</p>
        <p className="muted">
          Atalhos: <kbd>j</kbd> próxima, <kbd>k</kbd> anterior, <kbd>p</kbd> preparar candidatura.
        </p>
      </div>
    );
  }

  const close = (
    <button type="button" className="link-button match-panel__close" aria-label="Fechar detalhes" onClick={onClose}>
      Fechar
    </button>
  );

  if (state.name === "loading") {
    return (
      <div className="match-panel__body" aria-busy="true" aria-label="Carregando detalhes">
        <div className="match-panel__top">
          <span className="skeleton" aria-hidden="true" />
          {close}
        </div>
        <span className="skeleton skeleton--short" aria-hidden="true" />
      </div>
    );
  }

  if (state.name === "error") {
    return (
      <div className="match-panel__body">
        <div className="match-panel__top">
          <strong>Detalhes indisponíveis</strong>
          {close}
        </div>
        <div className="notice notice--flush" role="alert">
          <strong>Não deu para abrir esta vaga.</strong>
          <p>{state.message}</p>
        </div>
        <div className="actions">
          <button type="button" className="button button--secondary" onClick={onRetry}>
            Tentar de novo
          </button>
        </div>
      </div>
    );
  }

  const d = state.detail;
  const href = safeJobUrl(d.url);
  const salary = formatSalary(d);
  const unknown = new Set(d.unknown_fields);
  const where = [d.company_name, d.location_text].filter(Boolean).join(" · ");
  const strengths = d.strengths ?? [];
  const gaps = d.gaps ?? [];

  return (
    <div className="match-panel__body">
      <div className="match-panel__top">
        <div className="match-panel__heading">
          <h2 className="match-panel__title">{d.title}</h2>
          <p className="match-panel__where">
            {where}
            {d.posted_at && now > 0 && (
              <span className="mono"> · publicada {relativeTime(d.posted_at, now)}</span>
            )}
          </p>
        </div>
        {close}
      </div>

      <div className="match-panel__score">
        <ScoreBar aiScore={d.ai_score} vecScore={d.vec_score} size="panel" />
        {d.ai_score !== null && d.vec_score !== null && (
          <span className="mono muted">vetorial {Math.round(d.vec_score * 100)}</span>
        )}
      </div>

      {d.state === "pendente" && (
        <p className="match-panel__state">
          Na fila da IA. A nota e a justificativa aparecem aqui quando o worker terminar.
        </p>
      )}
      {d.state === "erro" && (
        <div className="match-panel__state" data-state="erro">
          {/* d.error holds an internal exception class name: never shown */}
          <p>Não foi possível avaliar esta vaga.</p>
          <p className="match-panel__error">
            Tente “Avaliar melhores vagas” de novo mais tarde.
          </p>
        </div>
      )}
      {d.state === null && (
        <p className="match-panel__state">
          Ainda sem avaliação da IA. “Avaliar melhores vagas” manda para a fila as 30 melhores pelos
          filtros atuais.
        </p>
      )}

      {d.rationale && (
        <section className="match-panel__section">
          <h3>Justificativa</h3>
          <p className="match-panel__text">{d.rationale}</p>
        </section>
      )}
      {strengths.length > 0 && (
        <section className="match-panel__section">
          <h3>Pontos fortes</h3>
          <ul className="match-panel__list" aria-label="Pontos fortes">
            {strengths.map((s, i) => (
              <li key={i}>{s}</li>
            ))}
          </ul>
        </section>
      )}
      {gaps.length > 0 && (
        <section className="match-panel__section">
          <h3>Lacunas</h3>
          <ul className="match-panel__list" data-kind="gaps" aria-label="Lacunas">
            {gaps.map((s, i) => (
              <li key={i}>{s}</li>
            ))}
          </ul>
        </section>
      )}

      <dl className="match-panel__facts">
        <div>
          <dt>Modelo</dt>
          <dd>{unknown.has("work_model") ? "desconhecido" : workModelLabel(d.work_model)}</dd>
        </div>
        <div>
          <dt>Senioridade</dt>
          <dd>{unknown.has("seniority") ? "desconhecida" : seniorityLabel(d.seniority)}</dd>
        </div>
        <div>
          <dt>Idioma</dt>
          <dd>{unknown.has("language") ? "desconhecido" : languageLabel(d.language)}</dd>
        </div>
        <div>
          <dt>Departamento</dt>
          <dd>{d.department || "—"}</dd>
        </div>
        <div>
          <dt>Salário</dt>
          <dd className={salary ? "mono" : undefined}>{salary ?? "não informado"}</dd>
        </div>
        <div>
          <dt>País</dt>
          <dd className="mono">{d.country ?? "—"}</dd>
        </div>
      </dl>

      {onPrepare ? (
        <div className="actions">
          <button type="button" className="button" disabled={preparing} onClick={onPrepare}>
            {preparing ? "Preparando…" : "Preparar candidatura"}
          </button>
          {prepareError ? (
            <p role="alert" className="match-panel__error">
              {prepareError}
            </p>
          ) : null}
        </div>
      ) : null}

      <div className="match-panel__link">
        {href ? (
          <a href={href} target="_blank" rel="noopener noreferrer">
            Abrir vaga no site da empresa
          </a>
        ) : (
          <span className="muted">link indisponível</span>
        )}
      </div>

      {d.description && (
        <details className="match-panel__desc">
          <summary>Descrição da vaga</summary>
          <p className="match-panel__text">{d.description}</p>
        </details>
      )}

      <p className="match-panel__foot mono">
        Nota vetorial calculada com {chunksUsed} {chunksUsed === 1 ? "trecho" : "trechos"} do perfil.
        {chunksUsed > 0 && chunksUsed < 3 ? " Com menos de 3, a nota oscila mais." : ""}
      </p>
    </div>
  );
}
