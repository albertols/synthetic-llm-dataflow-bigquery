/**
 * The knob sheet: everything one knob means — value vs the code default, how
 * to set it, turning it up vs down, the live maths chart, where the code
 * lives, the ADRs and docs, and any place the docs disagree with the code.
 */
import { ArrowDownRight, ArrowUpRight, BookOpen, ExternalLink, RotateCcw } from "lucide-react";
import type { ReactNode } from "react";

import { knobGuides } from "@contracts/concepts/config";
import type { KnobId } from "@contracts/generated/knobs";
import type { Knob, KnobValue } from "@contracts/knobs";

import { Callout } from "@/components/Callout";
import { Formula } from "@/components/Formula";
import { InfoHint } from "@/components/InfoHint";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Slider } from "@/components/ui/slider";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";

import { CodeLink } from "../CodeLink";
import { adrUrl, ADRS, CODE_REF_SHORT, docTitle, docUrl } from "../model/links";
import {
  annotationsFor,
  asText,
  CHANNELS,
  formatKnobValue,
  getKnob,
  kindOf,
  positions,
  SCENARIO_KNOBS,
  SETTABLE_LABEL,
} from "../model/knobs";
import { presetById } from "../model/scenario";
import { useScenario } from "../model/state";
import { settableText } from "./KnobControl";
import { KnobChart } from "./knobCharts";

export function KnobSheet({
  knobId,
  onClose,
  returnFocus,
}: {
  knobId: string | undefined;
  onClose: () => void;
  /** The element that opened the sheet (no Radix trigger here), focused again on close. */
  returnFocus?: () => HTMLElement | null;
}) {
  const k = knobId ? getKnob(knobId) : undefined;
  return (
    <Sheet open={Boolean(k)} onOpenChange={(open) => (open ? undefined : onClose())}>
      <SheetContent
        className="w-full sm:max-w-xl"
        data-testid="knob-sheet"
        onCloseAutoFocus={(event) => {
          const target = returnFocus?.();
          if (target?.isConnected) {
            event.preventDefault();
            target.focus();
          }
        }}
      >
        {k ? <KnobSheetBody knob={k} /> : null}
      </SheetContent>
    </Sheet>
  );
}

function Section({ title, concept, children }: { title: string; concept?: string; children: ReactNode }) {
  return (
    <section className="grid gap-2">
      <h3 className="flex items-center gap-1 text-xs font-semibold tracking-wide text-text-3 uppercase">
        {title}
        {concept ? <InfoHint concept={concept} /> : null}
      </h3>
      {children}
    </section>
  );
}

function KnobSheetBody({ knob: k }: { knob: Knob }) {
  const { settings, inputs, setSetting, resetSetting, presetValue, presetId } = useScenario();
  const guide = knobGuides[k.id as KnobId];
  const kind = kindOf(k);
  const value = settings[k.id] ?? k.value;
  const channel = CHANNELS.find((c) => c.id === k.channel);
  const annotations = annotationsFor(k.id);
  // A reset returns to the ACTIVE preset's value (the code default for knobs no preset sets).
  const resetTo = presetValue(k.id);
  const changed = JSON.stringify(value) !== JSON.stringify(resetTo);
  const presetSets = JSON.stringify(resetTo) !== JSON.stringify(k.value);
  const live = (SCENARIO_KNOBS as readonly string[]).includes(k.id);

  return (
    <>
      <SheetHeader>
        <p className="font-mono text-xs tracking-wide text-accent-text uppercase">
          {channel?.label ?? k.channel} · {k.group}
        </p>
        <SheetTitle>{k.label}</SheetTitle>
        <SheetDescription>{guide.purpose}</SheetDescription>
        <div className="flex flex-wrap items-center gap-1.5 pt-1">
          <code className="rounded-sm bg-surface-3 px-1.5 py-0.5 font-mono text-xs text-text-1">{k.id}</code>
          {k.settable_via.map((via) => (
            <Badge key={via} variant={via === "constant" || via === "derived" ? "neutral" : "info"}>
              {SETTABLE_LABEL[via] ?? via}
            </Badge>
          ))}
          {live ? <Badge variant="accent">feeds the scenario</Badge> : null}
        </div>
      </SheetHeader>

      <Section title="Value">
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
          <dt className="text-text-3">Now</dt>
          <dd className="font-mono text-text-1 [overflow-wrap:anywhere]" data-testid="knob-sheet-value">
            {formatKnobValue(k, value)}
          </dd>
          <dt className="text-text-3">Code default</dt>
          <dd className="font-mono text-text-2 [overflow-wrap:anywhere]">
            {k.id === "num_rows" ? "none (required flag)" : formatKnobValue(k)}
          </dd>
          {k.composer_default !== undefined ? (
            <>
              <dt className="text-text-3">Composer default</dt>
              <dd className="font-mono text-text-2 [overflow-wrap:anywhere]">{k.composer_default}</dd>
            </>
          ) : null}
        </dl>
        {kind === "dial" || kind === "selector" ? (
          <TurnControl knob={k} value={value} onChange={(v) => setSetting(k.id, v)} />
        ) : null}
        {changed ? (
          <Button size="sm" variant="ghost" className="justify-self-start" onClick={() => resetSetting(k.id)}>
            <RotateCcw aria-hidden="true" />
            Reset to {presetSets ? `the ${presetById(presetId).label} preset` : "the code default"} (
            {formatKnobValue(k, resetTo)})
          </Button>
        ) : null}
      </Section>

      <Section title="Settable?" concept="config:settable-via">
        <p className="text-sm text-text-2">{settableText(k, kind)}</p>
        <ul className="grid gap-1 text-sm">
          {k.cli_flag ? <Flag label="Launcher flag" value={k.cli_flag} /> : null}
          {k.composer_param ? <Flag label="Composer param" value={k.composer_param} /> : null}
          {k.flex_param ? <Flag label="Flex Template param" value={k.flex_param} /> : null}
        </ul>
      </Section>

      <Section title="Turning it">
        <div className="grid gap-2 sm:grid-cols-2">
          <div className="rounded-md border border-border bg-surface-1 p-3 text-sm">
            <p className="mb-1 flex items-center gap-1 font-semibold text-text-1">
              <ArrowUpRight className="size-4 text-accent-text" aria-hidden="true" />
              Up
            </p>
            <p className="text-text-2">{guide.up}</p>
          </div>
          <div className="rounded-md border border-border bg-surface-1 p-3 text-sm">
            <p className="mb-1 flex items-center gap-1 font-semibold text-text-1">
              <ArrowDownRight className="size-4 text-link" aria-hidden="true" />
              Down
            </p>
            <p className="text-text-2">{guide.down}</p>
          </div>
        </div>
        {guide.tip ? <p className="text-sm text-text-2">Tip: {guide.tip}</p> : null}
      </Section>

      <KnobChart id={k.id} settings={settings} inputs={inputs} />

      {annotations.map((a) => (
        <Callout key={a.id} tone="docs-differ" title={`Docs differ: ${a.title}`}>
          <p>
            <strong className="text-text-1">Docs say:</strong> {a.docs_say}
          </p>
          <p className="mt-1">
            <strong className="text-text-1">Code does (shown here):</strong> {a.code_does}
          </p>
          <ul className="mt-2 grid gap-1.5">
            {a.evidence.map((e) => (
              <li key={`${e.source}-${e.role}`} className="grid gap-0.5">
                <span className="flex flex-wrap items-center gap-1.5">
                  <Badge variant={e.role === "code" ? "accent" : "neutral"}>{e.role}</Badge>
                  <CodeLink source={e.source} />
                </span>
                <code className="font-mono text-xs text-text-2 [overflow-wrap:anywhere]">{e.excerpt}</code>
              </li>
            ))}
          </ul>
          {a.links?.map((l) => (
            <a
              key={l.url}
              href={l.url}
              target="_blank"
              rel="noopener noreferrer"
              className="mt-2 inline-flex items-center gap-1 text-link hover:underline"
            >
              {l.label}
              <ExternalLink className="size-3" aria-hidden="true" />
            </a>
          ))}
        </Callout>
      ))}

      {k.help || k.comment ? (
        <Section title="From the code">
          <blockquote className="border-l-2 border-accent/60 pl-3 text-sm text-text-2">
            {k.help ?? k.comment}
          </blockquote>
        </Section>
      ) : null}

      <Section title="Where it lives">
        <p className="text-sm text-text-2">
          <CodeLink source={k.source} />{" "}
          <span className="text-text-3">(at {CODE_REF_SHORT}, the commit knobs.json was exported from)</span>
        </p>
      </Section>

      {k.related_adrs.length || k.docs.length || guide.formula || guide.refs?.length ? (
        <Section title="Deep dive">
          {guide.formula ? <Formula tex={guide.formula} display /> : null}
          <ul className="grid gap-1 text-sm">
            {k.related_adrs.map((number) => {
              const url = adrUrl(number);
              return url ? (
                <LinkItem key={number} href={url}>
                  ADR {number} — {ADRS[number]?.title}
                </LinkItem>
              ) : (
                <li key={number}>ADR {number}</li>
              );
            })}
            {k.docs.map((path) => (
              <LinkItem key={path} href={docUrl(path)}>
                {docTitle(path)}
              </LinkItem>
            ))}
            {guide.refs?.map((ref) => (
              <LinkItem key={ref.url} href={ref.url}>
                {ref.label}
              </LinkItem>
            ))}
          </ul>
        </Section>
      ) : null}
    </>
  );
}

function Flag({ label, value }: { label: string; value: string }) {
  return (
    <li className="flex flex-wrap items-center gap-2">
      <span className="text-text-3">{label}</span>
      <code className="rounded-sm bg-surface-3 px-1.5 py-0.5 font-mono text-xs text-text-1">{value}</code>
    </li>
  );
}

function LinkItem({ href, children }: { href: string; children: ReactNode }) {
  return (
    <li>
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        className="inline-flex items-start gap-1.5 text-link hover:underline"
      >
        <BookOpen className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
        <span>{children}</span>
        <span className="sr-only"> (opens in a new tab)</span>
      </a>
    </li>
  );
}

function TurnControl({ knob: k, value, onChange }: { knob: Knob; value: KnobValue; onChange: (v: KnobValue) => void }) {
  const pos = positions(k, value);
  if (!pos) return null;
  if (kindOf(k) === "selector")
    return (
      <ToggleGroup
        type="single"
        value={asText(value)}
        onValueChange={(v) => v && onChange(v)}
        aria-label={`${k.label}: choose a value`}
        className="flex-wrap justify-start"
      >
        {pos.values.map((choice) => (
          <ToggleGroupItem key={asText(choice)} value={asText(choice)}>
            {asText(choice)}
          </ToggleGroupItem>
        ))}
      </ToggleGroup>
    );
  return (
    <div className="grid gap-1.5 pt-1">
      <Slider
        min={0}
        max={pos.values.length - 1}
        step={1}
        value={[pos.index]}
        onValueChange={([i]) => onChange(pos.values[i ?? 0] ?? null)}
        thumbLabels={[k.label]}
        formatValue={(i) => formatKnobValue(k, pos.values[i] ?? null)}
      />
      <p className="flex justify-between text-xs text-text-3" aria-hidden="true">
        <span>{formatKnobValue(k, pos.values[0] ?? null)}</span>
        <span>what-if range (the GUI's, not a code limit)</span>
        <span>{formatKnobValue(k, pos.values[pos.values.length - 1] ?? null)}</span>
      </p>
    </div>
  );
}
