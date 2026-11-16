import { request } from "@/lib/profile";

/* Tipos espelhando api/portals.py. */

export type PortalStatus = "allowed" | "conditional" | "blocked";

export type Portal = {
  id: string;
  name: string;
  kind: "portal" | "feed";
  status: PortalStatus;
  /** Data da verificação, "AAAA-MM-DD". */
  checked_at: string;
  evidence_urls: string[];
  evidence_summary: string;
  enabled: boolean;
  blocked_by_site: boolean;
  blocked_at: string | null;
  /** Só portais condicionais podem ser ligados ou desligados. */
  can_toggle: boolean;
};

export async function listPortals(): Promise<Portal[]> {
  return (await request<Portal[]>("/portals")).body;
}

export async function setPortalEnabled(id: string, enabled: boolean): Promise<Portal> {
  return (
    await request<Portal>(`/portals/${encodeURIComponent(id)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    })
  ).body;
}

const STATUS_LABEL: Record<PortalStatus, string> = {
  allowed: "Permitido",
  conditional: "Condicional",
  blocked: "Bloqueado",
};

/** Status em texto (nunca só cor). Valor desconhecido é tratado como bloqueado. */
export function portalStatusLabel(status: PortalStatus): string {
  return STATUS_LABEL[status] ?? STATUS_LABEL.blocked;
}
