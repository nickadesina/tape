export function pct(x: number | null | undefined, digits = 1, signed = true): string {
  if (x === null || x === undefined || Number.isNaN(x)) return "n/a";
  const s = (x * 100).toFixed(digits);
  return `${signed && x > 0 ? "+" : ""}${s}%`;
}

export function pp(x: number | null | undefined, digits = 1): string {
  if (x === null || x === undefined || Number.isNaN(x)) return "n/a";
  return `${x > 0 ? "+" : ""}${(x * 100).toFixed(digits)}pp`;
}

export function usd(x: number | null | undefined): string {
  if (x === null || x === undefined || Number.isNaN(x)) return "n/a";
  const a = Math.abs(x);
  if (a >= 1e9) return `$${(x / 1e9).toFixed(2)}B`;
  if (a >= 1e6) return `$${(x / 1e6).toFixed(2)}M`;
  if (a >= 1e3) return `$${(x / 1e3).toFixed(0)}k`;
  return `$${x.toFixed(0)}`;
}

export function bps(x: number | null | undefined): string {
  if (x === null || x === undefined || Number.isNaN(x)) return "n/a";
  return `${x > 0 ? "+" : ""}${x.toFixed(0)} bp`;
}

export function utc(d: Date): string {
  return `${d.toISOString().slice(0, 16).replace("T", " ")}Z`;
}

const etFmt = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York",
  year: "numeric",
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

export function et(d: Date): string {
  return `${etFmt.format(d)} ET`;
}

export function age(from: Date | null, to: Date): string {
  if (!from) return "n/a";
  const h = Math.round((to.getTime() - from.getTime()) / 3_600_000);
  if (h < 48) return `${h}h`;
  return `${Math.round(h / 24)}d`;
}

export function shortAddr(a: string): string {
  return `${a.slice(0, 6)}…${a.slice(-4)}`;
}
