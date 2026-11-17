import { connection } from "next/server";
import { Suspense } from "react";
import CandidaturaPageClient from "./CandidaturaPageClient";

/** Página da candidatura: lê o id da rota no servidor e entrega ao componente de cliente. */
export default async function CandidaturaPage({ params }: PageProps<"/candidaturas/[id]">) {
  // one user's record: rendered per request, never prerendered at build
  await connection();
  const { id } = await params;
  return (
    <Suspense fallback={<p className="muted">Carregando a candidatura…</p>}>
      <CandidaturaPageClient id={Number(id)} />
    </Suspense>
  );
}
