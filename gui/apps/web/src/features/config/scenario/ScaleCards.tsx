/**
 * Part 2 of the calculator — stress at scale: pool reuse (M/512), identifier
 * collisions (the birthday bound) and PK capacity. None of it is a sampling
 * question: M, not n, drives every number here.
 */
import { useMemo } from "react";

import { birthdayCollisionProb } from "@synthetic-platform/stats";

import { Callout } from "@/components/Callout";
import { ChartFrame } from "@/components/ChartFrame";
import { InfoHint } from "@/components/InfoHint";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Slider } from "@/components/ui/slider";
import { formatCount, formatPercent } from "@/lib/format";
import { useTheme } from "@/lib/theme";

import { compact, lineOption, pctFormat } from "../charts";
import { asText, knob } from "../model/knobs";
import { DOCS } from "../model/links";
import { CONSTANTS, formatSci } from "../model/scenario";
import { useScenario, type ScenarioData } from "../model/state";
import { DocFigure, Output } from "../ui";

type Props = { data: ScenarioData; setData: (patch: Partial<ScenarioData>) => void };

export function ScaleCards({ data, setData }: Props) {
  return (
    <div className="grid gap-4">
      <div className="grid gap-4 xl:grid-cols-2">
        <PoolReuseCard />
        <PkCapacityCard />
      </div>
      <BirthdayCard data={data} setData={setData} />
    </div>
  );
}

function PoolReuseCard() {
  const { inputs, outputs, settings } = useScenario();
  const expansion = asText(settings.freetext_expansion ?? knob("freetext_expansion").value);
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-1">
          Pool reuse <InfoHint concept="stats:pool-reuse" />
        </CardTitle>
        <p className="text-sm text-text-2">
          A free-text pool holds at most {CONSTANTS.poolCap} values, built once per column and digest; every generated
          row draws from it.
        </p>
      </CardHeader>
      <CardContent className="grid gap-3">
        <dl className="grid grid-cols-2 gap-3" data-testid="pool-outputs">
          <Output
            label="Rows per pool value"
            value={formatCount(Math.round(outputs.poolReuse))}
            concept="stats:pool-reuse"
            testId="pool-reuse"
            note={`${formatCount(inputs.M)} rows / ${CONSTANTS.poolCap} values`}
          />
          <Output
            label="Row documents embedded"
            value={formatPercent(outputs.rowDocShare, 1)}
            concept="knob:max_row_doc_rows"
            note={`${formatCount(CONSTANTS.rowDocCap)} of n = ${formatCount(outputs.nEff)} rows; top-${CONSTANTS.topK} seeds per prompt`}
          />
        </dl>
        <Callout tone="info" title="Memorization and diversity">
          <p>
            Reuse is not memorization: pool values are LLM-written and rejected against the source domain. It caps
            diversity — a pool column has at most {CONSTANTS.poolCap} distinct values, so identifiers never come from a
            pool. With --freetext_expansion <code className="font-mono">{expansion}</code>
            {expansion === "off"
              ? ", free-text distinct counts stop at the pool size."
              : ", code-like columns expand from their shape mix instead."}
          </p>
        </Callout>
      </CardContent>
    </Card>
  );
}

function PkCapacityCard() {
  const { inputs, outputs } = useScenario();
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-1">
          PK capacity <InfoHint concept="stats:duplicate-share" />
        </CardTitle>
        <p className="text-sm text-text-2">
          A primary key must have room for M distinct values before a single row is generated.
        </p>
      </CardHeader>
      <CardContent className="grid gap-3">
        <dl className="grid grid-cols-2 gap-3" data-testid="pk-outputs">
          <Output
            label="PK routed to a pool: rows lost"
            value={formatCount(outputs.pkPoolDlq)}
            note={`Only ${CONSTANTS.poolCap} distinct values survive (the pool cap's code comment tells the run that learned it).`}
            testId="pk-pool-dlq"
          />
          <Output
            label={`Random draws at ${CONSTANTS.keyMargin}× capacity`}
            value={formatPercent(outputs.pkDuplicateShareAtMargin, 1)}
            concept="stats:duplicate-share"
            note="pk.duplicate share expected (fk_key_sample_margin)"
          />
        </dl>
        <p className="text-sm text-text-2">
          The launcher's preflight refuses a PK whose capacity cannot hold {formatCount(inputs.M)} rows (ADR 0035);
          driven children draw their PK members without replacement per parent key, so they cannot collide (ADR 0036).
        </p>
      </CardContent>
    </Card>
  );
}

function BirthdayCard({ data, setData }: Props) {
  const { resolved } = useTheme();
  const { inputs, outputs } = useScenario();
  const M = inputs.M;
  const rows = useMemo(
    () =>
      Array.from({ length: 41 }, (_, i) => {
        const log10K = 6 + i * 0.5;
        return { log10K, p: birthdayCollisionProb(M, 10 ** log10K) };
      }),
    [M],
  );
  const option = useMemo(
    () =>
      lineOption(
        {
          x: "log10K",
          xName: "keyspace K (log₁₀)",
          yName: "P(at least one repeat)",
          yMin: 0,
          yMax: 1,
          series: [{ name: "P(collision)", y: "p" }],
          markers: [
            { x: data.keyspaceLog10, label: `K = 10^${data.keyspaceLog10}` },
            { x: Number(Math.log10(Math.max(outputs.keyspaceNeeded, 1)).toFixed(2)), label: "K = M²/2" },
          ],
          xFormatter: (v) => `10^${v}`,
          yFormatter: pctFormat,
        },
        resolved,
      ),
    [data.keyspaceLog10, outputs.keyspaceNeeded, resolved],
  );
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-1">
          Identifier collisions <InfoHint concept="stats:birthday" />
        </CardTitle>
        <p className="text-sm text-text-2">
          Uniqueness-like columns go through shape or keyspace generation with an explicit collision budget: expected
          colliding pairs ≈ M²/2K.
        </p>
      </CardHeader>
      <CardContent className="grid gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <div className="grid gap-3">
          <ChartFrame
            title={`P(repeat) among ${compact(M)} identifiers`}
            concept="stats:birthday"
            description="Birthday bound 1 − exp(−M(M − 1)/2K) against the keyspace size."
            option={option}
            data={rows}
            height={240}
            columns={[
              { key: "log10K", label: "log10 K", align: "right" },
              { key: "p", label: "P(collision)", format: (v) => formatPercent(v as number, 4), align: "right" },
            ]}
          />
          <div className="grid gap-1.5">
            <span className="text-xs text-text-2">
              Keyspace K = 10^{data.keyspaceLog10} (≈ 2^{(data.keyspaceLog10 * Math.log2(10)).toFixed(1)})
            </span>
            <Slider
              min={4}
              max={24}
              step={1}
              value={[data.keyspaceLog10]}
              onValueChange={([v]) => setData({ keyspaceLog10: v ?? data.keyspaceLog10 })}
              thumbLabels={["Identifier keyspace, powers of ten"]}
              formatValue={(v) => `10 to the ${v} values`}
            />
          </div>
        </div>
        <div className="grid content-start gap-3">
          <dl className="grid grid-cols-2 gap-3" data-testid="birthday-outputs">
            <Output
              label="P(at least one repeat)"
              value={formatPercent(outputs.collisionProb, outputs.collisionProb < 0.01 ? 4 : 1)}
              testId="collision-prob"
            />
            <Output label="Expected colliding pairs" value={formatCount(Math.round(outputs.expectedCollidingPairs))} />
            <Output
              label="K for < 1 expected pair"
              value={formatSci(outputs.keyspaceNeeded)}
              note={`≈ 2^${Math.log2(Math.max(outputs.keyspaceNeeded, 1)).toFixed(1)}`}
            />
          </dl>
          <DocFigure
            file="identifier-collisions.png"
            width={1280}
            height={736}
            alt="Expected duplicate pairs against keyspace size for several row counts: 50 million rows need a keyspace above about 1.25 times ten to the fifteenth."
            claim="Uniqueness is keyspace arithmetic, not a sampling question."
            doc={DOCS.scaling}
          />
        </div>
      </CardContent>
    </Card>
  );
}
