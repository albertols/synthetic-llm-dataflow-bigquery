/**
 * JsonView — a read-only JSON viewer: objects as key / value rows, nested
 * values collapsible, strings in quotes, empty strings shown as such (an
 * empty generation parameter is a fact, not a gap). "Copy JSON" copies the
 * exact snapshot.
 */
import { Check, Copy } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";

type Json = null | boolean | number | string | Json[] | { [key: string]: Json };

function Scalar({ value }: { value: Json }) {
  if (value === null) return <span className="text-text-3">null</span>;
  if (typeof value === "string")
    return value === "" ? (
      <span className="text-text-3">"" (empty)</span>
    ) : (
      <span className="text-text-1 break-all">"{value}"</span>
    );
  if (typeof value === "number") return <span className="text-link tabular-nums">{String(value)}</span>;
  if (typeof value === "boolean") return <span className="text-accent-text">{String(value)}</span>;
  return null;
}

function Node({ name, value, depth }: { name: string; value: Json; depth: number }) {
  if (value !== null && typeof value === "object") {
    const entries = Array.isArray(value) ? value.map((v, i) => [String(i), v] as const) : Object.entries(value);
    return (
      <details open={depth < 1} className="grid">
        <summary className="cursor-pointer py-1 font-mono text-xs text-text-2">
          {name}{" "}
          <span className="text-text-3">{Array.isArray(value) ? `[${entries.length}]` : `{${entries.length}}`}</span>
        </summary>
        <div className="ml-4 grid border-l border-border pl-3">
          {entries.map(([k, v]) => (
            <Node key={k} name={k} value={v} depth={depth + 1} />
          ))}
        </div>
      </details>
    );
  }
  return (
    <div className="grid grid-cols-[minmax(8rem,16rem)_minmax(0,1fr)] gap-3 border-b border-border py-1 font-mono text-xs last:border-0">
      <span className="truncate text-text-2" title={name}>
        {name}
      </span>
      <span className="min-w-0">
        <Scalar value={value} />
      </span>
    </div>
  );
}

export function JsonView({ value, label }: { value: unknown; label: string }) {
  const [copied, setCopied] = useState(false);
  const json = (value ?? null) as Json;
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(JSON.stringify(value, null, 2));
      setCopied(true);
      globalThis.setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  };
  return (
    <div className="grid gap-2">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs text-text-3">{label}</span>
        <Button variant="ghost" size="sm" onClick={() => void copy()}>
          {copied ? <Check aria-hidden="true" /> : <Copy aria-hidden="true" />}
          {copied ? "Copied" : "Copy JSON"}
          <span className="sr-only"> of {label}</span>
        </Button>
      </div>
      {json === null ? (
        <p className="text-sm text-text-3">Not recorded.</p>
      ) : typeof json === "object" ? (
        <div className="grid">
          {(Array.isArray(json) ? json.map((v, i) => [String(i), v] as const) : Object.entries(json)).map(([k, v]) => (
            <Node key={k} name={k} value={v} depth={0} />
          ))}
        </div>
      ) : (
        <Scalar value={json} />
      )}
    </div>
  );
}
