import Link from "next/link";
import { KINDS, pieces } from "../../lib/research";

// Pieces are files in the repository: the index is built with the site.
export const metadata = {
  title: "Research",
  description: "Deep dives, essays, trackers and the state of the stack, each figure linked to its source.",
};

export default function ResearchIndex() {
  const list = pieces();
  return (
    <>
      <h1>Research</h1>
      <p>
        Deep dives, essays, trackers and the periodic state of the stack. Figures come from the{" "}
        <Link href="/stack">stack model</Link>, the <Link href="/gaps">gaps</Link> and the{" "}
        <Link href="/ledger">ledger</Link>, and link there.
      </p>
      {list.length === 0 ? (
        <p className="dim">Nothing published yet.</p>
      ) : (
        <ul>
          {list.map((p) => (
            <li key={p.slug}>
              {p.date} <Link href={`/research/${p.slug}`}>{p.title}</Link>{" "}
              <span className="dim">{KINDS[p.kind]}</span>
              {p.summary && <div>{p.summary}</div>}
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
