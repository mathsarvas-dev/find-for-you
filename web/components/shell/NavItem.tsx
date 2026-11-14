import Link from "next/link";

type Props = {
  href: string;
  label: string;
  icon: React.ReactNode;
  active: boolean;
  /** Texto acessível da pendência ("perfil não confirmado"); null sem pendência. */
  pending: string | null;
  onNavigate?: () => void;
};

export function NavItem({ href, label, icon, active, pending, onNavigate }: Props) {
  return (
    <li>
      <Link
        className="nav-item"
        href={href}
        aria-current={active ? "page" : undefined}
        onClick={onNavigate}
      >
        <span className="nav-item__icon">{icon}</span>
        <span className="nav-item__label">{label}</span>
        {pending && (
          <>
            {" "}
            <span className="sr-only">({pending})</span>
            <span className="nav-item__dot" aria-hidden="true" />
          </>
        )}
      </Link>
    </li>
  );
}
