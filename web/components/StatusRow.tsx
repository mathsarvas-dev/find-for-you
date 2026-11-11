export type StatusState = "ok" | "down" | "loading" | "unknown";

const STATE_TEXT: Record<StatusState, string> = {
  ok: "Funcionando",
  down: "Fora do ar",
  loading: "Verificando…",
  unknown: "Sem resposta",
};

type StatusRowProps = {
  label: string;
  state: StatusState;
  /** Linha técnica curta, em mono (ex.: "GET /health · 200 · 38 ms"). */
  detail?: string;
};

/**
 * Uma linha da lista de serviços. O estado aparece sempre como texto;
 * a cor do marcador só reforça o que o texto já diz.
 */
export function StatusRow({ label, state, detail }: StatusRowProps) {
  const loading = state === "loading";
  return (
    <li className="status-row" data-state={state} aria-busy={loading || undefined}>
      <span className="status-row__label">{label}</span>
      <span className="status-row__state">
        <span className="status-row__mark" aria-hidden="true" />
        {STATE_TEXT[state]}
      </span>
      <span className="status-row__detail">
        {loading ? <span className="skeleton" aria-hidden="true" /> : (detail ?? "—")}
      </span>
    </li>
  );
}
