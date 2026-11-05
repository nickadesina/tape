import { chart, chartHour, latestReleasedStressHour, stressReleased } from "../../../../../lib/queries";
import { BASE_PATH, chartPng, parseHourKey, unslug } from "../../../../../lib/urls";

// Chart PNGs. A pinned hour never changes, so it is cached for a year;
// latest.png redirects to the newest pinned hour and is cached for 5 minutes.
export async function GET(_req: Request, ctx: RouteContext<"/c/[kind]/[subject]/[file]">) {
  const { kind, subject, file } = await ctx.params;
  const subj = unslug(decodeURIComponent(subject));
  if (!file.endsWith(".png")) return new Response("not found", { status: 404 });
  const key = file.slice(0, -4);
  if (key === "latest") {
    const h = kind === "stress" ? await latestReleasedStressHour() : await chartHour(kind, subj);
    if (!h) return new Response("not found", { status: 404 });
    return new Response(null, {
      status: 302,
      headers: { location: chartPng(kind, subj, h), "cache-control": "public, max-age=300, s-maxage=300" },
    });
  }
  const hour = parseHourKey(key);
  if (!hour) return new Response("not found", { status: 404 });
  // An unreleased stress run has not passed its gates: its charts do not exist publicly.
  if (kind === "stress" && !(await stressReleased(hour))) return new Response("not found", { status: 404 });
  const c = await chart(kind, subj, hour);
  if (!c) return new Response("not found", { status: 404 });
  const headers = {
    "cache-control": "public, max-age=31536000, immutable",
    "x-license": "CC BY 4.0",
  };
  if (c.bytes) {
    return new Response(new Uint8Array(c.bytes), { headers: { ...headers, "content-type": "image/png" } });
  }
  const base = process.env.S3_PUBLIC_BASE;
  if (c.path && base) {
    return Response.redirect(`${base.replace(/\/$/, "")}/${c.path}`, 308);
  }
  return new Response(`chart stored outside the database and S3_PUBLIC_BASE is unset (${BASE_PATH})`, {
    status: 500,
  });
}
