"use client";

import Link from "next/link";
import { useEffect, useId, useRef, useState } from "react";

const LETTER = /[\p{L}\p{N}]/u;

/** Iniciais do primeiro e do último nome (só letras/dígitos); "V" (Você) sem nome. */
export function initials(name: string | null): string {
  const firsts = (name ?? "")
    .trim()
    .split(/\s+/)
    .map((word) => word.match(LETTER)?.[0])
    .filter((c): c is string => Boolean(c));
  if (firsts.length === 0) return "V";
  const pair = firsts.length > 1 ? firsts[0] + firsts[firsts.length - 1] : firsts[0];
  return pair.toUpperCase();
}

type Props = {
  name: string | null;
  email: string | null;
  /** Sem perfil confirmado: "Você" e "Perfil não confirmado". */
  confirmed: boolean;
};

const ITEMS = [
  { href: "/onboarding", label: "Meu perfil" },
  { href: "/settings", label: "Configurações" },
];

export function UserMenu({ name, email, confirmed }: Props) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLUListElement>(null);
  const id = useId();
  const menuId = `${id}-menu`;

  const displayName = confirmed && name?.trim() ? name.trim() : "Você";
  const detail = confirmed ? email?.trim() || "Sem e-mail no perfil" : "Perfil não confirmado";

  function items(): HTMLElement[] {
    return Array.from(menuRef.current?.querySelectorAll<HTMLElement>("[role='menuitem']") ?? []);
  }

  function close(returnFocus: boolean) {
    setOpen(false);
    if (returnFocus) buttonRef.current?.focus();
  }

  // Ao abrir, o foco vai para o primeiro item.
  useEffect(() => {
    if (open) items()[0]?.focus();
  }, [open]);

  // Clique fora fecha; o foco volta ao botão se não foi parar em outro controle.
  useEffect(() => {
    if (!open) return;
    function onClick(event: MouseEvent) {
      const wrap = wrapRef.current;
      if (!wrap || wrap.contains(event.target as Node)) return;
      setOpen(false);
      const active = document.activeElement;
      if (!active || active === document.body || wrap.contains(active)) buttonRef.current?.focus();
    }
    document.addEventListener("click", onClick);
    return () => document.removeEventListener("click", onClick);
  }, [open]);

  function onMenuKeyDown(event: React.KeyboardEvent) {
    const list = items();
    if (list.length === 0) return;
    const index = list.indexOf(document.activeElement as HTMLElement);
    let next: number | null = null;
    switch (event.key) {
      case "ArrowDown":
        next = (index + 1) % list.length;
        break;
      case "ArrowUp":
        next = (index - 1 + list.length) % list.length;
        break;
      case "Home":
        next = 0;
        break;
      case "End":
        next = list.length - 1;
        break;
      case "Escape":
        event.preventDefault();
        close(true);
        return;
      default:
        return;
    }
    event.preventDefault();
    list[next].focus();
  }

  function onButtonKeyDown(event: React.KeyboardEvent) {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      setOpen(true);
    }
  }

  // Tab para fora do menu fecha sem roubar o foco.
  function onBlur(event: React.FocusEvent) {
    const next = event.relatedTarget as Node | null;
    if (next && !wrapRef.current?.contains(next)) setOpen(false);
  }

  return (
    <div className="user-menu" ref={wrapRef} onBlur={onBlur}>
      <button
        ref={buttonRef}
        type="button"
        className="user-menu__button"
        aria-label="Menu do perfil"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        onClick={() => setOpen((v) => !v)}
        onKeyDown={onButtonKeyDown}
      >
        <span aria-hidden="true">{initials(confirmed ? name : null)}</span>
      </button>
      {open && (
        <div className="user-menu__panel">
          <div className="user-menu__who">
            <p className="user-menu__name">{displayName}</p>
            <p className="user-menu__email">{detail}</p>
          </div>
          <ul
            ref={menuRef}
            id={menuId}
            className="user-menu__list"
            role="menu"
            aria-label="Perfil"
            onKeyDown={onMenuKeyDown}
          >
            {ITEMS.map((item) => (
              <li key={item.href} role="none">
                <Link
                  role="menuitem"
                  className="user-menu__item"
                  href={item.href}
                  onClick={() => setOpen(false)}
                >
                  {item.label}
                </Link>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
