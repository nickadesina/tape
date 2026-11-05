"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const ITEMS = [
  { href: "/legs", label: "legs", ja: "部門" },
  { href: "/m", label: "markets", ja: "銘柄" },
  { href: "/events", label: "events", ja: "速報" },
  { href: "/method", label: "method", ja: "手法" },
  { href: "/data", label: "data", ja: "資料" },
];

export function Nav() {
  const path = usePathname() ?? "/";
  return (
    <nav className="nav" aria-label="Site">
      {ITEMS.map((i) => {
        const here = path === i.href || path.startsWith(`${i.href}/`);
        return (
          <Link key={i.href} href={i.href} aria-current={here ? "page" : undefined}>
            {i.label}
            <small lang="ja" aria-hidden="true">
              {i.ja}
            </small>
          </Link>
        );
      })}
    </nav>
  );
}
