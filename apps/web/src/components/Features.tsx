"use client";

import type { ElementType, CSSProperties } from "react";
import { Trans, useLingui } from "@lingui/react/macro";
import { siteConfig } from "@/content/config";
import { ThemedImage } from "@/components/ThemedImage";
import { eyebrowClass, sectionHeadingClass } from "@/lib/styles";
import { Globe, SlidersHorizontal, Bell, GitGraph, ClipboardList, BarChart3, Target, Building2, Share2 } from "lucide-react";

const iconMap: Record<string, ElementType> = {
  source: Globe,
  filters: SlidersHorizontal,
  alerts: Bell,
  tracking: GitGraph,
  interviews: ClipboardList,
  stats: BarChart3,
  curate: Target,
  companies: Building2,
  share: Share2,
};

const CONTAINER_MAX = 1200;
const CONTAINER_PAD = 16;
const TEXT_MAX_W = 520;
const IMAGE_BORDER_RADIUS = 24;
const EXTRA_WIDE_BREAKPOINT = 2448;
const MEDIA_SHADOW = "0px 12px 32px rgba(15, 23, 42, 0.18)";

/**
 * CSS expression: padding that aligns a child's edge with the content edge
 * of a `max-w-[1200px] px-4` container.
 */
const ALIGN_PAD = `max(${CONTAINER_PAD}px, calc((100vw - ${CONTAINER_MAX}px) / 2 + ${CONTAINER_PAD}px))`;

function extraWideInset(mediaWidth: number) {
  const offset = CONTAINER_PAD + mediaWidth;
  return `max(0px, calc(50vw - ${offset}px))`;
}

type PointBlockProps = {
  icon: string;
  title: React.ReactNode;
  description: React.ReactNode;
};

function PointBlock({ icon, title, description }: PointBlockProps) {
  const IconComponent = iconMap[icon] ?? Bell;
  return (
    <div className="flex items-start gap-4">
      <IconComponent size={20} className="mt-0.5 shrink-0" />
      <div>
        <dt className="font-semibold">{title}</dt>
        <dd className="mt-1 text-muted">{description}</dd>
      </div>
    </div>
  );
}

function ImageWrapper({
  mediaWidth,
  inverted,
  sectionId,
  children,
}: {
  mediaWidth: number;
  inverted: boolean;
  sectionId?: string;
  children: React.ReactNode;
}) {
  const id = sectionId ?? (inverted ? "inv" : "std");

  const wrapperStyle: CSSProperties = {
    width: "100%",
    maxWidth: mediaWidth,
    overflow: "hidden",
    boxShadow: MEDIA_SHADOW,
    display: "flex",
    justifyContent: inverted ? "flex-end" : "flex-start",
  };

  return (
    <div className={`feat-img-${id} bg-surface`} style={wrapperStyle}>
      <style>{`
        .feat-img-${id} {
          border-radius: ${inverted
            ? `0 ${IMAGE_BORDER_RADIUS}px ${IMAGE_BORDER_RADIUS}px 0`
            : `${IMAGE_BORDER_RADIUS}px 0 0 ${IMAGE_BORDER_RADIUS}px`};
        }
        @media (min-width: ${EXTRA_WIDE_BREAKPOINT}px) {
          .feat-img-${id} { border-radius: ${IMAGE_BORDER_RADIUS}px; }
        }
        .feat-img-${id} img {
          width: ${mediaWidth}px;
          max-width: none;
          height: auto;
        }
      `}</style>
      {children}
    </div>
  );
}

function useLocaleScreenshot(screenshot: typeof siteConfig.features.sections[number]["screenshot"]) {
  const { i18n } = useLingui();
  const lang = i18n.locale;
  return {
    light: screenshot.light.replace("{lang}", lang),
    dark: screenshot.dark.replace("{lang}", lang),
  };
}

function FeatureSection1() {
  const { t } = useLingui();
  const cfg = siteConfig.features.sections[0];
  const mediaWidth = cfg.screenshot.width;
  const src = useLocaleScreenshot(cfg.screenshot);

  return (
    <>
      <style>{`
        .feat-row-1 { padding-left: ${CONTAINER_PAD}px; padding-right: 0; }
        @media (min-width: 1024px) {
          .feat-row-1 { padding-left: ${ALIGN_PAD}; }
        }
        @media (min-width: ${EXTRA_WIDE_BREAKPOINT}px) {
          .feat-row-1 { padding-right: ${extraWideInset(mediaWidth)}; }
        }
      `}</style>
      <div className="feat-row-1 flex flex-col items-stretch gap-12 lg:flex-row lg:gap-20">
        <div className="w-full shrink-0 pr-4 lg:w-auto lg:max-w-[520px] lg:pr-0" style={{ flexBasis: TEXT_MAX_W }}>
          <div className="flex flex-col gap-4">
            <div>
              <span className={eyebrowClass}>
                <Trans id="home.features.s1.eyebrow" comment="Feature section 1 eyebrow text">Search with precision</Trans>
              </span>
              <h2 className={`mt-2 ${sectionHeadingClass}`}>
                <Trans id="home.features.s1.title" comment="Feature section 1 heading">Every filter a job seeker actually needs</Trans>
              </h2>
              <p className="mt-4 text-muted">
                <Trans id="home.features.s1.description" comment="Feature section 1 description">Search across thousands of companies scraped directly from their career pages. Filter by seniority, tech stack, salary range, location, and language — all at once.</Trans>
              </p>
            </div>
            <dl className="mt-8 flex flex-col gap-6">
              <PointBlock
                icon={cfg.pointIcons[0]}
                title={<Trans id="home.features.s1.p1.title" comment="Feature: direct from source title">Direct from the source</Trans>}
                description={<Trans id="home.features.s1.p1.description" comment="Feature: direct from source description">We scrape career pages from Workday, Greenhouse, Lever, and more — roles show up here before they hit the big aggregators.</Trans>}
              />
              <PointBlock
                icon={cfg.pointIcons[1]}
                title={<Trans id="home.features.s1.p2.title" comment="Feature: multi-dimensional filters title">Multi-dimensional filters</Trans>}
                description={<Trans id="home.features.s1.p2.description" comment="Feature: multi-dimensional filters description">Combine seniority, technologies, salary, location, and job language in a single query. No more sifting through noise.</Trans>}
              />
              <PointBlock
                icon={cfg.pointIcons[2]}
                title={<Trans id="home.features.s1.p3.title" comment="Feature: watchlists and alerts title">Watchlists and alerts</Trans>}
                description={<Trans id="home.features.s1.p3.description" comment="Feature: watchlists and alerts description">Save any search as a watchlist and get email alerts when new roles match your criteria.</Trans>}
              />
            </dl>
          </div>
        </div>
        <div className="flex flex-1 justify-start lg:justify-end" style={{ minHeight: 400 }}>
          <ImageWrapper mediaWidth={mediaWidth} inverted={false}>
            <ThemedImage darkSrc={src.dark} lightSrc={src.light} alt={t({ id: "home.features.s1.screenshot.alt", comment: "Alt text for feature section 1 screenshot", message: "Job Seek search results filtered by role and location" })} width={cfg.screenshot.width} height={cfg.screenshot.height} />
          </ImageWrapper>
        </div>
      </div>
    </>
  );
}

function FeatureSection2() {
  const { t } = useLingui();
  const cfg = siteConfig.features.sections[1];
  const mediaWidth = cfg.screenshot.width;
  const src = useLocaleScreenshot(cfg.screenshot);

  return (
    <>
      <style>{`
        .feat-row-2 { padding-left: 0; padding-right: ${CONTAINER_PAD}px; }
        @media (min-width: 1024px) {
          .feat-row-2 { padding-left: 0; padding-right: ${ALIGN_PAD}; }
        }
        @media (min-width: ${EXTRA_WIDE_BREAKPOINT}px) {
          .feat-row-2 { padding-left: ${extraWideInset(mediaWidth)}; }
        }
      `}</style>
      <div className="feat-row-2 flex flex-col items-stretch gap-12 lg:flex-row-reverse lg:gap-20">
        <div className="w-full shrink-0 pl-4 lg:w-auto lg:max-w-[520px] lg:pl-0" style={{ flexBasis: TEXT_MAX_W }}>
          <div className="flex flex-col gap-4">
            <div>
              <span className={eyebrowClass}>
                <Trans id="home.features.s2.eyebrow" comment="Feature section 2 eyebrow text">Track your pipeline</Trans>
              </span>
              <h2 className={`mt-2 ${sectionHeadingClass}`}>
                <Trans id="home.features.s2.title" comment="Feature section 2 heading">From saved role to signed offer, all in one place</Trans>
              </h2>
              <p className="mt-4 text-muted">
                <Trans id="home.features.s2.description" comment="Feature section 2 description">Save any role you find, move it through your pipeline as you apply and interview, and see where every application stands at a glance.</Trans>
              </p>
            </div>
            <dl className="mt-8 flex flex-col gap-6">
              <PointBlock
                icon={cfg.pointIcons[0]}
                title={<Trans id="home.features.s2.p1.title" comment="Feature: status tracking title">Status tracking</Trans>}
                description={<Trans id="home.features.s2.p1.description" comment="Feature: status tracking description">Move each role from saved to applied, interviewing, offered, or rejected. Always know where you stand.</Trans>}
              />
              <PointBlock
                icon={cfg.pointIcons[1]}
                title={<Trans id="home.features.s2.p2.title" comment="Feature: interview log title">Interview log</Trans>}
                description={<Trans id="home.features.s2.p2.description" comment="Feature: interview log description">Record each interview round with date, type, and notes so nothing slips through the cracks.</Trans>}
              />
              <PointBlock
                icon={cfg.pointIcons[2]}
                title={<Trans id="home.features.s2.p3.title" comment="Feature: application stats title">Application stats</Trans>}
                description={<Trans id="home.features.s2.p3.description" comment="Feature: application stats description">See your funnel, conversion rates, and activity heatmap to understand what is working.</Trans>}
              />
            </dl>
          </div>
        </div>
        <div className="flex flex-1 justify-start" style={{ minHeight: 400 }}>
          <ImageWrapper mediaWidth={mediaWidth} inverted={true}>
            <ThemedImage darkSrc={src.dark} lightSrc={src.light} alt={t({ id: "home.features.s2.screenshot.alt", comment: "Alt text for feature section 2 screenshot", message: "Job Seek application tracker showing saved jobs and interview details" })} width={cfg.screenshot.width} height={cfg.screenshot.height} />
          </ImageWrapper>
        </div>
      </div>
    </>
  );
}

function FeatureSection3() {
  const { t } = useLingui();
  const cfg = siteConfig.features.sections[2];
  const mediaWidth = cfg.screenshot.width;
  const src = useLocaleScreenshot(cfg.screenshot);

  return (
    <>
      <style>{`
        .feat-row-3 { padding-left: ${CONTAINER_PAD}px; padding-right: 0; }
        @media (min-width: 1024px) {
          .feat-row-3 { padding-left: ${ALIGN_PAD}; }
        }
        @media (min-width: ${EXTRA_WIDE_BREAKPOINT}px) {
          .feat-row-3 { padding-right: ${extraWideInset(mediaWidth)}; }
        }
      `}</style>
      <div className="feat-row-3 flex flex-col items-stretch gap-12 lg:flex-row lg:gap-20">
        <div className="w-full shrink-0 pr-4 lg:w-auto lg:max-w-[520px] lg:pr-0" style={{ flexBasis: TEXT_MAX_W }}>
          <div className="flex flex-col gap-4">
            <div>
              <span className={eyebrowClass}>
                <Trans id="home.features.s3.eyebrow" comment="Feature section 3 eyebrow text">Your companies, your rules</Trans>
              </span>
              <h2 className={`mt-2 ${sectionHeadingClass}`}>
                <Trans id="home.features.s3.title" comment="Feature section 3 heading">Curate a watchlist for any niche</Trans>
              </h2>
              <p className="mt-4 text-muted">
                <Trans id="home.features.s3.description" comment="Feature section 3 description">Pick the companies you care about, set your filters, and get a live feed of matching roles. Share it publicly or keep it private.</Trans>
              </p>
            </div>
            <dl className="mt-8 flex flex-col gap-6">
              <PointBlock
                icon={cfg.pointIcons[0]}
                title={<Trans id="home.features.s3.p1.title" comment="Feature: focused search title">Focused search</Trans>}
                description={<Trans id="home.features.s3.p1.description" comment="Feature: focused search description">Combine specific companies with role filters to zero in on exactly the positions you want.</Trans>}
              />
              <PointBlock
                icon={cfg.pointIcons[1]}
                title={<Trans id="home.features.s3.p2.title" comment="Feature: request companies title">Request any company</Trans>}
                description={<Trans id="home.features.s3.p2.description" comment="Feature: request companies description">Missing a company? Paste its careers page URL and we start indexing it for you.</Trans>}
              />
              <PointBlock
                icon={cfg.pointIcons[2]}
                title={<Trans id="home.features.s3.p3.title" comment="Feature: share watchlists title">Share with anyone</Trans>}
                description={<Trans id="home.features.s3.p3.description" comment="Feature: share watchlists description">Make a watchlist public and share the link with friends, communities, or your network.</Trans>}
              />
            </dl>
          </div>
        </div>
        <div className="flex flex-1 justify-start lg:justify-end" style={{ minHeight: 400 }}>
          <ImageWrapper mediaWidth={mediaWidth} inverted={false} sectionId="s3">
            <ThemedImage darkSrc={src.dark} lightSrc={src.light} alt={t({ id: "home.features.s3.screenshot.alt", comment: "Alt text for feature section 3 screenshot", message: "Job Seek watchlist showing curated robotics engineering roles in Zurich" })} width={cfg.screenshot.width} height={cfg.screenshot.height} />
          </ImageWrapper>
        </div>
      </div>
    </>
  );
}

export function Features() {
  return (
    <section
      id={siteConfig.features.anchorId}
      className="relative z-[1] overflow-x-hidden overflow-y-visible py-16 pb-8 md:py-24 md:pb-12"
    >
      <div className="flex flex-col gap-24 md:gap-32">
        <FeatureSection1 />
        <FeatureSection2 />
        <FeatureSection3 />
      </div>
    </section>
  );
}
