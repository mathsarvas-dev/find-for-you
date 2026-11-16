import { ApiError, publicApiUrl } from "@/lib/profile";

/* Importar uma vaga por link. Usa fetch direto porque o 422 "needs_text"
 * traz um objeto em `detail` (o cliente comum só lê texto). */

export type ImportLinkInput = {
  url: string;
  /** Enviados juntos quando a página não pôde ser lida automaticamente. */
  title?: string;
  description?: string;
};

export type ImportLinkResult = { job_id: number; created: boolean };

/** A página não traz os dados da vaga: a pessoa precisa colar título e descrição. */
export class NeedsTextError extends ApiError {
  constructor(
    message: string,
    public reason: string,
  ) {
    super(422, message);
    this.name = "NeedsTextError";
  }
}

const NETWORK_MESSAGE = (base: string) =>
  `Não consegui falar com a API em ${base}. Confira se ela está no ar e tente de novo.`;

export async function importLink(input: ImportLinkInput): Promise<ImportLinkResult> {
  const base = publicApiUrl();
  let res: Response;
  try {
    res = await fetch(`${base}/jobs/import-link`, {
      method: "POST",
      cache: "no-store",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    });
  } catch {
    throw new ApiError(null, NETWORK_MESSAGE(base));
  }
  const body = (await res.json().catch(() => null)) as { detail?: unknown } | null;
  if (!res.ok) {
    const detail = body?.detail;
    if (detail && typeof detail === "object" && (detail as { code?: unknown }).code === "needs_text") {
      const d = detail as { message?: unknown; reason?: unknown };
      throw new NeedsTextError(
        typeof d.message === "string" ? d.message : "Cole o título e a descrição.",
        typeof d.reason === "string" ? d.reason : "",
      );
    }
    throw new ApiError(
      res.status,
      typeof detail === "string" && detail
        ? detail
        : `A API respondeu com erro (HTTP ${res.status}).`,
    );
  }
  const data = body as unknown as ImportLinkResult;
  return { job_id: data.job_id, created: res.status === 201 };
}
