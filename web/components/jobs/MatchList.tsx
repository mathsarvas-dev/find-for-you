import type { MatchItem } from "@/lib/matching";
import { MatchRow } from "./MatchRow";

type MatchListProps = {
  items: MatchItem[];
  selectedId: number | null;
  onSelect: (jobId: number) => void;
};

/** Lista na ordem da API (nota da IA, depois vetorial). */
export function MatchList({ items, selectedId, onSelect }: MatchListProps) {
  return (
    <ol className="match-list" aria-label="Vagas ordenadas por nota">
      {items.map((item) => (
        <MatchRow
          key={item.job.id}
          item={item}
          selected={item.job.id === selectedId}
          onSelect={onSelect}
        />
      ))}
    </ol>
  );
}
