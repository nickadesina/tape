import type { Metadata, Viewport } from "next";
import localFont from "next/font/local";
import Link from "next/link";
import type { ReactNode } from "react";
import { Nav } from "../components/Nav";
import { HANDLE, SITE_URL } from "../lib/urls";
import "./globals.css";

// Subset to Latin plus the few Japanese glosses the site uses (SIL OFL, see fonts/).
const display = localFont({
  src: "./fonts/ZenKakuGothicNew-Black.woff2",
  weight: "900",
  variable: "--f-display",
  display: "swap",
});
const sans = localFont({
  src: [
    { path: "./fonts/BIZUDPGothic-Regular.woff2", weight: "400" },
    { path: "./fonts/BIZUDPGothic-Bold.woff2", weight: "700" },
  ],
  variable: "--f-sans",
  display: "swap",
});
const mono = localFont({
  src: [
    { path: "./fonts/BIZUDGothic-Regular.woff2", weight: "400" },
    { path: "./fonts/BIZUDGothic-Bold.woff2", weight: "700" },
  ],
  variable: "--f-mono",
  display: "swap",
});

export const metadata: Metadata = {
  metadataBase: new URL(`${SITE_URL}/`),
  title: { default: "Positioning Tape", template: "%s · Positioning Tape" },
  description: "Who is positioned which way in the AI complex on Hyperliquid, from public chain state, every hour.",
  twitter: { card: "summary_large_image", creator: HANDLE },
};

export const viewport: Viewport = { themeColor: "#121a4a", colorScheme: "light" };

function Mark() {
  return (
    <Link href="/" className="mark" aria-label="Positioning Tape, home">
      <b>Tape</b>
      <span lang="ja" aria-hidden="true">
        建玉
      </span>
    </Link>
  );
}

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className={`${display.variable} ${sans.variable} ${mono.variable}`}>
      <body>
        <header className="masthead">
          <div className="wrap">
            <Mark />
            <Nav />
          </div>
        </header>
        <main className="wrap">{children}</main>
        <footer>
          <div className="wrap">
            <p>
              Data from public Hyperliquid chain state. Charts and data CC BY 4.0, attribution: {HANDLE}. Not advice; the
              tape says who is positioned where, not what to buy.
            </p>
            <Mark />
          </div>
        </footer>
      </body>
    </html>
  );
}
