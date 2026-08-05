---
name: visual-first-documentation
description: Use when writing or updating a design doc, ADR, milestone report, run postmortem, deck, or article that carries measured numbers, geometry/math, architecture, or a new config flag — and when a figure or diagram would carry the claim better than prose.
---

# Visual-first documentation

## Overview

A technical document is a **derived artifact** of measured evidence and code —
not prose that happens to quote numbers. Every quantitative claim traces to one
regenerable source, every figure regenerates from a committed script, and the
same asset set ladders from design doc → milestone report → deck/article
without anything being redrawn.

**Core principle: a number is typed once, in one file, and everything
downstream is generated from it.** A stale figure is worse than no figure.

**REQUIRED SUB-SKILL:** Use `dataviz` before writing any chart code — it owns
form choice, palette, marks, and the validator. Do not restate its rules here.

## The source-of-truth chain

This is the whole skill in one table. Each layer is generated from the one
above it; a measured number appears in exactly one place.

| Layer | Lives in | Rule |
|---|---|---|
| Raw evidence | `integration_tests/<JOB_ID>/` | immutable; never edited |
| Derived constants | `scripts/make_<topic>_figures.py` → `MEASURED` block | **the only place a measured number is typed** |
| Figures | `docs/designs/assets/<topic>-*.png` | generated only; never hand-edited |
| Narrative | `docs/designs/<date>-<topic>.md` | cites figures; no orphan numbers |
| Decision | `docs/adr/NNNN-*.md` | links the design doc; records decision + consequences only |

A superseding run is therefore a **one-place edit**: update `MEASURED`, re-run
the script, and the doc is true again.

## Document contract

Produce these parts, in this order. The contract is what makes the doc
assemblable into a report or deck later.

1. **Status banner** — `DESIGN / ACCEPTED / SUPERSEDED`, plus companion links
   (what this depends on, supersedes, or promotes).
2. **Evidence** — what was measured, named run ID, and the figure that shows it.
3. **Architecture or geometry** — mermaid for shape, PNG for math.
4. **Each flag or knob** — *one panel per mode*. A parameter table alone does
   not satisfy this; the reader must see what each value does.
5. **Acceptance criteria** — falsifiable, keyed to milestones that already exist
   in the code.
6. **Figure provenance** — regeneration command, palette note, and a table of
   figure → file → content.

## Choosing the form

| The claim is about… | Use | Why |
|---|---|---|
| Pipeline/DAG shape, control flow, decision tree | **inline mermaid** | diffable in git, no binary asset, renders everywhere |
| Math, geometry, distributions, measured magnitudes | **generated PNG** | needs real axes and real numbers |
| Enumerable facts with no magnitude relation | **table** | a chart would add nothing |
| A single number that *is* the point | **one sentence** | resist charting one value |

## Mermaid house style — icons + node classes

Inline mermaid is diffable and renders on GitHub, but unstyled boxes make a
Beam DAG, a BigQuery table and a plain value look identical. Every mermaid
diagram in this repo uses ONE shared visual vocabulary so a reader who has
seen one diagram can read them all. First applied:
`docs/designs/2026-08-05-freetext-expansion-modes.md`.

**Node classes** (declare the `classDef` block inline in EVERY diagram —
GitHub renders each fence standalone; explicit `fill` + `color` keeps both
light and dark themes legible):

```text
classDef beam  fill:#eb6834,color:#fff,stroke:#b44f26
classDef cpu   fill:#1baf7a,color:#fff,stroke:#127a55
classDef gpu   fill:#7a3fd1,color:#fff,stroke:#5a2f9d
classDef store fill:#2a78d6,color:#fff,stroke:#1d5599
classDef data  fill:#6b7280,color:#fff,stroke:#4b5563
```

| Class | Color | Means | Typical icons |
|---|---|---|---|
| `beam` | 🟠 orange (the repo palette's `#eb6834`) | **Apache Beam code** — DoFns, transforms, `PCollection`s, DAG stages | 🔀 transform/DoFn · 🧺 PCollection |
| `store` | 🔵 blue `#2a78d6` | Persistent stores — BigQuery tables/datasets, GCS, files | 🗄️ BQ table · 🪣 GCS bucket · 📄 file/JSON artifact |
| `gpu` | 🟣 purple `#7a3fd1` | GPU work — vLLM, LLM calls, CUDA-resident components | 🧠 LLM/vLLM · ⚡ ignition |
| `cpu` | 🟢 aqua (palette `#1baf7a`) | Pure-Python/NumPy engine work on CPU workers | ⚙️ compute · 🎲 seeded draw · 🛡️ guard/gate |
| `data` | ⚪ gray `#6b7280` | Plain values / in-memory data structures | ⚪ value · ∅ null/empty · 🐼 DataFrame |

Rules:

- **Beam code is always orange** — the one non-negotiable mapping; it is
  also Beam's brand color, so DAG shapes read instantly.
- **Text must fit the box**: ≤3 short lines per node (`<br/>` breaks), no
  parentheticals — detail belongs in the prose under the figure, not inside
  the node. If a label needs a fourth line, split the node.
- Stores use the cylinder shape where mermaid allows it: `[("🗄️ name")]`.
- Icons are emoji, never FontAwesome (`fa:` classes need a stylesheet
  GitHub does not load; emoji render everywhere).
- One icon per node, leading the label; don't decorate every word.
- Subgraphs mark lifecycle phases (e.g. `❄️ cold run` vs `🔥 every batch`),
  not ownership — ownership is the node class.

## Every figure carries a claim

Write the one-sentence claim **before** generating the figure. If you cannot
state it, the figure is decoration — cut it.

> *"The last pool completed at t+64 of a 68-minute job."*

This is the bridge to slides and articles: a deck is assembled from figure
claims, and an article is those claims joined by prose. **If a figure has to be
redrawn for the deck, it was authored wrong.** Author at dpi ≥ 160 and a
slide-friendly aspect (single wide panel or two panels side by side).

## The audience ladder

Same figures, three framings — never a second set of assets.

| Artifact | Adds | Drops |
|---|---|---|
| Design doc (engineers) | code refs, line numbers, contracts | — |
| Milestone report (tech leads) | before/after deltas vs targets, risks, caveats | code internals |
| Deck / article (leadership, Medium) | one claim per figure, narrative arc | code refs, provenance appendix |

## Tracking progress visually

Keep one **evolution figure** per long-running metric — the measured value
across successive runs, with estimates hatched rather than solid (see
`assets/embed-cost-evolution.png`). That figure *is* the progress record: it
makes regression visible and stops the doc from becoming a snapshot of one
lucky run.

## External images

Default to a tailored PNG — anything about *your* system, measurements, or
geometry must be generated, so it matches the palette and stays regenerable.

Use a public internet image only for a canonical *external* concept (a
published algorithm's standard diagram, a vendor architecture). When you do,
the provenance table MUST carry **source URL, licence, and retrieval date**.
Never embed an image you cannot attribute, and prefer redrawing it yourself.
Never use decorative or stock imagery.

## Quick reference

```bash
uv run --no-sync python3 scripts/make_<topic>_figures.py   # regenerate + validate palette
```

- Constants live in the script's `MEASURED` block, annotated with the milestone
  and count they came from.
- The script prints palette separation on every run — evidence, not assertion.
- **Render and look at the output** before shipping; the validator checks
  colour, not label collisions or overflow.

## Common mistakes

| Mistake | Fix |
|---|---|
| Measured numbers typed into prose | Move to `MEASURED`; the prose cites the figure |
| Figure made once in a notebook, script not committed | Commit the generator; an unregenerable figure is already stale |
| Parameter table used to explain a flag | One panel per mode |
| Design doc restates the ADR | Link it; the ADR owns decision + consequences |
| Figure kept after the code moved | Regenerate or delete — never leave both |
| Claim written after the figure | Write the claim first; it decides whether the figure exists |

## Status

Structure follows `superpowers:writing-skills`. **Not yet baseline-tested with
subagents** (RED phase outstanding) — validate against application scenarios
before treating its guidance as proven.
