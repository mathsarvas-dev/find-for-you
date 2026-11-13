"use client";

import { usePathname } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { getOverview, type Overview } from "@/lib/overview";
import { CloseIcon } from "./icons";
import { PageTitleContext } from "./PageHeader";
import { Sidebar, type ApiStatus } from "./Sidebar";
import { Topbar, titleForPath } from "./Topbar";

const DRAWER_ID = "nav-drawer";
const FOCUSABLE = "a[href], button:not([disabled]), [tabindex]:not([tabindex='-1'])";

/**
 * Casca do app: lateral fixa (gaveta < 860 px), barra superior e área de conteúdo.
 * O overview é buscado em efeito e falha em silêncio: a navegação funciona sem a API.
 * O primeiro render não depende de nada do navegador (sem mismatch de hidratação).
 */
export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname() ?? "/";
  const [overview, setOverview] = useState<Overview | null>(null);
  const [apiStatus, setApiStatus] = useState<ApiStatus>("loading");
  const [pageTitle, setPageTitle] = useState<string | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const menuButtonRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  // Refaz a cada navegação: as pendências acompanham o que a pessoa acabou de fazer.
  useEffect(() => {
    let alive = true;
    getOverview().then(
      (data) => {
        if (!alive) return;
        setOverview(data);
        setApiStatus("ok");
      },
      () => {
        if (alive) setApiStatus("down");
      },
    );
    return () => {
      alive = false;
    };
  }, [pathname]);

  const closeDrawer = useCallback(() => {
    setDrawerOpen(false);
    menuButtonRef.current?.focus();
  }, []);

  // Gaveta aberta: foco no primeiro controle, sem rolagem do fundo e fecha ao virar desktop.
  useEffect(() => {
    if (!drawerOpen) return;
    panelRef.current?.querySelector<HTMLElement>(FOCUSABLE)?.focus();
    const body = document.body;
    const previous = body.style.overflow;
    body.style.overflow = "hidden";
    const wide = typeof window.matchMedia === "function" ? window.matchMedia("(min-width: 860px)") : null;
    const onWide = (event: MediaQueryListEvent) => {
      if (event.matches) setDrawerOpen(false);
    };
    wide?.addEventListener("change", onWide);
    return () => {
      body.style.overflow = previous;
      wide?.removeEventListener("change", onWide);
    };
  }, [drawerOpen]);

  function onDrawerKeyDown(event: React.KeyboardEvent) {
    if (event.key === "Escape") {
      event.preventDefault();
      closeDrawer();
      return;
    }
    if (event.key !== "Tab") return;
    const focusables = Array.from(panelRef.current?.querySelectorAll<HTMLElement>(FOCUSABLE) ?? []);
    if (focusables.length === 0) return;
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    const active = document.activeElement;
    if (event.shiftKey && (active === first || !panelRef.current?.contains(active))) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && (active === last || !panelRef.current?.contains(active))) {
      event.preventDefault();
      first.focus();
    }
  }

  return (
    <PageTitleContext.Provider value={setPageTitle}>
      <div className="shell">
        <a className="skip-link" href="#conteudo">
          Pular para o conteúdo
        </a>
        <aside className="sidebar">
          <Sidebar pathname={pathname} overview={overview} apiStatus={apiStatus} />
        </aside>
        {drawerOpen && (
          <div className="drawer">
            <div className="drawer__overlay" aria-hidden="true" onClick={closeDrawer} />
            <div
              ref={panelRef}
              id={DRAWER_ID}
              className="drawer__panel"
              role="dialog"
              aria-modal="true"
              aria-label="Navegação"
              onKeyDown={onDrawerKeyDown}
            >
              <button
                type="button"
                className="drawer__close"
                aria-label="Fechar navegação"
                onClick={closeDrawer}
              >
                <CloseIcon />
              </button>
              <Sidebar
                pathname={pathname}
                overview={overview}
                apiStatus={apiStatus}
                onNavigate={() => setDrawerOpen(false)}
              />
            </div>
          </div>
        )}
        <div className="shell__main">
          <Topbar
            title={pageTitle ?? titleForPath(pathname)}
            overview={overview}
            drawerOpen={drawerOpen}
            drawerId={DRAWER_ID}
            onOpenDrawer={() => setDrawerOpen(true)}
            menuButtonRef={menuButtonRef}
          />
          <div className="shell__content" id="conteudo" tabIndex={-1}>
            {children}
          </div>
        </div>
      </div>
    </PageTitleContext.Provider>
  );
}
