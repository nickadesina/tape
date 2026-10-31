import type { Metadata } from "next";
import { MarketView, marketMeta } from "../../../components/MarketView";

export const revalidate = 300;

// No paths at build time; each is rendered on first request and then cached.
export async function generateStaticParams() {
  return [];
}

export async function generateMetadata(props: PageProps<"/m/[slug]">): Promise<Metadata> {
  const { slug } = await props.params;
  return marketMeta(slug, "30d");
}

export default async function Page(props: PageProps<"/m/[slug]">) {
  const { slug } = await props.params;
  return <MarketView slugParam={slug} window="30d" />;
}
