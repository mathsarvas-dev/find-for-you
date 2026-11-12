import { ApiError, request } from "@/lib/profile";

/* Tipos espelhando api/interview.py e rumo_core/interview/suggestions.py */

export type InterviewState = "generating" | "working" | "awaiting_answer" | "done" | "error";

export type Message = {
  id: number;
  role: "assistant" | "user";
  kind: "question" | "answer" | "skip" | "info";
  content: string;
  gap_id: string | null;
};

export type SuggestionOp =
  | "append_achievement"
  | "set_experience_description"
  | "set_experience_dates"
  | "set_summary"
  | "add_skill"
  | "set_preference"
  | "add_note";

/** SuggestionDraft: campos ausentes valem "" no servidor. */
export type SuggestionPayload = {
  op: SuggestionOp;
  target_ref?: string | null;
  text?: string;
  start?: string;
  end?: string;
  name?: string;
  level?: string | null;
  key?: string;
  value?: string;
};

export type SuggestionStatus = "pending" | "accepted" | "adjusted" | "rejected";

export type Suggestion = {
  id: number;
  message_id: number;
  gap_id: string;
  op: SuggestionOp;
  payload: SuggestionPayload;
  status: SuggestionStatus;
  applied_version: number | null;
  before: string;
  after: string;
};

export type Interview = {
  id: number;
  state: InterviewState;
  error: string | null;
  progress: { asked: number; max: number };
  messages: Message[];
  suggestions: Suggestion[];
};

export type ResolveAction = "accept" | "adjust" | "reject";

/** Estados em que o worker está trabalhando e a página consulta de novo. */
export const BUSY_STATES: ReadonlySet<InterviewState> = new Set(["generating", "working"]);

export const ITEM_CHANGED = "O item mudou; descarte esta sugestão";

/* Cliente */

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, {
    method: "POST",
    ...(body === undefined
      ? {}
      : { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
  }).then((r) => r.body);

/** Abre a entrevista ativa (200) ou uma nova (201). 409 quando não há perfil. */
export function startInterview(): Promise<{ id: number; state: InterviewState }> {
  return post("/interview");
}

/** Entrevista atual (ativa ou a última encerrada), ou null se nunca houve uma. */
export async function getInterview(): Promise<Interview | null> {
  try {
    return (await request<Interview>("/interview/current")).body;
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return null;
    throw err;
  }
}

type StateOut = { state: InterviewState };

export function answerInterview(id: number, text: string): Promise<StateOut> {
  return post(`/interview/${id}/answer`, { text });
}

export function skipQuestion(id: number): Promise<StateOut> {
  return post(`/interview/${id}/skip`);
}

export function finishInterview(id: number): Promise<StateOut> {
  return post(`/interview/${id}/finish`);
}

export function retryInterview(id: number): Promise<StateOut> {
  return post(`/interview/${id}/retry`);
}

export async function resolveSuggestion(
  id: number,
  action: ResolveAction,
  payload?: SuggestionPayload,
): Promise<{ status: SuggestionStatus; applied_version: number | null }> {
  try {
    return await post(
      `/interview/suggestions/${id}/resolve`,
      payload === undefined ? { action } : { action, payload },
    );
  } catch (err) {
    // 409 aqui: o item do perfil mudou (ou a sugestão já foi resolvida em outra aba).
    if (err instanceof ApiError && err.status === 409) throw new ApiError(409, ITEM_CHANGED);
    throw err;
  }
}

/* Apresentação das sugestões */

export const OP_LABELS: Record<SuggestionOp, string> = {
  append_achievement: "Nova conquista",
  set_experience_description: "Descrição da experiência",
  set_experience_dates: "Datas da experiência",
  set_summary: "Resumo",
  add_skill: "Nova habilidade",
  set_preference: "Preferência",
  add_note: "Nota",
};

export type EditableField = {
  key: "text" | "name" | "value" | "start" | "end";
  label: string;
  multiline: boolean;
};

/** O campo de texto que "Ajustar" deixa editar, por tipo de sugestão. */
export function editableFields(op: SuggestionOp): EditableField[] {
  switch (op) {
    case "append_achievement":
      return [{ key: "text", label: "Texto da conquista", multiline: true }];
    case "set_experience_description":
      return [{ key: "text", label: "Descrição", multiline: true }];
    case "set_summary":
      return [{ key: "text", label: "Resumo", multiline: true }];
    case "add_note":
      return [{ key: "text", label: "Nota", multiline: true }];
    case "add_skill":
      return [{ key: "name", label: "Habilidade", multiline: false }];
    case "set_preference":
      return [{ key: "value", label: "Valor", multiline: false }];
    case "set_experience_dates":
      return [
        { key: "start", label: "Início", multiline: false },
        { key: "end", label: "Fim", multiline: false },
      ];
  }
}
