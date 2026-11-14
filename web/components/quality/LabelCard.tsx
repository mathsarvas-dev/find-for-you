"use client";

import { useEffect, useRef } from "react";
import type { QualityLabel, SampleItem } from "@/lib/quality";

type Props = {
  item: SampleItem;
  onLabel: (label: QualityLabel) => void;
  onSkip: () => void;
  onPrevious: () => void;
  onClear: () => void;
  /** Uma gravação em andamento: os botões e os atalhos ficam parados. */
  busy?: boolean;
  canPrevious?: boolean;
  canSkip?: boolean;
};

/** Teclas de atalho não valem enquanto a pessoa digita. */
function isTyping(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName);
}

/** Só endereços http/https viram link; o resto aparece como texto. */
function safeUrl(raw: string | null): string | null {
  if (!raw) return null;
  try {
    const u = new URL(raw);
    return u.protocol === "http:" || u.protocol === "https:" ? u.href : null;
  } catch {
    return null;
  }
}

const LABEL_TEXT: Record<QualityLabel, string> = { good: "Boa", bad: "Ruim" };

export function LabelCard({
  item,
  onLabel,
  onSkip,
  onPrevious,
  onClear,
  busy = false,
  canPrevious = true,
  canSkip = true,
}: Props) {
  const onLabelRef = useRef(onLabel);
  const busyRef = useRef(busy);
  useEffect(() => {
    onLabelRef.current = onLabel;
    busyRef.current = busy;
  });

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.ctrlKey || e.metaKey || e.altKey || e.shiftKey || e.repeat) return;
      if (isTyping(e.target) || busyRef.current) return;
      const key = e.key.toLowerCase();
      if (key === "g") onLabelRef.current("good");
      else if (key === "b") onLabelRef.current("bad");
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const href = safeUrl(item.url);
  return (
    <article className="label-card" aria-label="Vaga para rotular">
      <header className="label-card__head">
        <h2 className="label-card__title">
          {href ? (
            <a href={href} target="_blank" rel="noopener noreferrer">
              {item.title}
            </a>
          ) : (
            item.title
          )}
        </h2>
        <p className="label-card__meta">
          {item.company_name}
          {item.country ? ` · ${item.country}` : ""}
        </p>
        {item.label && (
          <p className="label-card__current">Rótulo atual: {LABEL_TEXT[item.label]}</p>
        )}
      </header>
      <p className="label-card__snippet">{item.snippet}</p>
      <div className="label-card__actions">
        <button type="button" className="button" disabled={busy} onClick={() => onLabel("good")}>
          Boa
        </button>
        <button type="button" className="button" disabled={busy} onClick={() => onLabel("bad")}>
          Ruim
        </button>
        <button
          type="button"
          className="button button--secondary"
          disabled={busy || !canSkip}
          onClick={onSkip}
        >
          Pular
        </button>
        <button
          type="button"
          className="button button--secondary"
          disabled={busy || !canPrevious}
          onClick={onPrevious}
        >
          Anterior
        </button>
        {item.label && (
          <button type="button" className="button button--secondary" disabled={busy} onClick={onClear}>
            Remover rótulo
          </button>
        )}
      </div>
      <p className="label-card__hint muted">
        Atalhos: <kbd>g</kbd> boa, <kbd>b</kbd> ruim.
      </p>
    </article>
  );
}
