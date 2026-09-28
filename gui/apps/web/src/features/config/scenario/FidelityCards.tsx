/**
 * Part 1 of the calculator — the fidelity math, in article 8's order. Every
 * chart is live (it redraws at the current n, p, q …); each card also carries
 * the design doc's static figure it reuses.
 */
import { useMemo, useState } from "react";

import { dkwEpsilon, normalizedEntropy, rareCaptureProb, tailPoints } from "@synthetic-platform/stats";

import { Callout } from "@/components/Callout";
import { ChartFrame } from "@/components/ChartFrame";
import { Formula } from "@/components/Formula";
import { InfoHint } from "@/components/InfoHint";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Slider } from "@/components/ui/slider";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { formatCount, formatFixed, formatPercent } from "@/lib/format";
import { useTheme } from "@/lib/theme";

import { CodeLink } from "../CodeLink";
import { compact, lineOption, logSpace, pctFormat, pctLogFormat } from "../charts";
import { ANNOTATIONS, knobNumber } from "../model/knobs";
import { DOCS } from "../model/links";
import { ALPHA, CONSTANTS, TAIL_POINTS_WANTED, expectedDistinctUniform } from "../model/scenario";
import { useScenario, type ScenarioData } from "../model/state";
import { CountField, DocFigure, Output } from "../ui";

type Props = { data: ScenarioData; setData: (patch: Partial<ScenarioData>) => void };

export function FidelityCards({ data, setData }: Props) {
  return (
    <div className="grid gap-4">
      <EntropyCard />
      <DecilesCard />
      <DkwCard />
      <div className="grid gap-4 xl:grid-cols-2">
        <RareCard />
        <TailCard />
      </div>
      <DistinctCard data={data} setData={setData} />
      <NullPatternsCard />
    </div>
  );
}

function CardTitleWithHint({ title, concept }: { title: string; concept: string }) {
  return (
    <CardTitle className="flex items-center gap-1">
      {title} <InfoHint concept={concept} />
    </CardTitle>
  );
}

// ------------------------------------------------------------------ entropy --

const ENTROPY_K = [2, 4, CONSTANTS.literalMaxDistinct];

/** One value takes `top` of the mass, the other k − 1 share the rest evenly. */
function skewedNorm(k: number, top: number): number {
  const rest = (1 - top) / (k - 1);
  const counts = [top, ...Array.from({ length: k - 1 }, () => rest)].map((s) => Math.round(s * 1e9));
  return normalizedEntropy(counts) ?? 0;
}

function EntropyCard() {
  const { resolved } = useTheme();
  const [top, setTop] = useState(0.85);
  const [k, setK] = useState(4);
  const rows = useMemo(
    () =>
      Array.from({ length: 40 }, (_, i) => {
        const t = 0.02 + (i / 39) * 0.97;
        const row: Record<string, number> = { top1: Number(t.toFixed(3)) };
        for (const kk of ENTROPY_K) row[`k${kk}`] = t >= 1 / kk ? skewedNorm(kk, t) : Number.NaN;
        return row;
      }),
    [],
  );
  const option = useMemo(
    () =>
      lineOption(
        {
          x: "top1",
          xName: "top-1 share",
          yName: "normalized entropy",
          yMin: 0,
          yMax: 1,
          series: ENTROPY_K.map((kk) => ({ name: `${kk} categories`, y: `k${kk}` })),
          markers: [{ x: top, label: `top-1 = ${pctFormat(top)}` }],
          xFormatter: pctFormat,
        },
        resolved,
      ),
    [top, resolved],
  );
  const norm = skewedNorm(k, Math.max(top, 1 / k));
  const bits = norm * Math.log2(k);
  return (
    <Card>
      <CardHeader>
        <CardTitleWithHint title="Entropy: what distinct cannot see" concept="stats:entropy" />
        <p className="text-sm text-text-2">
          Two columns with the same distinct count can be balanced or collapsed. The profiler records entropy and
          entropy_norm next to distinct and top1_share, so a mode collapse is visible even when distinct looks healthy.
        </p>
      </CardHeader>
      <CardContent className="grid gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <div className="grid gap-3">
          <ChartFrame
            title="Normalized entropy as one value absorbs the mass"
            concept="stats:entropy-norm"
            description="The other values share the rest evenly; 50 is the literal top-values cap."
            option={option}
            data={rows}
            height={240}
          />
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="grid gap-1.5">
              <span className="text-xs text-text-2">Top-1 share: {pctFormat(top)}</span>
              <Slider
                min={0.02}
                max={0.99}
                step={0.01}
                value={[top]}
                onValueChange={([v]) => setTop(v ?? top)}
                thumbLabels={["Top-1 share"]}
                formatValue={pctFormat}
              />
            </div>
            <div className="grid gap-1.5">
              <span id="entropy-k" className="text-xs text-text-2">
                Categories
              </span>
              <ToggleGroup
                type="single"
                value={String(k)}
                onValueChange={(v) => v && setK(Number(v))}
                aria-labelledby="entropy-k"
              >
                {ENTROPY_K.map((kk) => (
                  <ToggleGroupItem key={kk} value={String(kk)}>
                    {kk}
                  </ToggleGroupItem>
                ))}
              </ToggleGroup>
            </div>
          </div>
          <dl className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            <Output label="Entropy H" value={`${formatFixed(bits, 3)} bits`} concept="stats:entropy" />
            <Output label="entropy_norm" value={formatFixed(norm, 3)} concept="stats:entropy-norm" />
            <Output label="Maximum log₂ k" value={`${formatFixed(Math.log2(k), 3)} bits`} />
          </dl>
        </div>
        <DocFigure
          file="stats-entropy-skew.png"
          width={2160}
          height={736}
          alt="Two four-category columns with identical distinct counts: one balanced, one with 85 percent on a single value; normalized entropy falls as the top share grows."
          claim="Distinct cannot tell a balanced enum from a collapsed one; entropy and top1_share can."
          doc={DOCS.sourceStats}
        />
      </CardContent>
    </Card>
  );
}

// ------------------------------------------------------------------ deciles --

/** Illustrative skewed marginal (exponential, rate 1): exact quantile vs the 11-point interpolation. */
function exponentialQuantile(u: number): number {
  return -Math.log1p(-Math.min(u, 0.999));
}

function DecilesCard() {
  const { resolved } = useTheme();
  const { outputs } = useScenario();
  const annotation = ANNOTATIONS.find((a) => a.id === "inverse-cdf-resolution");
  const rows = useMemo(() => {
    const knots = Array.from({ length: 11 }, (_, i) => exponentialQuantile(i / 10));
    return Array.from({ length: 101 }, (_, i) => {
      const u = i / 100;
      const j = Math.min(9, Math.floor(u * 10));
      const t = u * 10 - j;
      return {
        u,
        exact: exponentialQuantile(u),
        deciles: knots[j]! + t * (knots[j + 1]! - knots[j]!),
      };
    });
  }, []);
  const option = useMemo(
    () =>
      lineOption(
        {
          x: "u",
          xName: "uniform draw u",
          yName: "value x = F⁻¹(u)",
          yMin: 0,
          series: [
            { name: "full-sample interpolation (b1, ≈ exact)", y: "exact" },
            { name: "11-point deciles (b2)", y: "deciles" },
          ],
        },
        resolved,
      ),
    [resolved],
  );
  return (
    <Card>
      <CardHeader>
        <CardTitleWithHint title="Deciles and the inverse CDF" concept="stats:deciles" />
        <p className="text-sm text-text-2">
          Draw u uniformly, read x = F⁻¹(u): dense regions catch most draws, so a skewed marginal survives where
          uniform-in-range would flatten it. On the sample tier every decile's mass is within ±
          {outputs.census ? "0" : formatFixed(outputs.epsilon, 4)} (the DKW band at n = {formatCount(outputs.nEff)}).
        </p>
      </CardHeader>
      <CardContent className="grid gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <div className="grid gap-3">
          <ChartFrame
            title="Inverse CDF of a skewed marginal: full sample vs 11 deciles"
            concept="stats:deciles"
            description="Illustrative exponential marginal (a concept, not a run). The top decile is linearised across the tail."
            option={option}
            data={rows}
            height={240}
          />
          {annotation ? (
            <Callout tone="docs-differ" title={`Docs differ: ${annotation.title}`}>
              <p>{annotation.docs_say}</p>
              <p className="mt-1">
                <strong className="text-text-1">The code (drawn above):</strong> {annotation.code_does}
              </p>
              <p className="mt-1 flex flex-wrap gap-x-3 gap-y-1">
                {annotation.evidence.map((e) => (
                  <CodeLink key={e.source} source={e.source} />
                ))}
              </p>
            </Callout>
          ) : null}
        </div>
        <div className="grid content-start gap-3">
          <DocFigure
            file="stats-inverse-cdf.png"
            width={2160}
            height={736}
            alt="Inverse transform sampling through the 11-point decile vector: uniform draws walk to the CDF and down to a value; the synthetic histogram tracks the skewed source, uniform-in-range does not."
            claim="Uniform-in-range flattens a skewed marginal; inverse transform through 11 deciles preserves it."
            doc={DOCS.sourceStats}
          />
          <DocFigure
            file="stats-epoch-deciles.png"
            width={2160}
            height={736}
            alt="Decile lines on a calendar crowd together in a June burst and spread in quiet months; sampled instants follow the burst."
            claim="On the time axis, decile spacing adapts to burst density."
            doc={DOCS.sourceStats}
          />
        </div>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------- DKW --

function DkwCard() {
  const { resolved } = useTheme();
  const { outputs } = useScenario();
  const n = outputs.nEff;
  const rows = useMemo(
    () => logSpace(1_000, 1_000_000, 31).map((x) => ({ n: Math.round(x), epsilon: dkwEpsilon(x, ALPHA) })),
    [],
  );
  const option = useMemo(
    () =>
      lineOption(
        {
          x: "n",
          xType: "log",
          xName: "reference rows n",
          yName: "ε(n)",
          yMin: 0,
          series: [{ name: "ε(n)", y: "epsilon" }],
          markers: [{ x: Math.max(1_000, n), label: outputs.census ? "census" : `n = ${compact(n)}` }],
          xFormatter: compact,
        },
        resolved,
      ),
    [n, outputs.census, resolved],
  );
  return (
    <Card>
      <CardHeader>
        <CardTitleWithHint title="The DKW band: why 10k" concept="stats:dkw" />
        <p className="text-sm text-text-2">
          The sample size follows the estimand, not the output volume: neither N nor M enters the formula. Halving the
          band costs four times the rows.
        </p>
      </CardHeader>
      <CardContent className="grid gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <div className="grid gap-3">
          <ChartFrame
            title="DKW band ε(n) at α = 0.05"
            concept="stats:dkw"
            description="Worst-case CDF error of a sample of n rows, 95 % confidence."
            option={option}
            data={rows}
            height={240}
            columns={[
              { key: "n", format: (v) => formatCount(v as number), align: "right" },
              { key: "epsilon", label: "ε", format: (v) => formatFixed(v as number, 5), align: "right" },
            ]}
          />
          <Formula
            tex={"\\varepsilon(n)=\\sqrt{\\frac{\\ln(2/\\alpha)}{2n}},\\qquad n \\ge \\frac{\\ln 40}{2\\varepsilon^2}"}
            display
          />
        </div>
        <div className="grid content-start gap-3">
          <dl className="grid grid-cols-2 gap-3" data-testid="dkw-outputs">
            <Output
              label="ε at this n"
              value={outputs.census ? "0 (census)" : formatFixed(outputs.epsilon, 4)}
              concept="stats:dkw"
              testId="dkw-epsilon"
            />
            <Output
              label="Tightest honest threshold (2ε)"
              value={outputs.census ? "—" : formatFixed(outputs.thresholdFloor, 4)}
              note="Tighter tests sampling noise, not the generator."
            />
          </dl>
          <DocFigure
            file="sampling-error-dkw.png"
            width={1280}
            height={736}
            alt="The DKW bound falls from about 4.3 percent at 1,000 rows to 1.36 percent at 10,000 and 0.5 percent at 74,000."
            claim="What a sample buys: the same curve is the noise floor of any gate against the sample."
            doc={DOCS.scaling}
          />
        </div>
      </CardContent>
    </Card>
  );
}

// ------------------------------------------------------------ rare categories --

function RareCard() {
  const { resolved } = useTheme();
  const { inputs, outputs } = useScenario();
  const n = outputs.nEff;
  const sizes = useMemo(() => [Math.max(1, Math.round(n / 10)), n, n * 10], [n]);
  const rows = useMemo(
    () =>
      logSpace(1e-5, 0.1, 41).map((p) => {
        const row: Record<string, number> = { p };
        sizes.forEach((size, i) => (row[`n${i}`] = rareCaptureProb(p, size)));
        return row;
      }),
    [sizes],
  );
  const option = useMemo(
    () =>
      lineOption(
        {
          x: "p",
          xType: "log",
          xName: "category share p",
          yName: "P(appears in the sample)",
          yMin: 0,
          yMax: 1,
          series: sizes.map((s, i) => ({ name: `n = ${compact(s)}`, y: `n${i}` })),
          markers: [{ x: inputs.p, label: `p = ${formatPercent(inputs.p, 3)}` }],
          xFormatter: pctLogFormat,
          yFormatter: pctFormat,
        },
        resolved,
      ),
    [sizes, inputs.p, resolved],
  );
  return (
    <Card>
      <CardHeader>
        <CardTitleWithHint title="Rare categories: the first casualty" concept="stats:rare-capture" />
        <p className="text-sm text-text-2">A category absent from the sample is absent from all generated output.</p>
      </CardHeader>
      <CardContent className="grid gap-3">
        <ChartFrame
          title="Probability a share-p category appears"
          concept="stats:rare-capture"
          description="1 − (1 − p)ⁿ at a tenth, one and ten times the current sample."
          option={option}
          data={rows}
          height={240}
        />
        <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4" data-testid="rare-outputs">
          <Output
            label={`P(seen), p = ${formatPercent(inputs.p, 3)}`}
            value={formatPercent(outputs.rareCapture, 3)}
            testId="rare-capture"
          />
          <Output label="Expected rows in sample" value={formatFixed(outputs.rareExpectedRows, 1)} />
          <Output label="n to see it (95 %)" value={formatCount(outputs.rowsToSee)} note="≈ 3/p" />
          <Output label="n to estimate ±10 %" value={formatCount(outputs.rowsToEstimate)} note="≈ 100/p" />
        </dl>
        <DocFigure
          file="rare-category-coverage.png"
          width={1280}
          height={736}
          alt="Coverage curves for rare categories at several sample sizes: a 0.01 percent category is a coin flip at 10,000 rows."
          claim="Rare categories vanish first from a fixed sample."
          doc={DOCS.scaling}
        />
      </CardContent>
    </Card>
  );
}

// -------------------------------------------------------------------- tails --

const TAIL_QS = [0.9, 0.99, 0.999, 0.9999];
const quantileLabel = (q: number) => `p${Number((q * 100).toFixed(2))}`;

function TailCard() {
  const { resolved } = useTheme();
  const { inputs, outputs } = useScenario();
  const n = outputs.nEff;
  const rows = useMemo(() => TAIL_QS.map((q) => ({ quantile: quantileLabel(q), points: tailPoints(n, q) })), [n]);
  const option = useMemo(
    () =>
      lineOption(
        {
          x: "quantile",
          xType: "category",
          yType: "log",
          xName: "quantile q",
          yName: "sample points (log)",
          series: [{ name: "points past the quantile", y: "points", symbols: true }],
          markers: [
            { y: TAIL_POINTS_WANTED, label: `${TAIL_POINTS_WANTED} wanted` },
            { x: quantileLabel(inputs.q), label: "your q" },
          ],
          yFormatter: compact,
        },
        resolved,
      ),
    [inputs.q, resolved],
  );
  return (
    <Card>
      <CardHeader>
        <CardTitleWithHint title="Tails are fragile" concept="stats:tail-points" />
        <p className="text-sm text-text-2">A tail quantile is estimated from the handful of rows beyond it.</p>
      </CardHeader>
      <CardContent className="grid gap-3">
        <ChartFrame
          title={`Sample points past each quantile at n = ${compact(n)}`}
          concept="stats:tail-points"
          description="n(1 − q) on a log scale; the accent lines mark your q and the 20-point rule."
          option={option}
          data={rows}
          height={240}
          columns={[{ key: "quantile" }, { key: "points", format: (v) => formatFixed(v as number, 1), align: "right" }]}
        />
        <dl className="grid grid-cols-2 gap-3" data-testid="tail-outputs">
          <Output
            label={`Points past ${quantileLabel(inputs.q)}`}
            value={formatFixed(outputs.tailPoints, 1)}
            testId="tail-points"
          />
          <Output label={`n for ${TAIL_POINTS_WANTED} points`} value={formatCount(outputs.rowsForTail)} />
        </dl>
        <DocFigure
          file="tail-support.png"
          width={1280}
          height={736}
          alt="Points beyond p99, p99.9 and p99.99 against sample size: at 10,000 rows p99.9 rests on ten points."
          claim="Tail support is n(1 − q): p99.99 at 10k rests on one row."
          doc={DOCS.scaling}
        />
      </CardContent>
    </Card>
  );
}

// ------------------------------------------------------ distinct (HLL++) --

const SHARES = [1, 0.5, 0.05, 0.01];

function DistinctCard({ data, setData }: Props) {
  const { resolved } = useTheme();
  const { inputs, outputs } = useScenario();
  const n = outputs.nEff;
  const s = data.nonEmptyShare;
  const rows = useMemo(
    () =>
      logSpace(10, 10_000_000, 31).map((D) => ({
        D: Math.round(D),
        exact: D,
        sample: Math.min(expectedDistinctUniform(D, n * s), n * s),
      })),
    [n, s],
  );
  const option = useMemo(
    () =>
      lineOption(
        {
          x: "D",
          xType: "log",
          yType: "log",
          xName: "true distinct D",
          yName: "distinct reported",
          series: [
            { name: "exact tier (HLL++)", y: "exact" },
            { name: "sample tier", y: "sample" },
          ],
          markers: [{ x: data.columnDistinct, label: `D = ${compact(data.columnDistinct)}` }],
          xFormatter: compact,
          yFormatter: compact,
        },
        resolved,
      ),
    [data.columnDistinct, resolved],
  );
  return (
    <Card>
      <CardHeader>
        <CardTitleWithHint title="Cardinality: why --source_stats exact" concept="stats:distinct-truncation" />
        <p className="text-sm text-text-2">
          Fractions and deciles are DKW-bounded; a distinct count has no such bound. The sample tier reports at most the
          non-empty rows it saw, and the b1 pool target is min(M, distinct, {CONSTANTS.poolCap}).
        </p>
      </CardHeader>
      <CardContent className="grid gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <div className="grid gap-3">
          <ChartFrame
            title="Distinct values each tier reports"
            concept="stats:hll"
            description={`Sample tier: expected distinct in n·s = ${formatCount(Math.round(n * s))} uniform draws (an upper bound; skew lowers it).`}
            option={option}
            data={rows}
            height={240}
          />
          <div className="grid gap-4 sm:grid-cols-2">
            <CountField
              label="The column's true distinct D"
              value={data.columnDistinct}
              onCommit={(columnDistinct) => setData({ columnDistinct })}
              help="Preset: 4,022, the ADR 0033 column."
            />
            <div className="grid content-start gap-1.5">
              <span id="nonempty-label" className="text-xs font-medium text-text-2">
                Non-empty share s
              </span>
              <ToggleGroup
                type="single"
                value={String(s)}
                onValueChange={(v) => v && setData({ nonEmptyShare: Number(v) })}
                aria-labelledby="nonempty-label"
              >
                {SHARES.map((x) => (
                  <ToggleGroupItem key={x} value={String(x)} className="font-mono text-xs">
                    {pctFormat(x)}
                  </ToggleGroupItem>
                ))}
              </ToggleGroup>
            </div>
          </div>
        </div>
        <div className="grid content-start gap-3">
          <dl className="grid grid-cols-2 gap-3" data-testid="distinct-outputs">
            <Output
              label="Sample tier sees"
              value={`≤ ${formatCount(Math.round(outputs.distinctSample))}`}
              concept="stats:distinct-truncation"
              testId="distinct-sample"
            />
            <Output label="Exact tier counts" value={formatCount(outputs.distinctExact)} concept="stats:hll" />
            <Output label="Pool target (sample)" value={formatCount(outputs.poolTargetSample)} />
            <Output label="Pool target (exact)" value={formatCount(outputs.poolTargetExact)} />
          </dl>
          {inputs.tier === "exact" ? (
            <Callout tone="info" title="Exact tier on">
              One aggregate scan counts distinct with HLL++ (about 0.5 % typical error) and lifts the pool target.
            </Callout>
          ) : (
            <Callout tone="warn" title={`Tier: ${inputs.tier}`}>
              The pool target is sized from the sample: the R6 pair sized a pool at 94 while the source held 4,022 (ADR
              0033, before the source-filter fallback). Turn the amp's source_stats to exact.
            </Callout>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

// ------------------------------------------------------------ null patterns --

function NullPatternsCard() {
  const { outputs } = useScenario();
  return (
    <Card>
      <CardHeader>
        <CardTitleWithHint title="Null rates and null patterns" concept="stats:null-patterns" />
        <p className="text-sm text-text-2">
          A null or empty fraction is a proportion, so the DKW band (±
          {outputs.census ? "0" : formatFixed(outputs.epsilon, 4)} at this n) bounds it too; the explorer adds a Wilson
          interval per column. Row null patterns — which columns are null together — are the one joint statistic the
          profiler keeps (the top {knobNumber("null_pattern_top_k")} patterns, on tables up to{" "}
          {knobNumber("null_pattern_max_cols")} columns); sampling each column's rate independently invents patterns the
          source never had.
        </p>
      </CardHeader>
      <CardContent className="grid gap-4 lg:grid-cols-2">
        <DocFigure
          file="stats-null-patterns.png"
          width={1840}
          height={768}
          alt="Observed row null patterns: columns B and C are null together; the independence model invents 001 and 010 rows and starves the real 011 pattern."
          claim="Independent per-column null draws invent ghost patterns and starve real joint sparsity."
          doc={DOCS.sourceStats}
        />
        <div className="grid content-start gap-2 text-sm text-text-2">
          <p>
            Measured today, not yet consumed: the __table__ pseudo-column stores the top patterns and the designed
            consumer samples row patterns instead of per-column coin flips.
          </p>
          <Formula tex={"P(\\text{null}_A,\\ \\text{null}_B) \\ne P(\\text{null}_A)\\,P(\\text{null}_B)"} display />
          <p>Open the Source stats tab and pick __table__ to see a table's measured patterns.</p>
        </div>
      </CardContent>
    </Card>
  );
}
