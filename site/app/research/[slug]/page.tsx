import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { KINDS, piece, pieces, render } from "../../../lib/research";

// Every piece is built with the site; a slug without a file is a 404.
export const dynamicParams = false;

export function generateStaticParams() {
  return pieces().map((p) => ({ slug: p.slug }));
}

export async function generateMetadata(props: PageProps<"/research/[slug]">): Promise<Metadata> {
  const { slug } = await props.params;
  const p = piece(slug);
  if (!p) return {};
  return { title: p.title, description: p.summary || undefined };
}

export default async function ResearchPiece(props: PageProps<"/research/[slug]">) {
  const { slug } = await props.params;
  const p = piece(slug);
  if (!p) notFound();
  return (
    <article>
      <h1>{p.title}</h1>
      <p className="dim">
        {p.date}, {KINDS[p.kind]}. <Link href="/research">All research</Link>.
      </p>
      <div dangerouslySetInnerHTML={{ __html: render(p.body) }} />
    </article>
  );
}
