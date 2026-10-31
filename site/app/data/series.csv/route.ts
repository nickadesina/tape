import type { NextRequest } from "next/server";
import { bad, csv, list, range } from "../../../lib/api";
import { seriesRange } from "../../../lib/queries";

export async function GET(req: NextRequest) {
  const url = req.nextUrl;
  const r = range(url);
  if (!r) return bad("bad from/to");
  const subjects = list(url.searchParams.get("subject"), ["complex"]);
  const metrics = list(url.searchParams.get("metric"), ["whale_net"]);
  const rows = await seriesRange(subjects, metrics, r.from, r.to);
  return csv(
    "tape-series.csv",
    ["subject", "metric", "hour", "value", "coverage"],
    rows.map((x) => [x.subject, x.metric, x.hour.toISOString(), x.value, x.coverage]),
  );
}
