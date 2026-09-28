/**
 * The scenario calculator ("amplifier"): inputs on top, then article 8's order
 * — the fidelity math (entropy, deciles, DKW, rare categories, tails, HLL++,
 * null patterns), stress at scale (pool reuse, collisions, PK capacity), the
 * performance anatomy (time from measured runs, pool economics, warm vs cold),
 * rule-based recommendations, and article 2's honest tables on the 10k sample.
 */
import { AlertTriangle, Info } from "lucide-react";

import { InfoHint } from "@/components/InfoHint";
import { StatTile } from "@/components/StatTile";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { formatCount, formatDuration, formatFixed, formatPercent } from "@/lib/format";

import { choicesOf, knob } from "../model/knobs";
import { docUrl } from "../model/links";
import { CONSTANTS, PRESETS } from "../model/scenario";
import { useScenario } from "../model/state";
import { CountField, Part } from "../ui";
import { Article2Tables } from "./Article2Tables";
import { FidelityCards } from "./FidelityCards";
import { PerformanceCards } from "./PerformanceCards";
import { ScaleCards } from "./ScaleCards";

const P_CHOICES = [0.01, 0.001, 0.0001, 0.00001];
const Q_CHOICES = [0.9, 0.99, 0.999, 0.9999];

export function ScenarioCalculator({ onOpenKnob }: { onOpenKnob: (id: string) => void }) {
  const { presetId, custom, inputs, outputs, data, setData, setSetting, applyPreset, recommendations } = useScenario();

  return (
    <div className="grid gap-10">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-1">
            Scenario inputs <InfoHint concept="stats:amplification" />
          </CardTitle>
          <p className="text-sm text-text-2">
            Pick a preset or type your own. n, M, the tier, the uniqueness mode and the env are the amp's knobs: turning
            them there changes them here.
          </p>
        </CardHeader>
        <CardContent className="grid gap-5">
          <div className="grid gap-1.5">
            <span id="preset-label" className="text-xs font-medium text-text-2">
              Preset
            </span>
            <ToggleGroup
              type="single"
              value={presetId}
              onValueChange={(id) => id && applyPreset(id)}
              aria-labelledby="preset-label"
              className="flex-wrap justify-start"
            >
              {PRESETS.map((p) => (
                <ToggleGroupItem key={p.id} value={p.id} title={p.description} data-testid={`preset-${p.id}`}>
                  {p.label}
                </ToggleGroupItem>
              ))}
            </ToggleGroup>
            {custom ? (
              <p className="text-xs text-text-3" data-testid="preset-edited">
                Edited after the preset: the URL carries every change, and a knob's reset returns to this preset.
              </p>
            ) : null}
          </div>

          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <CountField
              label="Source rows N"
              value={inputs.N}
              onCommit={(N) => setData({ N })}
              help="The live table the sample is drawn from."
            />
            <CountField
              label="Target rows M"
              value={inputs.M}
              concept="knob:num_rows"
              onCommit={(M) => setSetting("num_rows", M)}
              help="--num_rows"
            />
            <CountField
              label="Reference sample n"
              value={inputs.n}
              concept="knob:reference_rows_limit"
              onCommit={(n) => setSetting("reference_rows_limit", n)}
              help={`--reference_rows_limit (default ${formatCount(CONSTANTS.defaultN)})`}
            />
            <div className="grid content-start gap-1">
              <span className="text-xs font-medium text-text-2">Fixed by the code</span>
              <ul className="grid gap-1 text-sm">
                {(
                  [
                    ["free_text_pool_max", CONSTANTS.poolCap],
                    ["max_row_doc_rows", CONSTANTS.rowDocCap],
                    ["rag_top_k", CONSTANTS.topK],
                  ] as const
                ).map(([id, value]) => (
                  <li key={id}>
                    <button
                      type="button"
                      onClick={() => onOpenKnob(id)}
                      className="inline-flex cursor-pointer items-center gap-1.5 rounded-sm text-left text-text-2 hover:text-text-1"
                    >
                      <svg viewBox="0 0 12 12" className="size-3 shrink-0" aria-hidden="true">
                        <circle cx="6" cy="6" r="5" fill="var(--surface-3)" stroke="var(--control-border)" />
                        <line x1="3" y1="4.5" x2="9" y2="7.5" stroke="var(--text-3)" strokeWidth="1.2" />
                      </svg>
                      {knob(id).label}: <span className="font-mono text-text-1">{formatCount(value)}</span>
                      <span className="sr-only">, fixed constant; open its details</span>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <Choice
              label="Rarest category that must survive (share p)"
              concept="stats:rare-capture"
              value={String(inputs.p)}
              options={P_CHOICES.map((p) => ({ value: String(p), label: formatPercent(p, 3) }))}
              onChange={(v) => setData({ p: Number(v) })}
            />
            <Choice
              label="Deepest tail quantile that matters (q)"
              concept="stats:tail-points"
              value={String(inputs.q)}
              options={Q_CHOICES.map((q) => ({ value: String(q), label: `p${formatNumberTrim(q * 100)}` }))}
              onChange={(v) => setData({ q: Number(v) })}
            />
            <Choice
              label="Stats tier (--source_stats)"
              concept="config:stats-tier"
              value={inputs.tier}
              options={(choicesOf(knob("source_stats")) ?? []).map((c) => ({ value: c, label: c }))}
              onChange={(v) => setSetting("source_stats", v)}
            />
            <Choice
              label="Uniqueness mode (--uniqueness_mode)"
              concept="config:uniqueness-modes"
              value={inputs.uniqueness}
              options={(choicesOf(knob("uniqueness_mode")) ?? []).map((c) => ({ value: c, label: c }))}
              onChange={(v) => setSetting("uniqueness_mode", v)}
            />
          </div>
        </CardContent>
      </Card>

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4" data-testid="scenario-headline">
        <StatTile
          label="Rows per reference row (M/n)"
          value={outputs.ampSample}
          format={(v) => `${formatCount(Math.round(v))}×`}
          concept="stats:amplification"
          footnote={`M/N = ${formatCount(Math.round(outputs.ampSource))}× rows per source row`}
        />
        <StatTile
          label="DKW band ε(n), α = 0.05"
          value={outputs.census ? "0 (census)" : formatFixed(outputs.epsilon, 4)}
          concept="stats:dkw"
          footnote={`n = ${formatCount(outputs.nEff)}: each column's CDF within ±${formatPercent(outputs.epsilon, 2)} of mass (per column, 95 %)`}
        />
        <StatTile
          label={`Rows per pool value (M / ${formatCount(outputs.poolTarget)})`}
          value={Math.round(outputs.poolReuse)}
          format={formatCount}
          concept="stats:pool-reuse"
          footnote={`Pool target min(M, D, ${formatCount(CONSTANTS.poolCap)}) = ${formatCount(outputs.poolTarget)}, D from the ${outputs.poolDistinctVia}`}
        />
        <StatTile
          label="Estimated wall time"
          value={formatDuration(outputs.time.totalMin * 60)}
          concept="config:time-estimate"
          footnote={`From the measured ${outputs.time.basis.label} run, ×${formatFixed(outputs.time.scale, 1)} rows`}
        />
      </div>

      <Part
        id="scn-fidelity"
        number="1"
        title="The fidelity math"
        lead="What the reference sample can estimate, and where it breaks: entropy, deciles and the inverse CDF, the DKW band, rare categories, tails, cardinality (HLL++) and null patterns."
      >
        <FidelityCards data={data} setData={setData} />
      </Part>

      <Part
        id="scn-scale"
        number="2"
        title="Stress at scale"
        lead={`What ${formatCount(inputs.M)} generated rows do to pools, identifier keyspaces and primary keys — none of it is a sampling question.`}
      >
        <ScaleCards data={data} setData={setData} />
      </Part>

      <Part
        id="scn-perf"
        number="3"
        title="Performance anatomy"
        lead="How long the run takes, from measured runs (typed once, in the figure scripts); what persisting pools is worth; and what warming saves — article 8's order."
      >
        <PerformanceCards data={data} setData={setData} />
      </Part>

      <Part
        id="scn-recs"
        number="4"
        title="Recommended settings"
        lead="Rules, each with the document or code it comes from; they re-evaluate as you turn the knobs."
      >
        <ul className="grid gap-2" data-testid="recommendations">
          {recommendations.map((r) => {
            const Icon = r.tone === "warn" ? AlertTriangle : Info;
            return (
              <li
                key={r.id}
                data-rec={r.id}
                className="flex items-start gap-3 rounded-md border border-border bg-surface-1 px-3.5 py-3 text-sm"
              >
                <Icon
                  className={
                    r.tone === "warn"
                      ? "mt-0.5 size-4 shrink-0 text-status-warn-text"
                      : "mt-0.5 size-4 shrink-0 text-link"
                  }
                  aria-hidden="true"
                />
                <div className="grid min-w-0 gap-1">
                  <p className="font-semibold text-text-1">
                    <span className="sr-only">{r.tone === "warn" ? "Warning: " : "Note: "}</span>
                    {r.title}
                  </p>
                  <p className="text-text-2">{r.why}</p>
                  <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
                    <a
                      href={docUrl(r.cite.path, r.cite.line)}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex min-h-6 items-center text-link underline"
                    >
                      Source: {r.cite.label}
                      <span className="sr-only"> (opens in a new tab)</span>
                    </a>
                    {r.knob ? (
                      <button
                        type="button"
                        onClick={() => onOpenKnob(r.knob!)}
                        className="inline-flex min-h-6 cursor-pointer items-center font-mono text-accent-text hover:underline"
                      >
                        Open knob {r.knob}
                      </button>
                    ) : null}
                  </p>
                </div>
              </li>
            );
          })}
        </ul>
      </Part>

      <Part
        id="scn-honest"
        number="5"
        title="The 10k sample, honestly"
        lead="Article 2's two tables, verbatim: what a live reference sample is good and bad at, and what each knob's aftermath is."
      >
        <Article2Tables />
      </Part>
    </div>
  );
}

function formatNumberTrim(v: number): string {
  return Number(v.toFixed(4)).toString();
}

function Choice({
  label,
  concept,
  value,
  options,
  onChange,
}: {
  label: string;
  concept?: string;
  value: string;
  options: Array<{ value: string; label: string }>;
  onChange: (value: string) => void;
}) {
  const id = `choice-${label.replace(/[^a-z]+/gi, "-").toLowerCase()}`;
  return (
    <div className="grid content-start gap-1.5">
      <span id={id} className="flex items-center gap-1 text-xs font-medium text-text-2">
        {label}
        {concept ? <InfoHint concept={concept} /> : null}
      </span>
      <ToggleGroup
        type="single"
        value={value}
        onValueChange={(v) => v && onChange(v)}
        aria-labelledby={id}
        className="flex-wrap justify-start"
      >
        {options.map((o) => (
          <ToggleGroupItem key={o.value} value={o.value} className="font-mono text-xs">
            {o.label}
          </ToggleGroupItem>
        ))}
      </ToggleGroup>
    </div>
  );
}
