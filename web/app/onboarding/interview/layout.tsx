import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Entrevista · Rumo",
};

export default function InterviewLayout({ children }: LayoutProps<"/onboarding/interview">) {
  return children;
}
