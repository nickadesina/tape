# Design

The visual system of Positioning Tape: the site (`site/`) and the chart PNGs (`tape/render.py`) share it, so a
screenshot of the site and a posted chart are the same picture. Direction ("Telop", chosen 2026-11-05 from six
mockups): a 2000s Japanese broadcast caption, kept quiet. Navy caption bands with a red tab, white plates on a
cool grey ground, one heavy gothic voice for titles.

## Tokens

Defined in `site/app/globals.css` (`:root`) and mirrored in `tape/render.py`. Change both together.

| Token | Value | Use |
|---|---|---|
| `--ground` | `#f3f4f7` | Page and chart ground. Light only. |
| `--paper` | `#ffffff` | Plates: tables, figures, the caption strip, the spec sheet. |
| `--zebra` | `#f7f8fb` | Even table rows. |
| `--line` / `--line-soft` | `#d7dae3` / `#e4e6ec` | Plate borders / row rules; chart grid uses `--line`. |
| `--text` | `#121733` | Body text; the headline series in charts (whales, AI complex). |
| `--dim` | `#4c5270` | Secondary text, table header band. |
| `--navy` / `--navy-2` | `#121a4a` / `#2a3474` | Masthead, section bands, footer, buttons / hover and current nav item. |
| `--on-navy` | `#c9cde0` | Secondary text on navy. |
| `--red` | `#d3202a` | Section-head tabs, the time tag, the warning caption, short bars. |
| `--yellow` | `#ffd23f` | Only as a marker: the 最新 tab, the 注意 tab, the current-nav underline, selection. |
| `--link` | `#1a4fc4` | Links and focus ring. |
| `--long` / `--short` | `#1e7a36` / `#c81d26` | Signed figures as text. Bars use `#1e8e3e` / `#d3202a`. |

Chart series palette (`PALETTE` in render.py), in legend order, free of the long, short and link colours:
`#2F6FE0 #E08A00 #C2185B #7A4FD0 #0E7C86 #8C5A2B #6B7280 #B08900`. Chart callouts take green or red by sign.

## Type

Self-hosted, subset to Latin plus the Japanese glosses below (SIL OFL; licences in `site/app/fonts/` and
`tape/fonts/`). Re-subset with `fonttools subset` when a new glyph is needed.

- Display: **Zen Kaku Gothic New Black** (900). Page titles (`clamp(32px, 5.2vw, 66px)`, leading 1.12), section
  bands (20px), tabs, the wordmark, chart titles.
- Body: **BIZ UDPGothic** 400/700, 16px / 1.65, measure 72ch. Lede 18px.
- Figures: **BIZ UDGothic** 400/700 (half-width monospaced Latin), tabular. Tables 13.5px, captions 12px,
  the time tag, chart ticks.

## Signature elements

- **Time tag**: red box with the UTC hour, white chip with ET and the sweep size. Above every title.
- **Caption strip** (home, market): yellow 最新 ("latest") tab, white plate with the lede, soft shadow.
- **Section band**: red tab with the Japanese gloss, navy band with the English title; the table's slate header
  row sits directly under it. Glosses: 部門 legs, 銘柄 markets, 速報 events, 上位 top addresses, 資料 data; 建玉
  (open positions) beside the wordmark. Glosses are `aria-hidden` and `lang="ja"`. A band without a gloss keeps a
  12px red tab.
- **Warning caption**: red strip, white text, yellow 注意 tab. Replaces the time tag when the indexer is two or more
  hours behind or has no data.
- **Diverging bar** beside whale net: 56px (36px on phones), zero in the middle, ±100% of OI at the ends.
- **Plates**: charts on white with a caption rail (permalink, png, copy image); edge to edge under 720px.

## Components

- Tables: white sheet, slate header row with white labels, zebra rows, yellow-tint hover, numbers right-aligned;
  coverage right after whale net and 24h.
- Spec sheet (market page): two-column definition table on a white plate.
- Window switch: navy-bordered segmented control, current item navy with white text.
- Buttons: navy fill, white text; green when copied, red when the copy failed.
- Nav: white on the navy masthead with small glosses; current item lighter navy with a yellow underline.

## Rules

- No cards, KPI tiles, gradients, glow. One soft shadow, on the caption strip only.
- No motion beyond 120ms colour transitions on hover.
- Yellow is a marker, never a text colour on the light ground.
- Japanese stays a gloss layer: never the only label, never in running text, never emblems or flags.
- Body and labels meet 4.5:1 (`--dim` on `--ground` is about 7:1).
