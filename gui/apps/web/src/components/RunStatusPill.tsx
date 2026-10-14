import { displayStatus, STALE, STALE_TITLE, type RegistryEvent } from "@/lib/runState";

import { StatusPill, type StatusPillProps } from "./StatusPill";

export type RunStatusPillProps = Pick<StatusPillProps, "size" | "className"> & {
  /** The evaluation's last registry event: a list row, a detail's `evaluation`, or `latestEvent(facets.latest)`. */
  row: RegistryEvent & { status: string };
  /** Epoch milliseconds to judge staleness against; the clock by default. */
  now?: number;
};

/**
 * An evaluation's status as a view shows it: the registry's own status, or
 * Stale when its last event is RUNNING and more than 24 hours old
 * (lib/runState.ts). The registry status of a stale evaluation is still
 * RUNNING; the pill's title says so.
 */
export function RunStatusPill({ row, now, size, className }: RunStatusPillProps) {
  const status = displayStatus(row, now);
  return (
    <StatusPill status={status} title={status === STALE ? STALE_TITLE : undefined} size={size} className={className} />
  );
}
