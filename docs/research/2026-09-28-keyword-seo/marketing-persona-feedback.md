# Marketing page reactions and revisions

Date: 2026-09-28. Three independent agents reviewed rendered desktop/mobile
screenshots and the page copy. The multilingual reviewer also inspected the
German and French catalogs and all four guide translations. These are modeled
persona reactions, not interviews with real customers or conversion evidence.

## Reactions

### Selective engineer, employed in Switzerland

The company-first proposition and restrained visual design felt relevant. The
likely first action was checking whether a shortlist of Swiss employers was
covered; the tracker was a secondary benefit. The reviewer wanted the weekly
email promise in the hero, a readable real watchlist example, and less defensive
copy beside the digest illustration. A technical digest example was suggested.

### Active quality/operations job seeker, frequent mobile use

The free tracker, simple statuses, and quality-specialist example felt useful.
The likely next action was searching for a relevant role before registering.
The reviewer found the desktop screenshot too small on a phone and wanted to
see the product sooner. “Pipeline,” “funnel,” and “arbitrary external jobs” made
simple behavior sound technical. Existing applications from other sites were
an important scope boundary, and weekly cadence needed to appear earlier.

### German-speaking career changer, also reads French

The tracker and interview notes were immediately understandable. The alerts
page assumed a shortlist the persona did not yet have. The reviewer was unsure
whether a watchlist meant saved jobs or a saved search, and how it led to email.
They wanted the role/location starting point beside the first CTA, an explicit
watchlist definition, full-size screenshots, and links to the guide's three
methods. German “Jobbenachrichtigungen für Unternehmen” sounded employer-facing.

## Changes made

| Feedback | Revision |
| --- | --- |
| Email value appeared too late | Alerts hero now states one weekly email with new matches. Free allowance and opt-in cadence sit beside the CTA. |
| Watchlist terminology was unclear | Define it as a saved job search in the hero; the setup step says to save a search as a watchlist. |
| No target employers yet | Add a role/location starting-point link beside the first alerts CTA. |
| Product proof arrived late | Move the real product screenshot ahead of the setup steps on both landing pages. |
| Screenshots too small on mobile | Make each screenshot and its localized caption open the full-resolution image in the browser, where it can be enlarged. |
| Tracker sounded technical | Replace pipeline/funnel language with saved jobs, application progress, and interview notes. |
| Scope paragraph felt defensive | Lead the alerts section with notification control; keep the tracker limitation concise and explicit. |
| Guide required too much scrolling | Add links to employer alerts, LinkedIn, and Job Seek instructions, and remove internal “notification policy” wording. |
| German related link was ambiguous | Use “Job-Alerts deiner Wunschunternehmen.” |

All landing-page changes are translated into English, German, French, and
Italian. Existing FAQ answers retain relevant limitations and weekly cadence.
Email delivery is already live; there is no rollout notice.

## Choices retained

- Keep the nontechnical quality-specialist example. It helps the broader audience
  requested for this research; replacing every example with engineering would
  narrow the message again.
- Keep genuine product screenshots. We did not fabricate a mobile interface or
  substitute fictional job data into a screenshot. Opening the existing image
  solves enlargement without adding a new lightbox component.
- Keep the site's existing `PublicDomainArt`, including its overlay credits.
  Both newly sourced works use the repository's existing preprocessing pipeline.
- Keep the tracker scoped to jobs saved on Job Seek. The reviews do not justify
  promising imports, automatic applications, or reminder functionality.

The next evidence should come from real visitors: relevant searches, watchlist
creation, email opt-in, and return visits. No conversion improvement is claimed
from these modeled reactions. New pages remain subject to the user's visual
review before publication.

## Follow-up review and validation

The multilingual reviewer revisited the updated screenshot and copy and found
no material remaining issues for that persona. The weekly promise, saved-search
definition, role/location alternative, and simpler terminology addressed the
original concerns. This remains a modeled reaction, not customer validation.

Implementation checks passed:

- Production build and TypeScript; ESLint on changed TypeScript components.
- All four translation catalogs complete and in sync.
- 41 focused existing tests and one new regression test for article jump links.
- 24 route/layout checks: three new pages, four locales, desktop and mobile.
- 24 additional 320px checks across four locales and both themes: no horizontal
  overflow, artwork overlays visible, and product images loaded.
- Canonical URLs, five hreflang alternatives, sitemap entries, and all 16 visible
  FAQ answers matching JSON-LD; no browser runtime errors in the route checks.
- All 12 guide jump links, a localized search CTA, and full-size screenshot
  navigation verified in the browser.

The jump-link check exposed a real PPR issue: hidden article copies retain the
same section IDs. The guide's links now resolve their target within the visible
article, with keyboard focus and a regression test. Blog prose styles also no
longer override the standard white artwork-credit overlay on these images.

## Visual review follow-up

The tracker now uses Holbein's *The Emperor*, selected for denser linework that
matches the site's existing illustrations. The lighter writing illustration was
removed. The existing artwork component and overlay credits are unchanged.
The production build and desktop/mobile captures in both themes pass.

Open before publication: refresh the watchlist product screenshots. The globe
in the old capture is stale; the current application's sharing controls already
use `Share2`. The original public watchlist URL is no longer available, and the
configured capture-account login is rejected. Fresh authenticated captures are
pending working capture access; do not alter the screenshot to fake a current UI.
