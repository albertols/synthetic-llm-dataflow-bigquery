/** Lazy chunk: KaTeX and its stylesheet load only when a formula first renders. */
import katex from "katex";
import "katex/dist/katex.min.css";
import { useMemo } from "react";

export default function KatexRender({ tex, display }: { tex: string; display: boolean }) {
  const html = useMemo(
    () =>
      katex.renderToString(tex, {
        displayMode: display,
        throwOnError: false,
        output: "htmlAndMathml",
        strict: "ignore",
        trust: false,
      }),
    [tex, display],
  );
  // KaTeX output with trust:false contains no script or links; the MathML half is read by screen readers.
  return <span className="katex-host" dangerouslySetInnerHTML={{ __html: html }} />;
}
