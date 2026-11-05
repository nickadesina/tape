import { H2 } from "../../components/Tables";
import { CHANGELOG } from "../../lib/changelog";
import { metricNames, subjectNames } from "../../lib/queries";
import { BASE_PATH, HANDLE } from "../../lib/urls";

export const revalidate = 300;
export const metadata = { title: "Data" };

export default async function Data() {
  const [metrics, subjects] = await Promise.all([metricNames(), subjectNames()]);
  const b = BASE_PATH;
  return (
    <>
      <h1>Data</h1>
      <p className="lede">
        Every series behind the charts, updated hourly. Licensed{" "}
        <a href="https://creativecommons.org/licenses/by/4.0/">CC BY 4.0</a>. Attribution string: <b>{HANDLE}</b>.
      </p>

      <H2 ja="資料">Hourly series</H2>
      <pre>
        {`${b}/data/series.csv?subject=complex&metric=whale_net&from=2026-10-01&to=2026-10-31
${b}/api/series?subject=xyz:CRWV,leg:neoclouds&metric=whale_net,coverage&from=2026-10-01`}
      </pre>
      <p>
        Parameters: <code>subject</code> and <code>metric</code> take comma-separated lists (default complex, whale_net),{" "}
        <code>from</code> and <code>to</code> take ISO dates or times (default the last 30 days). Columns: subject,
        metric, hour (UTC), value, coverage. Every row carries the coverage of its subject at that hour.
      </p>
      <p>Subjects: {subjects.join(", ") || "none yet"}.</p>
      <p>Metrics: {metrics.join(", ") || "none yet"}. Definitions are on the method page.</p>

      <H2>Daily series</H2>
      <pre>{`${b}/data/daily.csv?from=2026-10-01\n${b}/api/daily?subject=complex`}</pre>
      <p>One row per ET day from the daily snapshot hour, only for subjects whose coverage cleared the posting bar.</p>

      <H2>Events</H2>
      <pre>{`${b}/data/events.csv\n${b}/api/events`}</pre>

      <H2>Latest</H2>
      <pre>{`${b}/api/latest`}</pre>

      <H2>Charts</H2>
      <p>
        Every chart is a PNG at a URL that pins its hour, e.g. <code>{b}/c/home/complex/2026-10-31T19Z.png</code>, and{" "}
        <code>latest.png</code> in place of the hour redirects to the newest one.
      </p>

      <H2>Changelog</H2>
      <ul>
        {CHANGELOG.map((c) => (
          <li key={c.date + c.change.slice(0, 20)}>
            {c.date}: {c.change}
          </li>
        ))}
      </ul>
    </>
  );
}
