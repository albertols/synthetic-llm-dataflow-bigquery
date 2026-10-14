/**
 * Provenance for a measured number: the figure script's MEASURED block it was
 * typed in (once), the line, and the command that regenerates the figure.
 */
import { FlaskConical } from "lucide-react";

import { CodeLink } from "./CodeLink";
import { measured, MEASURED_SOURCES } from "./model/knobs";

export function MeasuredNote({ ids, className }: { ids: readonly string[]; className?: string }) {
  const items = ids.map((id) => measured(id));
  const blocks = [...new Set(items.map((m) => m.block))];
  const scripts = [...new Set(items.map((m) => m.script))];
  return (
    <div className={className ?? "flex flex-col gap-1 text-xs text-text-3"} data-testid="measured-note">
      <p className="flex items-start gap-1.5">
        <FlaskConical className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
        <span>
          Measured: {blocks.join(" · ")}. Typed once in{" "}
          {items.map((m, i) => (
            <span key={m.id}>
              {i > 0 ? ", " : ""}
              <CodeLink source={m.source}>
                <code className="font-mono">{m.name}</code>
              </CodeLink>
            </span>
          ))}
          .
        </span>
      </p>
      {scripts.map((script) => {
        const provenance = MEASURED_SOURCES.find((s) => s.script === script)?.provenance;
        return provenance ? (
          <p key={script} className="pl-5">
            Regenerate: <code className="font-mono [overflow-wrap:anywhere]">{provenance}</code>
          </p>
        ) : null;
      })}
    </div>
  );
}
