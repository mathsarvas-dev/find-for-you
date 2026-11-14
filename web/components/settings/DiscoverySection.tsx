"use client";

import { DiscoveryPanel } from "@/components/sources/DiscoveryPanel";
import { useOverview } from "@/components/shell/OverviewContext";

/**
 * Seção #descoberta de Configurações: o mesmo painel de /sources, sozinho. A âncora é o
 * h2 "Descoberta automática" do próprio painel (id="descoberta"). Quando o número de
 * empresas automáticas muda, o overview da casca é relido (trilha, ponto da lateral).
 */
export function DiscoverySection() {
  const { refresh } = useOverview();
  return (
    <div className="settings__section settings__discovery">
      <DiscoveryPanel onCompaniesChanged={() => void refresh()} />
    </div>
  );
}
