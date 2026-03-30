"use client";

import { Trans } from "@lingui/react/macro";
import { useLingui } from "@lingui/react/macro";
import { Bot, Radar, Search } from "lucide-react";
import { eyebrowClass, sectionHeadingClass } from "@/lib/styles";
import { Button } from "@/components/ui/Button";

function ApifyIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
      <rect width="32" height="32" rx="6" fill="#71C151" />
      <path d="M16 5L26 24H6L16 5Z" fill="white" />
      <path d="M16 13L20 21H12L16 13Z" fill="#71C151" />
    </svg>
  );
}

const featureCards = [
  {
    id: "search",
    icon: Search,
  },
  {
    id: "ghosting",
    icon: Bot,
  },
  {
    id: "discovery",
    icon: Radar,
  },
] as const;

export function AgenticFeatures() {
  const { t } = useLingui();

  return (
    <section className="mx-auto max-w-[1200px] px-4 py-12 md:py-20">
      <div className="mx-auto max-w-[760px] text-center">
        <span className={eyebrowClass}>
          <Trans id="home.agentic.eyebrow" comment="Agentic features section eyebrow">For AI agents</Trans>
        </span>
        <h2 className={`mt-2 ${sectionHeadingClass}`}>
          <Trans id="home.agentic.title" comment="Agentic features section heading">Agentic workflows on top of the job index</Trans>
        </h2>
        <p className="mt-4 text-muted">
          <Trans id="home.agentic.description" comment="Agentic features section description">
            Job Seek is not only a search interface for people. It also exposes structured workflows for agents that need to search jobs, detect ghost listings, and map where hiring is accelerating.
          </Trans>
        </p>
      </div>

      <div className="mt-10 grid gap-6 lg:grid-cols-3">
        {featureCards.map(({ id, icon: Icon }) => (
          <article key={id} className="flex h-full flex-col rounded-2xl border border-border-soft bg-surface p-6">
            <div className="flex h-11 w-11 items-center justify-center rounded-full bg-primary/10 text-primary">
              <Icon size={20} />
            </div>
            <h3 className="mt-5 text-lg font-semibold">
              {id === "search" && (
                <Trans id="home.agentic.cards.search.title" comment="Agentic feature card title: search">
                  Search the index directly
                </Trans>
              )}
              {id === "ghosting" && (
                <Trans id="home.agentic.cards.ghosting.title" comment="Agentic feature card title: ghosting">
                  Detect likely ghost jobs
                </Trans>
              )}
              {id === "discovery" && (
                <Trans id="home.agentic.cards.discovery.title" comment="Agentic feature card title: discovery">
                  Discover who is hiring now
                </Trans>
              )}
            </h3>
            <p className="mt-3 text-sm text-muted">
              {id === "search" && (
                <Trans id="home.agentic.cards.search.description" comment="Agentic feature card description: search">
                  Query jobs, companies, and filter taxonomies over JSON so copilots and internal tools can work against the same structured inventory as the product.
                </Trans>
              )}
              {id === "ghosting" && (
                <Trans id="home.agentic.cards.ghosting.description" comment="Agentic feature card description: ghosting">
                  Reconstruct posting history from archived snapshots to flag roles that appear to stay open without real hiring intent and return a report your agent can reason over.
                </Trans>
              )}
              {id === "discovery" && (
                <Trans id="home.agentic.cards.discovery.description" comment="Agentic feature card description: discovery">
                  Pull trend signals across 39+ job boards to spot expanding companies, shrinking activity, and fresh hiring bursts before they become obvious elsewhere.
                </Trans>
              )}
            </p>
            <p className="mt-4 text-xs font-medium uppercase tracking-wider text-muted">
              {id === "search" && (
                <Trans id="home.agentic.cards.search.meta" comment="Agentic feature card metadata: search">
                  Subscription required
                </Trans>
              )}
              {id === "ghosting" && (
                <Trans id="home.agentic.cards.ghosting.meta" comment="Agentic feature card metadata: ghosting">
                  Open tier
                </Trans>
              )}
              {id === "discovery" && (
                <Trans id="home.agentic.cards.discovery.meta" comment="Agentic feature card metadata: discovery">
                  Open tier
                </Trans>
              )}
            </p>
          </article>
        ))}
      </div>

      <div className="mt-8 flex flex-col items-start justify-between gap-4 rounded-2xl border border-border-soft bg-surface px-6 py-5 md:flex-row md:items-center">
        <div className="max-w-[720px]">
          <p className="text-sm font-semibold">
            <Trans id="home.agentic.cta.title" comment="Agentic features CTA title">
              Need the endpoint-level docs?
            </Trans>
          </p>
          <p className="mt-1 text-sm text-muted">
            <Trans id="home.agentic.cta.description" comment="Agentic features CTA description">
              Review the agentic API reference, auth model, and example requests for search, ghost-job analysis, and company discovery.
            </Trans>
          </p>
          <a
            href="https://apify.com"
            target="_blank"
            rel="noopener noreferrer"
            className="mt-3 inline-flex items-center gap-1.5 text-xs text-muted hover:text-foreground transition-colors"
          >
            <ApifyIcon className="w-3.5 h-3.5 shrink-0" />
            Powered by Apify
          </a>
        </div>
        <Button href="/agentic" variant="outline">
          {t({
            id: "home.agentic.cta.button",
            comment: "Agentic features CTA button label",
            message: "View API docs",
          })}
        </Button>
      </div>
    </section>
  );
}
