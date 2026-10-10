/**
 * CONFIG / SOURCE_STATS — every knob the pipeline reads, what a scenario
 * implies, what the profiler measured, and the guardrails a run must pass.
 *
 *   Amp          the knob console (knobs.json), a sheet per knob
 *   Scenario     the calculator ("amplifier"), in article 8's order
 *   Source stats the source_table_stats explorer, sample vs exact tiers
 *   Guardrails   Mode A's lines of defence, the DLQ rules, uniqueness, the gate
 *
 * One ScenarioProvider holds the knob settings, so turning a dial on the amp
 * re-computes the meter bridge, the calculator and the guardrail numbers.
 */
import { getRouteApi } from "@tanstack/react-router";
import { Calculator, Database, ShieldCheck, SlidersHorizontal } from "lucide-react";
import { useCallback, useRef } from "react";

import { PageHeader } from "@/components/PageHeader";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

import { AmpPanel } from "./amp/AmpPanel";
import { KnobSheet } from "./amp/KnobSheet";
import { Guardrails } from "./guardrails/Guardrails";
import { SCENARIO_PARAM_KEYS, ScenarioProvider, type ScenarioParams } from "./model/state";
import type { ConfigSearch, ConfigSection } from "./route";
import { ScenarioCalculator } from "./scenario/ScenarioCalculator";
import { SourceStatsExplorer } from "./sources/SourceStatsExplorer";

const routeApi = getRouteApi("/config");

const SECTIONS: Array<{ id: ConfigSection; label: string; icon: typeof Calculator }> = [
  { id: "amp", label: "Amp", icon: SlidersHorizontal },
  { id: "scenario", label: "Scenario", icon: Calculator },
  { id: "sources", label: "Source stats", icon: Database },
  { id: "guardrails", label: "Guardrails", icon: ShieldCheck },
];

export function ConfigPage() {
  const search = routeApi.useSearch();
  const navigate = routeApi.useNavigate();
  const section: ConfigSection = search.section ?? "amp";
  const patchSearch = useCallback(
    (patch: Partial<ConfigSearch>, replace = false) =>
      void navigate({ search: (prev) => ({ ...prev, ...patch }), replace }),
    [navigate],
  );
  // The sheet is opened by state, not by a Radix trigger: remember who opened it to return focus there.
  const opener = useRef<HTMLElement | null>(null);
  const openKnob = useCallback(
    (knob: string) => {
      opener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      patchSearch({ knob });
    },
    [patchSearch],
  );

  const onParamsChange = useCallback((params: ScenarioParams) => patchSearch(params, true), [patchSearch]);
  const initialParams = Object.fromEntries(
    ["scenario", ...SCENARIO_PARAM_KEYS].map((key) => [key, search[key as keyof ConfigSearch]]),
  ) as ScenarioParams;

  return (
    <ScenarioProvider initialParams={initialParams} onParamsChange={onParamsChange}>
      <div className="flex flex-col gap-6">
        <PageHeader
          eyebrow="Config"
          title="Knobs, scenarios and source statistics"
          description="Every setting the pipeline reads, with its value from the code and a link to the line; what turning it does; what a 90M-row run from a 10k sample implies; and what the profiler actually measured."
          concept="config:amp"
        />
        <Tabs value={section} onValueChange={(value) => patchSearch({ section: value as ConfigSection })}>
          <TabsList aria-label="Config sections" className="w-full justify-start sm:w-auto">
            {SECTIONS.map(({ id, label, icon: Icon }) => (
              <TabsTrigger key={id} value={id}>
                <Icon aria-hidden="true" />
                {label}
              </TabsTrigger>
            ))}
          </TabsList>
          <TabsContent value="amp">
            <AmpPanel
              onOpenKnob={openKnob}
              initialChannel={search.channel}
              onChannelChange={(channel) => patchSearch({ channel: channel === "all" ? undefined : channel }, true)}
            />
          </TabsContent>
          <TabsContent value="scenario">
            <ScenarioCalculator onOpenKnob={openKnob} />
          </TabsContent>
          <TabsContent value="sources">
            <SourceStatsExplorer search={search} onSearch={(patch) => patchSearch(patch, true)} />
          </TabsContent>
          <TabsContent value="guardrails">
            <Guardrails onOpenKnob={openKnob} />
          </TabsContent>
        </Tabs>
      </div>
      <KnobSheet
        knobId={search.knob}
        onClose={() => patchSearch({ knob: undefined })}
        returnFocus={() => opener.current}
      />
    </ScenarioProvider>
  );
}
