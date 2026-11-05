"""Loaders: figures straight from their source into `quantities` (EDGAR,
Taiwan, Korea) and the SF Compute order book into `rent_quotes`. Each module
has a pure parser and a `load(s)` that fetches and stores."""


def record_run(conn, job: str, key: str, ok: bool, note: str) -> None:
    conn.execute(
        """insert into runs (job, key, started_at, finished_at, ok, note) values (%s, %s, now(), now(), %s, %s)
           on conflict (job, key) do update set finished_at = now(), ok = excluded.ok, note = excluded.note""",
        (job, key, ok, note[:1000]),
    )
