import type { Metadata } from "next";
import { MarketView, marketMeta } from "../../../../components/MarketView";

export const revalidate = 300;

// No paths at build time; each is rendered on first request and then cached.
export async function generateStaticParams() {
  return [];
}

export async function generateMetadata(props: PageProps<"/m/[slug]/[window]">): Promise<Metadata> {
  const { slug, window } = await props.params;
  return marketMeta(slug, window);
}

export default async function Page(props: PageProps<"/m/[slug]/[window]">) {
  const { slug, window } = await props.params;
  return <MarketView slugParam={slug} window={window} />;
}
