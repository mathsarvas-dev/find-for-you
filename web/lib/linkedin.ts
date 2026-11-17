import { ApiError, request } from "@/lib/profile";

/* Tipos espelhando api/linkedin.py e rumo_core/linkedin/optimizer.py. */

export type DraftText = { text: string; source_refs: string[] };
export type ExperienceDraft = { item_id: string; title: string; company: string; bullets: DraftText[] };
export type ViewNotes = { key: string; label: string; notes: string[] };
export type Priority = "high" | "medium" | "low";
export type ImprovementSection = "headline" | "about" | "experience" | "skills" | "education" | "general";
export type Improvement = { priority: Priority; section: ImprovementSection; text: string; source_refs: string[] };
export type Rejected = { section: string; reasons: string[] };

export type Optimization = {
  language: string;
  headline: DraftText | null;
  about: DraftText[];
  experiences: ExperienceDraft[];
  skills: { id: string; name: string }[];
  viewpoints: ViewNotes[];
  improvements: Improvement[];
  rejected: Rejected[];
};

export type OptimizationOut = {
  profile_version: number;
  language: string;
  /** O perfil foi confirmado de novo depois desta análise. */
  stale: boolean;
  created_at: string;
  result: Optimization;
};

export type OptimizeLanguage = "pt" | "en" | "de";

export const LANGUAGE_OPTIONS: { value: OptimizeLanguage; label: string }[] = [
  { value: "pt", label: "Português" },
  { value: "en", label: "Inglês" },
  { value: "de", label: "Alemão" },
];

export const PRIORITY_LABEL: Record<Priority, string> = { high: "Alta", medium: "Média", low: "Baixa" };

export const SECTION_LABEL: Record<ImprovementSection, string> = {
  headline: "Título",
  about: "Sobre",
  experience: "Experiência",
  skills: "Habilidades",
  education: "Formação",
  general: "Geral",
};

/** "experience:abc" vira "Experiência"; as demais seções têm nome próprio. */
export function rejectedLabel(section: string): string {
  if (section.startsWith("experience")) return "Experiência";
  return SECTION_LABEL[section as ImprovementSection] ?? section;
}

export async function getOptimization(): Promise<OptimizationOut | null> {
  return (await request<{ optimization: OptimizationOut | null }>("/linkedin/optimization")).body.optimization;
}

/** Gera (ou refaz) as sugestões. 409: perfil ausente ou provisório; 429: 5 por hora; 503: IA ocupada. */
export async function optimizeLinkedin(language: OptimizeLanguage): Promise<OptimizationOut> {
  try {
    const res = await request<{ optimization: OptimizationOut }>("/linkedin/optimization", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ language }),
    });
    return res.body.optimization;
  } catch (err) {
    if (err instanceof ApiError && err.status === 429 && err.message.startsWith("A API respondeu com erro")) {
      throw new ApiError(429, "Você usou as 5 otimizações desta hora. Tente mais tarde.");
    }
    throw err;
  }
}
