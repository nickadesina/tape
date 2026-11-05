# Research pieces

Each piece is one Markdown file in this directory, named `<slug>.md`, and is
served at `/research/<slug>`. The slug is lowercase letters, digits and
hyphens. This README is not a piece and is never listed.

A piece starts with front matter: `key: value` lines between two `---` fences.
`title`, `date` and `kind` are required; `summary` is shown on the index and
should be there too.

```
---
title: The title as it appears on the page
date: 2026-11-05
kind: deep-dive
summary: One or two sentences for the /research index.
---

The body, in Markdown.
```

- `date` is the publication date, `YYYY-MM-DD`. The index lists newest first.
- `kind` is one of `deep-dive`, `essay`, `tracker`, `state-of-the-stack`.
- Values are plain text on one line; nothing is quoted or parsed as YAML.

The site reads these files at build time and renders the body with `marked`,
so a new or changed piece ships with the next deploy. A file with missing or
malformed front matter fails the build rather than publishing wrong.

Figures in a piece should come from the stack model, the gaps or the ledger
and link there, so a reader can check each one against its source.
