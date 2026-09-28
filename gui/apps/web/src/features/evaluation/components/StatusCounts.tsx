/**
 * StatusCounts — a status breakdown that always adds up: every non-zero
 * bucket (fail, warn, pass, info, not evaluated, other) and its total,
 * "2 fail · 14 warn · 200 pass · 3 info = 219 measured". INFO rows are
 * measured but not gated; leaving them out made breakdowns fall short of
 * their totals. `data-total` / `data-count` let tests check the sum.
 */
import { InfoHint } from "@/components/InfoHint";
import { cn } from "@/lib/cn";

import { countsTotal, visibleBuckets, type StatusCounts as Counts } from "../lib/model";

export function StatusCounts({
  counts,
  totalLabel = "",
  hint = false,
  className,
}: {
  counts: Counts;
  /** Word after the total ("measured"); empty for a bare "= 219". */
  totalLabel?: string;
  /** Adds the (i) that explains INFO and "other". */
  hint?: boolean;
  className?: string;
}) {
  const total = countsTotal(counts);
  const buckets = visibleBuckets(counts);
  return (
    <span
      data-slot="status-counts"
      data-total={total}
      className={cn("inline-flex flex-wrap items-center gap-x-1 text-[11px] text-text-3 tabular-nums", className)}
    >
      {buckets.map((b, i) => (
        <span key={b.key} data-bucket={b.key} data-count={b.count} className="whitespace-nowrap">
          {i > 0 ? <span aria-hidden="true">· </span> : null}
          {b.count.toLocaleString("en-US")} {b.label}
        </span>
      ))}
      <span className="whitespace-nowrap">
        = {total.toLocaleString("en-US")}
        {totalLabel ? ` ${totalLabel}` : ""}
      </span>
      {hint ? <InfoHint concept="eval:info-status" /> : null}
    </span>
  );
}
