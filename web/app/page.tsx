import { JourneyPanel } from "@/components/home/JourneyPanel";

/**
 * Início. Estática: o progresso vem do overview da casca (no navegador), e a
 * saudação é calculada em efeito. O estado da API/banco fica em Configurações.
 */
export default function HomePage() {
  return (
    <main className="page">
      <JourneyPanel />
    </main>
  );
}
