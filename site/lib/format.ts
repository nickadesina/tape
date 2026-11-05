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

// ---- Stack Engine: quantities in their units ----

/** A number in three significant figures, with k / M / B / T for large ones. */
export function num(x: number | null | undefined): string {
  if (x === null || x === undefined || Number.isNaN(x)) return "n/a";
  const a = Math.abs(x);
  if (a >= 1e12) return `${(x / 1e12).toFixed(2)}T`;
  if (a >= 1e9) return `${(x / 1e9).toFixed(2)}B`;
  if (a >= 1e6) return `${(x / 1e6).toFixed(2)}M`;
  if (a >= 1e5) return `${(x / 1e3).toFixed(0)}k`;
  if (a >= 1e4) return `${(x / 1e3).toFixed(1)}k`;
  if (a >= 100) return x.toFixed(0);
  return String(Number(x.toPrecision(3)));
}

const UNIT: Record<string, string> = {
  usd_per_gpu_hour: "per GPU-hour",
  gpus: "GPUs",
  gb: "GB",
  tb: "TB",
  mw: "MW",
  gw: "GW",
  wafers_per_month: "wafers/month",
  x: "×",
};

/** One figure in its unit: usd as dollars, shares as percent, the rest with the unit after. */
export function amount(x: number | null | undefined, unit: string): string {
  if (x === null || x === undefined || Number.isNaN(x)) return "n/a";
  if (unit === "usd") return usd(x);
  if (unit === "usd_per_gpu_hour") return `$${x.toFixed(2)}`;
  if (unit === "share" || unit === "fraction" || unit === "pct") return pct(x, 1, false);
  if (unit === "x") return `${num(x)}×`;
  return num(x);
}

export function unitLabel(unit: string): string {
  if (unit === "usd" || unit === "share" || unit === "fraction" || unit === "pct" || unit === "x") return "";
  return UNIT[unit] ?? unit.replace(/_/g, " ");
}

/** value [lo–hi] unit; the bracket is left out when the range is a point. */
export function ranged(value: number, lo: number, hi: number, unit: string, withUnit = true): string {
  const u = withUnit ? unitLabel(unit) : "";
  const r = lo === hi ? "" : ` [${amount(lo, unit)}–${amount(hi, unit)}]`;
  return `${amount(value, unit)}${r}${u ? ` ${u}` : ""}`;
}

export function period(start: string, end: string): string {
  return start === end ? end : `${start} to ${end}`;
}
