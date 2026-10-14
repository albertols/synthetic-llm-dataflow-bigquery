/**
 * The greedy farthest-point walk (Gonzalez 1985), one pick at a time, on the
 * PCA plane. Each point is shaded by its distance (in the full space) to the
 * nearest pick so far — the frontier — and the next pick is always the
 * darkest point. The picks and the frontier are `kcenterWalk`'s, the exact
 * port, computed in the worker (strategyJob.ts). Play animates the walk; under reduced motion there is no playback and
 * the whole walk shows at once, numbered (the static equivalent), with the
 * same steps listed below.
 */
import { Pause, Play, SkipBack, SkipForward } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { InfoHint } from "@/components/InfoHint";
import { Button } from "@/components/ui/button";
import { Slider } from "@/components/ui/slider";
import { formatFixed } from "@/lib/format";
import { useReducedMotion } from "@/lib/motion";

import type { StrategyId } from "../lib/strategies";
import type { WalkData } from "../lib/strategyJob";
import type { Cloud } from "../lib/useCloud";

const W = 420;
const H = 300;
const SHADES = ["--seq-1", "--seq-2", "--seq-3", "--seq-4", "--seq-5", "--seq-6", "--seq-7"] as const;

export function KcenterWalk({
  cloud,
  coords,
  strategy,
  walk,
}: {
  cloud: Cloud;
  coords: Float32Array;
  strategy: StrategyId;
  walk: WalkData;
}) {
  const reducedMotion = useReducedMotion();
  const walkId = walk.strategy;
  const attempt = walk.attempt;
  const steps = walk.steps;
  const total = steps.length;
  const [shown, setShown] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  // Reduced motion: the static equivalent — every step at once.
  const step = reducedMotion ? total : Math.min(shown ?? total, total);

  const isPlaying = playing && !reducedMotion && step < total;

  useEffect(() => {
    if (!isPlaying) return;
    const timer = window.setInterval(() => setShown((s) => Math.min((s ?? 0) + 1, total)), 900);
    return () => window.clearInterval(timer);
  }, [isPlaying, total]);

  const n = cloud.n;
  const top = useMemo(() => {
    let max = 0;
    for (let i = 0; i < n; i += 1) max = Math.max(max, walk.frontier[i] ?? 0);
    return max || 1;
  }, [walk, n]);

  const scale = Math.min(W, H) / 2.7;
  const at = (i: number): [number, number] => [W / 2 + coords[i * 3]! * scale, H / 2 - coords[i * 3 + 1]! * scale];
  const current = step > 0 ? walk.frontier.subarray((step - 1) * n, step * n) : null;
  const picked = steps.slice(0, step);
  const next = step > 0 && step < total ? steps[step] : null;
  const last = step > 0 ? steps[step - 1] : null;
  const lastFrom =
    last && step > 1
      ? picked.slice(0, -1).reduce(
          (best, s) => {
            const d = sq(cloud.rows[s.pick]!, cloud.rows[last.pick]!);
            return d < best.d ? { pick: s.pick, d } : best;
          },
          { pick: -1, d: Infinity },
        )
      : null;

  const text = (i: number) => cloud.meta[i]?.chunk_text ?? "";
  const caption =
    step === 0
      ? "Nothing picked yet. Press Play, or step forward."
      : step === 1
        ? `Pick 1: ${walkId === "kcenter_rotate" ? `item (attempt × k) mod n = ${steps[0]!.pick + 1}` : "the medoid, the item nearest the centroid"} — “${clip(text(steps[0]!.pick))}”.`
        : `Pick ${step}: “${clip(text(last!.pick))}”, the item farthest from every earlier pick (d² = ${formatFixed(last!.distance, 3)}, cos = ${formatFixed(1 - last!.distance / 2, 3)} to its nearest).`;

  return (
    <div className="grid gap-3 rounded-lg border border-border bg-surface-1 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold text-text-1">
          The {walkId === "kcenter_rotate" ? `k-center rotate walk (attempt ${attempt})` : "k-center walk"}, pick by
          pick
        </h3>
        <InfoHint concept={walkId === "kcenter_rotate" ? "rag:kcenter-rotate" : "rag:kcenter"} />
        {strategy !== walkId ? (
          <span className="text-xs text-text-3">(shown for contrast: the selected strategy is not k-center)</span>
        ) : null}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {!reducedMotion ? (
          <Button
            size="sm"
            variant="primary"
            onClick={() => {
              if (isPlaying) return setPlaying(false);
              if (step >= total) setShown(0);
              setPlaying(true);
            }}
          >
            {isPlaying ? <Pause aria-hidden="true" /> : <Play aria-hidden="true" />}
            {isPlaying ? "Pause" : step >= total ? "Replay" : "Play"}
          </Button>
        ) : (
          <span className="text-xs text-text-3">Reduced motion: the whole walk is shown at once.</span>
        )}
        <Button
          size="icon-sm"
          variant="secondary"
          aria-label="Previous pick"
          disabled={reducedMotion || step <= 0}
          onClick={() => {
            setPlaying(false);
            setShown(Math.max(0, step - 1));
          }}
        >
          <SkipBack aria-hidden="true" />
        </Button>
        <Button
          size="icon-sm"
          variant="secondary"
          aria-label="Next pick"
          disabled={reducedMotion || step >= total}
          onClick={() => {
            setPlaying(false);
            setShown(Math.min(total, step + 1));
          }}
        >
          <SkipForward aria-hidden="true" />
        </Button>
        <div className="w-40">
          <Slider
            min={0}
            max={total}
            step={1}
            value={[step]}
            disabled={reducedMotion}
            thumbLabels={["Picks shown"]}
            formatValue={(v) => `${v} of ${total} picks`}
            onValueChange={([v]) => {
              setPlaying(false);
              setShown(v ?? 0);
            }}
          />
        </div>
        <span className="text-xs text-text-2 tabular-nums">
          {step} / {total}
        </span>
      </div>
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1.25fr)_minmax(0,1fr)] lg:items-start">
        <svg
          role="img"
          aria-label={`k-center walk on ${cloud.n} items: ${step} of ${total} picks shown; the list below names them`}
          viewBox={`0 0 ${W} ${H}`}
          className="h-auto max-h-[26rem] w-full rounded-md bg-surface-2"
        >
          {Array.from({ length: cloud.n }, (_, i) => {
            const [x, y] = at(i);
            const shade = current
              ? SHADES[Math.min(SHADES.length - 1, Math.floor((current[i]! / top) * SHADES.length))]!
              : null;
            return (
              <circle
                key={i}
                cx={x}
                cy={y}
                r={2.6}
                fill={shade ? `var(${shade})` : "var(--chart-other)"}
                fillOpacity={shade ? 0.9 : 0.5}
              />
            );
          })}
          {last && lastFrom && lastFrom.pick >= 0 ? (
            <line
              x1={at(lastFrom.pick)[0]}
              y1={at(lastFrom.pick)[1]}
              x2={at(last.pick)[0]}
              y2={at(last.pick)[1]}
              stroke="var(--accent)"
              strokeWidth={1.5}
            />
          ) : null}
          {next ? (
            <circle
              cx={at(next.pick)[0]}
              cy={at(next.pick)[1]}
              r={9}
              fill="none"
              stroke="var(--text-3)"
              strokeWidth={1.5}
            />
          ) : null}
          {picked.map((s, rank) => {
            const [x, y] = at(s.pick);
            return (
              <g key={s.pick} className="motion-safe:transition-opacity">
                <circle
                  cx={x}
                  cy={y}
                  r={7}
                  fill="var(--surface-1)"
                  stroke={rank === step - 1 ? "var(--accent)" : "var(--text-1)"}
                  strokeWidth={2}
                />
                <text x={x} y={y + 3.2} textAnchor="middle" fontSize={8.5} fill="var(--text-1)" fontWeight={600}>
                  {rank + 1}
                </text>
              </g>
            );
          })}
        </svg>
        <div className="grid content-start gap-3">
          <div className="flex flex-wrap items-center gap-2 text-xs text-text-3">
            <span>Distance to the nearest pick:</span>
            <span className="text-text-2">near</span>
            {SHADES.map((s) => (
              <span
                key={s}
                aria-hidden="true"
                className="inline-block h-2.5 w-4 rounded-sm"
                style={{ background: `var(${s})` }}
              />
            ))}
            <span className="text-text-2">far</span>
            <span>· grey ring: the next pick (the farthest point now)</span>
          </div>
          <p className="text-sm text-text-2" aria-live="polite">
            {caption}
          </p>
          <ol className="grid gap-1 text-xs" aria-label="The walk's picks, in order">
            {steps.map((s, rank) => (
              <li key={s.pick} className={rank < step ? "text-text-1" : "text-text-3"}>
                <span className="mr-1.5 font-mono tabular-nums">{rank + 1}.</span>
                {clip(text(s.pick), 56)}
                <span className="ml-1 font-mono text-text-3 tabular-nums">
                  {rank === 0 ? "start" : `d² ${formatFixed(s.distance, 3)}`}
                </span>
              </li>
            ))}
          </ol>
        </div>
      </div>
    </div>
  );
}

function sq(a: ArrayLike<number>, b: ArrayLike<number>): number {
  let d = 0;
  for (let j = 0; j < a.length; j += 1) d += (a[j]! - b[j]!) ** 2;
  return d;
}

function clip(text: string, max = 48): string {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}
