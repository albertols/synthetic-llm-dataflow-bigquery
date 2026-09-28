/**
 * Part 3 of the calculator — the performance anatomy, from measured runs only:
 * a time estimate extrapolated from the R6/R7 phases, where the minutes of a
 * cold and a warm 10M-row pair went, and what persisting pools is worth.
 * Every number is read from knobs.json `measured` and cites its MEASURED block.
 */
import type { EChartsOption } from "echarts";
import { useMemo } from "react";

import { Callout } from "@/components/Callout";
import { ChartFrame } from "@/components/ChartFrame";
import { InfoHint } from "@/components/InfoHint";
import { StatTile } from "@/components/StatTile";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { formatCount, formatDuration, formatFixed } from "@/lib/format";
import { readToken, useTheme } from "@/lib/theme";

import { themed } from "../charts";
import { MeasuredNote } from "../MeasuredNote";
import { measured } from "../model/knobs";
import { DOCS } from "../model/links";
import { useScenario, type ScenarioData } from "../model/state";
import { DocFigure } from "../ui";

type Props = { data: ScenarioData; setData: (patch: Partial<ScenarioData>) => void };

const PHASE_TOKENS = ["--chart-2", "--chart-7", "--chart-3", "--chart-1"] as const;

export function PerformanceCards({ data, setData }: Props) {
  return (
    <div className="grid gap-4">
      <TimeCard data={data} setData={setData} />
      <WarmColdCard warm={data.warm} />
      <PoolEconomicsCard />
    </div>
  );
}

function TimeCard({ data, setData }: Props) {
  const { resolved } = useTheme();
  const { inputs, outputs } = useScenario();
  const t = outputs.time;
  const workers = measured("make_throughput_figures.WORKERS").value as number;
  const vcpus = measured("make_throughput_figures.VCPUS_PER_WORKER").value as number;
  const phases = useMemo(
    () => [
      { phase: "startup (launcher + boot)", minutes: t.startupMin, scales: "fixed" },
      { phase: "cold pool branch", minutes: t.poolBranchMin, scales: data.warm ? "skipped (warm)" : "fixed" },
      { phase: "generation", minutes: t.generationMin, scales: `× ${formatFixed(t.scale, 2)}` },
      {
        phase: "dedup + load",
        minutes: t.dedupLoadMin,
        scales: t.dedupLoadMin === null ? "no barrier: not measured" : `× ${formatFixed(t.scale, 2)}`,
      },
    ],
    [t, data.warm],
  );
  const option = useMemo<EChartsOption>(() => {
    const surface = readToken("--surface-1");
    return themed<EChartsOption>(
      {
        grid: { left: 8, right: 16, top: 36, bottom: 28, containLabel: true },
        legend: { type: "scroll", top: 0, left: 0, right: 0 },
        tooltip: { trigger: "item" },
        xAxis: { type: "value", name: "minutes", nameLocation: "middle", nameGap: 24 },
        yAxis: { type: "category", data: ["estimate"], show: false },
        series: phases.map((p, i) => ({
          type: "bar",
          name: p.phase,
          stack: "time",
          data: [p.minutes ?? 0],
          barMaxWidth: 24,
          itemStyle: { color: readToken(PHASE_TOKENS[i]!), borderColor: surface, borderWidth: 2, borderRadius: 0 },
        })),
      },
      resolved,
    );
  }, [phases, resolved]);
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-1">
          How long {formatCount(inputs.M)} rows take <InfoHint concept="config:time-estimate" />
        </CardTitle>
        <p className="text-sm text-text-2">
          Startup and the cold pool branch stay fixed; generation and the dedup + load barrier scale with the rows. The
          basis is the measured run that matches the uniqueness mode ({inputs.uniqueness}) and the SDK topology.
        </p>
      </CardHeader>
      <CardContent className="grid gap-4">
        <div className="flex flex-wrap items-center gap-4">
          <div className="grid gap-1">
            <span id="sdk-label" className="flex items-center gap-1 text-xs text-text-2">
              SDK processes per worker <InfoHint concept="knob:sdk_containers" />
            </span>
            <ToggleGroup
              type="single"
              value={data.sdkContainers}
              onValueChange={(v) => v && setData({ sdkContainers: v as "single" | "multi" })}
              aria-labelledby="sdk-label"
            >
              <ToggleGroupItem value="single">single</ToggleGroupItem>
              <ToggleGroupItem value="multi">multi</ToggleGroupItem>
            </ToggleGroup>
          </div>
          <div className="grid gap-1">
            <span id="warm-label" className="flex items-center gap-1 text-xs text-text-2">
              Pools <InfoHint concept="config:warm-cold" />
            </span>
            <ToggleGroup
              type="single"
              value={data.warm ? "warm" : "cold"}
              onValueChange={(v) => v && setData({ warm: v === "warm" })}
              aria-labelledby="warm-label"
            >
              <ToggleGroupItem value="cold">cold (built this run)</ToggleGroupItem>
              <ToggleGroupItem value="warm">warm (persisted)</ToggleGroupItem>
            </ToggleGroup>
          </div>
          <StatTile
            className="min-w-48"
            label="Estimated wall time"
            value={formatDuration(t.totalMin * 60)}
            footnote={`basis: ${t.basis.label}`}
          />
        </div>
        <ChartFrame
          title="Where the estimated minutes go"
          concept="config:time-estimate"
          description={`Phases of the measured ${t.basis.label} run over ${formatCount(t.basis.rows)} rows, scaled to M = ${formatCount(inputs.M)}.`}
          option={option}
          data={phases}
          height={140}
          columns={[
            { key: "phase" },
            {
              key: "minutes",
              format: (v) => (typeof v === "number" ? formatFixed(v, 1) : "not measured"),
              align: "right",
            },
            { key: "scales", label: "scaling" },
          ]}
          footer={
            <MeasuredNote
              ids={[
                "make_throughput_figures.ACCEPT_PHASES_MIN",
                "make_throughput_figures.ACCEPT_RUNS",
                "make_throughput_figures.ROWS_PER_TABLE",
                "make_throughput_figures.TABLES",
              ]}
            />
          }
        />
        <Callout tone="warn" title="An extrapolation, not a promise">
          Measured on a two-table relational pair on {workers} workers × {vcpus} vCPUs; the estimate assumes the same
          fleet and a table as wide. A {formatFixed(t.scale, 1)}× extrapolation inherits every assumption.
          {inputs.uniqueness === "streaming" ? " streaming has no measured barrier, so dedup + load is left out." : ""}
        </Callout>
      </CardContent>
    </Card>
  );
}

type Phase = [string, number, number, string];

const CLASS_TOKEN: Record<string, `--${string}`> = {
  beam: "--chart-2",
  gpu: "--chart-7",
  cpu: "--chart-3",
  shuffle: "--chart-1",
};

function WarmColdCard({ warm }: { warm: boolean }) {
  const { resolved } = useTheme();
  const coldWall = measured("make_throughput_figures.WALL_MIN_COLD").value as number;
  const warmWall = measured("make_throughput_figures.WALL_MIN_WARM").value as number;
  const id = warm ? "make_throughput_figures.PHASES_WARM" : "make_throughput_figures.PHASES_COLD";
  const phases = measured(id).value as Phase[];
  const rows = useMemo(
    () =>
      phases.map(([label, start, end, kind]) => ({
        phase: label,
        start,
        end,
        minutes: Number((end - start).toFixed(1)),
        kind,
      })),
    [phases],
  );
  const option = useMemo<EChartsOption>(() => {
    const surface = readToken("--surface-1");
    const kinds = [...new Set(rows.map((r) => r.kind))];
    return themed<EChartsOption>(
      {
        grid: { left: 8, right: 16, top: 36, bottom: 24, containLabel: true },
        legend: { type: "scroll", top: 0, left: 0, right: 0, data: kinds },
        tooltip: { trigger: "item" },
        xAxis: { type: "value", name: "minutes since job create", nameLocation: "middle", nameGap: 24 },
        yAxis: {
          type: "category",
          data: rows.map((r) => r.phase),
          inverse: true,
          axisLabel: { width: 180, overflow: "truncate" },
        },
        series: [
          {
            type: "bar",
            stack: "gantt",
            silent: true,
            itemStyle: { color: "transparent" },
            data: rows.map((r) => r.start),
            tooltip: { show: false },
            barMaxWidth: 18,
          },
          ...kinds.map((kind) => ({
            type: "bar" as const,
            name: kind,
            stack: "gantt",
            barMaxWidth: 18,
            itemStyle: {
              color: readToken(CLASS_TOKEN[kind] ?? "--chart-other"),
              borderColor: surface,
              borderWidth: 1,
              borderRadius: 3,
            },
            data: rows.map((r) => (r.kind === kind ? r.minutes : 0)),
          })),
        ],
      },
      resolved,
    );
  }, [rows, resolved]);
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-1">
          Warm vs cold: where the minutes went <InfoHint concept="config:warm-cold" />
        </CardTitle>
        <p className="text-sm text-text-2">
          The R6 pair, 10M rows per table: a cold run and its immediate warm re-trigger. The pool branch is the only
          phase warming removes; generation and the dedup barriers are the same in both.
        </p>
      </CardHeader>
      <CardContent className="grid gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <div className="grid gap-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <StatTile
              label="Cold wall time"
              value={`${formatFixed(coldWall, 1)} min`}
              footnote="pools and chunks built"
            />
            <StatTile
              label="Warm wall time"
              value={`${formatFixed(warmWall, 1)} min`}
              delta={{
                value: warmWall - coldWall,
                vs: "cold",
                goodWhen: "down",
                format: (v) => `${formatFixed(v, 1)} min`,
              }}
              footnote="zero vLLM spawns"
            />
          </div>
          <ChartFrame
            title={`Phases of the ${warm ? "warm" : "cold"} run`}
            description="Colour is the resource class: GPU purple, CPU aqua, shuffle blue, Beam orange."
            option={option}
            data={rows}
            height={260}
            columns={[
              { key: "phase" },
              { key: "start", align: "right" },
              { key: "end", align: "right" },
              { key: "minutes", align: "right" },
              { key: "kind" },
            ]}
            footer={
              <MeasuredNote
                ids={[
                  "make_throughput_figures.WALL_MIN_COLD",
                  "make_throughput_figures.WALL_MIN_WARM",
                  warm ? "make_throughput_figures.PHASES_WARM" : "make_throughput_figures.PHASES_COLD",
                ]}
              />
            }
          />
        </div>
        <DocFigure
          file="throughput-where-time-went.png"
          width={2160}
          height={1504}
          alt="Timeline of the cold 10M-row pair: launcher, worker boot, the pool branch on the GPU, two generate stages on CPU and two dedup barriers."
          claim="Warming buys minutes; generation and the barriers set the ceiling (ADR 0034)."
          doc={DOCS.throughput}
        />
      </CardContent>
    </Card>
  );
}

function PoolEconomicsCard() {
  const llm = measured("make_ws5_figures.POOL_BUILD_LLM").value as number;
  const rebuilt = measured("make_ws5_figures.POOLS_REBUILT").value as number;
  const cached = measured("make_ws5_figures.POOLS_CACHED").value as number;
  const wall = measured("make_ws5_figures.JOB_WALL").value as number;
  const rows = measured("make_ws5_figures.N_ROWS").value as number;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-1">
          Pool persistence economics <InfoHint concept="config:pool-economics" />
        </CardTitle>
        <p className="text-sm text-text-2">
          Before pools were a persisted artifact (ADR 0020), every worker process rebuilt them in setup(). The
          {` ${formatCount(rows)}`}-row run below paid for it in LLM service time.
        </p>
      </CardHeader>
      <CardContent className="grid gap-3">
        <div className="grid gap-3 sm:grid-cols-3" data-testid="pool-economics">
          <StatTile
            label="LLM service time on pool builds"
            value={`${formatFixed(llm / 3600, 1)} GPU-h`}
            footnote={`${formatCount(llm)} s over ${rebuilt} builds`}
          />
          <StatTile label="Pools rebuilt vs cache hits" value={`${rebuilt} / ${cached}`} />
          <StatTile label="Job wall time" value={formatDuration(wall)} />
        </div>
        <MeasuredNote
          ids={[
            "make_ws5_figures.POOL_BUILD_LLM",
            "make_ws5_figures.POOLS_REBUILT",
            "make_ws5_figures.POOLS_CACHED",
            "make_ws5_figures.JOB_WALL",
          ]}
        />
      </CardContent>
    </Card>
  );
}
