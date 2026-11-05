import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { ScenarioView } from "../../../../components/Scenario";
import { scenario } from "../../../../lib/queries";

// A past run never changes.
export const revalidate = 86400;

export async function generateStaticParams() {
  return [];
}

export async function generateMetadata(props: PageProps<"/scenarios/[id]/[day]">): Promise<Metadata> {
  const { id, day } = await props.params;
  const r = await scenario(id, day);
  if (!r || r.scenario.day !== day) return {};
  return {
    title: `Scenario: ${r.scenario.label}, ${day}`,
    description: `${r.scenario.label} as run on ${day}: the path and the revenue at risk.`,
  };
}

export default async function ScenarioDayPage(props: PageProps<"/scenarios/[id]/[day]">) {
  const { id, day } = await props.params;
  const r = await scenario(id, day);
  if (!r || r.scenario.day !== day) notFound();
  return <ScenarioView scenario={r.scenario} days={r.days} />;
}
