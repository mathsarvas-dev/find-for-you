"use client";

import Link from "next/link";
import { useOverview } from "@/components/shell/OverviewContext";
import type { InterviewState, Overview } from "@/lib/overview";

const num = new Intl.NumberFormat("pt-BR");

const asText = (value: unknown): string => (typeof value === "string" ? value.trim() : "");

const INTERVIEW_LABEL: Record<InterviewState, string> = {
  none: "Não iniciada",
  in_progress: "Em andamento",
  done: "Concluída",
};

/** Há perfil (rascunho ou confirmado) para a entrevista partir. */
function hasProfile(profile: Overview["profile"]): boolean {
  return profile.confirmed || profile.version !== null;
}

function profileLabel(profile: Overview["profile"]): string {
  if (!hasProfile(profile)) return "Nenhum perfil ainda";
  const state = profile.confirmed ? "confirmado" : "não confirmado";
  return profile.version !== null ? `Versão ${num.format(profile.version)} · ${state}` : "Confirmado";
}

/** Mesmas regras do menu do avatar: só texto, e só do perfil confirmado. */
function identity(overview: Overview | null) {
  const profile = overview?.profile;
  const confirmed = profile?.confirmed === true;
  const name = asText(profile?.name);
  return {
    name: confirmed && name ? name : "Você",
    detail: confirmed ? asText(profile?.email) || "Sem e-mail no perfil" : "Perfil não confirmado",
  };
}

/**
 * Seção #conta de Configurações. Lê o overview da casca (não busca nada sozinha).
 * Nome e e-mail entram como texto e são cortados pelo CSS; o valor inteiro fica no title.
 */
export function AccountSection() {
  const { overview, status } = useOverview();
  const loading = overview === null && status === "loading";
  const who = identity(overview);
  const interview = overview?.interview.state;
  const showInterview =
    overview !== null && hasProfile(overview.profile) && interview !== "done";

  return (
    <section
      id="conta"
      className="panel settings__section"
      aria-labelledby="conta-titulo"
      aria-busy={loading || undefined}
    >
      <div className="panel__head">
        <h2 id="conta-titulo">Conta e perfil</h2>
      </div>

      <div className="panel__body settings__account">
        {loading ? (
          <>
            <span className="sr-only">Carregando o perfil…</span>
            <div className="settings__who" aria-hidden="true">
              <span className="skeleton" />
              <span className="skeleton skeleton--short" />
            </div>
          </>
        ) : (
          <>
            <div className="settings__who">
              <p className="settings__value settings__name" title={who.name}>
                {who.name}
              </p>
              <p className="settings__value settings__detail" title={who.detail}>
                {who.detail}
              </p>
            </div>
            <dl className="settings__facts">
              <div>
                <dt>Perfil</dt>
                <dd>{overview ? profileLabel(overview.profile) : "—"}</dd>
              </div>
              <div>
                <dt>Entrevista</dt>
                <dd>
                  {overview && interview && Object.hasOwn(INTERVIEW_LABEL, interview)
                    ? INTERVIEW_LABEL[interview]
                    : "—"}
                </dd>
              </div>
            </dl>
          </>
        )}

        {status === "down" && (
          <p className="settings__note" role="status">
            {overview === null
              ? "Não consegui ler o seu perfil agora."
              : "Não consegui atualizar agora; estes são os últimos dados recebidos."}{" "}
            <a href="#sistema">Ver estado do sistema</a>
          </p>
        )}
      </div>

      <div className="panel__foot settings__actions">
        <span>O nome e o e-mail vêm do currículo do perfil confirmado.</span>
        <div className="actions">
          {showInterview && (
            <Link className="button button--secondary" href="/onboarding/interview">
              {interview === "none" ? "Começar entrevista" : "Continuar entrevista"}
            </Link>
          )}
          <Link className="button" href="/onboarding">
            Editar perfil
          </Link>
        </div>
      </div>
    </section>
  );
}
