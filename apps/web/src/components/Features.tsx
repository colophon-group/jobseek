"use client";

/**
 * Features — "bleed" showcase sections on the homepage.
 *
 * ## Layout concept
 *
 * Each section is a two-column row: text + screenshot image. The screenshot
 * is intentionally wider than the viewport — it "bleeds" past the screen edge
 * and gets progressively clipped as the viewport narrows.
 *
 * - Section 1 (standard):  text LEFT,  image bleeds RIGHT.
 * - Section 2 (inverted):  text RIGHT, image bleeds LEFT.
 *
 * Text columns are aligned with the 1200px page container via ALIGN_PAD,
 * a CSS max() expression. The image side has zero padding so it sits flush
 * against the viewport edge.
 *
 * ## Responsive breakpoints
 *
 * - < 1024px   — Stacked. Text on top with px-4 inset, image below bleeding
 *                to the appropriate edge.
 * - >= 1024px  — Side by side. Text max 520px, image fills remaining space.
 * - >= 2448px  — Extra-wide. Both edges pull inward; image gets full border-
 *                radius and no longer touches the viewport edge.
 *
 * ## Image clipping
 *
 * ImageWrapper sets `overflow: hidden` with `max-width: <screenshot-width>px`.
 * The inner <img> has a fixed pixel width (e.g. 1200px) with `max-width: none`,
 * so it overflows its container. As the viewport narrows:
 *   - Standard:  image is left-aligned  → right side clips first.
 *   - Inverted:  image is right-aligned → left side clips first.
 *
 * Border-radius is applied only to the visible (inner) edge:
 *   - Standard:  left-rounded  (24px 0 0 24px)
 *   - Inverted:  right-rounded (0 24px 24px 0)
 *   - Extra-wide: fully rounded (24px)
 *
 * ## Theme handling
 *
 * ThemedImage is a client component that renders a single <img> matching
 * the active theme, so no CSS display toggles are needed.
 *
 * ## Key constants
 *
 * See CONTAINER_MAX, CONTAINER_PAD, ALIGN_PAD, IMAGE_BORDER_RADIUS,
 * EXTRA_WIDE_BREAKPOINT, and MEDIA_SHADOW below.
 *
 * @see docs/features.md for the full specification.
 */

import type { ElementType, CSSProperties } from "react";
import { Trans } from "@lingui/react/macro";
import { siteConfig } from "@/content/config";
import { ThemedImage } from "@/components/ThemedImage";
import { eyebrowClass, sectionHeadingClass } from "@/lib/styles";
import { Bell, CircleCheck, Bookmark, Bug, Globe, Megaphone } from "lucide-react";

const iconMap: Record<string, ElementType> = {
  notifications: Bell,
  check_circle: CircleCheck,
  bookmark: Bookmark,
  bug: Bug,
  travel_explore: Globe,
  campaign: Megaphone,
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
  children,
}: {
  mediaWidth: number;
  inverted: boolean;
  children: React.ReactNode;
}) {
  const id = inverted ? "inv" : "std";

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

function FeatureSection1() {
  const cfg = siteConfig.features.sections[0];
  const mediaWidth = cfg.screenshot.width;

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
      <div className="feat-row-1 flex flex-col items-stretch gap-12 lg:flex-row lg:gap-16 lg:gap-20">
        <div className="w-full shrink-0 pr-4 lg:w-auto lg:max-w-[520px] lg:pr-0" style={{ flexBasis: TEXT_MAX_W }}>
          <div className="flex flex-col gap-4">
            <div>
              <span className={eyebrowClass}>
                <Trans id="home.features.s1.eyebrow" comment="Feature section 1 eyebrow text">Everything you need to stay ahead</Trans>
              </span>
              <h2 className={`mt-2 ${sectionHeadingClass}`}>
                <Trans id="home.features.s1.title" comment="Feature section 1 heading">Built for active job seekers</Trans>
              </h2>
              <p className="mt-4 text-muted">
                <Trans id="home.features.s1.description" comment="Feature section 1 description">Track roles, get notified when companies post something new, and keep your pipeline clean.</Trans>
              </p>
            </div>
            <dl className="mt-8 flex flex-col gap-6">
              <PointBlock
                icon={cfg.pointIcons[0]}
                title={<Trans id="home.features.s1.p1.title" comment="Feature: company alerts title">Company alerts</Trans>}
                description={<Trans id="home.features.s1.p1.description" comment="Feature: company alerts description">Follow target employers and get notified when they add new roles.</Trans>}
              />
              <PointBlock
                icon={cfg.pointIcons[1]}
                title={<Trans id="home.features.s1.p2.title" comment="Feature: application tracker title">Application tracker</Trans>}
                description={<Trans id="home.features.s1.p2.description" comment="Feature: application tracker description">Log where you applied, status, contacts, and next steps.</Trans>}
              />
              <PointBlock
                icon={cfg.pointIcons[2]}
                title={<Trans id="home.features.s1.p3.title" comment="Feature: saved searches title">Saved searches</Trans>}
                description={<Trans id="home.features.s1.p3.description" comment="Feature: saved searches description">Save your filters for quick scans without retyping everything.</Trans>}
              />
            </dl>
          </div>
        </div>
        <div className="flex flex-1 justify-start lg:justify-end" style={{ minHeight: 400 }}>
          <ImageWrapper mediaWidth={mediaWidth} inverted={false}>
            <ThemedImage darkSrc={cfg.screenshot.dark} lightSrc={cfg.screenshot.light} alt="Job Seek dashboard showing tracked applications and company alerts" width={cfg.screenshot.width} height={cfg.screenshot.height} />
          </ImageWrapper>
        </div>
      </div>
    </>
  );
}

function FeatureSection2() {
  const cfg = siteConfig.features.sections[1];
  const mediaWidth = cfg.screenshot.width;

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
      <div className="feat-row-2 flex flex-col items-stretch gap-12 lg:flex-row-reverse lg:gap-16 lg:gap-20">
        <div className="w-full shrink-0 pl-4 lg:w-auto lg:max-w-[520px] lg:pl-0" style={{ flexBasis: TEXT_MAX_W }}>
          <div className="flex flex-col gap-4">
            <div>
              <span className={eyebrowClass}>
                <Trans id="home.features.s2.eyebrow" comment="Feature section 2 eyebrow text">Stay in control</Trans>
              </span>
              <h2 className={`mt-2 ${sectionHeadingClass}`}>
                <Trans id="home.features.s2.title" comment="Feature section 2 heading">The first job aggregator that puts you behind the wheel</Trans>
              </h2>
              <p className="mt-4 text-muted">
                <Trans id="home.features.s2.description" comment="Feature section 2 description">{"Don't see your favourite company in the feed? Paste its careers link and we'll start scraping it for you\u2014no scripts, no spreadsheets, no waiting in support queues."}</Trans>
              </p>
            </div>
            <dl className="mt-8 flex flex-col gap-6">
              <PointBlock
                icon={cfg.pointIcons[0]}
                title={<Trans id="home.features.s2.p1.title" comment="Feature: paste a link title">Paste a link, kick off a crawl</Trans>}
                description={<Trans id="home.features.s2.p1.description" comment="Feature: paste a link description">Point us at any careers page or Notion job board and Job Seek mirrors it in your workspace within minutes.</Trans>}
              />
              <PointBlock
                icon={cfg.pointIcons[1]}
                title={<Trans id="home.features.s2.p2.title" comment="Feature: kill tab routine title">Kill the 50-tab routine</Trans>}
                description={<Trans id="home.features.s2.p2.description" comment="Feature: kill tab routine description">Park every interesting startup in one dashboard instead of juggling Chrome windows and bookmarks.</Trans>}
              />
              <PointBlock
                icon={cfg.pointIcons[2]}
                title={<Trans id="home.features.s2.p3.title" comment="Feature: alerts you drive title">Alerts you actually drive</Trans>}
                description={<Trans id="home.features.s2.p3.description" comment="Feature: alerts you drive description">Set the cadence per company so you hear about fresh openings without doom-scrolling job sites all day.</Trans>}
              />
            </dl>
          </div>
        </div>
        <div className="flex flex-1 justify-start" style={{ minHeight: 400 }}>
          <ImageWrapper mediaWidth={mediaWidth} inverted={true}>
            <ThemedImage darkSrc={cfg.screenshot.dark} lightSrc={cfg.screenshot.light} alt="Job Seek interface for submitting company links and configuring custom alerts" width={cfg.screenshot.width} height={cfg.screenshot.height} />
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
      </div>
    </section>
  );
}
