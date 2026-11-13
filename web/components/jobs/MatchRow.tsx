import { formatSalary, type MatchItem } from "@/lib/matching";
import { workModelLabel } from "@/lib/sources";
import { ScoreBar } from "./ScoreBar";

type MatchRowProps = {
  item: MatchItem;
  selected: boolean;
  onSelect: (jobId: number) => void;
};

const STATE_TEXT: Record<string, string> = {
  pendente: "avaliando…",
  erro: "avaliação falhou",
};

/** Uma linha densa da lista. Tudo que vem da fonte (título, empresa) é renderizado como texto. */
export function MatchRow({ item, selected, onSelect }: MatchRowProps) {
  const { job } = item;
  const unknown = new Set(item.unknown_fields);
  const salary = formatSalary(job);
  const stateText = item.state ? STATE_TEXT[item.state] : undefined;

  return (
    <li className="match" data-selected={selected || undefined}>
      <button
        type="button"
        className="match__button"
        aria-current={selected ? "true" : undefined}
        data-job-id={job.id}
        onClick={() => onSelect(job.id)}
      >
        <span className="match__main">
          <span className="match__title">{job.title}</span>
          <span className="match__meta">
            <span className="match__company">{job.company_name}</span>
            {job.country ? (
              <span className="mono">{job.country}</span>
            ) : (
              unknown.has("country") && <span className="match__unknown">país desconhecido</span>
            )}
            {unknown.has("work_model") ? (
              <span className="match__unknown">modelo desconhecido</span>
            ) : (
              <span>{workModelLabel(job.work_model)}</span>
            )}
            {salary ? (
              <span className="mono">{salary}</span>
            ) : (
              unknown.has("salary") && <span className="match__unknown">salário desconhecido</span>
            )}
            {stateText && (
              <span className="match__state" data-state={item.state}>
                {stateText}
              </span>
            )}
          </span>
        </span>
        <ScoreBar aiScore={item.ai_score} vecScore={item.vec_score} />
      </button>
    </li>
  );
}
