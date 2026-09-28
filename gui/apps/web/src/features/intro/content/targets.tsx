/**
 * Where an INTRO element deep-links: another tab (with the params that tab's
 * route accepts) or an anchor on this page. One component renders every
 * target so each tab's route contract is typed in one place.
 */
import { Link } from "@tanstack/react-router";
import type { ComponentPropsWithRef, ReactNode } from "react";

import type { KnobId } from "@contracts/generated/knobs";

export type IntroTarget =
  | { tab: "rag" }
  | { tab: "evaluation" }
  | { tab: "evaluation-run"; evaluationId: string }
  | { tab: "config"; knob: KnobId };

/** The tab's name as the top nav prints it. */
export function targetTabName(target: IntroTarget): string {
  switch (target.tab) {
    case "rag":
      return "RAG";
    case "evaluation":
    case "evaluation-run":
      return "Evaluation";
    case "config":
      return "Config";
  }
}

/** The URL a target resolves to (tests and aria descriptions). */
export function targetHref(target: IntroTarget): string {
  switch (target.tab) {
    case "rag":
      return "/rag";
    case "evaluation":
      return "/evaluation";
    case "evaluation-run":
      return `/evaluation/${encodeURIComponent(target.evaluationId)}`;
    case "config":
      return `/config?knob=${encodeURIComponent(target.knob)}`;
  }
}

type AnchorProps = Omit<ComponentPropsWithRef<"a">, "href" | "target"> & { to: IntroTarget; children: ReactNode };

/** A router Link to an INTRO target; every other prop goes to the anchor. */
export function TargetLink({ to: target, children, ...props }: AnchorProps) {
  switch (target.tab) {
    case "rag":
      return (
        <Link to="/rag" {...props}>
          {children}
        </Link>
      );
    case "evaluation":
      return (
        <Link to="/evaluation" {...props}>
          {children}
        </Link>
      );
    case "evaluation-run":
      return (
        <Link to="/evaluation/$evaluationId" params={{ evaluationId: target.evaluationId }} {...props}>
          {children}
        </Link>
      );
    case "config":
      return (
        <Link to="/config" search={{ knob: target.knob }} {...props}>
          {children}
        </Link>
      );
  }
}
