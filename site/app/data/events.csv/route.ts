import { csv } from "../../../lib/api";
import { events } from "../../../lib/queries";

export async function GET() {
  const rows = await events(5000);
  return csv(
    "tape-events.csv",
    ["id", "time", "coin", "kind", "address", "size_usd", "detail", "post_url"],
    rows.map((e) => [e.id, e.time.toISOString(), e.coin, e.kind, e.address, e.size_usd, JSON.stringify(e.detail), e.post_url]),
  );
}
