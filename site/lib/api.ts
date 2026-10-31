import { HANDLE } from "./urls";

export const LICENSE = {
  license: "CC BY 4.0",
  license_url: "https://creativecommons.org/licenses/by/4.0/",
  attribution: HANDLE,
};

const CACHE = "public, max-age=60, s-maxage=300, stale-while-revalidate=600";

export function json(body: unknown): Response {
  return Response.json({ ...LICENSE, ...(body as object) }, { headers: { "cache-control": CACHE } });
}

export function csv(name: string, header: string[], rows: (string | number | null | undefined)[][]): Response {
  const esc = (v: string | number | null | undefined) => {
    if (v === null || v === undefined) return "";
    const s = String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const body = [header.join(","), ...rows.map((r) => r.map(esc).join(","))].join("\n") + "\n";
  return new Response(body, {
    headers: {
      "content-type": "text/csv; charset=utf-8",
      "content-disposition": `inline; filename="${name}"`,
      "cache-control": CACHE,
      "x-license": `${LICENSE.license}; attribution ${LICENSE.attribution}`,
    },
  });
}

export function list(v: string | null, dflt: string[]): string[] {
  if (!v) return dflt;
  return v
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean)
    .slice(0, 50);
}

export function range(url: URL, days = 30): { from: Date; to: Date } | null {
  const to = url.searchParams.get("to") ? new Date(url.searchParams.get("to") as string) : new Date();
  const from = url.searchParams.get("from")
    ? new Date(url.searchParams.get("from") as string)
    : new Date(to.getTime() - days * 86_400_000);
  if (Number.isNaN(from.getTime()) || Number.isNaN(to.getTime()) || from > to) return null;
  return { from, to };
}

export function bad(msg: string): Response {
  return Response.json({ error: msg }, { status: 400 });
}
