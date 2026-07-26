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
