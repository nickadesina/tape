import type { Metadata } from "next";
import Link from "next/link";
import type { ReactNode } from "react";
import { HANDLE, SITE_URL } from "../lib/urls";
import "./globals.css";

export const metadata: Metadata = {
  metadataBase: new URL(`${SITE_URL}/`),
  title: { default: "Positioning Tape", template: "%s · Positioning Tape" },
  description: "Who is positioned which way in the AI complex on Hyperliquid, from public chain state, every hour.",
  twitter: { card: "summary_large_image", creator: HANDLE },
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header>
          <nav>
            <Link href="/">
              <b>Tape</b>
            </Link>
            <Link href="/legs">legs</Link>
            <Link href="/m">markets</Link>
            <Link href="/events">events</Link>
            <Link href="/stress">stress</Link>
            <Link href="/map">map</Link>
            <Link href="/method">method</Link>
            <Link href="/data">data</Link>
          </nav>
        </header>
        <main>{children}</main>
        <footer>
          Data from public Hyperliquid chain state. Charts and data CC BY 4.0, attribution: {HANDLE}. Not advice; the
          tape says who is positioned where, not what to buy.
        </footer>
      </body>
    </html>
  );
}
