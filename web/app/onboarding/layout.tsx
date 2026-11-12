import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Seu perfil · Rumo",
};

export default function OnboardingLayout({ children }: LayoutProps<"/onboarding">) {
  return children;
}
