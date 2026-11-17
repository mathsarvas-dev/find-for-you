import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "LinkedIn · Rumo",
};

export default function LinkedinLayout({ children }: LayoutProps<"/linkedin">) {
  return children;
}
