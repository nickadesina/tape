import Link from "next/link";
import type { ReactNode } from "react";
import { et, utc } from "../lib/format";
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
      <a href={src} aria-label={`${alt}, full-size PNG`}>
        <img src={src} alt={alt} width={width} height={height} fetchPriority={priority ? "high" : "auto"} />
      </a>
      <figcaption>
        <Link href={chartPage(kind, subject, hour)}>permalink {utc(hour)}</Link>
        <a href={src}>png</a>
        <span className="grow" />
        <CopyImage src={src} />
      </figcaption>
    </figure>
  );
}

/**
 * The line above every title: the hour the page shows. When the indexer is
 * behind, or has nothing yet, it becomes the warning band instead.
 */
export function Status({ hour, children }: { hour: Date | null; children?: ReactNode }) {
  if (!hour)
    return (
      <p className="notice" role="status">
        <b lang="ja">注意</b>
        <span>No data yet: the indexer has not finished its first sweep.</span>
      </p>
    );
  const behind = Date.now() - hour.getTime();
  if (behind >= 2 * 3_600_000)
    return (
      <p className="notice" role="status">
        <b lang="ja">注意</b>
        <span>
          The indexer is behind. Showing the last good hour, {utc(hour)} ({Math.floor(behind / 3_600_000)}h ago).
        </span>
      </p>
    );
  return (
    <p className="status">
      <span className="tag">{utc(hour)}</span>
      <span className="chip">
        {et(hour)}
        {children && <> · {children}</>}
      </span>
    </p>
  );
}
