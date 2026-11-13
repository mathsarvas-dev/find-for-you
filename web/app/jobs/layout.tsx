import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Vagas · Rumo",
};

export default function JobsLayout({ children }: LayoutProps<"/jobs">) {
  return children;
}
