import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Fontes · Rumo",
};

export default function SourcesLayout({ children }: LayoutProps<"/sources">) {
  return children;
}
