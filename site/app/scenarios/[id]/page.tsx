import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { ScenarioView } from "../../../components/Scenario";
import { scenario } from "../../../lib/queries";

export const revalidate = 300;

// No paths at build time; each is rendered on first request and then cached.
export async function generateStaticParams() {
  return [];
}

export async function generateMetadata(props: PageProps<"/scenarios/[id]">): Promise<Metadata> {
  const { id } = await props.params;
  const r = await scenario(id);
  if (!r) return {};
  return {
    title: `Scenario: ${r.scenario.label}`,
    description: `${r.scenario.label}, pushed through the stack model: the path and the revenue at risk.`,
  };
}

/** The latest run of a scenario; earlier runs are at /scenarios/<id>/<day>. */
export default async function ScenarioPage(props: PageProps<"/scenarios/[id]">) {
  const { id } = await props.params;
  const r = await scenario(id);
  if (!r) notFound();
  return <ScenarioView scenario={r.scenario} days={r.days} />;
}
