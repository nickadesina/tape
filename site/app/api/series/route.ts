import type { NextRequest } from "next/server";
import { bad, json, list, range } from "../../../lib/api";
import { seriesRange } from "../../../lib/queries";

export async function GET(req: NextRequest) {
  const url = req.nextUrl;
  const r = range(url);
  if (!r) return bad("bad from/to");
  const subjects = list(url.searchParams.get("subject"), ["complex"]);
  const metrics = list(url.searchParams.get("metric"), ["whale_net"]);
  const rows = await seriesRange(subjects, metrics, r.from, r.to);
  return json({
    from: r.from,
    to: r.to,
    rows: rows.map((x) => ({ subject: x.subject, metric: x.metric, hour: x.hour, value: x.value, coverage: x.coverage })),
  });
}
