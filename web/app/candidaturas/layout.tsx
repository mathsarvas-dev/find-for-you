import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Candidaturas · Rumo",
};

export default function CandidaturasLayout({ children }: LayoutProps<"/candidaturas">) {
  return children;
}
