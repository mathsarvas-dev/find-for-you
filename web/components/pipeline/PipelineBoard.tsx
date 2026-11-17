"use client";

import { STAGES, STAGE_LABEL, type PipelineItem, type PipelinePatch } from "@/lib/pipeline";
import { PipelineCard } from "./PipelineCard";

type Props = {
  items: PipelineItem[];
  today: string;
  onPatch: (id: number, patch: PipelinePatch) => Promise<void>;
};

/** Quadro por etapa. Mover é um seletor em cada cartão (teclado e leitor de tela), sem arrastar. */
export function PipelineBoard({ items, today, onPatch }: Props) {
  return (
    <div className="pipeline-board">
      {STAGES.map((stage) => {
        const inStage = items.filter((i) => i.stage === stage);
        const headId = `pipeline-col-${stage}`;
        return (
          <section key={stage} className="pipeline-col" aria-labelledby={headId}>
            <h2 id={headId} className="pipeline-col__title">
              {STAGE_LABEL[stage]} <span className="pipeline-col__count">({inStage.length})</span>
            </h2>
            {inStage.length === 0 ? (
              <p className="muted pipeline-col__empty">Nenhuma vaga.</p>
            ) : (
              <ul className="pipeline-col__list">
                {inStage.map((item) => (
                  <PipelineCard key={item.id} item={item} today={today} onPatch={onPatch} />
                ))}
              </ul>
            )}
          </section>
        );
      })}
    </div>
  );
}
