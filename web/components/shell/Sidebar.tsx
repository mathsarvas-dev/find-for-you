import Link from "next/link";
import type { Overview } from "@/lib/overview";
import { NavItem } from "./NavItem";
import {
  HomeIcon,
  InterviewIcon,
  JobsIcon,
  LogoIcon,
  ProfileIcon,
  SettingsIcon,
  SourcesIcon,
} from "./icons";

/** "loading": overview ainda não respondeu; "down": a requisição falhou. */
export type ApiStatus = "loading" | "ok" | "down";

export type NavEntry = {
  href: string;
  label: string;
  Icon: (props: { className?: string }) => React.ReactNode;
  /** Fica depois da linha separadora. */
  footer?: boolean;
};

export const NAV_ITEMS: NavEntry[] = [
  { href: "/", label: "Início", Icon: HomeIcon },
  { href: "/onboarding", label: "Perfil", Icon: ProfileIcon },
  { href: "/onboarding/interview", label: "Entrevista", Icon: InterviewIcon },
  { href: "/sources", label: "Fontes", Icon: SourcesIcon },
  { href: "/jobs", label: "Vagas", Icon: JobsIcon },
  { href: "/settings", label: "Configurações", Icon: SettingsIcon, footer: true },
];

/** "/" e "/onboarding" só no caminho exato (a entrevista mora debaixo de /onboarding). */
export function isActive(href: string, pathname: string): boolean {
  if (href === "/" || href === "/onboarding") return pathname === href;
  return pathname === href || pathname.startsWith(`${href}/`);
}

/** Texto acessível da pendência de um item, ou null. */
export function pendingFor(href: string, overview: Overview): string | null {
  switch (href) {
    case "/onboarding":
      return overview.profile.confirmed ? null : "perfil não confirmado";
    case "/sources":
      // Igual à trilha: o passo "Fontes de vagas" exige ao menos uma empresa ativa.
      return overview.companies.active > 0 ? null : "nenhuma empresa ativa";
    case "/jobs":
      return overview.jobs.open > 0 && overview.matches.scored === 0 ? "nenhuma vaga avaliada" : null;
    default:
      return null;
  }
}

const STATUS_TEXT: Record<ApiStatus, string> = {
  loading: "verificando",
  ok: "ok",
  down: "sem resposta da API",
};

type Props = {
  pathname: string;
  overview: Overview | null;
  apiStatus: ApiStatus;
  /** Chamado ao escolher um link (a gaveta fecha). */
  onNavigate?: () => void;
};

export function Sidebar({ pathname, overview, apiStatus, onNavigate }: Props) {
  const item = (entry: NavEntry) => (
    <NavItem
      key={entry.href}
      href={entry.href}
      label={entry.label}
      icon={<entry.Icon />}
      active={isActive(entry.href, pathname)}
      pending={overview ? pendingFor(entry.href, overview) : null}
      onNavigate={onNavigate}
    />
  );

  return (
    <div className="sidebar__inner">
      <Link className="wordmark sidebar__brand" href="/" onClick={onNavigate}>
        <LogoIcon />
        Rumo
      </Link>
      <nav className="sidebar__nav" aria-label="Principal">
        <ul className="nav-list">{NAV_ITEMS.filter((e) => !e.footer).map(item)}</ul>
        <hr className="nav-separator" />
        <ul className="nav-list">{NAV_ITEMS.filter((e) => e.footer).map(item)}</ul>
      </nav>
      <Link className="sidebar__status" href="/settings#sistema" onClick={onNavigate}>
        <span className={`status-dot status-dot--${apiStatus}`} aria-hidden="true" />
        <span className="sidebar__status-label">Sistema</span>
        <span className="sidebar__status-text">
          <span className="sr-only">: </span>
          {STATUS_TEXT[apiStatus]}
        </span>
      </Link>
    </div>
  );
}
