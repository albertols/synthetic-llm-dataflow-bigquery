/**
 * /kit — the design system reference: tokens, the UI kit and the shared
 * frames, rendered live in both themes. Tab agents build from these pieces;
 * the e2e smoke runs axe over this page.
 */
import { ScatterplotLayer } from "@deck.gl/layers";
import type { EChartsOption } from "echarts";
import { Bell, Download, Palette, Plus } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";

import { ChartFrame } from "@/components/ChartFrame";
import { DataTableFallback } from "@/components/DataTableFallback";
import { DeckFrame } from "@/components/DeckFrame";
import { EmptyState } from "@/components/EmptyState";
import { Formula } from "@/components/Formula";
import { InfoHint } from "@/components/InfoHint";
import { LEVEL_ORDER, LevelChip } from "@/components/LevelChip";
import { Mermaid } from "@/components/Mermaid";
import { PageHeader } from "@/components/PageHeader";
import { SourceLink } from "@/components/SourceLink";
import { StatusPill } from "@/components/StatusPill";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Combobox } from "@/components/ui/combobox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { toast } from "@/components/ui/toast";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { tokenRgba } from "@/lib/color";
import { formatNumber } from "@/lib/format";
import { useTheme } from "@/lib/theme";

function Section({
  id,
  title,
  description,
  children,
}: {
  id: string;
  title: string;
  description?: string;
  children: ReactNode;
}) {
  return (
    <section aria-labelledby={id} className="grid gap-4">
      <div className="grid gap-1">
        <h2 id={id} className="text-lg font-semibold tracking-tight text-text-1">
          {title}
        </h2>
        {description ? <p className="max-w-3xl text-sm text-text-2">{description}</p> : null}
      </div>
      {children}
    </section>
  );
}

const SWATCHES: Array<{ group: string; tokens: Array<{ name: `--${string}`; label: string }> }> = [
  {
    group: "Surfaces",
    tokens: [
      { name: "--bg", label: "bg" },
      { name: "--surface-1", label: "surface-1" },
      { name: "--surface-2", label: "surface-2" },
      { name: "--surface-3", label: "surface-3" },
      { name: "--border", label: "border" },
      { name: "--control-border", label: "control-border" },
    ],
  },
  {
    group: "Brand",
    tokens: [
      { name: "--accent", label: "Beam orange" },
      { name: "--brand-blue", label: "GCP blue" },
      { name: "--cpu", label: "aqua · CPU" },
      { name: "--gpu", label: "purple · GPU" },
      { name: "--slate", label: "slate" },
    ],
  },
  {
    group: "Status (icon + label always)",
    tokens: [
      { name: "--status-good", label: "good" },
      { name: "--status-warn", label: "warn" },
      { name: "--status-serious", label: "serious" },
      { name: "--status-critical", label: "critical" },
      { name: "--status-info", label: "info" },
      { name: "--status-neutral", label: "neutral" },
    ],
  },
  {
    group: "Chart categorical (fixed order)",
    tokens: [1, 2, 3, 4, 5, 6, 7, 8].map((i) => ({ name: `--chart-${i}` as const, label: `chart-${i}` })),
  },
];

function Swatches() {
  // Re-render on theme change so the hex labels match the swatches.
  const { resolved } = useTheme();
  return (
    <div className="grid gap-4 md:grid-cols-2" data-theme-rendered={resolved}>
      {SWATCHES.map(({ group, tokens }) => (
        <Card key={group}>
          <CardHeader>
            <CardTitle className="text-sm">{group}</CardTitle>
          </CardHeader>
          <CardContent className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            {tokens.map(({ name, label }) => {
              const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
              return (
                <div key={name} className="flex items-center gap-2">
                  <span
                    className="size-8 shrink-0 rounded-md border border-border-strong"
                    style={{ background: `var(${name})` }}
                  />
                  <span className="grid min-w-0">
                    <span className="truncate text-xs text-text-1">{label}</span>
                    <code className="text-[11px] text-text-3">{value}</code>
                  </span>
                </div>
              );
            })}
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

const DKW_ROWS = [1_000, 2_000, 5_000, 10_000, 20_000, 50_000, 100_000, 200_000, 500_000, 1_000_000].map((n) => ({
  n,
  epsilon: Number(Math.sqrt(Math.log(2 / 0.05) / (2 * n)).toFixed(5)),
}));

const DKW_OPTION: EChartsOption = {
  grid: { left: 8, right: 24, top: 28, bottom: 30, containLabel: true },
  tooltip: { trigger: "axis" },
  xAxis: { type: "log", name: "reference rows n", nameLocation: "middle", nameGap: 28, min: 1_000, max: 1_000_000 },
  yAxis: { type: "value", name: "ε(n)", min: 0 },
  series: [
    {
      type: "line",
      name: "DKW ε at α = 0.05",
      encode: { x: "n", y: "epsilon" },
      showSymbol: true,
      markLine: { data: [{ xAxis: 10_000, label: { formatter: "10k: ε ≈ 0.0136" } }] },
    },
  ],
};

const HIST_ROWS = ["0–25", "25–50", "50–75", "75–100", "100–150", "150+"].map((bucket, i) => ({
  bucket,
  source: [0.18, 0.27, 0.21, 0.14, 0.12, 0.08][i],
  synthetic: [0.17, 0.29, 0.2, 0.15, 0.11, 0.08][i],
}));

const HIST_OPTION: EChartsOption = {
  legend: { top: 0, left: 0 },
  grid: { left: 8, right: 16, top: 32, bottom: 8, containLabel: true },
  tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
  xAxis: { type: "category" },
  yAxis: { type: "value", axisLabel: { formatter: (v: number) => `${Math.round(v * 100)}%` } },
  series: [
    { type: "bar", name: "source", encode: { x: "bucket", y: "source" } },
    { type: "bar", name: "synthetic", encode: { x: "bucket", y: "synthetic" } },
  ],
};

type Point = { position: [number, number, number]; cluster: number; label: string };

function spherePoints(count: number): Point[] {
  let seed = 7;
  const random = () => {
    seed = (seed * 16807) % 2147483647;
    return seed / 2147483647;
  };
  const centres: Array<[number, number, number]> = [
    [0.8, 0.3, 0.2],
    [-0.4, 0.7, -0.5],
    [-0.3, -0.8, 0.5],
  ];
  return Array.from({ length: count }, (_, i) => {
    const cluster = i % 3;
    const [cx, cy, cz] = centres[cluster]!;
    const v: [number, number, number] = [
      cx + (random() - 0.5) * 0.6,
      cy + (random() - 0.5) * 0.6,
      cz + (random() - 0.5) * 0.6,
    ];
    const norm = Math.hypot(...v) || 1;
    return { position: [v[0] / norm, v[1] / norm, v[2] / norm], cluster, label: `row ${i} · cluster ${cluster + 1}` };
  });
}

function DeckDemo() {
  const { resolved } = useTheme();
  const points = useMemo(() => spherePoints(600), []);
  const layers = useMemo(() => {
    const colors = [tokenRgba("--chart-1"), tokenRgba("--chart-2"), tokenRgba("--chart-3")];
    return [
      new ScatterplotLayer<Point>({
        id: `kit-points-${resolved}`,
        data: points,
        getPosition: (d) => d.position,
        getFillColor: (d) => colors[d.cluster] ?? colors[0]!,
        getRadius: 0.018,
        radiusUnits: "common",
        pickable: true,
      }),
    ];
  }, [points, resolved]);
  return (
    <DeckFrame
      ariaLabel="Demo: 600 unit vectors on a sphere in three clusters"
      view="orbit"
      initialViewState={{ target: [0, 0, 0], rotationX: 20, rotationOrbit: 30, zoom: 7 }}
      layers={layers}
      height={320}
      getTooltip={(info) => (info.object ? (info.object as Point).label : null)}
    />
  );
}

const GUI_FLOW = `flowchart LR
  WEB["browser app<br/>holds no credentials"] -->|"query name + parameters"| BFF["local BFF<br/>named queries only"]
  BFF -->|"DATA_SOURCE=bigquery"| CAP["dry run + bytes cap"]
  CAP -->|"read-only SELECT"| BQ[("project tables")]
  BFF -->|"DATA_SOURCE=mock"| MOCK[("seeded mock fixtures")]`;

const ENGINES = [
  { value: "b1", label: "b1 — RAG", description: "retrieval-augmented engine" },
  { value: "b2", label: "b2 — library", description: "sdgx library wrapper" },
];
const TABLES = ["users", "orders", "order_items", "products"].map((t) => ({ value: t, label: t }));

function Controls() {
  const [engine, setEngine] = useState<string | null>("b1");
  const [tables, setTables] = useState<string[]>(["orders"]);
  const [similarity, setSimilarity] = useState([0.3, 0.8]);
  const [exact, setExact] = useState(true);
  const [projection, setProjection] = useState("pca");
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Buttons and badges</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-3">
          <div className="flex flex-wrap gap-2">
            <Button variant="primary">
              <Plus aria-hidden="true" />
              Primary
            </Button>
            <Button>Secondary</Button>
            <Button variant="outline">Outline</Button>
            <Button variant="ghost">Ghost</Button>
            <Button variant="link">Link</Button>
            <Button variant="secondary" size="icon" aria-label="Download">
              <Download aria-hidden="true" />
            </Button>
          </div>
          <div className="flex flex-wrap gap-2">
            <Badge>neutral</Badge>
            <Badge variant="accent">accent</Badge>
            <Badge variant="info">info</Badge>
            <Badge variant="cpu">CPU</Badge>
            <Badge variant="gpu">GPU · L4</Badge>
            <Badge variant="outline">outline</Badge>
          </div>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Inputs</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-3 sm:grid-cols-2">
          <Select defaultValue="hashing-384">
            <SelectTrigger aria-label="Embedder">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="hashing-384">hashing-384</SelectItem>
              <SelectItem value="bge-small-en-v1.5">bge-small-en-v1.5</SelectItem>
            </SelectContent>
          </Select>
          <Combobox
            label="Engine"
            options={ENGINES}
            value={engine}
            onValueChange={setEngine}
            placeholder="Any engine"
          />
          <Combobox
            label="Tables"
            multiple
            options={TABLES}
            value={tables}
            onValueChange={setTables}
            placeholder="All tables"
          />
          <ToggleGroup
            type="single"
            value={projection}
            onValueChange={(v) => v && setProjection(v)}
            aria-label="Projection"
          >
            <ToggleGroupItem value="pca">PCA</ToggleGroupItem>
            <ToggleGroupItem value="umap">UMAP</ToggleGroupItem>
          </ToggleGroup>
          <div className="grid gap-2 sm:col-span-2">
            <span className="text-xs text-text-3">
              Similarity {formatNumber(similarity[0])} – {formatNumber(similarity[1])}
            </span>
            <Slider
              min={0}
              max={1}
              step={0.05}
              value={similarity}
              onValueChange={setSimilarity}
              thumbLabels={["Minimum similarity", "Maximum similarity"]}
              formatValue={(v) => formatNumber(v, 2)}
            />
          </div>
          <div className="flex items-center gap-2">
            <Switch id="kit-exact-stats" checked={exact} onCheckedChange={setExact} />
            <label htmlFor="kit-exact-stats" className="text-sm text-text-2">
              Exact source stats
            </label>
          </div>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Overlays</CardTitle>
          <CardDescription>
            Dialog, sheet, toast and tooltip. Esc closes each; focus returns to the trigger.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-2">
          <Dialog>
            <DialogTrigger asChild>
              <Button>Open dialog</Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>Compare two evaluations?</DialogTitle>
                <DialogDescription>
                  They use different catalogue versions, so some metrics are not comparable.
                </DialogDescription>
              </DialogHeader>
              <DialogFooter>
                <Button variant="primary">Compare anyway</Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
          <Sheet>
            <SheetTrigger asChild>
              <Button>Open sheet</Button>
            </SheetTrigger>
            <SheetContent>
              <SheetHeader>
                <SheetTitle>reference_rows_limit</SheetTitle>
                <SheetDescription>How many source rows the generator reads as its reference sample.</SheetDescription>
              </SheetHeader>
              <Formula tex={"\\varepsilon(n) = \\sqrt{\\ln(2/\\alpha) / 2n}"} />
            </SheetContent>
          </Sheet>
          <Button
            onClick={() =>
              toast({
                title: "Filters saved to the URL",
                description: "Share the link to reopen this view.",
                tone: "good",
              })
            }
          >
            <Bell aria-hidden="true" />
            Show toast
          </Button>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">In-page tabs</CardTitle>
        </CardHeader>
        <CardContent>
          <Tabs defaultValue="fidelity">
            <TabsList aria-label="Families">
              <TabsTrigger value="fidelity">Fidelity</TabsTrigger>
              <TabsTrigger value="privacy">Privacy</TabsTrigger>
              <TabsTrigger value="integrity">Integrity</TabsTrigger>
            </TabsList>
            <TabsContent value="fidelity" className="text-sm text-text-2">
              Distribution metrics against the source.
            </TabsContent>
            <TabsContent value="privacy" className="text-sm text-text-2">
              Memorization, exposure and distance to the closest record.
            </TabsContent>
            <TabsContent value="integrity" className="text-sm text-text-2">
              Keys, duplicates and referential integrity.
            </TabsContent>
          </Tabs>
        </CardContent>
      </Card>
    </div>
  );
}

export function KitPage() {
  return (
    <div className="flex flex-col gap-10">
      <PageHeader
        eyebrow="Foundation"
        title="Design system"
        description="Tokens, components and frames every tab builds on. Dark by default; the sun/moon button switches to the light theme kept for accessibility."
        actions={
          <Badge variant="outline">
            <Palette aria-hidden="true" />
            WCAG 2.2 AA
          </Badge>
        }
      />

      <Section
        id="kit-tokens"
        title="Tokens"
        description="Every colour is a CSS variable in styles/tokens.css. The chart palette passes the dataviz six checks on this surface."
      >
        <Swatches />
      </Section>

      <Section id="kit-vocabulary" title="Status, levels and hints">
        <Card>
          <CardContent className="grid gap-4 pt-5">
            <div className="flex flex-wrap items-center gap-1.5">
              {["pass", "warn", "fail", "info", "not_evaluated", "RUNNING", "PARTIAL", "SKIPPED"].map((status) => (
                <StatusPill key={status} status={status} />
              ))}
            </div>
            <div className="flex flex-wrap items-center gap-1.5">
              {LEVEL_ORDER.map((level) => (
                <LevelChip key={level} level={level} withHint />
              ))}
            </div>
            <div className="flex flex-wrap items-center gap-4 text-sm text-text-2">
              <span className="inline-flex items-center gap-1">
                Noise floor <InfoHint concept="core:noise-floor" />
              </span>
              <span className="inline-flex items-center gap-1">
                Reference baseline <InfoHint concept="core:baseline" side="right" size="md" />
              </span>
              <SourceLink source="packages/sdfb-evaluation/src/sdfb_evaluation/catalogue/metrics.yaml:347" />
            </div>
            <Formula tex={"\\varepsilon_{\\mathrm{DKW}}(n) = \\sqrt{\\frac{\\ln(2/\\alpha)}{2n}}"} />
          </CardContent>
        </Card>
      </Section>

      <Section id="kit-controls" title="Controls">
        <Controls />
      </Section>

      <Section
        id="kit-charts"
        title="ChartFrame"
        description="ECharts through one frame: token theme, lazy modules, a View data table, labelled empty states."
      >
        <div className="grid gap-4 lg:grid-cols-2">
          <ChartFrame
            title="DKW band ε(n) at α = 0.05"
            concept="core:noise-floor"
            description="How far an empirical CDF can sit from the truth at n reference rows."
            option={DKW_OPTION}
            data={DKW_ROWS}
          />
          <ChartFrame
            title="Order totals: source vs synthetic"
            description="Share of rows per bucket (demo values)."
            option={HIST_OPTION}
            data={HIST_ROWS}
          />
          <ChartFrame
            title="Null rate by column"
            option={HIST_OPTION}
            data={HIST_ROWS}
            empty={{ when: true, message: "Not evaluated: the reference sample is unverified." }}
          />
          <Card className="p-4">
            <DataTableFallback caption="DKW band" rows={DKW_ROWS.slice(0, 5)} />
          </Card>
        </div>
      </Section>

      <Section
        id="kit-3d"
        title="DeckFrame"
        description="deck.gl OrbitView on one canvas, disposed on route leave, with a labelled fallback when WebGL2 is missing or lost."
      >
        <DeckDemo />
      </Section>

      <Section id="kit-mermaid" title="Mermaid">
        <Mermaid
          chart={GUI_FLOW}
          ariaLabel="The browser names a query; the BFF runs it against BigQuery under a bytes cap, or serves mock fixtures."
        />
      </Section>

      <Section id="kit-states" title="Empty and loading states">
        <div className="grid gap-4 md:grid-cols-2">
          <EmptyState
            compact
            headingLevel={3}
            title="No evaluations match these filters"
            description="Clear a filter or widen the date range."
          />
          <div className="grid gap-2" role="status" aria-busy="true">
            <span className="sr-only">Loading example</span>
            <Skeleton className="h-6 w-1/2" />
            <Skeleton className="h-24 w-full" />
          </div>
        </div>
      </Section>
    </div>
  );
}
