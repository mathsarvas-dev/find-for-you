import { request } from "@/lib/profile";

/* Tipos espelhando api/feeds.py. Nunca traz o valor de nenhuma chave. */

export type FeedAttribution = { label: string; url: string };

export type FeedParams = { keywords: string[]; country: string | null };

export type Feed = {
  id: number;
  source: string;
  params: FeedParams;
  active: boolean;
  last_run_at: string | null;
  next_run_at: string | null;
  last_error: string | null;
  jobs_seen: number;
  attribution: FeedAttribution | null;
};

export type FeedSourceInfo = {
  source: string;
  /** Nome curto da fonte ("Remotive"); o crédito é "Vagas via <label>". */
  label: string;
  needs_key: boolean;
  key_configured: boolean;
  gate_status: "allowed" | "conditional" | "blocked";
  gate_allowed: boolean;
  attribution: FeedAttribution | null;
};

export type FeedsState = { feeds: Feed[]; sources: FeedSourceInfo[] };

export const MAX_KEYWORDS = 5;
export const MAX_KEYWORD_CHARS = 60;

export async function listFeeds(): Promise<FeedsState> {
  return (await request<FeedsState>("/feeds")).body;
}

export async function addFeed(input: {
  source: string;
  params: { keywords: string[]; country?: string | null };
}): Promise<Feed> {
  return (
    await request<Feed>("/feeds", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    })
  ).body;
}

export async function deleteFeed(id: number): Promise<void> {
  await request<null>(`/feeds/${id}`, { method: "DELETE" });
}

export type KeywordsResult =
  | { ok: true; keywords: string[] }
  | { ok: false; error: string };

/** Separa palavras-chave por vírgula ou quebra de linha; vazias são descartadas. */
export function parseKeywords(text: string): KeywordsResult {
  const keywords = text
    .split(/[,\n]/)
    .map((k) => k.trim())
    .filter((k) => k.length > 0);
  if (keywords.length > MAX_KEYWORDS) {
    return { ok: false, error: "Use no máximo 5 palavras-chave." };
  }
  if (keywords.some((k) => k.length > MAX_KEYWORD_CHARS)) {
    return { ok: false, error: "Cada palavra-chave pode ter até 60 caracteres." };
  }
  return { ok: true, keywords };
}
