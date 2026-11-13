import { scoreLabel } from "@/lib/matching";

type ScoreBarProps = {
  aiScore: number | null;
  vecScore: number | null;
  /** "panel": versão maior para o painel de detalhes. */
  size?: "row" | "panel";
};

/**
 * Nota como elemento dominante: número tabular e barra fina de 0 a 100.
 * Sem nota da IA, mostra a vetorial em cinza com a legenda "pré-seleção".
 */
export function ScoreBar({ aiScore, vecScore, size = "row" }: ScoreBarProps) {
  const label = scoreLabel({ ai_score: aiScore, vec_score: vecScore });
  if (!label) {
    return (
      <div className="score" data-source="none" data-size={size}>
        <span className="score__value" aria-hidden="true">
          —
        </span>
        <span className="score__caption">Sem nota</span>
      </div>
    );
  }
  const spoken =
    label.source === "ai"
      ? `Nota da IA: ${label.value} de 100`
      : `Pré-seleção vetorial: ${label.value} de 100`;
  return (
    <div className="score" data-source={label.source} data-size={size}>
      <span className="sr-only">{spoken}</span>
      <span className="score__value" aria-hidden="true">
        {label.value}
      </span>
      <span className="score__bar" aria-hidden="true">
        <span className="score__fill" style={{ width: `${label.value}%` }} />
      </span>
      {label.source === "vector" && (
        <span className="score__caption" aria-hidden="true">
          pré-seleção
        </span>
      )}
    </div>
  );
}
