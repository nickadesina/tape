import { json } from "../../../lib/api";
import { events } from "../../../lib/queries";

export async function GET() {
  const rows = await events(1000);
  return json({
    rows: rows.map((e) => ({
      id: Number(e.id),
      time: e.time,
      coin: e.coin,
      kind: e.kind,
      address: e.address,
      size_usd: e.size_usd,
      detail: e.detail,
      post_url: e.post_url,
    })),
  });
}
