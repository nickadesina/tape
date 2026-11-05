# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Primary: someone on X who sees a Positioning Tape chart in a post and taps through, usually on a phone, to get the
context behind it: what the chart says, the pinned permalink, the market page, and whether the number is
trustworthy. Most visits are a single look. Secondary: the operator, who checks the hourly numbers and copies
charts into posts, and the occasional reader who returns to scan legs, markets and events.

## Product Purpose

A daily and hourly chart of who is positioned which way in the AI complex on Hyperliquid (whales, mid, small,
bots), built only from public chain state. Success is a reader who arrives from a post and, within one screen,
knows the current whale positioning, how it moved in 24 hours, and how much of open interest the tape can see.

## Positioning

Every number is reproducible from public Hyperliquid chain state, with coverage published beside it. Positions come
from hourly `clearinghouseState` snapshots of every discovered address, never reconstructed from fills; a chart is
not posted when coverage is under the bar. Neighbouring dashboards show aggregate long/short ratios; the tape cuts
open interest by cohort and makes every cut sum back to it.

## Operating Context

The chart PNGs are rendered by the Python pipeline (`tape/render.py`) and are the same pictures that go to X and
Telegram; the site serves those PNGs plus tables, a permalink per chart-hour, CSV/JSON data, and a method page.
The site is read-only Next.js on Vercel, revalidating every five minutes.

## Capabilities and Constraints

- Routes: home, `/legs`, `/m`, `/m/<market>[/<window>]`, `/events`, `/method`, `/data`, permalinks `/p/...`,
  PNGs `/c/...`, CSV and JSON.
- Terminology: leg, market, cohort (whale, mid, small, bot, fresh overlay), whale net, coverage, crowding, funding,
  premium, liquidation share.
- Every copy change to the method page must stay true to `config/tape.yaml` and the pipeline.
- The tape never names a person; addresses are addresses.

## Brand Commitments

- Name: Positioning Tape, short form Tape. Attribution handle `@zetsurui` (from `TAPE_HANDLE`).
- Voice: plain, exact, unhyped. "Not advice; the tape says who is positioned where, not what to buy."
- The site and the posted charts share one look: a screenshot of the site and a post are the same picture.
- Owner direction (2026-11-05): 2000s Japanese / Evangelion-adjacent feel, not overtly so. The owner invited
  experimentation; earlier rules (monospace everywhere, default matplotlib look) may change. Picked the "Telop"
  direction (broadcast caption, light) from six mockups on 2026-11-05.

## Evidence on Hand

Live data only: series, events, top positions and charts from the pipeline. No testimonials, users, or press exist;
none may be invented.

## Product Principles

- Coverage travels with every number; a gap is shown, not hidden.
- The chart is the product; the page gives it context and gets out of the way.
- Everything is reproducible: permalinks pin the hour, data is downloadable, the method is public.
- Plain words over persuasion.
