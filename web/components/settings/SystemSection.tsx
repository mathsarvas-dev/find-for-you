import { Suspense } from "react";
import { HealthPanel, HealthPanelLoading } from "@/components/HealthPanel";

const TITLE = "Estado do sistema";

/**
 * Seção #sistema de Configurações (componente de servidor). O HealthPanel checa a API a
 * cada requisição (connection()); o esqueleto entra no pré-render e o resultado chega
 * depois, por streaming. Não importe isto de um componente de cliente.
 */
export function SystemSection() {
  return (
    <div id="sistema" className="settings__section">
      <Suspense fallback={<HealthPanelLoading title={TITLE} />}>
        <HealthPanel recheckHref="/settings#sistema" title={TITLE} />
      </Suspense>
    </div>
  );
}
