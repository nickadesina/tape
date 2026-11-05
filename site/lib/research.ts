import fs from "node:fs";
import path from "node:path";
import { marked } from "marked";

// Research pieces are Markdown files in content/research/<slug>.md, read at
// build time. Front matter is `key: value` lines between two `---` fences;
// see content/research/README.md.

export const KINDS: Record<string, string> = {
  "deep-dive": "deep dive",
  essay: "essay",
  tracker: "tracker",
  "state-of-the-stack": "state of the stack",
};

export type Piece = { slug: string; title: string; date: string; kind: string; summary: string; body: string };

const DIR = path.join(process.cwd(), "content", "research");
const SLUG = /^[a-z0-9][a-z0-9-]*$/;

function parse(slug: string, text: string): Piece {
  const m = /^---\r?\n([\s\S]*?)\r?\n---\r?\n?([\s\S]*)$/.exec(text);
  if (!m) throw new Error(`content/research/${slug}.md: no front matter between --- fences`);
  const meta: Record<string, string> = {};
  for (const line of m[1].split(/\r?\n/)) {
    const i = line.indexOf(":");
    if (i > 0) meta[line.slice(0, i).trim()] = line.slice(i + 1).trim();
  }
  const { title, date, kind, summary } = meta;
  if (!title) throw new Error(`content/research/${slug}.md: no title`);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date ?? "")) throw new Error(`content/research/${slug}.md: date must be YYYY-MM-DD`);
  if (!(kind in KINDS)) throw new Error(`content/research/${slug}.md: kind must be one of ${Object.keys(KINDS).join(", ")}`);
  return { slug, title, date, kind, summary: summary ?? "", body: m[2] };
}

/** Every piece, newest first. README.md is the format note, not a piece. */
export function pieces(): Piece[] {
  if (!fs.existsSync(DIR)) return [];
  return fs
    .readdirSync(DIR)
    .filter((f) => f.endsWith(".md") && f.toLowerCase() !== "readme.md")
    .map((f) => f.slice(0, -3))
    .filter((s) => SLUG.test(s))
    .map((s) => parse(s, fs.readFileSync(path.join(DIR, `${s}.md`), "utf8")))
    .sort((a, b) => (a.date === b.date ? a.slug.localeCompare(b.slug) : a.date < b.date ? 1 : -1));
}

export function piece(slug: string): Piece | null {
  return pieces().find((p) => p.slug === slug) ?? null;
}

export function render(body: string): string {
  return marked.parse(body, { async: false });
}
