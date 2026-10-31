import { json } from "../../../lib/api";
import { latestHour, seriesAt } from "../../../lib/queries";

export async function GET() {
  const hour = await latestHour();
  if (!hour) return json({ hour: null, subjects: {} });
  return json({ hour, subjects: await seriesAt(hour) });
}
