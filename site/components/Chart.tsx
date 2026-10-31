import Link from "next/link";
import { utc } from "../lib/format";
import { chartPage, chartPng } from "../lib/urls";
import { CopyImage } from "./CopyImage";

/** A chart, its pinned-hour permalink, and a copy button. Nothing else in the way. */
export function Chart({
  kind,
  subject,
  hour,
  alt,
  width = 1200,
  height = 675,
  priority = false,
}: {
  kind: string;
  subject: string;
  hour: Date;
  alt: string;
  width?: number;
  height?: number;
  priority?: boolean;
}) {
  const src = chartPng(kind, subject, hour);
  return (
    <figure>
      {/* Plain img: the PNG is already the final pixels, rendered by the same code as the posts. */}
      <img src={src} alt={alt} width={width} height={height} fetchPriority={priority ? "high" : "auto"} />
      <figcaption>
        <Link href={chartPage(kind, subject, hour)}>permalink {utc(hour)}</Link>
        <a href={src}>png</a>
        <CopyImage src={src} />
      </figcaption>
    </figure>
  );
}

export function Stale({ hour }: { hour: Date | null }) {
  if (!hour) return <p className="notice">No data yet: the indexer has not finished its first sweep.</p>;
  const behind = Date.now() - hour.getTime();
  if (behind < 2 * 3_600_000) return null;
  return (
    <p className="notice">
      The indexer is behind. Showing the last good hour, {utc(hour)} ({Math.floor(behind / 3_600_000)}h ago).
    </p>
  );
}
