import { lazy, Suspense } from "react";

import { cn } from "@/lib/cn";

const KatexRender = lazy(() => import("./KatexRender"));

export type FormulaProps = {
  /** KaTeX source (standard macros only), e.g. the catalogue's `formula`. */
  tex: string;
  /** Block (default) or inline. */
  display?: boolean;
  className?: string;
};

/**
 * A formula rendered with KaTeX. KaTeX (JS, CSS, fonts) is a lazy chunk; until
 * it arrives the TeX source shows as code, so nothing jumps to empty.
 */
export function Formula({ tex, display = true, className }: FormulaProps) {
  const Wrapper = display ? "div" : "span";
  return (
    <Wrapper
      data-slot="formula"
      className={cn(display && "overflow-x-auto py-1 text-center text-[0.95rem] text-text-1", className)}
    >
      <Suspense fallback={<code className="text-xs text-text-3">{tex}</code>}>
        <KatexRender tex={tex} display={display} />
      </Suspense>
    </Wrapper>
  );
}
