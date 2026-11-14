import Link from "next/link";
import type { Overview } from "@/lib/overview";
import { MenuIcon } from "./icons";
import { NAV_ITEMS, isActive } from "./Sidebar";
import { UserMenu } from "./UserMenu";

/** Título padrão por rota (antes de a página definir o seu): nunca vazio. */
export function titleForPath(pathname: string): string {
  return NAV_ITEMS.find((item) => isActive(item.href, pathname))?.label ?? "Rumo";
}

type Props = {
  title: string;
  overview: Overview | null;
  drawerOpen: boolean;
  drawerId: string;
  onOpenDrawer: () => void;
  menuButtonRef: React.Ref<HTMLButtonElement>;
};

export function Topbar({ title, overview, drawerOpen, drawerId, onOpenDrawer, menuButtonRef }: Props) {
  const profile = overview?.profile;
  return (
    <header className="topbar">
      <button
        ref={menuButtonRef}
        type="button"
        className="topbar__menu"
        aria-label="Abrir navegação"
        aria-expanded={drawerOpen}
        aria-controls={drawerOpen ? drawerId : undefined}
        onClick={onOpenDrawer}
      >
        <MenuIcon />
      </button>
      {/* Não é h1: cada página tem o seu (PageHeader). */}
      <nav className="topbar__crumbs" aria-label="Caminho">
        <Link className="topbar__root" href="/">
          Rumo
        </Link>
        <span className="topbar__sep" aria-hidden="true">
          /
        </span>
        <span className="topbar__title" aria-current="page">
          {title}
        </span>
      </nav>
      <UserMenu
        name={profile?.name ?? null}
        email={profile?.email ?? null}
        confirmed={profile?.confirmed ?? false}
      />
    </header>
  );
}
