import Link from "next/link";
import { amount, pct, period, ranged } from "../lib/format";
import type { Gap, GapDetail, GapPoint, Quantity } from "../lib/queries";

// Presentation for the Stack Engine pages. Every number here was written by
// the model job or a loader; these only lay it out.

const KIND: Record<string, string> = {
  filed: "filed",
  company: "company-stated",
  reported: "reported",
  estimate: "estimate",
  market: "market",
};

export function KindLabel({ kind }: { kind: string }) {
  if (kind === "estimate") return <b>estimate</b>;
  return <>{KIND[kind] ?? kind}</>;
}

/** Where a figure comes from: its source document, or for an estimate its method and inputs. */
export function SourceCell({ q }: { q: Quantity }) {
  if (q.kind === "estimate") {
    return (
      <>
        {q.method ?? "method not stated"}
        {q.inputs.length > 0 && (
          <>
            {" from "}
            {q.inputs.map((i, n) => (
              <span key={i.key}>
                {n > 0 ? ", " : ""}
                {i.entity ? <Link href={`/node/${i.entity}#${i.key}`}>{i.key}</Link> : i.key}
              </span>
            ))}
          </>
        )}
      </>
    );
  }
  if (!q.source_url) return <span className="dim">{q.source ?? "no source"}</span>;
  return (
    <a href={q.source_url} title={q.source_title ?? undefined}>
      {q.source_publisher ?? q.source_title ?? "source"}
    </a>
  );
}

export function CheckedCell({ q }: { q: Quantity }) {
  return (
    <span className={q.stale ? "neg" : "dim"}>
      {q.verified_at ?? ""}
      {q.stale ? " stale" : ""}
    </span>
  );
}

/** Current figures for one entity; row ids are the quantity keys so estimates can link their inputs. */
export function QuantityTable({ rows, anchors = true }: { rows: Quantity[]; anchors?: boolean }) {
  return (
    <div className="scroll">
      <table>
        <thead>
          <tr>
            <th>metric</th>
            <th className="n">value [range]</th>
            <th>period</th>
            <th>kind</th>
            <th>source</th>
            <th>checked</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((q) => (
            <tr key={q.id} id={anchors ? q.key : undefined}>
              <td>{q.metric}</td>
              <td className="n">{ranged(q.value, q.lo, q.hi, q.unit)}</td>
              <td>{period(q.period_start, q.period_end)}</td>
              <td>
                <KindLabel kind={q.kind} />
              </td>
              <td>
                <SourceCell q={q} />
              </td>
              <td>
                <CheckedCell q={q} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const DAY = 86_400_000;

/**
 * A node's gap history: per day, the model's range (black) and the market-implied range (red) as two
 * whiskers side by side, with a tick at each mid. Server-rendered; no script.
 */
export function GapChart({ points, label, unit }: { points: GapPoint[]; label: string; unit: string }) {
  if (points.length === 0) return null;
  const W = 720;
  const H = 170;
  const L = 78;
  const R = 8;
  const T = 8;
  const B = 22;
  const t0 = Date.parse(points[0].day);
  const t1 = Date.parse(points[points.length - 1].day);
  const span = Math.round((t1 - t0) / DAY) + 1;
  const step = (W - L - R) / span;
  let ymin = Math.min(...points.map((p) => Math.min(p.model_lo, p.market_lo)));
  let ymax = Math.max(...points.map((p) => Math.max(p.model_hi, p.market_hi)));
  if (ymin === ymax) {
    ymin -= Math.abs(ymin) * 0.1 || 1;
    ymax += Math.abs(ymax) * 0.1 || 1;
  }
  const pad = (ymax - ymin) * 0.05;
  ymin -= pad;
  ymax += pad;
  const y = (v: number) => T + ((ymax - v) / (ymax - ymin)) * (H - T - B);
  const x = (day: string) => L + (Math.round((Date.parse(day) - t0) / DAY) + 0.5) * step;
  const off = Math.min(step * 0.22, 4);
  const sw = Math.max(1, Math.min(step * 0.3, 4));
  const tick = Math.max(2, Math.min(step * 0.35, 6));
  const last = points[points.length - 1];
  const id = `gap-${last.node.replace(/[^a-zA-Z0-9_-]/g, "-")}`;
  // <title> and <desc> take one string each.
  const title = `${label}: model range and market-implied range by day, ${points[0].day} to ${last.day}`;
  const desc =
    `On ${last.day} the model range is ${ranged(last.model_value, last.model_lo, last.model_hi, unit)} and the ` +
    `market range is ${ranged(last.market_value, last.market_lo, last.market_hi, unit)}, a distance of ` +
    `${pct(last.distance, 1)}` +
    (last.overlap ? "; the ranges overlap." : `; the ranges have not overlapped for ${last.days_apart} days.`);
  return (
    <figure>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        width="100%"
        role="img"
        aria-labelledby={`${id}-t ${id}-d`}
        style={{ display: "block", maxWidth: W, border: "1px solid #bfbfbf", fontFamily: "inherit" }}
      >
        <title id={`${id}-t`}>{title}</title>
        <desc id={`${id}-d`}>{desc}</desc>
        <line x1={L} x2={W - R} y1={y(ymax - pad)} y2={y(ymax - pad)} stroke="#e0e0e0" />
        <line x1={L} x2={W - R} y1={y(ymin + pad)} y2={y(ymin + pad)} stroke="#e0e0e0" />
        <text x={L - 4} y={y(ymax - pad) + 4} fontSize="11" textAnchor="end" fill="#595959">
          {amount(ymax - pad, unit)}
        </text>
        <text x={L - 4} y={y(ymin + pad) + 4} fontSize="11" textAnchor="end" fill="#595959">
          {amount(ymin + pad, unit)}
        </text>
        <text x={L} y={H - 6} fontSize="11" fill="#595959">
          {points[0].day}
        </text>
        <text x={W - R} y={H - 6} fontSize="11" textAnchor="end" fill="#595959">
          {last.day}
        </text>
        {points.map((p) => {
          const cx = x(p.day);
          return (
            <g key={p.day}>
              {p.flagged && <rect x={cx - step / 2} width={step} y={T} height={H - T - B} fill="#fbe9e9" />}
              <line x1={cx - off} x2={cx - off} y1={y(p.model_lo)} y2={y(p.model_hi)} stroke="#000" strokeWidth={sw} />
              <line
                x1={cx - off - tick / 2}
                x2={cx - off + tick / 2}
                y1={y(p.model_value)}
                y2={y(p.model_value)}
                stroke="#000"
                strokeWidth={1.5}
              />
              <line
                x1={cx + off}
                x2={cx + off}
                y1={y(p.market_lo)}
                y2={y(p.market_hi)}
                stroke="#c0504d"
                strokeWidth={sw}
              />
              <line
                x1={cx + off - tick / 2}
                x2={cx + off + tick / 2}
                y1={y(p.market_value)}
                y2={y(p.market_value)}
                stroke="#c0504d"
                strokeWidth={1.5}
              />
            </g>
          );
        })}
      </svg>
      <figcaption>
        <span>black: model range</span>
        <span style={{ color: "#c0504d" }}>red: market-implied range</span>
        <span>tick: mid</span>
        <span>shaded: flagged day</span>
      </figcaption>
    </figure>
  );
}

/** The formula, inputs, assumptions and sensitivity a gap was computed with, as the model job wrote them. */
export function GapDetailView({ gap }: { gap: Gap }) {
  const d: GapDetail = gap.detail ?? {};
  return (
    <>
      {d.formula && (
        <p>
          <b>Formula:</b> <code>{d.formula}</code>
        </p>
      )}
      {d.model_from && d.model_from.length > 0 && (
        <p>
          <b>Model side from:</b>{" "}
          {d.model_from.map((k, i) => (
            <span key={k}>
              {i > 0 ? ", " : ""}
              <Link href={`/stack#${k}`}>{k}</Link>
            </span>
          ))}
        </p>
      )}
      {d.inputs && d.inputs.length > 0 && (
        <div className="scroll">
          <table>
            <thead>
              <tr>
                <th>input</th>
                <th className="n">value [range]</th>
                <th>kind</th>
                <th>source</th>
              </tr>
            </thead>
            <tbody>
              {d.inputs.map((i) => (
                <tr key={`${i.name}-${i.key ?? ""}`}>
                  <td>
                    {i.name}
                    {i.key && (
                      <>
                        {" "}
                        <Link href={`/stack#${i.key}`} className="dim">
                          {i.key}
                        </Link>
                      </>
                    )}
                  </td>
                  <td className="n">{ranged(i.value, i.lo, i.hi, i.unit)}</td>
                  <td>{i.kind ? <KindLabel kind={i.kind} /> : ""}</td>
                  <td>{i.source ? <a href={i.source}>source</a> : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {d.assumptions && d.assumptions.length > 0 && (
        <div className="scroll">
          <table>
            <thead>
              <tr>
                <th>assumption</th>
                <th className="n">value [range]</th>
                <th>note</th>
              </tr>
            </thead>
            <tbody>
              {d.assumptions.map((a) => (
                <tr key={a.name}>
                  <td>{a.name}</td>
                  <td className="n">{ranged(a.value, a.lo, a.hi, a.unit)}</td>
                  <td>{a.note ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {d.sensitivity && d.sensitivity.length > 0 && (
        <div className="scroll">
          <table>
            <thead>
              <tr>
                <th>sensitivity: implied mid when</th>
                <th className="n">assumption at low</th>
                <th className="n">assumption at high</th>
              </tr>
            </thead>
            <tbody>
              {d.sensitivity.map((s) => (
                <tr key={s.name}>
                  <td>{s.name}</td>
                  <td className="n">{amount(s.low, gap.unit)}</td>
                  <td className="n">{amount(s.high, gap.unit)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="dim">
        Market prices from Hyperliquid, one venue; not the whole market.
        {d.caveat ? ` ${d.caveat}` : ""}
      </p>
    </>
  );
}
