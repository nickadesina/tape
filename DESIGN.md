# Design

The visual system of Positioning Tape: the site (`site/`) and the chart PNGs (`tape/render.py`) share it, so a
screenshot of the site and a posted chart are the same picture. Direction: 2000s Japanese broadcast / Evangelion
title card, kept quiet. Each page opens on one heavy Mincho headline on ink, then drops into calm, dense data.

## Tokens

Defined in `site/app/globals.css` (`:root`) and mirrored in `tape/render.py`. Change both together.

| Token | Value | Use |
|---|---|---|
| `--ink` | `#0b0a0f` | Page and chart ground. Dark only; there is no light theme. |
| `--panel` | `#131119` | Row hover, code and pre ground. |
| `--line` | `#26232e` | Hairlines: table rows, figure frames, section rules, chart grid. |
| `--line-strong` | `#3a3645` | Table header rule, button and switch borders. |
| `--text` | `#ecebe4` | Body text; the headline series in charts (whales, AI complex). |
| `--dim` | `#9b97a8` | Secondary text, labels, glosses, tick labels. |
| `--violet` | `#b9a4ff` | Links, focus ring, selection, hover on controls. Nothing else. |
| `--green` | `#9ad45a` | Long / positive values and bars; "on time" status mark. |
| `--orange` | `#ff6a1a` | Short / negative values, warnings (the 注意 band), section-head squares, current nav item. |

Chart series palette (`PALETTE` in render.py), in legend order, deliberately free of green, orange and violet:
`#5CC8E0 #F0B84A #FF7AA8 #7F9CFF #C9B99A #E39BD6 #8FA3B8 #E8DC8A`. Chart callouts take green or orange by the sign
of the value they label.

## Type

Self-hosted, subset to Latin plus the Japanese glosses below (SIL OFL; sources and licences in `site/app/fonts/` and
`tape/fonts/`). Re-subset with `fonttools subset` if a new glyph is needed.

- Display: **Noto Serif JP Black** (900). Page titles (`h1`, `clamp(34px, 6.2vw, 78px)`, leading 1.0, tracking
  -0.02em, balanced), section heads (`h2`, 22px), the wordmark, chart titles.
- Body: **BIZ UDPGothic** 400/700, 16px / 1.65, measure 72ch. Lede 18px.
- Figures: **BIZ UDGothic** 400/700 (half-width monospaced Latin), tabular. Tables 13.5px, status line and captions
  12px, chart ticks and labels.

## Signature elements

- **Title card**: status line (hour UTC, hour ET, sweep) above a single heavy Mincho `h1`.
- **Section head**: orange 0.42em square, Mincho title, small Japanese gloss in `--dim`, hairline to the right edge.
  Glosses: 部門 legs, 銘柄 markets, 速報 events/alerts, 手法 method, 資料 data, 上位 top addresses, 建玉 (open
  positions) beside the wordmark, 注意 on the warning band. Glosses are `aria-hidden` and `lang="ja"`.
- **Diverging bar** beside whale net in every table: 56px track (36px on phones), zero in the middle, ±100% of OI at
  the ends, green right, orange left.
- **Plates**: charts sit in a hairline frame on the same ground with a caption rail (permalink, png, copy image).
  Edge to edge under 720px.
- **Warning band**: solid orange, ink text, 注意 in Mincho. Replaces the status line when the indexer is two or more
  hours behind or has no data.

## Components

- Tables: no vertical rules, hairline rows, header in body face 12px `--dim`, numeric columns right-aligned. Column
  order puts coverage right after whale net and 24h.
- Definition table (`.spec`, market page): label left, figure right, two columns on desktop, one on phones.
- Window switch (`.seg`): bordered segmented control; current item inverted (text on ink → ink on text).
- Buttons: transparent, 1px `--line-strong` border, violet on hover; copy state green (copied) or orange (failed).
- Nav: lowercase labels with glosses; current item has an orange 2px underline. Scrolls horizontally on phones with
  a right-edge fade.

## Rules

- No cards, KPI tiles, glow, gradients, or drop shadows. Depth is hairlines only.
- No motion beyond 120ms colour transitions on hover.
- Japanese stays a gloss layer: never the only label, never set in running text, never emblems or flags.
- Browser surfaces are themed: `::selection` violet, caret violet, focus ring violet, scrollbar `--line-strong`.
- Body and labels meet 4.5:1 on `--ink` (`--dim` is about 6.7:1).
