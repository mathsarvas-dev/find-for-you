import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Qualidade · Rumo",
};

export default function QualityLayout({ children }: LayoutProps<"/jobs/qualidade">) {
  return children;
}
