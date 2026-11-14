"use client";

import { usePathname } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { CloseIcon } from "./icons";
import { OverviewContext, useOverviewLoader } from "./OverviewContext";
import { PageTitleContext } from "./PageHeader";
import { Sidebar } from "./Sidebar";
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
  // Refaz a cada navegação: as pendências acompanham o que a pessoa acabou de fazer.
  const { overview, status: apiStatus, refresh } = useOverviewLoader(pathname);
  const overviewValue = useMemo(() => ({ overview, status: apiStatus, refresh }), [overview, apiStatus, refresh]);
  const [pageTitle, setPageTitle] = useState<string | null>(null);
  // A gaveta fica aberta só na rota em que foi aberta: qualquer navegação
  // (link, voltar/avançar do navegador) a fecha. O reset acontece no render (padrão
  // "ajustar estado quando a prop muda"), então voltar à rota de origem não a reabre.
  const [drawerPath, setDrawerPath] = useState<string | null>(null);
  if (drawerPath !== null && drawerPath !== pathname) setDrawerPath(null);
  const drawerOpen = drawerPath !== null && drawerPath === pathname;
  const menuButtonRef = useRef<HTMLButtonElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  const closeDrawer = () => setDrawerPath(null);

  // Gaveta aberta: foco no primeiro controle, sem rolagem do fundo e fecha ao virar desktop.
  // Ao fechar (por qualquer motivo) o foco não fica perdido no <body>.
  useEffect(() => {
    if (!drawerOpen) return;
    panelRef.current?.querySelector<HTMLElement>(FOCUSABLE)?.focus();
    const body = document.body;
    const previous = body.style.overflow;
    body.style.overflow = "hidden";
    // 860 = ponto de quebra da gaveta/lateral. Manter em sincronia com globals.css:
    // 859.98px/860px da casca e 1091.98px (= --sidebar-w 232px + 860px) das páginas.
    const wide = typeof window.matchMedia === "function" ? window.matchMedia("(min-width: 860px)") : null;
    const onWide = (event: MediaQueryListEvent) => {
      if (event.matches) setDrawerPath(null);
    };
    wide?.addEventListener("change", onWide);
    const menuButton = menuButtonRef.current;
    const content = contentRef.current;
    return () => {
      body.style.overflow = previous;
      wide?.removeEventListener("change", onWide);
      const active = document.activeElement;
      if (active && active !== body && active.isConnected) return;
      if (menuButton?.isConnected && getComputedStyle(menuButton).display !== "none") menuButton.focus();
      else content?.focus();
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
    <OverviewContext.Provider value={overviewValue}>
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
                  onNavigate={closeDrawer}
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
              onOpenDrawer={() => setDrawerPath(pathname)}
              menuButtonRef={menuButtonRef}
            />
            <div className="shell__content" id="conteudo" tabIndex={-1} ref={contentRef}>
              {children}
            </div>
          </div>
        </div>
      </PageTitleContext.Provider>
    </OverviewContext.Provider>
  );
}
