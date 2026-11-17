import type { SearchFilters } from "@/lib/matching";
import { request } from "@/lib/profile";

/* Tipos espelhando api/search_profiles.py. */

export type NamedProfile = {
  id: number;
  name: string;
  /** O perfil ativo é o que a página Vagas, o ranking e a coleta usam. */
  active: boolean;
  filters: SearchFilters;
  query_text: string | null;
  updated_at: string;
};

export const MAX_PROFILE_NAME = 60;
export const MAX_PROFILES = 20;

const JSON_HEADERS = { "Content-Type": "application/json" };

export async function listSearchProfiles(): Promise<NamedProfile[]> {
  return (await request<{ items: NamedProfile[] }>("/search-profiles")).body.items;
}

/** Cria uma cópia do perfil ativo (filtros e consulta); `activate` já a torna o ativo. */
export async function createSearchProfile(name: string, activate = false): Promise<NamedProfile> {
  const res = await request<NamedProfile>("/search-profiles", {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify({ name, activate }),
  });
  return res.body;
}

export async function activateSearchProfile(id: number): Promise<NamedProfile> {
  return (await request<NamedProfile>(`/search-profiles/${id}/activate`, { method: "POST" })).body;
}

export async function renameSearchProfile(id: number, name: string): Promise<NamedProfile> {
  const res = await request<NamedProfile>(`/search-profiles/${id}`, {
    method: "PATCH",
    headers: JSON_HEADERS,
    body: JSON.stringify({ name }),
  });
  return res.body;
}

export async function deleteSearchProfile(id: number): Promise<void> {
  await request<null>(`/search-profiles/${id}`, { method: "DELETE" });
}

/** Mesma regra da API: 1 a 60 caracteres, sem quebras de linha. */
export function validProfileName(raw: string): boolean {
  const name = raw.trim();
  return name.length > 0 && name.length <= MAX_PROFILE_NAME && !/[\u0000-\u001f\u007f]/.test(name);
}
