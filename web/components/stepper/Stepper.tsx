import Link from "next/link";
import type { Step, StepState } from "./computeSteps";

/** Texto oculto por estado: o estado nunca depende só de cor. */
const STATE_TEXT: Record<StepState, string | null> = {
  done: "concluído",
  current: "passo atual",
  optional: "opcional",
  blocked: "bloqueado",
  upcoming: null,
};

type Props = {
  steps: Step[];
  orientation: "vertical" | "horizontal";
  /** Substitui o texto compacto da variante horizontal (< 560 px). */
  compactLabel?: string;
  /** Nome acessível da lista. */
  label?: string;
};

function compactText(steps: Step[]): string | null {
  if (steps.length === 0) return null;
  let index = steps.findIndex((s) => s.state === "current");
  if (index === -1) {
    // Sem atual: o último concluído (ou o primeiro passo).
    const lastDone = steps.map((s) => s.state).lastIndexOf("done");
    index = Math.max(0, lastDone);
  }
  return `Passo ${index + 1} de ${steps.length} · ${steps[index].title}`;
}

/**
 * Trilha de passos. A variante horizontal vira "Passo N de M · título" abaixo de
 * 560 px só por CSS (.stepper__compact), sem ler a janela no render.
 */
export function Stepper({ steps, orientation, compactLabel, label = "Passos" }: Props) {
  const compact = orientation === "horizontal" ? compactLabel ?? compactText(steps) : null;

  return (
    <div className={`stepper-wrap stepper-wrap--${orientation}`}>
      {compact !== null && <p className="stepper__compact">{compact}</p>}
      <ol className={`stepper stepper--${orientation}`} aria-label={label}>
        {steps.map((step, i) => {
          const stateText = STATE_TEXT[step.state];
          return (
            <li
              key={step.id}
              className={`stepper__step stepper__step--${step.state}`}
              aria-current={step.state === "current" ? "step" : undefined}
            >
              <span className="stepper__marker" aria-hidden="true">
                {step.state === "done" ? "✓" : i + 1}
              </span>
              <div className="stepper__body">
                <span className="stepper__title">
                  {step.title}
                  {stateText && <span className="sr-only"> ({stateText})</span>}
                </span>
                <span className="stepper__detail">{step.detail}</span>
                {step.state === "blocked" && step.blockedReason && (
                  <span className="stepper__reason">{step.blockedReason}</span>
                )}
                {step.state === "current" && step.cta && (
                  <Link className="button stepper__cta" href={step.cta.href}>
                    {step.cta.label}
                  </Link>
                )}
                {step.state === "done" && step.reviewHref && (
                  <Link className="stepper__review" href={step.reviewHref}>
                    Revisar <span className="sr-only">{step.title}</span>
                  </Link>
                )}
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
