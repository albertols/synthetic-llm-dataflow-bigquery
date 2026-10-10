/**
 * INTRO — the landing page: what this project does, in one picture and a
 * minute. The header states the promise (copied from the README), the hero
 * walks the nine pipeline stages, the counters show what the registry holds,
 * then the design (one card per DESIGN.md section), the package map, the
 * relationship shapes and a glossary over every concept.
 */
import { Link } from "@tanstack/react-router";
import { LazyMotion, MotionConfig, domAnimation } from "motion/react";
import { ArrowRight, Ban, BookOpen, HardDrive, ShieldCheck } from "lucide-react";

import { PageHeader } from "@/components/PageHeader";
import { Button } from "@/components/ui/button";
import { useFacets } from "@/lib/api";

import { designUrl } from "./content/designSections";
import { Glossary } from "./components/Glossary";
import { HowItWorks } from "./components/HowItWorks";
import { LiveCounters } from "./components/LiveCounters";
import { PackageMap } from "./components/PackageMap";
import { PipelineHero } from "./components/PipelineHero";
import { ShapeGallery } from "./components/ShapeGallery";

const ON_THIS_PAGE = [
  { href: "#pipeline", label: "Pipeline" },
  { href: "#counters", label: "Live counters" },
  { href: "#how-it-works", label: "How it works" },
  { href: "#packages", label: "Packages" },
  { href: "#shapes", label: "Relationship shapes" },
  { href: "#glossary", label: "Glossary" },
] as const;

/** The README's promise, one clause per chip, each chip sentence-cased as shown. */
const PROMISES = [
  { icon: ShieldCheck, text: "No data or prompts ever leave the project boundary" },
  { icon: Ban, text: "No external AI APIs" },
  { icon: HardDrive, text: "No model hubs at runtime" },
] as const;

export function IntroPage() {
  const facets = useFacets();
  return (
    <MotionConfig reducedMotion="user">
      <LazyMotion features={domAnimation}>
        <div className="flex flex-col gap-10 md:gap-14">
          <div className="grid gap-6">
            <PageHeader
              eyebrow="Intro"
              title="Synthetic BigQuery data with self-hosted LLMs on Apache Beam / Dataflow"
              description={
                <p>
                  Generate fictitious-but-realistic synthetic rows for any BigQuery table — driven by its DDL plus a
                  bounded reference sample, with all LLM inference self-hosted on GPU workers inside the Dataflow
                  pipeline.
                </p>
              }
              actions={
                <>
                  <Button asChild variant="primary">
                    <Link to="/evaluation">
                      Explore evaluations
                      <ArrowRight aria-hidden="true" />
                    </Link>
                  </Button>
                  <Button asChild variant="outline">
                    <a href={designUrl()} target="_blank" rel="noopener noreferrer">
                      <BookOpen aria-hidden="true" />
                      Read DESIGN.md
                      <span className="sr-only"> (opens in a new tab)</span>
                    </a>
                  </Button>
                </>
              }
            />
            <ul aria-label="What the project promises" className="flex flex-wrap gap-2">
              {PROMISES.map(({ icon: Icon, text }) => (
                <li
                  key={text}
                  className="inline-flex items-center gap-1.5 rounded-full border border-border-strong bg-surface-1 px-3 py-1 text-xs font-medium text-text-2"
                >
                  <Icon className="size-3.5 text-cpu-text" aria-hidden="true" />
                  {text}
                </li>
              ))}
            </ul>
            <nav aria-label="On this page">
              <ul className="flex flex-wrap gap-x-1 gap-y-1 border-y border-border py-2 text-sm">
                {ON_THIS_PAGE.map((item) => (
                  <li key={item.href}>
                    <a
                      href={item.href}
                      className="inline-flex h-8 items-center rounded-md px-2.5 text-text-2 hover:bg-surface-3 hover:text-text-1"
                    >
                      {item.label}
                    </a>
                  </li>
                ))}
              </ul>
            </nav>
          </div>

          <PipelineHero facets={facets.data?.data} />
          <LiveCounters />
          <HowItWorks />
          <PackageMap />
          <ShapeGallery />
          <Glossary />
        </div>
      </LazyMotion>
    </MotionConfig>
  );
}
