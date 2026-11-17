import type { ApplicationDetail, Counts } from "@/lib/applications";

type Props = { detail: ApplicationDetail };

function countText(c: Counts): string {
  return `${c.covered} de ${c.total}`;
}

/** Passo "Ler a vaga": requisitos como texto e a cobertura declarada. */
export function RequirementsStep({ detail }: Props) {
  const req = detail.requirements;
  const cov = detail.coverage;
  return (
    <section className="application-step" aria-labelledby="requirements-title">
      <h2 id="requirements-title" className="application-step__title">
        Requisitos da vaga
      </h2>
      {cov ? (
        <p className="application-coverage">
          {countText(cov.must)} obrigatórios · {countText(cov.nice)} desejáveis
          <span className="muted"> (contagem aproximada)</span>
        </p>
      ) : null}
      {req ? (
        <>
          {req.must.length > 0 ? (
            <>
              <h3>Obrigatórios</h3>
              <ul className="application-list">
                {req.must.map((r) => (
                  <li key={r.id}>{r.text}</li>
                ))}
              </ul>
            </>
          ) : null}
          {req.nice.length > 0 ? (
            <>
              <h3>Desejáveis</h3>
              <ul className="application-list">
                {req.nice.map((r) => (
                  <li key={r.id}>{r.text}</li>
                ))}
              </ul>
            </>
          ) : null}
          {req.keywords.length > 0 ? (
            <>
              <h3>Palavras-chave</h3>
              <p className="application-keywords">{req.keywords.join(", ")}</p>
            </>
          ) : null}
        </>
      ) : (
        <p className="muted">Os requisitos aparecem quando o currículo é gerado.</p>
      )}
    </section>
  );
}
