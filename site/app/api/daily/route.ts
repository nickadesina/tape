import type { NextRequest } from "next/server";
import { json, list } from "../../../lib/api";
import { dailyRange } from "../../../lib/queries";

export async function GET(req: NextRequest) {
  const q = req.nextUrl.searchParams;
  const subjects = q.get("subject") ? list(q.get("subject"), []) : null;
  const metrics = q.get("metric") ? list(q.get("metric"), []) : null;
  const rows = await dailyRange(subjects, metrics, q.get("from") ?? "2000-01-01", q.get("to") ?? "2100-01-01");
  return json({ rows });
}
