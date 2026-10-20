# ADR 0043 — Generator config: a typed model, a catalogue, file wins

**Status:** Proposed (2026-10-20).
**Design:** [`docs/DESIGN.md` §8 Configuration](../DESIGN.md#8-configuration)
**Spec:** [`docs/superpowers/specs/2026-10-20-config-record-and-model-launch-design.md`](../superpowers/specs/2026-10-20-config-record-and-model-launch-design.md)

## Context

The generator has 47 plain argparse flags and no `PipelineOptions` subclass
(`sdfb_beam/cli/run_pipeline.py`). About 100 module constants sit beside them.
Defaults are duplicated in up to five places (reference rows 10k,
`vllm_max_model_len` 8192, batch size 16, FK sample floor 100k). Composer and
the CLI disagree on defaults (`vllm_dtype` is float16 in one, auto in the
other). The GUI `/config` page renders `knobs.json` (83 knobs, exported from
code) with prose hand-written in `gui/packages/contracts/src/concepts/config.ts`.
17 IO and identity flags are not covered.

## Decision

- **D2, which settings are settable:** all 47 flags and every constant the GUI already shows as a tunable knob. Derived values and fixed identities are recorded, not settable. Other internal constants stay in code.
- **D3, where the docs live:** one strict catalogue in `sdfb-core`; `config/generator.yaml` and the GUI `/config` are rendered from it.
- **D5, precedence:** the `--config_uri` file wins over flags and DAG params, which win over defaults.
- **D6, launch arguments:** every flag and param keeps working; `config_uri` is added everywhere.
- **D7, IO in config:** yes. One file per environment fully describes a launch.
- **D14, architecture:** a typed Pydantic model (values) plus a YAML catalogue (prose), strict 1:1.

## Consequences

Proposed. Phase A1 of the spec builds the model; consequences are filled in
when it lands.

## Alternatives rejected

See the spec, section 3.

## Acceptance criteria

See the spec, section 12 (AC-A1-1 onward).
