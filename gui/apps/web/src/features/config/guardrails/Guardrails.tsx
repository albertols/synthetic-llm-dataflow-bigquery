/**
 * The guardrails explainer: Mode A's lines of defence in the order the code
 * runs them, every DLQ rule as the code emits it (emitted / declared /
 * counted, from generated/dlq_rules.json), the uniqueness modes and the env
 * BLOCKER gate. Where the docs disagree with the code, the code is drawn and
 * a "Docs differ" note says so.
 */
import { ArrowDown, CircleCheck, CircleMinus, CircleX } from "lucide-react";
import { useMemo, type ReactNode } from "react";

import type { DlqRule } from "@contracts/relational";
import { dlqRules } from "@contracts/generated/dlqRules";

import { Callout } from "@/components/Callout";
import { InfoHint } from "@/components/InfoHint";
import { MiniDiagram } from "@/components/MiniDiagram";
import { StatTile } from "@/components/StatTile";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableContainer, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { useRuns } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatCount, formatFixed, formatPercent, MISSING } from "@/lib/format";

import { CITES } from "../citations";
import { CodeLink } from "../CodeLink";
import { MeasuredNote } from "../MeasuredNote";
import { ANNOTATIONS, asText, choicesOf, knob, knobNumber, measured } from "../model/knobs";
import { MEASURED_PAIR_LABEL } from "../model/scenario";
import { useScenario } from "../model/state";
import { Part } from "../ui";

const RULES: readonly DlqRule[] = dlqRules.rules;

/** The DAG's validation chain in code order (pipeline.py, `_table_branch`). */
const STAGES: Array<{
  step: string;
  line: string;
  what: string;
  where: string;
  optional?: string;
}> = [
  {
    step: "GenerateRecordsDoFn",
    line: "Generate",
    what: "The engine writes rows; a crashed batch and an unmatched parent key divert here.",
    where: "packages/sdfb-beam/src/sdfb_beam/dofns/generate.py",
  },
  {
    step: "EnforceFkIntegrityDoFn",
    line: "FK integrity",
    what: "Referential integrity, measured per run: a child row whose parent key was not landed is an orphan.",
    where: CITES.fkIntegrityLine.source,
    optional: "Only on tables with enforced FK edges; the code comment calls it line 4 (ADR 0031).",
  },
  {
    step: "ValidateRecordDoFn",
    line: "Line 1 · per record",
    what: "Pydantic, per record, against the model derived from the BigQuery DDL — plus a load-safety check for non-finite floats.",
    where: CITES.loadSafety.source,
  },
  {
    step: "PanderaValidateBatchDoFn",
    line: "Line 2 · per batch",
    what: "Pandera over batches of 1,000–10,000 rows, lazy (all failures collected); the schema is derived from the same contract.",
    where: CITES.panderaBatch.source,
  },
  {
    step: "EnforceUniqueness",
    line: "Line 3 · uniqueness",
    what: "Full-row, primary-key and identity duplicates divert to the DLQ; the first survivor per key lands.",
    where: CITES.uniquenessLine.source,
  },
];

/** A rule's step: the one dlq.py assigns, or — when it has no mapping — the DoFn file that emits it. */
function stepOf(rule: DlqRule): string | null {
  if (rule.pipeline_step) return rule.pipeline_step;
  if (rule.emitted_by?.includes("validate_record.py")) return "ValidateRecordDoFn";
  return null;
}

export function Guardrails({ onOpenKnob }: { onOpenKnob: (id: string) => void }) {
  return (
    <div className="grid gap-10">
      <LinesOfDefence />
      <DlqRules />
      <UniquenessModes onOpenKnob={onOpenKnob} />
      <BlockerGate onOpenKnob={onOpenKnob} />
    </div>
  );
}

// -------------------------------------------------------- lines of defence --

function LinesOfDefence() {
  return (
    <Part
      id="gr-lines"
      number="1"
      title="Mode A: the lines of defence"
      lead="Every generated row crosses these gates inside the Beam DAG before it lands. A reject is never dropped: it goes to the DLQ with its rule, step and error context."
    >
      <div className="grid gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <ol
          className="grid gap-1"
          aria-label="Validation chain, in the order the pipeline runs it"
          data-testid="defence-chain"
        >
          {STAGES.map((stage, i) => {
            const rules = RULES.filter((r) => r.emitted && stepOf(r) === stage.step);
            return (
              <li key={stage.step} className="grid gap-1">
                <div
                  className={cn(
                    "grid gap-2 rounded-lg border bg-surface-1 p-3.5",
                    stage.line.startsWith("Line") ? "border-accent/50" : "border-border",
                  )}
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-xs font-semibold tracking-wide text-accent-text uppercase">
                      {stage.line}
                    </span>
                    <code className="font-mono text-xs text-text-2">{stage.step}</code>
                    {stage.optional ? <Badge variant="outline">conditional</Badge> : null}
                  </div>
                  <p className="text-sm text-text-2">{stage.what}</p>
                  {stage.optional ? <p className="text-xs text-text-3">{stage.optional}</p> : null}
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="text-xs text-text-3">DLQ rules:</span>
                    {rules.length ? (
                      rules.map((r) => (
                        <code
                          key={r.rule_id}
                          className="rounded-sm bg-surface-3 px-1.5 py-0.5 font-mono text-xs text-text-1"
                        >
                          {r.rule_id}
                        </code>
                      ))
                    ) : (
                      <span className="text-xs text-text-3">none</span>
                    )}
                    <CodeLink source={stage.where} className="ml-auto" />
                  </div>
                </div>
                {i < STAGES.length - 1 ? <ArrowDown className="mx-auto size-4 text-text-3" aria-hidden="true" /> : null}
              </li>
            );
          })}
          <li className="grid gap-1">
            <ArrowDown className="mx-auto size-4 text-text-3" aria-hidden="true" />
            <div className="grid gap-1 rounded-lg border border-border bg-surface-1 p-3.5">
              <span className="font-mono text-xs font-semibold tracking-wide text-text-1 uppercase">
                Land (FILE_LOADS), then the BLOCKER gate
              </span>
              <p className="text-sm text-text-2">
                Valid, unique rows load into the landing table; the gate runs after the load jobs commit, so a failed
                run still writes its validation_runs row.
              </p>
              <CodeLink source={CITES.gateAfterLoad.source} />
            </div>
          </li>
        </ol>
        <div className="grid content-start gap-3">
          <Card>
            <CardContent className="grid gap-2 pt-5">
              <MiniDiagram id="config:three-lines" />
              <p className="flex items-center gap-1 text-sm text-text-2">
                Every reject lands in the DLQ <InfoHint concept="config:three-lines" />
              </p>
            </CardContent>
          </Card>
          <Callout tone="docs-differ" title="Docs differ: what line 3 is">
            <p>
              Article 1 and the validation-mode-a skill call line 3 the BigQuery load's rejects (BigQueryIO FailedRows,
              rule schema.bq_reject). The code labels uniqueness as line 3 and wires no FailedRows handler; no DoFn
              emits schema.bq_reject. A FILE_LOADS load aborts on one invalid row instead, which is why line 1 blocks
              non-finite floats before the load (schema.non_finite). README's list matches the code.
            </p>
            <ul className="mt-2 grid gap-1">
              <li>
                <CodeLink source={CITES.uniquenessLine.source} />
              </li>
              <li>
                <CodeLink source={CITES.loadSafety.source} />
              </li>
              <li>
                <CodeLink source={CITES.article1LineThree.source} />
              </li>
              <li>
                <CodeLink source={CITES.skillLineThree.source} />
              </li>
              <li>
                <CodeLink source={CITES.readmeUniqueness.source} />
              </li>
            </ul>
          </Callout>
        </div>
      </div>
    </Part>
  );
}

// --------------------------------------------------------------- DLQ rules --

function YesNo({ value, yes, no }: { value: boolean; yes: string; no: string }) {
  return value ? (
    <span className="inline-flex items-center gap-1 text-text-1">
      <CircleCheck className="size-3.5 text-status-good-text" aria-hidden="true" />
      {yes}
    </span>
  ) : (
    <span className="inline-flex items-center gap-1 text-text-3">
      <CircleMinus className="size-3.5" aria-hidden="true" />
      {no}
    </span>
  );
}

function DlqRules() {
  const runs = useRuns({ limit: 1000 });
  const seen = useMemo(() => {
    const counts = new Map<string, number>();
    for (const run of runs.data?.data ?? [])
      for (const [rule, count] of Object.entries(run.dlq_by_rule_map))
        counts.set(rule, (counts.get(rule) ?? 0) + count);
    return counts;
  }, [runs.data]);
  const source = runs.data?.dataSource ?? null;
  const fkAnnotation = ANNOTATIONS.find((a) => a.id === "fk-orphan-not-blocker");
  const sorted = [...RULES].sort((a, b) => Number(b.emitted) - Number(a.emitted) || a.rule_id.localeCompare(b.rule_id));
  const nullRequired = RULES.find((r) => r.rule_id === "null.required");
  return (
    <Part
      id="gr-dlq"
      number="2"
      title="The DLQ, rule by rule"
      lead="Each rule_id as the code emits it (an AST scan of the DoFns), what config/thresholds.yml declares, and whether the BLOCKER gate counts it. The three can disagree; this table shows where."
    >
      <TableContainer aria-label="DLQ rules: emitted, declared and counted" data-testid="dlq-rules">
        <Table>
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead scope="col">rule_id</TableHead>
              <TableHead scope="col">Emitted</TableHead>
              <TableHead scope="col">Declared</TableHead>
              <TableHead scope="col">Counted by the gate</TableHead>
              <TableHead scope="col">Stage · step</TableHead>
              <TableHead scope="col">error_type</TableHead>
              <TableHead scope="col" className="text-right">
                Seen in runs{source ? ` (${source})` : ""}
              </TableHead>
              <TableHead scope="col">Emitted by</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {sorted.map((r) => (
              <TableRow key={r.rule_id} data-rule={r.rule_id}>
                <TableCell className="font-mono text-text-1">{r.rule_id}</TableCell>
                <TableCell className="whitespace-nowrap">
                  <YesNo value={r.emitted} yes="emitted" no={r.scope === "post_run" ? "post-run" : "never"} />
                </TableCell>
                <TableCell className="whitespace-nowrap">
                  {r.declared ? (
                    <Badge variant={r.severity === "BLOCKER" ? "accent" : "neutral"}>{r.severity}</Badge>
                  ) : (
                    <span className="text-text-3">not declared</span>
                  )}
                </TableCell>
                <TableCell className="whitespace-nowrap">
                  <YesNo value={r.counted_in_blocker_gate} yes="counted" no="not counted" />
                </TableCell>
                <TableCell className="font-mono text-xs whitespace-nowrap text-text-2">
                  {r.stage ?? MISSING} · {stepOf(r) ?? MISSING}
                  {r.emitted && !r.pipeline_step ? (
                    <span className="block text-text-3">(no step mapping in dlq.py)</span>
                  ) : null}
                </TableCell>
                <TableCell className="font-mono text-xs text-text-2">{r.error_type ?? MISSING}</TableCell>
                <TableCell className="text-right font-mono tabular-nums">
                  {runs.isPending ? "…" : formatCount(seen.get(r.rule_id) ?? 0)}
                </TableCell>
                <TableCell className="whitespace-nowrap">
                  {r.emitted_by ? (
                    <CodeLink source={r.emitted_by}>
                      <code className="font-mono text-xs" title={r.emitted_by}>
                        {r.emitted_by.split("/").pop()}
                      </code>
                    </CodeLink>
                  ) : (
                    MISSING
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </TableContainer>
      <div className="grid gap-3 lg:grid-cols-2">
        {nullRequired && !nullRequired.emitted ? (
          <Callout tone="warn" title="null.required: declared and counted, never emitted">
            thresholds.yml declares it BLOCKER and BLOCKER_RULE_IDS counts it, but no DoFn builds a null.required
            envelope: a NULL on a REQUIRED column fails Pydantic (schema.types) or Pandera (schema.batch) instead. The
            gate's count for it is always zero.
          </Callout>
        ) : null}
        {fkAnnotation ? (
          <Callout tone="docs-differ" title={`Docs differ: ${fkAnnotation.title}`}>
            <p>{fkAnnotation.docs_say}</p>
            <p className="mt-1">
              <strong className="text-text-1">Code does:</strong> {fkAnnotation.code_does}
            </p>
            <p className="mt-1 flex flex-wrap gap-x-3">
              {fkAnnotation.evidence.map((e) => (
                <CodeLink key={e.source} source={e.source} />
              ))}
            </p>
          </Callout>
        ) : null}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-1 text-sm">
              fk.orphan vs fk.unmatched <InfoHint concept="config:fk-rules" />
            </CardTitle>
          </CardHeader>
          <CardContent className="grid gap-2 text-sm text-text-2">
            <p>
              <code className="font-mono text-text-1">fk.orphan</code> — stage pre_write, EnforceFkIntegrityDoFn: a
              generated child row points at a parent key that did not land. Referential integrity is by construction, so
              an orphan is a generator regression.
            </p>
            <p>
              <code className="font-mono text-text-1">fk.unmatched</code> — stage pre_generate, GenerateRecordsDoFn: a
              parent key had no candidate on a conditional edge, so the rows were never generated. Not declared in
              thresholds.yml, so no severity and no gate.
            </p>
          </CardContent>
        </Card>
        <Callout tone="info" title="post_run rules">
          Rules with scope post_run (freetext.copy_fraction, freetext.distinct_floor, freetext.empty_parity,
          numeric.decile_ks) are scored after the run from the landed table; they never produce DLQ rows.
        </Callout>
      </div>
    </Part>
  );
}

// -------------------------------------------------------------- uniqueness --

const MODE_TEXT: Record<string, { barriers: string; lands: string; cost: string }> = {
  exact: {
    barriers: "one full-row shuffle barrier; PK and identity resolved from key-only groups (ADR 0034)",
    lands: "only unique rows; every duplicate diverts to the DLQ",
    cost: "one shuffle of every row",
  },
  exact_chained: {
    barriers: "three barriers: row digest → PK → identity (before ADR 0034)",
    lands: "only unique rows",
    cost: "every row crosses the shuffle three times; kept for A/B runs",
  },
  streaming: {
    barriers: "no barrier between generation and BigQuery",
    lands: "every row, duplicates included; the rate is measured, not removed",
    cost: "cheapest; a failing run is still FAILED_BLOCKER — re-run with --write_disposition=overwrite",
  },
};

function UniquenessModes({ onOpenKnob }: { onOpenKnob: (id: string) => void }) {
  const { settings, setSetting } = useScenario();
  const modes = choicesOf(knob("uniqueness_mode")) ?? [];
  const current = asText(settings.uniqueness_mode);
  const phases = measured("make_throughput_figures.ACCEPT_PHASES_MIN").value as Record<string, number[]>;
  const dedup = phases["dedup + load C + A"] ?? [];
  const runs = (measured("make_throughput_figures.ACCEPT_RUNS").value as string[]).map((r) => r.split("\n")[0] ?? r);
  // exact_chained: the R6 cold run; exact: the R7 pair, one and several SDK processes per worker.
  const minutes: Record<string, Array<{ run: string; value: number }>> = {
    exact_chained: dedup[0] === undefined ? [] : [{ run: runs[0] ?? "R6", value: dedup[0] }],
    exact: [1, 2].flatMap((i) => (dedup[i] === undefined ? [] : [{ run: runs[i] ?? `run ${i}`, value: dedup[i] }])),
    streaming: [],
  };
  return (
    <Part
      id="gr-uniq"
      number="3"
      title="Uniqueness modes"
      lead="How duplicates are handled before landing. The mode is the amp's uniqueness_mode knob; a driven child without identity columns uses driven_uniqueness_mode."
    >
      <div className="flex flex-wrap items-center gap-2">
        <span id="uniq-mode" className="flex items-center gap-1 text-xs text-text-2">
          --uniqueness_mode <InfoHint concept="config:uniqueness-modes" />
        </span>
        <ToggleGroup
          type="single"
          value={current}
          onValueChange={(v) => v && setSetting("uniqueness_mode", v)}
          aria-labelledby="uniq-mode"
        >
          {modes.map((m) => (
            <ToggleGroupItem key={m} value={m} className="font-mono text-xs">
              {m}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
        <button
          type="button"
          onClick={() => onOpenKnob("uniqueness_mode")}
          className="cursor-pointer text-xs text-link hover:underline"
        >
          Knob sheet
        </button>
      </div>
      <div className="grid gap-3 lg:grid-cols-3" data-testid="uniqueness-modes">
        {modes.map((m) => {
          const text = MODE_TEXT[m];
          return (
            <Card key={m} className={cn(m === current && "border-accent/60")} data-mode={m}>
              <CardHeader>
                <CardTitle className="flex items-center gap-2 font-mono text-sm">
                  {m}
                  {m === knob("uniqueness_mode").value ? <Badge>default</Badge> : null}
                  {m === current ? <Badge variant="accent">selected</Badge> : null}
                </CardTitle>
              </CardHeader>
              <CardContent className="grid gap-2 text-sm">
                <Row label="Barriers">{text?.barriers ?? MISSING}</Row>
                <Row label="What lands">{text?.lands ?? MISSING}</Row>
                <Row label="Cost">{text?.cost ?? MISSING}</Row>
                <Row label={`Dedup + load, ${MEASURED_PAIR_LABEL}`}>
                  {minutes[m]?.length ? (
                    <span data-testid={`dedup-minutes-${m}`}>
                      {minutes[m]
                        .map((x) => `${formatFixed(x.value, 1)} min (${x.run.replace(/^R\d+ /, "")})`)
                        .join(" · ")}
                    </span>
                  ) : (
                    <span className="text-text-3">not measured</span>
                  )}
                </Row>
              </CardContent>
            </Card>
          );
        })}
      </div>
      <div className="grid gap-3 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
        <Card>
          <CardContent className="pt-5">
            <MiniDiagram id="config:uniqueness" />
          </CardContent>
        </Card>
        <div className="grid content-start gap-2 text-sm text-text-2">
          <p>
            Driven children (ADR 0036) draw their PK members without replacement per parent key, so their PK is unique
            by construction: <code className="font-mono text-text-1">driven_uniqueness_mode</code> defaults to{" "}
            <code className="font-mono text-text-1">{asText(knob("driven_uniqueness_mode").value)}</code> and measures
            instead of shuffling.
          </p>
          <MeasuredNote ids={["make_throughput_figures.ACCEPT_PHASES_MIN"]} />
        </div>
      </div>
    </Part>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid gap-0.5">
      <span className="text-xs text-text-3">{label}</span>
      <span className="text-text-2">{children}</span>
    </div>
  );
}

// ------------------------------------------------------------ blocker gate --

function BlockerGate({ onOpenKnob }: { onOpenKnob: (id: string) => void }) {
  const { settings, setSetting, inputs } = useScenario();
  const envs = choicesOf(knob("env")) ?? [];
  const current = asText(settings.env);
  return (
    <Part
      id="gr-gate"
      number="4"
      title="The BLOCKER gate, per env"
      lead="After the load jobs commit, a run fails when the share of rows failing a counted BLOCKER rule exceeds its env's ratio (config/thresholds.yml)."
    >
      <div className="flex flex-wrap items-center gap-2">
        <span id="gate-env" className="flex items-center gap-1 text-xs text-text-2">
          --env <InfoHint concept="config:blocker-gate" />
        </span>
        <ToggleGroup
          type="single"
          value={current}
          onValueChange={(v) => v && setSetting("env", v)}
          aria-labelledby="gate-env"
        >
          {envs.map((e) => (
            <ToggleGroupItem key={e} value={e} className="font-mono text-xs">
              {e}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
      </div>
      <div className="grid gap-3 sm:grid-cols-3" data-testid="blocker-ratios">
        {envs.map((e) => {
          const ratio = knobNumber(`blocker_failure_ratio_${e}`);
          return (
            <button
              key={e}
              type="button"
              onClick={() => onOpenKnob(`blocker_failure_ratio_${e}`)}
              className={cn(
                "cursor-pointer rounded-lg text-left focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring",
                e === current && "ring-2 ring-accent/60",
              )}
            >
              <StatTile
                label={`${e}${e === current ? " (selected)" : ""}`}
                value={formatPercent(ratio, 0)}
                footnote={`${formatCount(Math.floor(ratio * inputs.M))} blocker rows of ${formatCount(inputs.M)} before FAILED_BLOCKER`}
              />
            </button>
          );
        })}
      </div>
      <Card>
        <CardContent className="grid gap-2 pt-5 text-sm text-text-2">
          <p className="flex flex-wrap items-center gap-1">
            <CircleX className="size-4 text-status-critical-text" aria-hidden="true" />
            <span>
              FAILED_BLOCKER when counted blocker rows ÷ (valid + DLQ rows the gate weighs) is strictly greater than the
              ratio. engine_failure is weighted by its lost batch size; an ADR 0038 adjusted table excludes pk.duplicate
              from both sides.
            </span>
          </p>
          <CodeLink source={CITES.gateRatio.source} />
        </CardContent>
      </Card>
    </Part>
  );
}
