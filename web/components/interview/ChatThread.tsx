import type { Message, ResolveAction, Suggestion, SuggestionPayload } from "@/lib/interview";
import { SuggestionCard } from "./SuggestionCard";

type ChatThreadProps = {
  messages: Message[];
  suggestions: Suggestion[];
  onResolve: (id: number, action: ResolveAction, payload?: SuggestionPayload) => Promise<void> | void;
};

function who(m: Message): string {
  if (m.role === "assistant") return m.kind === "question" ? "Pergunta" : "Rumo";
  return m.kind === "skip" ? "Pergunta pulada" : "Sua resposta";
}

/**
 * Conversa até aqui. Cada sugestão aparece logo abaixo da resposta que a gerou
 * (suggestion.message_id aponta para a mensagem da resposta).
 */
export function ChatThread({ messages, suggestions, onResolve }: ChatThreadProps) {
  const ids = new Set(messages.map((m) => m.id));
  const orphans = suggestions.filter((s) => !ids.has(s.message_id));

  const cards = (list: Suggestion[]) =>
    list.length > 0 && (
      <div className="thread__suggestions">
        {list.map((s) => (
          <SuggestionCard
            key={s.id}
            suggestion={s}
            onResolve={(action, payload) => onResolve(s.id, action, payload)}
          />
        ))}
      </div>
    );

  return (
    <ol className="thread" aria-label="Conversa até aqui">
      {messages.map((m) => (
        <li key={m.id} className="thread__item" data-role={m.role} data-kind={m.kind}>
          <p className="thread__who">{who(m)}</p>
          {m.kind !== "skip" && <p className="thread__text">{m.content}</p>}
          {cards(suggestions.filter((s) => s.message_id === m.id))}
        </li>
      ))}
      {orphans.length > 0 && (
        <li className="thread__item" data-role="assistant">
          <p className="thread__who">Sugestões</p>
          {cards(orphans)}
        </li>
      )}
    </ol>
  );
}
