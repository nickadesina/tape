import { Status } from "../../components/Chart";
import { H2, MarketTable } from "../../components/Tables";
import { latestHour, legs, markets, seriesAt } from "../../lib/queries";

export const revalidate = 300;
export const metadata = { title: "Markets" };

export default async function Markets() {
  const [hour, legList, marketList] = await Promise.all([latestHour(), legs(), markets()]);
  const latest = hour ? await seriesAt(hour) : {};
  return (
    <>
      <Status hour={hour} />
      <h1>Markets in the AI complex</h1>
      {legList.map((l) => (
        <section key={l.key}>
          <H2 id={l.key}>{l.label}</H2>
          <MarketTable
            rows={marketList.filter((m) => m.leg === l.key).map((m) => ({ ...m, s: latest[m.coin] ?? {} }))}
          />
        </section>
      ))}
    </>
  );
}
