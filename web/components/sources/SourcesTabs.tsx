"use client";

import { useRef, type KeyboardEvent } from "react";

export const SOURCE_TABS = [
  { id: "empresas", label: "Empresas" },
  { id: "feeds", label: "Feeds" },
  { id: "portais", label: "Portais" },
  { id: "link", label: "Importar por link" },
] as const;

export type SourcesTab = (typeof SOURCE_TABS)[number]["id"];

export const DEFAULT_TAB: SourcesTab = "empresas";

/** Lê a aba da query `?tab=`; valor desconhecido cai na aba padrão. */
export function parseTab(value: string | null | undefined): SourcesTab {
  return SOURCE_TABS.find((t) => t.id === value)?.id ?? DEFAULT_TAB;
}

type SourcesTabsProps = {
  tab: SourcesTab;
  onChange: (tab: SourcesTab) => void;
};

/**
 * Abas de /sources. Setas mudam de aba (com foco), Home e End vão ao início e ao fim;
 * só a aba selecionada entra na ordem de tabulação.
 */
export function SourcesTabs({ tab, onChange }: SourcesTabsProps) {
  const buttons = useRef<Record<string, HTMLButtonElement | null>>({});

  function onKeyDown(e: KeyboardEvent<HTMLButtonElement>, index: number) {
    const last = SOURCE_TABS.length - 1;
    let next: number | null = null;
    if (e.key === "ArrowRight") next = index === last ? 0 : index + 1;
    else if (e.key === "ArrowLeft") next = index === 0 ? last : index - 1;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = last;
    if (next === null) return;
    e.preventDefault();
    const target = SOURCE_TABS[next];
    onChange(target.id);
    buttons.current[target.id]?.focus();
  }

  return (
    <div className="source-tabs" role="tablist" aria-label="Seções das fontes">
      {SOURCE_TABS.map((t, index) => {
        const selected = t.id === tab;
        return (
          <button
            key={t.id}
            ref={(el) => {
              buttons.current[t.id] = el;
            }}
            id={`tab-${t.id}`}
            type="button"
            role="tab"
            className="source-tab"
            aria-selected={selected}
            aria-controls={`panel-${t.id}`}
            tabIndex={selected ? 0 : -1}
            onClick={() => onChange(t.id)}
            onKeyDown={(e) => onKeyDown(e, index)}
          >
            {t.label}
          </button>
        );
      })}
    </div>
  );
}
