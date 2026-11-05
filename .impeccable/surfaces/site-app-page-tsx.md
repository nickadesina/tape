---
version: 1
slug: "site-app-page-tsx"
primary_target: "site/app/page.tsx"
related_targets: ["site/app/layout.tsx","site/app/globals.css","tape/render.py"]
---

## Scope

The whole public site (layout, home, legs, markets, market page, events, method, data, permalinks) and the chart
PNGs it serves. Visitor mode: Read, with Operate-style tables. Code-led (no image generation).

## Audience and job

An X reader on a phone at night who tapped a chart: understand the current whale positioning, its 24h move and the
coverage, then optionally open the permalink, the market page, or the method.

## Direction contract

THESIS: The tape as an episode title card. Each page opens on one heavy Mincho headline, white on near-black, the way
a late-90s/2000s anime card or a Japanese broadcast caption does, then drops into calm dense data. Refuses the
category default of a SaaS dashboard: no cards, no KPI tiles, no glow, no neon.

OWN-WORLD: Ground ink #0b0a0f; hairlines #26232e; text #ecebe4; dim #9b97a8. Unit-01 palette without the robot:
violet #b9a4ff for links and controls, green #9ad45a for long/positive, NERV orange #ff6a1a for short/negative and
warnings. Display: Noto Serif JP Black (Matisse-like heavy Mincho). Body: BIZ UDPGothic. Figures: BIZ UDGothic,
tabular. Section heads carry a small Japanese gloss (部門, 銘柄, 速報) and a solid orange square, 2000s Japanese
portal style. Charts use the same ground, palette and faces.

STORY: The reader sees the headline, the one-sentence state of the complex with its numbers, the chart, then legs and
markets ranked by move. They believe it because coverage sits beside every number; they leave with a permalink.

FIRST VIEWPORT: Top hairline header: TAPE 建玉 wordmark left, nav right with glosses. Status line in figures: as-of
hour, coverage, sweep size. Title card headline at 36-64px. Lede sentence. The chart PNG begins inside the first
viewport on desktop and right after the lede on phones.

FORM: Brief-pinned world (2000s Japanese / Eva, not overt); brief-pinned, no roll. seed: none (brief-pinned)

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance

## Signature interaction

None that moves by default. Rows highlight on hover, the copy-image control confirms in place. The diverging bar
beside whale net in every table is the one data device added.

## Unresolved

Whether to add a light theme later; whether Japanese glosses stay after the owner sees them.
