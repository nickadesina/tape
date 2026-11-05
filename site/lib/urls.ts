// Every chart has a permalink that pins its hour. Coins carry a colon
// (xyz:CRWV) that goes into URLs as a dash (xyz-CRWV).

export const HANDLE = process.env.TAPE_HANDLE ?? "@zetsurui";
export const SITE_URL = (process.env.SITE_URL ?? "http://localhost:3000").replace(/\/$/, "");
export const BASE_PATH = process.env.BASE_PATH ?? "";

export function slug(subject: string): string {
  return subject.replace(/:/g, "-");
}

export function unslug(s: string): string {
  return s.replace(/^(xyz|io|leg)-/, "$1:");
}

export function hourKey(d: Date): string {
  return d.toISOString().slice(0, 13) + "Z";
}

export function parseHourKey(k: string): Date | null {
  const m = /^(\d{4}-\d{2}-\d{2})T(\d{2})Z$/.exec(k);
  if (!m) return null;
  const d = new Date(`${m[1]}T${m[2]}:00:00Z`);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** Phone versions of a chart are stored under kind + this suffix ("home-m"). */
export const PHONE_SUFFIX = "-m";
/** Below this width the site serves the phone version (matches globals.css). */
export const PHONE_MEDIA = "(max-width: 719px)";

/** Path (with base path) to a chart PNG. */
export function chartPng(kind: string, subject: string, hour: Date): string {
  return `${BASE_PATH}/c/${kind}/${slug(subject)}/${hourKey(hour)}.png`;
}

/** Path (without base path: for <Link>) to a chart's permalink page. */
export function chartPage(kind: string, subject: string, hour: Date): string {
  return `/p/${kind}/${slug(subject)}/${hourKey(hour)}`;
}

export function absolute(path: string): string {
  return `${SITE_URL}${path.startsWith(BASE_PATH) ? path.slice(BASE_PATH.length) : path}`;
}

export const EXPLORER = "https://app.hyperliquid.xyz/explorer/address/";
