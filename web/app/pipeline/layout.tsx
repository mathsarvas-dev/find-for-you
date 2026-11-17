import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Pipeline · Rumo",
};

export default function PipelineLayout({ children }: LayoutProps<"/pipeline">) {
  return children;
}
