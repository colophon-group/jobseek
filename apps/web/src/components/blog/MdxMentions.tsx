/**
 * MDX entity-mention components (#2828).
 *
 * Blog posts reference internal entities (companies, watchlists,
 * future: postings, taxonomies, authors) by id rather than hand-
 * authored markdown links. Two visual treatments per type:
 *
 *  - **Inline pill** (`<Company slug="..." />`, `<Watchlist owner="..."
 *    slug="..." />`) — flows inside paragraph text, just the entity
 *    icon + name. Use when the mention is one reference among many in
 *    a sentence.
 *  - **Block card** (`<CompanyCard slug="..." />`, `<WatchlistCard
 *    owner="..." slug="..." />`) — multi-line embed with logo / icon,
 *    name, derived stats, description excerpt, and a "view" CTA. Use
 *    when the mention IS the content — a featured spotlight, a "see
 *    also" reference, or a stat anchor.
 *
 * All variants resolve their entity at server-render time so the post
 * body reflects current state. Missing references fall back to
 * `<code>{Type ...}</code>` so broken links are visible during review.
 *
 * Iconography follows `apps/web/docs/icons.md` — `Building2` for
 * companies (avatar fallback), `Eye` for watchlists (matches the
 * watchlists nav slot in `AppHeader`). Don't introduce new icons
 * without adding rows to `icons.md` first.
 *
 * To add a new mention type: write the inline + card pair, fetch the
 * entity via an ISR-safe action (must NOT read session/cookies/headers
 * — see `app/__tests__/isr-routes.test.ts`'s `TAINTED_HELPERS`),
 * register both variants in `buildMdxComponents()`. Mention components
 * run inside the post page's `compileMDX` call which is itself
 * ISR-cached at revalidate=86400.
 */

import { cache } from "react";
import Link from "next/link";
import Image from "next/image";
import { Building2, Eye, Briefcase } from "lucide-react";
import { loadCatalog, isLocale, defaultLocale, type Locale } from "@/lib/i18n";
import { getCompanyBySlug } from "@/lib/actions/company";
import {
  getPublicWatchlistByUserAndSlug,
  getWatchlistPostingDisplayCounts,
} from "@/lib/actions/watchlists";

/**
 * Helper: load the Lingui catalog for a string locale, normalizing
 * unknown values to the default. Returns the i18n instance so the
 * caller can resolve message ids via `i18n._({...})`. Mention cards
 * render in MDX where each post-page render binds the locale at
 * `buildMdxComponents()` time, so the value is already validated;
 * this guard exists for safety against MDX authors typing a bad value.
 */
async function loadLocaleCatalog(locale: string) {
  const normalized: Locale = isLocale(locale) ? locale : defaultLocale;
  return loadCatalog(normalized);
}

// ── Inline pill ─────────────────────────────────────────────────────

/**
 * Shared inline-pill skeleton. Every inline mention type uses the same
 * shape so readers learn the pattern once. `inline-flex` +
 * `align-baseline` keeps the pill from breaking surrounding paragraph
 * rhythm at body-text sizes.
 */
function MentionPill({
  href,
  icon,
  label,
  meta,
}: {
  href: string;
  icon: React.ReactNode;
  label: string;
  meta?: string;
}) {
  // The `mention` class is consumed by `.blog-post a.mention` in
  // `app/globals.css` to undo the post-body link underline + info-color
  // inheritance. Without it, the prose `a` rule paints the whole pill
  // blue and underlines every word inside.
  return (
    <Link
      href={href}
      className="mention inline-flex items-center gap-1.5 align-baseline rounded-md border border-border-soft bg-border-soft/40 px-2 py-0.5 text-[0.95em] transition-colors hover:bg-border-soft"
    >
      <span className="inline-flex h-4 w-4 shrink-0 items-center justify-center text-muted">
        {icon}
      </span>
      <span className="font-medium">{label}</span>
      {meta && <span className="text-muted">{meta}</span>}
    </Link>
  );
}

function MissingMention({ raw }: { raw: string }) {
  return <code>{raw}</code>;
}

// ── Block card ─────────────────────────────────────────────────────

/**
 * Shared card skeleton. Multi-line embed for spotlight / featured
 * mentions. Renders as a `not-prose`-style block that breaks the
 * paragraph flow — the entire card is clickable. The optional
 * `stats` row sits between the description and the CTA so the
 * data-led anchors common to this blog (posting count, company
 * count, posting frequency) render with consistent spacing.
 */
function MentionCard({
  href,
  icon,
  eyebrow,
  title,
  meta,
  description,
  stats,
}: {
  href: string;
  icon: React.ReactNode;
  eyebrow?: string;
  title: string;
  meta?: string;
  description?: string;
  stats?: { label: string; value: string }[];
}) {
  // `mention` class consumed by `.blog-post a.mention` in
  // `app/globals.css` — undoes the post-body link underline + info-color
  // inheritance so the card's internal color/typography controls win.
  return (
    <Link
      href={href}
      className="mention my-6 flex w-full flex-col gap-3 rounded-md border border-border-soft bg-border-soft/30 p-5 transition-colors hover:bg-border-soft"
    >
      {eyebrow && (
        <div className="flex items-center gap-1.5 text-xs uppercase tracking-wide text-muted">
          <span className="inline-flex shrink-0 items-center justify-center">
            {icon}
          </span>
          <span>{eyebrow}</span>
        </div>
      )}
      <div className="flex flex-col">
        <span className="text-base font-semibold leading-tight">
          {title}
        </span>
        {meta && (
          <span className="mt-1 text-sm text-muted">{meta}</span>
        )}
      </div>

      {description && (
        <p className="text-sm text-muted leading-relaxed">{description}</p>
      )}

      {stats && stats.length > 0 && (
        <ul className="flex flex-wrap gap-x-6 gap-y-2 text-sm">
          {stats.map(({ label, value }) => (
            <li key={label} className="flex flex-col">
              <span className="font-semibold">{value}</span>
              <span className="text-xs uppercase tracking-wide text-muted">
                {label}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Link>
  );
}

// ── Data resolvers ─────────────────────────────────────────────────

// React-cache wrappers so the same entity referenced multiple times
// in one post is fetched once per render. Falls back to a fresh fetch
// on the next ISR regen (revalidate=86400 on the post page).
const cachedCompany = cache(getCompanyBySlug);
const cachedWatchlist = cache(getPublicWatchlistByUserAndSlug);

function companyIcon(
  icon: string | null | undefined,
  size: 16 | 24,
): React.ReactNode {
  // `Building2` is the codebase's canonical company-fallback icon
  // (see apps/web/docs/icons.md). Only swap to <Image> when the
  // company has a real icon URL.
  if (icon && icon.startsWith("http")) {
    return (
      <Image
        src={icon}
        alt=""
        width={size}
        height={size}
        sizes={`${size}px`}
        className="rounded"
        style={{ width: size, height: size }}
      />
    );
  }
  return <Building2 size={size === 16 ? 14 : 20} aria-hidden="true" />;
}

// ── Inline mentions ────────────────────────────────────────────────

export async function CompanyMention({
  slug,
  locale,
}: {
  slug: string;
  locale: string;
}) {
  const company = await cachedCompany(slug, locale);
  if (!company) return <MissingMention raw={`{Company ${slug}}`} />;
  return (
    <MentionPill
      href={`/${locale}/company/${company.slug}`}
      icon={companyIcon(company.icon, 16)}
      label={company.name}
    />
  );
}

export async function WatchlistMention({
  owner,
  slug,
  locale,
}: {
  owner: string;
  slug: string;
  locale: string;
}) {
  const detail = await cachedWatchlist(owner, slug);
  if (!detail) return <MissingMention raw={`{Watchlist ${owner}/${slug}}`} />;
  const ownerLabel =
    detail.owner.displayUsername ?? detail.owner.username ?? detail.owner.name;
  return (
    <MentionPill
      href={`/${locale}/${owner}/${slug}`}
      // `Eye` matches the watchlists nav slot in AppHeader (see icons.md).
      icon={<Eye size={14} aria-hidden="true" />}
      label={detail.title}
      meta={`@${ownerLabel}`}
    />
  );
}

// ── Block-card mentions ────────────────────────────────────────────

function formatEmployeeRange(range: number | null | undefined): string | null {
  if (range == null) return null;
  // Mirror the server-side label range used elsewhere — keep terse
  // for the card meta line.
  const bounds: Record<number, string> = {
    1: "1–10",
    2: "11–50",
    3: "51–200",
    4: "201–500",
    5: "501–1k",
    6: "1k–5k",
    7: "5k–10k",
    8: "10k+",
  };
  return bounds[range] ?? null;
}

export async function CompanyCard({
  slug,
  locale,
}: {
  slug: string;
  locale: string;
}) {
  const company = await cachedCompany(slug, locale);
  if (!company) return <MissingMention raw={`{CompanyCard ${slug}}`} />;

  const { i18n } = await loadLocaleCatalog(locale);

  const employees = formatEmployeeRange(company.employeeCountRange);
  const metaParts: string[] = [];
  if (company.industryName) metaParts.push(company.industryName);
  if (employees) {
    metaParts.push(
      i18n._({
        id: "blog.mention.company.employeesCount",
        comment: "Meta line on a CompanyCard mention card — '{range} employees' (range like '11–50' or '5k–10k')",
        message: "{range} employees",
        values: { range: employees },
      }),
    );
  }
  if (company.foundedYear) {
    metaParts.push(
      i18n._({
        id: "blog.mention.company.foundedYear",
        comment: "Meta line on a CompanyCard mention card — 'founded {year}'",
        message: "founded {year}",
        values: { year: company.foundedYear },
      }),
    );
  }

  const stats: { label: string; value: string }[] = [];
  if (typeof company.activeJobCount === "number") {
    stats.push({
      label: i18n._({
        id: "blog.mention.company.activePostingsLabel",
        comment: "Stat label on a CompanyCard mention card — pluralized 'active posting' / 'active postings'. {count} is the integer count.",
        message: "{count, plural, one {active posting} other {active postings}}",
        values: { count: company.activeJobCount },
      }),
      value: String(company.activeJobCount),
    });
  }

  const eyebrow = i18n._({
    id: "blog.mention.company.eyebrow",
    comment: "Eyebrow label on a CompanyCard mention card (the small uppercase tag above the company name)",
    message: "Company",
  });

  return (
    <MentionCard
      href={`/${locale}/company/${company.slug}`}
      eyebrow={eyebrow}
      icon={companyIcon(company.icon, 16)}
      title={company.name}
      meta={metaParts.length > 0 ? metaParts.join(" · ") : undefined}
      description={company.description ?? undefined}
      stats={stats.length > 0 ? stats : undefined}
    />
  );
}

export async function WatchlistCard({
  owner,
  slug,
  locale,
}: {
  owner: string;
  slug: string;
  locale: string;
}) {
  const detail = await cachedWatchlist(owner, slug);
  if (!detail) return <MissingMention raw={`{WatchlistCard ${owner}/${slug}}`} />;

  const { i18n } = await loadLocaleCatalog(locale);

  // Posting counts mirror the in-app "N active · M in the last year"
  // stats row so the card embed reads consistently with the watchlist
  // detail view. ISR-safe (session-free Typesense queries).
  const counts = await getWatchlistPostingDisplayCounts(detail);

  const ownerLabel =
    detail.owner.displayUsername ?? detail.owner.username ?? detail.owner.name;

  const stats: { label: string; value: string }[] = [];
  // Skip the company-count stat for `anyCompany` watchlists — the
  // numbers in `detail.companies` are leftover noise, not what the
  // watchlist tracks (same caveat as in PR #2833).
  if (!detail.filters.anyCompany) {
    stats.push({
      label: i18n._({
        id: "blog.mention.watchlist.companiesLabel",
        comment: "Stat label on a WatchlistCard mention card — pluralized 'company' / 'companies'. {count} is the integer count.",
        message: "{count, plural, one {company} other {companies}}",
        values: { count: detail.companies.length },
      }),
      value: String(detail.companies.length),
    });
  }
  if (counts.activeJobs > 0) {
    stats.push({
      label: i18n._({
        id: "blog.mention.watchlist.activeJobsLabel",
        comment: "Stat label on a WatchlistCard mention card — pluralized 'active job' / 'active jobs'. {count} is the integer count.",
        message: "{count, plural, one {active job} other {active jobs}}",
        values: { count: counts.activeJobs },
      }),
      value: String(counts.activeJobs),
    });
  }
  if (counts.yearJobs > 0) {
    stats.push({
      label: i18n._({
        id: "blog.mention.watchlist.yearJobsLabel",
        comment: "Stat label on a WatchlistCard mention card — 'in the past year' (year-to-date jobs counted by the watchlist filters)",
        message: "in the past year",
      }),
      value: String(counts.yearJobs),
    });
  }

  const eyebrow = i18n._({
    id: "blog.mention.watchlist.eyebrow",
    comment: "Eyebrow label on a WatchlistCard mention card (the small uppercase tag above the watchlist title)",
    message: "Watchlist",
  });

  return (
    <MentionCard
      href={`/${locale}/${owner}/${slug}`}
      eyebrow={eyebrow}
      icon={<Eye size={14} aria-hidden="true" />}
      title={detail.title}
      meta={`@${ownerLabel}`}
      description={detail.description ?? undefined}
      stats={stats.length > 0 ? stats : undefined}
    />
  );
}

// `Briefcase` is intentionally imported (#2828) so the future
// `<JobCard id="..." />` mention follows the convention without an
// extra import shuffle. Remove this hint when JobCard lands.
export const __FUTURE_JOB_ICON_HINT = Briefcase;

/**
 * Build the `components` map passed to `compileMDX` from the post page.
 * Each entry is a partially-applied async component — locale is bound
 * at the call site so MDX authors don't need to pass it through.
 *
 * To register a new mention type:
 *
 *   1. Implement the inline + card pair following the patterns above.
 *   2. Add both variants to the returned object below with TitleCase
 *      MDX tags (`<Type ... />` for inline, `<TypeCard ... />` for the
 *      card).
 *   3. Document the new type in `apps/web/src/content/blog/README.md`
 *      and any new icons in `apps/web/docs/icons.md`.
 *
 * Future candidates flagged in #2828: `<Job id="..." />` /
 * `<JobCard id="..." />` (use `Briefcase`), `<Occupation slug="..." />`
 * (use `Briefcase`), `<Location slug="..." />` (use `MapPin`),
 * `<Author slug="..." />` once we have multiple post authors.
 */
export function buildMdxComponents(locale: string): Record<string, React.ComponentType<Record<string, string>>> {
  return {
    Company: (props) => <CompanyMention slug={props.slug} locale={locale} />,
    CompanyCard: (props) => <CompanyCard slug={props.slug} locale={locale} />,
    Watchlist: (props) => (
      <WatchlistMention owner={props.owner} slug={props.slug} locale={locale} />
    ),
    WatchlistCard: (props) => (
      <WatchlistCard owner={props.owner} slug={props.slug} locale={locale} />
    ),
  };
}
