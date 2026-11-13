"use client";

import { createContext, useContext, useEffect } from "react";

/**
 * Setter do título mostrado na barra superior. A casca (AppShell) fornece o valor;
 * fora dela é um no-op. null volta ao título padrão da rota.
 */
export const PageTitleContext = createContext<(title: string | null) => void>(() => {});

type Props = {
  title: string;
  description?: React.ReactNode;
  /** Um botão primário e até dois secundários, à direita. */
  actions?: React.ReactNode;
  /** Trilha horizontal do fluxo, abaixo do cabeçalho. */
  stepper?: React.ReactNode;
};

/** Cabeçalho padrão da página: o h1 da página e o título da barra superior. */
export function PageHeader({ title, description, actions, stepper }: Props) {
  const setTitle = useContext(PageTitleContext);

  // Em efeito: o título do topo começa no padrão da rota (igual no servidor) e troca depois.
  useEffect(() => {
    setTitle(title);
    return () => setTitle(null);
  }, [setTitle, title]);

  return (
    <div className="page-header">
      <div className="page-header__row">
        <div className="page-header__text">
          <h1>{title}</h1>
          {description && <p>{description}</p>}
        </div>
        {actions && <div className="page-header__actions">{actions}</div>}
      </div>
      {stepper && <div className="page-header__stepper">{stepper}</div>}
    </div>
  );
}
