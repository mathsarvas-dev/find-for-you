type VectorStatusProps = {
  total: number;
  embedded: number;
  /** Passou tempo demais sem terminar: explica o motivo provável. */
  slow?: boolean;
};

/**
 * Progresso da vetorização dos trechos do perfil (o worker gera os embeddings).
 * O número em texto é a informação; a barra só reforça.
 */
export function VectorStatus({ total, embedded, slow = false }: VectorStatusProps) {
  const done = embedded >= total;
  const pct = total > 0 ? Math.min(100, Math.round((embedded / total) * 100)) : 100;
  return (
    <div className="vector" data-done={done || undefined}>
      <div className="vector__row">
        <span className="vector__label">{done ? "Trechos vetorizados" : "Vetorizando trechos do perfil"}</span>
        <span className="mono vector__count">
          {embedded} de {total}
        </span>
      </div>
      <div
        className="vector__bar"
        role="progressbar"
        aria-label="Trechos vetorizados"
        aria-valuemin={0}
        aria-valuemax={total}
        aria-valuenow={embedded}
      >
        <span style={{ width: `${pct}%` }} />
      </div>
      <p className="vector__note" aria-live="polite">
        {done
          ? "Perfil pronto. O Rumo já pode comparar suas experiências com as vagas."
          : "Cada trecho do perfil vira um vetor para a busca por semelhança. Você pode sair desta página; o trabalho continua."}
      </p>
      {slow && !done && (
        <p className="vector__note vector__note--slow">
          Está demorando: a primeira vez baixa/carrega o modelo local e pode levar alguns minutos.
        </p>
      )}
    </div>
  );
}
