import { DEFAULT_API_URL } from "@/lib/health";

/* Tipos espelhando rumo_core/profile/schema.py (CanonicalProfile).
 * Todo item de lista tem `id`; item novo vai com id "" e o backend preenche. */

export type Basics = {
  name: string;
  headline: string;
  email: string;
  phone: string;
  location: string;
  links: string[];
};

export type Achievement = { id: string; text: string };

export type Experience = {
  id: string;
  company: string;
  title: string;
  start: string;
  end: string;
  location: string;
  description: string;
  achievements: Achievement[];
};

export type Education = {
  id: string;
  institution: string;
  degree: string;
  field: string;
  start: string;
  end: string;
};

export type Skill = { id: string; name: string; level: string | null };
export type Language = { id: string; name: string; level: string };
export type Certification = { id: string; name: string; issuer: string; date: string };
export type Project = { id: string; name: string; description: string; url: string };

export type CanonicalProfile = {
  basics: Basics;
  summary: string;
  experiences: Experience[];
  education: Education[];
  skills: Skill[];
  languages: Language[];
  certifications: Certification[];
  projects: Project[];
};

export type ImportStatus = "pending" | "extracting" | "ready" | "error";

export type ImportState = {
  id: number;
  status: ImportStatus;
  error: string | null;
  truncated: boolean;
  profile: CanonicalProfile | null;
};

export type ConfirmResult = {
  version: number;
  chunks_total: number;
  chunks_embedded: number;
};

export type ProfileState = ConfirmResult & {
  profile: CanonicalProfile;
  narrative: string | null;
};

export function emptyProfile(): CanonicalProfile {
  return {
    basics: { name: "", headline: "", email: "", phone: "", location: "", links: [] },
    summary: "",
    experiences: [],
    education: [],
    skills: [],
    languages: [],
    certifications: [],
    projects: [],
  };
}

/* Cliente da API (roda no navegador) */

export function publicApiUrl(): string {
  return (process.env.NEXT_PUBLIC_API_URL || DEFAULT_API_URL).replace(/\/+$/, "");
}

/** Erro com mensagem pronta para a interface. `status` é null quando não houve resposta. */
export class ApiError extends Error {
  constructor(
    public status: number | null,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<{ status: number; body: T }> {
  const base = publicApiUrl();
  let res: Response;
  try {
    res = await fetch(`${base}${path}`, { cache: "no-store", ...init });
  } catch {
    throw new ApiError(
      null,
      `Não consegui falar com a API em ${base}. Confira se ela está no ar e tente de novo.`,
    );
  }
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = (body as { detail?: unknown } | null)?.detail;
    throw new ApiError(
      res.status,
      typeof detail === "string" && detail ? detail : `A API respondeu com erro (HTTP ${res.status}).`,
    );
  }
  return { status: res.status, body: body as T };
}

export async function uploadResume(file: File): Promise<{ id: number }> {
  const form = new FormData();
  form.append("file", file);
  const { body } = await request<{ id: number }>("/profile/imports", { method: "POST", body: form });
  return { id: body.id };
}

export async function getImport(id: number): Promise<ImportState> {
  return (await request<ImportState>(`/profile/imports/${id}`)).body;
}

export async function confirmProfile(
  profile: CanonicalProfile,
  narrative: string | null,
): Promise<ConfirmResult> {
  const text = narrative?.trim() ? narrative : null;
  const { body } = await request<ConfirmResult>("/profile/confirm", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ profile, narrative: text }),
  });
  return body;
}

/** Perfil salvo com contagens de vetorização, ou null se ainda não existe. */
export async function getProfile(): Promise<ProfileState | null> {
  try {
    return (await request<ProfileState>("/profile")).body;
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return null;
    throw err;
  }
}

/* Validação no navegador (o servidor valida de novo) */

/** Mesmo limite de `upload_max_bytes` na API (5 MB decimais). */
export const MAX_UPLOAD_BYTES = 5_000_000;

const ACCEPTED_TYPES = new Set([
  "application/pdf",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
]);

export const ACCEPT_ATTR = ".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document";

export function formatBytes(bytes: number): string {
  if (bytes < 1_000_000) return `${Math.max(1, Math.round(bytes / 1000))} KB`;
  return `${(bytes / 1_000_000).toLocaleString("pt-BR", { minimumFractionDigits: 1, maximumFractionDigits: 1 })} MB`;
}

/** Devolve a mensagem de erro em PT-BR, ou null se o arquivo pode ser enviado. */
export function validateResumeFile(file: File): string | null {
  const name = file.name.toLowerCase();
  const byType = ACCEPTED_TYPES.has(file.type);
  const byExt = name.endsWith(".pdf") || name.endsWith(".docx");
  if (!byType && !byExt) {
    if (name.endsWith(".doc")) {
      return `“${file.name}” é um .doc antigo do Word. Salve como DOCX ou PDF e envie de novo.`;
    }
    return `“${file.name}” não é PDF nem DOCX. Envie o currículo em um desses formatos.`;
  }
  if (file.size === 0) return `“${file.name}” está vazio. Escolha o arquivo do currículo de novo.`;
  if (file.size > MAX_UPLOAD_BYTES) {
    return `O arquivo tem ${formatBytes(file.size)}; o limite é 5 MB. Exporte uma versão menor (sem imagens, por exemplo).`;
  }
  return null;
}
