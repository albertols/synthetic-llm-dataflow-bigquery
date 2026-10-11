/**
 * The stale rule, on both sides of its 24-hour boundary, with the clock
 * injected: an evaluation whose last registry event is RUNNING, recorded more
 * than 24 hours ago, is stale. The registry's own status never changes.
 */
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";

import { afterEach, describe, expect, it, vi } from "vitest";

import { concepts as evaluationConcepts } from "@contracts/concepts/evaluation";

import { CLOSE_BY_HAND, displayStatus, isStale, latestEvent, STALE, STALE_AFTER_HOURS, STALE_TITLE } from "./runState";

const RECORDED = "2026-09-20T10:00:00.123456Z";
const RECORDED_MS = Date.parse("2026-09-20T10:00:00.123Z");
const HOUR = 3_600_000;

const running = { event: "RUNNING", status: "RUNNING", recorded_at: RECORDED };
const final = { event: "FINAL", status: "SUCCEEDED", recorded_at: RECORDED };

afterEach(() => {
  vi.useRealTimers();
});

describe("the stale rule", () => {
  it("is 24 hours: the lifetime of every table an evaluation prepares for its reads", () => {
    expect(STALE_AFTER_HOURS).toBe(24);
  });

  it("keeps a RUNNING event running up to and including 24 hours", () => {
    for (const elapsed of [0, 60_000, 12 * HOUR, 24 * HOUR - 1, 24 * HOUR]) {
      expect(isStale(running, RECORDED_MS + elapsed), `${elapsed} ms`).toBe(false);
    }
  });

  it("calls a RUNNING event stale once it is more than 24 hours old", () => {
    for (const elapsed of [24 * HOUR + 1, 25 * HOUR, 21 * 24 * HOUR]) {
      expect(isStale(running, RECORDED_MS + elapsed), `${elapsed} ms`).toBe(true);
    }
  });

  it("never calls a FINAL event stale, however old", () => {
    for (const status of ["SUCCEEDED", "SUCCEEDED_WITH_WARNINGS", "PARTIAL", "SKIPPED", "FAILED"]) {
      const closed = { ...final, status };
      expect(isStale(closed, RECORDED_MS + 365 * 24 * HOUR), status).toBe(false);
    }
  });

  it("reads the event, not the status", () => {
    // A FINAL row is over whatever its status text; a RUNNING event is open whatever its status text.
    const closed = { ...final, status: "RUNNING" };
    const open = { ...running, status: "A_NEWER_STATUS" };
    expect(isStale(closed, RECORDED_MS + 48 * HOUR)).toBe(false);
    expect(isStale(open, RECORDED_MS + 48 * HOUR)).toBe(true);
  });

  it("keeps a row running when its time cannot be read, or lies in the future", () => {
    expect(isStale({ ...running, recorded_at: "not a timestamp" }, RECORDED_MS + 48 * HOUR)).toBe(false);
    expect(isStale({ ...running, recorded_at: "" }, RECORDED_MS + 48 * HOUR)).toBe(false);
    expect(isStale(running, RECORDED_MS - 48 * HOUR)).toBe(false);
  });

  it("reads the clock when none is given", () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(RECORDED_MS + 24 * HOUR);
    expect(isStale(running)).toBe(false);
    vi.setSystemTime(RECORDED_MS + 24 * HOUR + 1);
    expect(isStale(running)).toBe(true);
  });
});

describe("the status a view shows", () => {
  it("is STALE for a stale evaluation and the registry's own status otherwise", () => {
    expect(displayStatus(running, RECORDED_MS + HOUR)).toBe("RUNNING");
    expect(displayStatus(running, RECORDED_MS + 25 * HOUR)).toBe(STALE);
    expect(displayStatus(final, RECORDED_MS + 25 * HOUR)).toBe("SUCCEEDED");
    expect(displayStatus({ ...final, status: "A_NEWER_STATUS" }, RECORDED_MS)).toBe("A_NEWER_STATUS");
  });

  it("is not a value the registry can hold", () => {
    expect(STALE).toBe("STALE");
    expect(STALE_TITLE).toMatch(/registry status RUNNING/i);
    expect(STALE_TITLE).toContain(`${STALE_AFTER_HOURS} hours`);
  });

  it("is explained by the Evaluation run hint with the same number of hours", () => {
    // Concept text cannot import the constant (packages/contracts has no dependency on the app).
    const hint = evaluationConcepts.find((c) => c.id === "eval:evaluation") as { pitfalls?: string } | undefined;
    expect(hint?.pitfalls).toContain(`more than ${STALE_AFTER_HOURS} hours old`);
    expect(hint?.pitfalls).toContain("shown as Stale");
  });
});

describe("the facets' latest evaluation", () => {
  it("is a RUNNING event recorded when the evaluation started, or a FINAL one", () => {
    // The evaluator writes the RUNNING event with recorded_at = evaluated_at.
    const open = latestEvent({ status: "RUNNING", evaluated_at: RECORDED });
    expect(open).toEqual({ event: "RUNNING", status: "RUNNING", recorded_at: RECORDED });
    expect(isStale(open, RECORDED_MS + 23 * HOUR)).toBe(false);
    expect(isStale(open, RECORDED_MS + 25 * HOUR)).toBe(true);
    const closed = latestEvent({ status: "FAILED", evaluated_at: RECORDED });
    expect(closed.event).toBe("FINAL");
    expect(isStale(closed, RECORDED_MS + 25 * HOUR)).toBe(false);
  });
});

describe("the link to the manual close", () => {
  /** The repository root: the nearest directory above the working directory that holds the document. */
  function repoRoot(): string {
    let dir = process.cwd();
    while (!existsSync(join(dir, CLOSE_BY_HAND.doc))) {
      const parent = dirname(dir);
      if (parent === dir) throw new Error(`${CLOSE_BY_HAND.doc} not found above ${process.cwd()}`);
      dir = parent;
    }
    return dir;
  }
  const read = (path: string) => readFileSync(join(repoRoot(), path), "utf8");

  /** GitHub's heading anchor. */
  function slug(heading: string): string {
    return heading
      .toLowerCase()
      .replace(/[^\p{L}\p{N}\s-]/gu, "")
      .trim()
      .replace(/\s/g, "-");
  }

  /** A markdown document's headings. A `# …` line inside a code fence is a comment, not a heading. */
  function headings(markdown: string): string[] {
    const found: string[] = [];
    let fenced = false;
    for (const line of markdown.split("\n")) {
      if (/^\s*(```|~~~)/.test(line)) fenced = !fenced;
      else if (!fenced) {
        const heading = /^#{1,6} (.+)$/.exec(line);
        if (heading) found.push(heading[1]!.trim());
      }
    }
    return found;
  }

  /** Every anchor GitHub gives a document's headings: a repeated heading gets `-1`, `-2`… */
  function anchors(markdown: string): Set<string> {
    const seen = new Map<string, number>();
    const all = new Set<string>();
    for (const heading of headings(markdown)) {
      const base = slug(heading);
      const n = seen.get(base) ?? 0;
      seen.set(base, n + 1);
      all.add(n ? `${base}-${n}` : base);
    }
    return all;
  }

  it("does not take a comment inside a code fence for a heading", () => {
    const markdown =
      "# Title\n\n```bash\n# new table:\nbq mk t\n```\n\n  ```sql\n  # indented fence\n  ```\n\n## Part\n## Part\n";
    expect(headings(markdown)).toEqual(["Title", "Part", "Part"]);
    expect([...anchors(markdown)]).toEqual(["title", "part", "part-1"]);
  });

  it("names a heading the playbook has, by its title and by the anchor GitHub gives it", () => {
    const playbook = headings(read(CLOSE_BY_HAND.doc));
    expect(playbook.length).toBeGreaterThan(20);
    // Exactly one heading with this title: a rename, or a number put in front of it, fails here.
    expect(playbook.filter((heading) => heading === CLOSE_BY_HAND.title)).toHaveLength(1);
    expect(slug(CLOSE_BY_HAND.title)).toBe(CLOSE_BY_HAND.anchor);
    // ... and no other heading shares the anchor (GitHub would number the second one).
    expect(playbook.filter((heading) => slug(heading) === CLOSE_BY_HAND.anchor)).toHaveLength(1);
  });

  it("finds a playbook heading behind every anchor the GUI's documents type by hand", () => {
    const known = anchors(read(CLOSE_BY_HAND.doc));
    const doc = CLOSE_BY_HAND.doc.split("/").at(-1)!.replace(".", "\\.");
    const linked = ["gui/README.md", "gui/docs/UX.md"].flatMap((path) =>
      [...read(path).matchAll(new RegExp(`${doc}#([\\w-]+)`, "g"))].map((m) => ({ path, anchor: m[1]! })),
    );
    // Any heading of the playbook may be linked; each link must land on one.
    expect(linked.length).toBeGreaterThan(0);
    for (const { path, anchor } of linked) expect(known.has(anchor), `${path} → #${anchor}`).toBe(true);
  });
});
