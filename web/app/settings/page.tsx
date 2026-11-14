import { AccountSection } from "@/components/settings/AccountSection";
import { DiscoverySection } from "@/components/settings/DiscoverySection";
import { SystemSection } from "@/components/settings/SystemSection";
import { PageHeader } from "@/components/shell/PageHeader";

const SECTIONS = [
  { id: "conta", label: "Conta e perfil" },
  { id: "descoberta", label: "Descoberta automática" },
  { id: "sistema", label: "Estado do sistema" },
];

/**
 * Configurações (componente de servidor). Conta e descoberta rodam no navegador; o estado
 * do sistema é checado no servidor a cada requisição, dentro de Suspense: o resto da
 * página entra no pré-render.
 */
export default function SettingsPage() {
  return (
    <main className="page settings">
      <PageHeader
        title="Configurações"
        description="Seu perfil, a descoberta automática de empresas e o estado da API e do banco."
      />
      <nav className="settings__toc" aria-label="Seções de Configurações">
        <ul>
          {SECTIONS.map((s) => (
            <li key={s.id}>
              <a href={`#${s.id}`}>{s.label}</a>
            </li>
          ))}
        </ul>
      </nav>
      <AccountSection />
      <DiscoverySection />
      <SystemSection />
    </main>
  );
}
