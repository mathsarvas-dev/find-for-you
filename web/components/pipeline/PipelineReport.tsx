import type { Report, ReportGroup } from "@/lib/pipeline";

const pct = new Intl.NumberFormat("pt-BR", { style: "percent", maximumFractionDigits: 0 });
const num = new Intl.NumberFormat("pt-BR");

const BAND_LABEL: Record<string, string> = {
  "80_100": "Nota 80 a 100",
  "60_79": "Nota 60 a 79",
  "0_59": "Nota 0 a 59",
  none: "Sem nota",
};

function rateText(rate: number | null): string {
  return rate === null ? "—" : pct.format(rate);
}

function GroupTable({
  id,
  title,
  groups,
  label,
}: {
  id: string;
  title: string;
  groups: ReportGroup[];
  label?: (key: string) => string;
}) {
  return (
    <section className="pipeline-report__group" aria-labelledby={`rep-${id}`}>
      <h3 id={`rep-${id}`}>{title}</h3>
      {groups.length === 0 ? (
        <p className="muted">Sem candidaturas aplicadas ainda.</p>
      ) : (
        <table className="pipeline-report__table">
          <thead>
            <tr>
              <th scope="col">Grupo</th>
              <th scope="col">Aplicadas</th>
              <th scope="col">Respondidas</th>
              <th scope="col">Taxa de resposta</th>
            </tr>
          </thead>
          <tbody>
            {groups.map((g) => (
              <tr key={g.key}>
                <th scope="row">{label ? label(g.key) : g.key === "unknown" ? "Desconhecido" : g.key}</th>
                <td>{num.format(g.applied)}</td>
                <td>{num.format(g.answered)}</td>
                <td>{rateText(g.rate)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

/** Relatório em números e tabelas de texto (sem gráficos que dependam de cor). */
export function PipelineReport({ report }: { report: Report }) {
  return (
    <div className="pipeline-report">
      <dl className="pipeline-report__totals">
        <div>
          <dt>Vagas encontradas</dt>
          <dd>{num.format(report.found)}</dd>
        </div>
        <div>
          <dt>Aplicadas</dt>
          <dd>{num.format(report.applied)}</dd>
        </div>
        <div>
          <dt>Respondidas</dt>
          <dd>{num.format(report.answered)}</dd>
        </div>
        <div>
          <dt>Taxa de resposta</dt>
          <dd>{rateText(report.response_rate)}</dd>
        </div>
      </dl>
      <p className="muted">Respondida é entrevista, oferta ou recusa; &quot;sem resposta&quot; não conta.</p>
      <GroupTable id="country" title="Por país" groups={report.by_country} />
      <GroupTable id="source" title="Por fonte" groups={report.by_source} />
      <GroupTable id="band" title="Por faixa de nota" groups={report.by_score_band} label={(k) => BAND_LABEL[k] ?? k} />
    </div>
  );
}
