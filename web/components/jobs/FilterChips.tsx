"use client";

import { useEffect, useRef, useState } from "react";
import { filterChips, type Chip } from "@/lib/chatFilters";
import type { SearchFilters } from "@/lib/matching";

/** Mesmo limite da API para a consulta semântica. */
export const QUERY_MAX = 300;

const CHIP_CLASS: Record<Chip["kind"], string> = {
  include: "chip--include",
  exclude: "chip--exclude",
  query: "chip--query",
  salary: "chip--include",
};

/** Ícone por tipo: o estado nunca fica só na cor (a exclusão também diz "sem …"). */
export function ChipIcon({ kind }: { kind: Chip["kind"] }) {
  return (
    <svg className="filter-chip__icon" viewBox="0 0 12 12" width="12" height="12" aria-hidden="true">
      {kind === "exclude" ? (
        <>
          <circle cx="6" cy="6" r="4.5" fill="none" stroke="currentColor" strokeWidth="1.5" />
          <path d="M2.8 9.2 9.2 2.8" stroke="currentColor" strokeWidth="1.5" />
        </>
      ) : kind === "query" ? (
        <path d="M6 1.5 7.2 4.8 10.5 6 7.2 7.2 6 10.5 4.8 7.2 1.5 6 4.8 4.8Z" fill="currentColor" />
      ) : (
        <path d="M2.5 6.2 5 8.7 9.5 3.5" fill="none" stroke="currentColor" strokeWidth="1.5" />
      )}
    </svg>
  );
}

/** Chip só para leitura (a proposta do chat usa o mesmo visual). */
export function ChipView({ chip }: { chip: Chip }) {
  return (
    <li className={`filter-chip ${CHIP_CLASS[chip.kind]}`}>
      <ChipIcon kind={chip.kind} />
      <span className="filter-chip__label">{chip.label}</span>
    </li>
  );
}

type Props = {
  filters: SearchFilters;
  queryText: string | null;
  queryPending: boolean;
  onRemove: (chip: Chip) => void;
  /** Texto novo da consulta; null remove. */
  onEditQuery: (text: string | null) => void;
};

/** Um chip por valor dos filtros salvos; "×" remove, o da consulta se edita no lugar. */
export function FilterChips({ filters, queryText, queryPending, onRemove, onEditQuery }: Props) {
  const chips = filterChips(filters, queryText);
  if (chips.length === 0 && !queryPending) return null;

  return (
    <div className="filter-chips">
      {chips.length > 0 && (
        <ul className="filter-chips__list" aria-label="Filtros ativos">
          {chips.map((chip) =>
            chip.kind === "query" ? (
              <QueryChip key={chip.key} chip={chip} onRemove={onRemove} onEdit={onEditQuery} />
            ) : (
              <li key={chip.key} className={`filter-chip ${CHIP_CLASS[chip.kind]}`}>
                <ChipIcon kind={chip.kind} />
                <span className="filter-chip__label">{chip.label}</span>
                <RemoveButton chip={chip} onRemove={onRemove} />
              </li>
            ),
          )}
        </ul>
      )}
      {queryPending && (
        <span className="filter-chips__pending" role="status">
          consulta semântica pendente
        </span>
      )}
    </div>
  );
}

function RemoveButton({ chip, onRemove }: { chip: Chip; onRemove: (chip: Chip) => void }) {
  return (
    <button
      type="button"
      className="filter-chip__remove"
      aria-label={`Remover filtro ${chip.label}`}
      onClick={() => onRemove(chip)}
    >
      <span aria-hidden="true">×</span>
    </button>
  );
}

function QueryChip({
  chip,
  onRemove,
  onEdit,
}: {
  chip: Chip;
  onRemove: (chip: Chip) => void;
  onEdit: (text: string | null) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(chip.value);
  const inputRef = useRef<HTMLInputElement>(null);
  const editRef = useRef<HTMLButtonElement>(null);
  /** true: ao fechar a edição (Enter/Escape), o foco volta ao chip. */
  const refocus = useRef(false);

  useEffect(() => {
    if (editing) {
      inputRef.current?.focus();
      inputRef.current?.select();
    } else if (refocus.current) {
      refocus.current = false;
      editRef.current?.focus();
    }
  }, [editing]);

  function open() {
    setText(chip.value);
    setEditing(true);
  }

  function close() {
    refocus.current = true;
    setEditing(false);
  }

  function save() {
    const next = text.trim();
    close();
    if (next === chip.value.trim()) return;
    onEdit(next === "" ? null : next);
  }

  return (
    <li className={`filter-chip ${CHIP_CLASS.query}`}>
      <ChipIcon kind="query" />
      {editing ? (
        <input
          ref={inputRef}
          className="filter-chip__input"
          type="text"
          aria-label="Editar consulta"
          autoComplete="off"
          maxLength={QUERY_MAX}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              save();
            } else if (e.key === "Escape") {
              e.preventDefault();
              e.stopPropagation();
              close();
            }
          }}
          onBlur={() => {
            if (!refocus.current) setEditing(false);
          }}
        />
      ) : (
        <button ref={editRef} type="button" className="filter-chip__edit" onClick={open}>
          <span className="filter-chip__label">{chip.label}</span>
          <span className="sr-only"> (editar)</span>
        </button>
      )}
      <RemoveButton chip={chip} onRemove={onRemove} />
    </li>
  );
}
