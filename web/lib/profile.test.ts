import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  MAX_UPLOAD_BYTES,
  confirmProfile,
  emptyProfile,
  getImport,
  getProfile,
  publicApiUrl,
  uploadResume,
  validateResumeFile,
} from "./profile";

function jsonResponse(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function mockFetch(impl: (url: string, init?: RequestInit) => Promise<Response>) {
  const fn = vi.fn(impl);
  vi.stubGlobal("fetch", fn);
  return fn;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("publicApiUrl", () => {
  it("usa http://localhost:8000 por padrão", () => {
    vi.stubEnv("NEXT_PUBLIC_API_URL", "");
    expect(publicApiUrl()).toBe("http://localhost:8000");
  });

  it("respeita NEXT_PUBLIC_API_URL sem barra final", () => {
    vi.stubEnv("NEXT_PUBLIC_API_URL", "http://api.test:9000/");
    expect(publicApiUrl()).toBe("http://api.test:9000");
  });
});

describe("validateResumeFile", () => {
  const pdf = "application/pdf";
  const docx = "application/vnd.openxmlformats-officedocument.wordprocessingml.document";

  it("aceita PDF e DOCX dentro do limite", () => {
    expect(validateResumeFile(new File(["x"], "cv.pdf", { type: pdf }))).toBeNull();
    expect(validateResumeFile(new File(["x"], "cv.docx", { type: docx }))).toBeNull();
  });

  it("aceita pela extensão quando o navegador não informa o tipo", () => {
    expect(validateResumeFile(new File(["x"], "CV.PDF", { type: "" }))).toBeNull();
  });

  it("recusa arquivo acima de 5 MB dizendo o tamanho", () => {
    const big = new File([new Uint8Array(MAX_UPLOAD_BYTES + 1)], "cv.pdf", { type: pdf });
    expect(validateResumeFile(big)).toMatch(/5,0 MB.*limite é 5 MB/);
  });

  it("recusa tipo errado citando o nome do arquivo", () => {
    expect(validateResumeFile(new File(["x"], "foto.png", { type: "image/png" }))).toBe(
      "“foto.png” não é PDF nem DOCX. Envie o currículo em um desses formatos.",
    );
  });

  it("explica que .doc antigo não serve", () => {
    expect(validateResumeFile(new File(["x"], "cv.doc", { type: "application/msword" }))).toMatch(
      /\.doc.*Salve como DOCX ou PDF/,
    );
  });

  it("recusa arquivo vazio", () => {
    expect(validateResumeFile(new File([], "cv.pdf", { type: pdf }))).toMatch(/vazio/);
  });
});

describe("uploadResume", () => {
  it("envia multipart com o campo file e devolve o id", async () => {
    const fetchMock = mockFetch(async () => jsonResponse(201, { id: 7, status: "pending" }));
    const file = new File(["%PDF"], "cv.pdf", { type: "application/pdf" });
    await expect(uploadResume(file)).resolves.toEqual({ id: 7 });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://localhost:8000/profile/imports");
    expect(init?.method).toBe("POST");
    expect((init?.body as FormData).get("file")).toBeInstanceOf(File);
  });

  it("repassa o detail em PT-BR do servidor", async () => {
    mockFetch(async () => jsonResponse(415, { detail: "Tipo de arquivo não suportado. Envie um PDF ou DOCX." }));
    const err = await uploadResume(new File(["x"], "cv.pdf")).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(415);
    expect(err.message).toBe("Tipo de arquivo não suportado. Envie um PDF ou DOCX.");
  });

  it("erro de rede vira mensagem legível com o endereço da API", async () => {
    mockFetch(async () => {
      throw new TypeError("Failed to fetch");
    });
    const err = await uploadResume(new File(["x"], "cv.pdf")).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBeNull();
    expect(err.message).toMatch(/Não consegui falar com a API em http:\/\/localhost:8000/);
  });
});

describe("getImport", () => {
  it("devolve o estado da importação", async () => {
    const body = { id: 3, status: "ready", error: null, truncated: false, profile: emptyProfile() };
    const fetchMock = mockFetch(async () => jsonResponse(200, body));
    await expect(getImport(3)).resolves.toEqual(body);
    expect(fetchMock.mock.calls[0][0]).toBe("http://localhost:8000/profile/imports/3");
  });

  it("detail não textual (422 do FastAPI) usa mensagem genérica com o código", async () => {
    mockFetch(async () => jsonResponse(500, { detail: [{ msg: "x" }] }));
    await expect(getImport(3)).rejects.toThrow("A API respondeu com erro (HTTP 500).");
  });
});

describe("confirmProfile", () => {
  it("envia perfil e narrativa em JSON", async () => {
    const fetchMock = mockFetch(async () =>
      jsonResponse(200, { version: 1, chunks_total: 8, chunks_embedded: 0 }),
    );
    const profile = emptyProfile();
    await expect(confirmProfile(profile, "Quero remoto")).resolves.toEqual({
      version: 1,
      chunks_total: 8,
      chunks_embedded: 0,
    });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://localhost:8000/profile/confirm");
    expect(JSON.parse(init?.body as string)).toEqual({ profile, narrative: "Quero remoto" });
  });

  it("narrativa em branco vai como null", async () => {
    const fetchMock = mockFetch(async () =>
      jsonResponse(200, { version: 1, chunks_total: 1, chunks_embedded: 0 }),
    );
    await confirmProfile(emptyProfile(), "   ");
    expect(JSON.parse(fetchMock.mock.calls[0][1]?.body as string).narrative).toBeNull();
  });
});

describe("getProfile", () => {
  it("404 significa sem perfil: devolve null", async () => {
    mockFetch(async () => jsonResponse(404, { detail: "Perfil não encontrado." }));
    await expect(getProfile()).resolves.toBeNull();
  });

  it("devolve perfil e contagens", async () => {
    const body = { version: 2, chunks_total: 5, chunks_embedded: 5, profile: emptyProfile(), narrative: null };
    mockFetch(async () => jsonResponse(200, body));
    await expect(getProfile()).resolves.toEqual(body);
  });
});
