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

THESIS: The tape as a broadcast caption. Each page opens on a red time tag, one heavy gothic headline and a white
caption strip with the state of things, the way a 2000s Japanese news super frames a figure. Refuses the SaaS
dashboard: no cards, no KPI tiles, no glow.

OWN-WORLD: Cool grey ground #f3f4f7, white plates, navy #121a4a bands and masthead, red #d3202a tabs, yellow
#ffd23f as a marker only. Display Zen Kaku Gothic New Black; body BIZ UDPGothic; figures BIZ UDGothic. Section
bands carry Japanese glosses in the red tab. Charts share ground, palette and faces.

STORY: The reader sees the time tag, the headline, the caption strip with whale net, 24h move and coverage, then the
chart, then legs and markets ranked by move. Coverage sits beside every number; they leave with a permalink.

FIRST VIEWPORT: Navy masthead (TAPE 建玉, nav with glosses). Red time tag + white chip. Headline 32-66px. Caption
strip with the 最新 tab. The chart begins inside the first viewport on desktop.

FORM: Telop, direction C of six mockups (title card, vertical rail, telop, departure board, manual, signal); owner picked C. seed: none (owner pick)

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance

## Signature interaction

None that moves by default. Rows highlight on hover, the copy-image control confirms in place. The diverging bar
beside whale net in every table is the one data device added.

## Unresolved

Whether to add a dark theme later.
