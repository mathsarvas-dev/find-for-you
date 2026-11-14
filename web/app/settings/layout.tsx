import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Configurações · Rumo",
};

export default function SettingsLayout({ children }: LayoutProps<"/settings">) {
  return children;
}
