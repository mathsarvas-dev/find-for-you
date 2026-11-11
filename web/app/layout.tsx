import type { Metadata } from "next";
import { IBM_Plex_Mono, Instrument_Sans } from "next/font/google";
import Link from "next/link";
import "./globals.css";

const instrumentSans = Instrument_Sans({
  variable: "--font-instrument-sans",
  subsets: ["latin", "latin-ext"],
});

const plexMono = IBM_Plex_Mono({
  variable: "--font-plex-mono",
  subsets: ["latin"],
  weight: ["400", "500"],
});

export const metadata: Metadata = {
  title: "Rumo",
  description: "Sua busca de vagas, organizada.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="pt-BR" className={`${instrumentSans.variable} ${plexMono.variable}`}>
      <body>
        <header className="topbar">
          <div className="topbar__inner">
            <Link className="wordmark" href="/">
              <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true">
                <circle cx="9" cy="9" r="7.5" fill="none" stroke="currentColor" strokeWidth="1.5" />
                <path d="M9 3.5 L11 9 L9 14.5 L7 9 Z" fill="currentColor" />
              </svg>
              Rumo
            </Link>
          </div>
        </header>
        {children}
      </body>
    </html>
  );
}
