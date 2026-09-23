import { createHash } from "node:crypto";
import { type NextRequest, NextResponse } from "next/server";
import { match } from "@formatjs/intl-localematcher";
import Negotiator from "negotiator";
import { defaultLocale, locales, isLocale } from "@/lib/i18n";
import { isPlausiblePublicWatchlistPath } from "@/lib/public-watchlist-path";
import { isReservedUsername } from "@/lib/username";
import { logExternalError } from "@/lib/safe-external-error";
import { auth } from "@/lib/auth";
import {
  hasPublicCompanyRoute,
  hasWatchlistRouteForViewer,
} from "@/lib/services/public-resource-status";
import { staticMissingResourceDocument } from "@/lib/missing-resource-recovery";
import {
  getClientIp,
  publicReadBurstLimiter,
  publicReadSustainedLimiter,
} from "@/lib/rate-limit";

const COOKIE_NAME = "NEXT_LOCALE";
const LOGGED_IN_HINT_COOKIE = "logged_in";
const COMPANY_REQUEST_PATH = /^\/(en|de|fr|it)\/companies\/request$/;
const LOCALIZED_EXPLORE_PATH = /^\/(?:en|de|fr|it)\/explore$/;
const LOCALIZED_WATCHLIST_INDEX_PATH =
  /^\/(?:en|de|fr|it)\/watchlists$/;
const LOCALIZED_WATCHLIST_DETAIL_PATH =
  /^\/(?:en|de|fr|it)\/watchlists\/[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const OBSOLETE_EXPLORE_ACTION_IDS = new Set([
  "7ffac6a500b0410a78dcf5f6a75ea0d2253b635222",
]);
const LOCALIZED_COMPANY_PATH = /^\/(en|de|fr|it)\/company\/([^/]+)$/;
const LOCALIZED_WATCHLIST_PATH =
  /^\/(en|de|fr|it)\/([^/]+)\/([^/]+)$/;
const LOCALIZED_SCANNER_PATH =
  /^\/(?:en|de|fr|it)\/(?:(?:adminer|cgi-bin|phpmyadmin|wp-admin|wp-content|wp-includes|wp-json|xmlrpc|\.env|\.git)(?:\/|$)|[^/]+\/(?:\.env|\.git)(?:\/|$))/i;

type PublicReadActionSurface =
  | "explore"
  | "company"
  | "watchlists";

function publicReadActionSurface(
  request: NextRequest,
): PublicReadActionSurface | null {
  if (request.method !== "POST" || !request.headers.has("next-action")) {
    return null;
  }

  const pathname = request.nextUrl.pathname;
  if (LOCALIZED_EXPLORE_PATH.test(pathname)) return "explore";
  if (LOCALIZED_WATCHLIST_INDEX_PATH.test(pathname)) return "watchlists";
  if (LOCALIZED_WATCHLIST_DETAIL_PATH.test(pathname)) return "watchlists";
  if (LOCALIZED_COMPANY_PATH.test(pathname)) return "company";

  return null;
}

function clientReference(ip: string): string {
  return createHash("sha256")
    .update(`public-read:${ip}`)
    .digest("hex")
    .slice(0, 12);
}

async function publicReadRateLimitResponse(
  request: NextRequest,
  surface: PublicReadActionSurface,
): Promise<NextResponse | null> {
  const ip = getClientIp(request.headers);
  const checks = await Promise.allSettled([
    publicReadBurstLimiter.limit(ip),
    publicReadSustainedLimiter.limit(ip),
  ]);
  const failures = checks.filter(
    (result): result is PromiseRejectedResult => result.status === "rejected",
  );
  if (failures.length > 0) {
    // Never log the raw IP or the Upstash error object. Transport errors can
    // contain credential-bearing request configuration.
    console.error(JSON.stringify({
      event: "public_read.rate_limit_unavailable",
      surface,
      client_ref: clientReference(ip),
      failed_checks: failures.length,
    }));
  }

  const denied = checks.flatMap((result) =>
    result.status === "fulfilled" && !result.value.success
      ? [result.value]
      : [],
  );
  if (denied.length === 0) return null;

  const reset = Math.max(...denied.map((result) => result.reset));
  const retryAfter = Math.max(1, Math.ceil((reset - Date.now()) / 1000));
  console.warn(JSON.stringify({
    event: "public_read.rate_limited",
    surface,
    client_ref: clientReference(ip),
    retry_after_seconds: retryAfter,
  }));

  return new NextResponse("Too Many Requests", {
    status: 429,
    headers: {
      "Cache-Control": "private, no-store",
      "Content-Type": "text/plain; charset=utf-8",
      "Retry-After": String(retryAfter),
      "X-Content-Type-Options": "nosniff",
      "X-Robots-Tag": "noindex",
    },
  });
}

function getLocale(request: NextRequest): string {
  // 1. Explicit cookie from a previous locale switch
  const cookieLocale = request.cookies.get(COOKIE_NAME)?.value;
  if (cookieLocale && isLocale(cookieLocale)) return cookieLocale;

  // 2. Accept-Language negotiation
  const headers: Record<string, string> = {};
  request.headers.forEach((value, key) => {
    headers[key] = value;
  });
  const languages = new Negotiator({ headers })
    .languages()
    .filter((l) => l !== "*");
  return match(languages, locales as unknown as string[], defaultLocale);
}

function isDocumentRequest(request: NextRequest): boolean {
  if (request.headers.get("rsc") === "1" || request.headers.has("next-action")) {
    return false;
  }
  if (request.method === "HEAD") return true;
  if (request.method !== "GET") return false;
  const accept = request.headers.get("accept");
  return !accept || accept === "*/*" || accept.includes("text/html");
}

function hasSessionCookie(request: NextRequest): boolean {
  return (
    request.cookies.has("__Secure-better-auth.session_token") ||
    request.cookies.has("better-auth.session_token")
  );
}

async function authenticatedUserId(request: NextRequest): Promise<string | null> {
  if (!hasSessionCookie(request)) return null;
  const session = await auth.api.getSession({ headers: request.headers });
  return session?.user?.id ?? null;
}

async function missingResourceResponse(
  request: NextRequest,
  kind: "company" | "watchlist",
  lang: string,
  slug?: string,
): Promise<NextResponse> {
  const locale = isLocale(lang) ? lang : defaultLocale;
  const responseHeaders = new Headers({
    "Content-Language": locale,
    "Content-Type": "text/html; charset=utf-8",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
  });
  // A newly-created company or a watchlist privacy toggle must not be hidden
  // behind a cached 404. The lookup itself is bounded by a short shared cache.
  responseHeaders.set("Cache-Control", "private, no-store");
  responseHeaders.set("X-Robots-Tag", "noindex, follow");
  responseHeaders.set(
    "Content-Security-Policy",
    "default-src 'none'; script-src 'none'; style-src 'unsafe-inline'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
  );
  const body = request.method === "HEAD"
    ? null
    : staticMissingResourceDocument(kind, locale, slug);
  return new NextResponse(body, {
    status: 404,
    headers: responseHeaders,
  });
}

async function resolveLocalizedResourceRequest(
  request: NextRequest,
  companyMatch: RegExpMatchArray | null,
  watchlistMatch: RegExpMatchArray | null,
): Promise<NextResponse> {
  if (companyMatch) {
    const [, lang, slug] = companyMatch;
    try {
      return (await hasPublicCompanyRoute(slug))
        ? NextResponse.next()
        : await missingResourceResponse(request, "company", lang, slug);
    } catch (err) {
      // Fail open: an upstream outage must not turn every company candidate
      // into a definitive 404. The page keeps its existing noindex fallback.
      logExternalError(
        "error",
        { service: "typesense", operation: "company_route_status" },
        err,
      );
      return NextResponse.next();
    }
  }

  if (watchlistMatch) {
    const [, lang, userSlug, watchlistSlug] = watchlistMatch;
    if (isReservedUsername(userSlug.toLowerCase())) {
      return NextResponse.next();
    }
    if (!isPlausiblePublicWatchlistPath(userSlug, watchlistSlug)) {
      return missingResourceResponse(request, "watchlist", lang);
    }

    try {
      const viewerUserId = await authenticatedUserId(request);
      const routeExists = viewerUserId
        ? await hasWatchlistRouteForViewer(
            userSlug,
            watchlistSlug,
            viewerUserId,
          )
        : false;
      return routeExists
        ? NextResponse.next()
        : await missingResourceResponse(request, "watchlist", lang);
    } catch (err) {
      logExternalError(
        "error",
        { service: "database", operation: "watchlist_route_status" },
        err,
      );
      return NextResponse.next();
    }
  }

  return NextResponse.next();
}

export async function proxy(request: NextRequest): Promise<NextResponse> {
  // A sustained client is replaying this action ID after its deployment was
  // retired. Next rejects it as unknown, but only after invoking the full page
  // Function. Keep this exact deployment-skew signature at the lightweight
  // proxy boundary as defense in depth behind the zero-Function WAF rule.
  // Current and future action IDs intentionally continue straight to Next.
  if (
    request.method === "POST" &&
    LOCALIZED_EXPLORE_PATH.test(request.nextUrl.pathname) &&
    OBSOLETE_EXPLORE_ACTION_IDS.has(
      request.headers.get("next-action") ?? "",
    )
  ) {
    return new NextResponse("Not Found", {
      status: 404,
      headers: {
        "Cache-Control": "private, no-store",
        "Content-Type": "text/plain; charset=utf-8",
        "X-Content-Type-Options": "nosniff",
        "X-Robots-Tag": "noindex",
      },
    });
  }

  const legacyWatchlistAction = request.nextUrl.pathname.match(
    LOCALIZED_WATCHLIST_PATH,
  );
  if (
    request.method === "POST" &&
    request.headers.has("next-action") &&
    legacyWatchlistAction &&
    !isReservedUsername(legacyWatchlistAction[2].toLowerCase())
  ) {
    return missingResourceResponse(
      request,
      "watchlist",
      legacyWatchlistAction[1],
    );
  }

  const actionSurface = publicReadActionSurface(request);
  if (actionSurface) {
    const rateLimited = await publicReadRateLimitResponse(
      request,
      actionSurface,
    );
    if (rateLimited) return rateLimited;
    return NextResponse.next();
  }

  if (
    LOCALIZED_WATCHLIST_DETAIL_PATH.test(request.nextUrl.pathname) &&
    (request.method === "GET" || request.method === "HEAD")
  ) {
    // The UUID page can serve either its owner or a shared-link viewer, but
    // neither the readable hint cookie nor a presented session cookie proves
    // identity at this boundary. Apply the same permissive IP read budget to
    // both and leave authorization to the page's server-side data loader.
    const rateLimited = await publicReadRateLimitResponse(
      request,
      "watchlists",
    );
    if (rateLimited) return rateLimited;
    return NextResponse.next();
  }

  // Stop common exploit-probe shapes at the network boundary. Without this,
  // Cache Components can stream the public-watchlist PPR shell with HTTP 200
  // before the route-level notFound() guard runs, consuming a Fluid function
  // invocation and making obvious probes look like valid pages.
  if (LOCALIZED_SCANNER_PATH.test(request.nextUrl.pathname)) {
    return new NextResponse("Not Found", {
      status: 404,
      headers: { "Cache-Control": "public, max-age=86400, s-maxage=86400" },
    });
  }

  // The Explore RSC shell does not read search params: filters are restored
  // from the browser URL by ExploreContent after hydration. Normalize only
  // full HTML document requests to the queryless internal URL so long-tail
  // filter links share one PPR shell per locale instead of generating a new
  // Fluid invocation for every query-string permutation.
  //
  // RSC navigations and Server Actions intentionally bypass this rewrite.
  // Their framework query/header state is part of the request protocol and
  // must reach Next unchanged.
  if (
    LOCALIZED_EXPLORE_PATH.test(request.nextUrl.pathname) &&
    request.nextUrl.search &&
    (request.method === "GET" || request.method === "HEAD") &&
    request.headers.get("accept")?.includes("text/html") &&
    request.headers.get("rsc") !== "1" &&
    !request.headers.has("next-action")
  ) {
    const shellUrl = request.nextUrl.clone();
    shellUrl.search = "";
    return NextResponse.rewrite(shellUrl);
  }

  if (LOCALIZED_EXPLORE_PATH.test(request.nextUrl.pathname)) {
    return NextResponse.next();
  }

  // The public IndexNow proof filename is derived from a secret at runtime and
  // therefore cannot be listed in the static matcher below. Let that one
  // configured dotted root path continue to the rewrite in next.config.ts.
  const indexNowKey = process.env.INDEXNOW_KEY;
  if (
    indexNowKey &&
    request.nextUrl.pathname === `/${indexNowKey}.txt`
  ) {
    return NextResponse.next();
  }

  const companyRequestMatch = request.nextUrl.pathname.match(
    COMPANY_REQUEST_PATH,
  );
  if (companyRequestMatch) {
    // Decide the anonymous continuation before the Cache Components app shell
    // can hydrate. Otherwise SalaryDisplayProvider starts getCurrencyRates(),
    // the page redirect redirects that Server Action response, and Next falls
    // back to a blank full-document navigation (#6043). The page still checks
    // the real httpOnly session for hinted visitors; this cookie is only the
    // same non-sensitive fast-path hint used by AppBootstrapProvider.
    if (request.cookies.has(LOGGED_IN_HINT_COOKIE)) {
      return NextResponse.next();
    }

    const returnPath = `${request.nextUrl.pathname}${request.nextUrl.search}`;
    const signInUrl = request.nextUrl.clone();
    signInUrl.pathname = `/${companyRequestMatch[1]}/sign-in`;
    signInUrl.search = "";
    signInUrl.searchParams.set("next", returnPath);
    return NextResponse.redirect(signInUrl);
  }

  const companyMatch = request.nextUrl.pathname.match(LOCALIZED_COMPANY_PATH);
  const watchlistMatch = request.nextUrl.pathname.match(LOCALIZED_WATCHLIST_PATH);
  if (isDocumentRequest(request) && (companyMatch || watchlistMatch)) {
    return resolveLocalizedResourceRequest(
      request,
      companyMatch,
      watchlistMatch,
    );
  }
  if (companyMatch || watchlistMatch) return NextResponse.next();

  const cookieLocale = request.cookies.get(COOKIE_NAME)?.value;
  const locale = getLocale(request);
  const url = request.nextUrl.clone();
  url.pathname = `/${locale}${request.nextUrl.pathname}`;
  const response = NextResponse.redirect(url);

  // Cache the redirect at Vercel's CDN when the chosen locale comes from
  // Accept-Language negotiation. Repeat requests with matching headers (most
  // bot/shared-link traffic on root URLs) then reuse the redirect without
  // re-invoking the proxy. We deliberately skip the cache when an
  // explicit NEXT_LOCALE cookie is set: that path varies per user and Vary:
  // Cookie would shard the cache by every session token. See issue #2642.
  if (!cookieLocale || !isLocale(cookieLocale)) {
    response.headers.set(
      "Cache-Control",
      "public, max-age=86400, s-maxage=86400",
    );
    response.headers.set("Vary", "Accept-Language");
  }

  return response;
}

export const config = {
  // Only match paths that do NOT start with a locale prefix, a known static
  // asset/discovery route, an API route, or Next.js internals. Unknown dotted
  // root paths must still pass through the proxy: otherwise `[lang]` treats
  // the filename as an invalid locale and the dynamic root layout turns its
  // intended 404 into a 500. Redirecting to `/<locale>/<path>` reaches the
  // localized 404 surface correctly.
  //
  // `opengraph-image*` is excluded so previously shared root OG URLs reach
  // the compatibility redirects in next.config.ts instead of first acquiring
  // a locale prefix. Current metadata points directly at immutable R2 assets.
  matcher: [
    "/((?!_next|api|mcp|og|flags|fonts|publicdomain|screenshots|\\.well-known|favicon\\.ico$|favicon-16x16\\.png$|favicon-32x32\\.png$|apple-touch-icon\\.png$|apple-touch-icon-[^/]+\\.png$|android-chrome-192x192\\.png$|android-chrome-512x512\\.png$|site\\.webmanifest$|BingSiteAuth\\.xml$|js_[^/]+\\.svg$|js_missing_screenshot_black\\.png$|js_missing_screenshot_white\\.png$|logo-dark\\.svg$|logo-light\\.svg$|opengraph-image|indexnow-key\\.txt$|llms\\.txt$|openapi\\.json$|openapi\\.yaml$|robots\\.txt$|sitemap\\.xml$|en|de|fr|it).*)",
    "/:lang(en|de|fr|it)/companies/request",
    // BEGIN GENERATED COMPANY MISS MATCHERS
    // Generated from apps/crawler/data/companies.csv. Canonical company
    // documents bypass Proxy so a warm page-cache hit consumes no Fluid
    // middleware compute. Only absent/unsafe slug candidates reach the
    // Typesense-backed real-404 guard below. Run `pnpm proxy-matchers:update`.
    "/:lang(en|de|fr|it)/company/:slug((?!(?:etalytics-gmbh|eth-zurich|ethereum-foundation|ethernovia-inc|ethon-ai|ethos-life|ethyca|etisalat|etoro|etsy|in-the-pocket|in3|inc-innovation-center-gmbh|inceptive|incepto-medical|incharge-energy|incident|incident-iq|includedhealth|incode|incyte|indeed|indent|index-ventures|indiana-university-health|indie-campers|inditex-tech|indosuez-wealth-management|inductive-bio|industrial-electric-manufacturing|industrious-labs|ineffable-intelligence|inetum|infarm|infineon|infinidat|infinite-machine|infinite-orbits|infinitus-systems|infinity-constellation|infisical|inflection-ai|influxdata|infosys|infuse|ing|ingenieurb-ro-dr-petry-partner-mbb|inhome-therapy|inizio|inizio-ignite|inizio-ignite-putnam|inizio-ignite-research-partnership|inizio-ignite-stem|inizio-ignite-vynamic|inizio-medical|inkhouse|inkind|inmobi|innatera|inngest|innok-robotics|innosuisse|innotec-gmbh|innovafeed|innoviz-technologies|inova|insel-gruppe|insify|insightsoftware|insitro|insomnia-cookies|insomniac-games|inspira-education|inspiration-commerce-group|inspire-medical-systems-inc|inspiren|instabase|instacart|instawork|instead|institut-f-r-rehabilitation|instories|instride|instructure|instrumental|insurello|insurely|insurify|insurtech-insights|integrafec|integral-services-gmbh|integrated-resources|integrated-specialty-coverages-llc|integrity-rehab-group|intel|intelligent-energy|intellum-inc|inter-con-security|inter-parliamentary-union|interactive-brokers|interactive-brokers-external|intercom|interface-ai|intermex-wire-transfer|intermountain-health|internal-job-board|international-basketball-federation|international-boxing-association|international-canoe-federation|international-commission-of-jurists|international-electrotechnical-commission|international-golf-federation|international-gymnastics-federation|international-hockey-federation|international-ice-hockey-federation|international-labour-organization|international-olympic-committee|international-organization-for-migration|international-skating-union|international-table-tennis-federation|international-testing-agency|interpeace|interplay|interstellar-lab|intertek|interview-engineering|interview-kickstart|interworks|intesa-sanpaolo|intradiem|intrinsic|intro|intuit|intuitive|intus|inuru|inversion|invert|invesco|investors-community-bank|invgate|invisible-technologies|invited-clubs|invivyd|inworld-ai|stability-ai|stable|stack-av|stack-overflow|stackadapt|stackblitz|stackgini-gmbh|stackhawk|stackline|stacks|stadler-rail|stahlwerk-annah-tte-max-aicher-gmbh-co-kg|stainless|stam-holding-gmbh|stambaugh-ness|standard-nuclear|standardfleet|stanley-1913|stanley-black-decker|stantec|staples-canada|starbridge|starbucks|starburst|starcloud|stark|starling-bank|starrez|start-campus|startree|startup-team|state-bank-of-india|state-of-florida|state-of-north-carolina|state-street|statsig|staubli|stayai|steady-energy|stealth-start-up-mobility-berlin|stedi|steer|stegra|stela-laxhuber|stellantis|stellar|sterlington-pllc|stitch-fix|stmicroelectronics|stockx|stoik|stoiximan|stone-linkedin|store-space-self-storage|storiogroup|story-cannabis|str|strabag|straight-arrow-news|strand-therapeutics|strata-decision-technology|strata-information-group|strategic-hr-client-job-openings|strategy-and|straumann|strava|stream|striim-inc|strike|stripe|strive-health|striveworks|stronghold|stronghold-investment-management|stryker|stubhub|studapart|study-com-c|studyflash|stuut-technologies|stytch)(?:/|$))(?:in[^/]*|st[^/]*|et[^/]*))",
    "/:lang(en|de|fr|it)/company/:slug((?!(?:alacris|alamar-biosciences|alan|alarm-com|alaska-airlines|albany-med|albert-cz|albert-mackenzie-llp|albertsons-companies|alchemy|alcon|aldersly|aldi-hofer|aldi-suisse|alector|aledade|alembic|alentis-therapeutics|aleph|aleph-alpha|alertmedia|alexander-shunnarah-trial-attorneys|alexandra-lozano-immigration-law-pllc|algo1|algolia|algorized|alibaba|alice-and-bob|alight|alika-personal-gmbh|alivedx|alixpartners|all-space|all-ways-caring-homecare|allarahealth|allbirds|allegro|allen-control-systems|alliance|alliance-defending-freedom|allianz|allianz-suisse|allica-bank|allied-universal|allium|alloheim|alloy|alloy-ai|alloyenterprises|allps|allspice|alltrails|alluxio|alma|alnylam|alo|alpaca|alpenlabs|alpha|alpha-financial-markets-consulting|alpha-fmc-insurance-consulting|alphabe-insight|alphagrep-securities|alphalion|alphasense|alphasense-india|alpian|alpine-investors|alpiq|alstom|alt|alta-ares|altana|alten|alten-technology-usa|altilium-metals|altium|altos-labs|altris|altscore|alu|alumni-ventures|alvean|alvotech|alvys|alx-africa|cube|cubesoftware|cubist|culinary-agency|culture-amp|cummins|curaleaf|curaponte|curi-capital|current|cursor|curtiss-wright|cushman-wakefield|cuspai|custom-surgical-gmbh|customcells|customer-io|n-ix|n1|n26|n8n|nabis|nabla|nachhilfeunterricht|nagarro|namespace|nanonets|nansen|nansen-ai|nanuq-gmbh|narvar|nas-company|nash|natera|national-design-build-services|national-life-insurance-company|national-science-center-kharkiv-institute-of-physics-and-technology|national-vision|nationwide|naturalmotion|nature-s-bakery|naughty-dog|nauticus-robotics|nava-pbc|navan|naver-vietnam|navier|navvis|nawah|nayya|nccgroup|near-space-labs|nearfield-instruments|nearform|nebius|nec-laboratories|neighbor|neighbors-bank|neko-health|neo4j|neon|neon-health|neon-pagamentos|neoris|neptune-ai|nerdwallet|nerdy|neros-technologies|nestai|nestle|netapp|netdocuments|netease-games|netflix|netjets|netlify|netskope|netwealth|neuehealth|neura-robotics|neuraflash-part-of-accenture|neural-concept|neural-frames|neuralink|new-era-technology|new-leaf-energy-inc|new-relic|new-york-city-economic-development-corporation|new-york-iso|newcleo|newco-communications|newcore|newfront|newlab-careers|newlimit|newsbreak|newsela|newstel-gmbh|newsweek|nex|nexgen-cloud|nexos|next|next-insurance|next-sense|nexthink|nexthire|nexthop-ai|nexwafe|neysa-networks-careers-page|nfon|ng-cash|ngrok-inc|nhl|nhoa|nhs|nice|nicoll-curtin|nielseniq|nift|nike|nikon|nimble-gravity|ninjatrader|nintendo|nira-energy|nissan|nitricity|nivoda|nmi|no-limits-academy-b-v|noise-labs|nokia|noma-security|nomad|nomagic|nomiso|nonprofit-finance-fund|noodles-company|nord-ostsee-automobile-se-co-kg|nord-ostsee-sparkasse|nord-security|nordason|nordstrom|norm-ai|normative|norsepower|norsk-titanium|nortal|north-america|northbeam|northern-trust|northmarq|northmill|northpoint-search-group|northrop-grumman|northside-hospital|northwell-health|northwest-pipe-fittings|northwestern-memorial-healthcare|northwood-space|norwegian-refugee-council|notabene|notability|notable|nothing|notion|noto|notraffic|nourish|nova-credit|nova-founders-capital|novartis|novatron-fusion-group|novel|novo|novo-nordisk|novocure|novogene|novu|noxon|noxtua|nozomi-networks|npr|nscale|nt-concepts|ntt-data|ntt-data-europe-latam-branch-in-usa-inc|nu-quantum|nubank|numa|numeral|numerix|numeus|nunu-ai|nuro|nutanix|nutrafol|nutrisense|nvidia|nvision-quantum|nviso|nw|nxp|nyobolt|nzz)(?:/|$))(?:n[^/]*|al[^/]*|cu[^/]*))",
    "/:lang(en|de|fr|it)/company/:slug((?!(?:o2-cz|oak|oak-foundation|oak-view-group|oaknorth|oboe|obsidian|obsidian-security|obviant|ocado|ocbc|ochsner-health|ocrolus-inc|octa-steuerberater-ralf-sommer|octave|octopus-energy|octopus-robots|octus|oddball|odeko|odle-sales|odoo|odyssey|odysseyhotelgroup|oerlikon|offerup|offerzen|office-hours|officespace-software|offshore-launch|ofi|ogilvy|ogilvy-australia|ogilvy-social-lab|ogt|oh-io|ohalo|ohpen|oklo|okta|okx|olam-agri|olam-group|olema-oncology|olipop|oliv-ai|oliver-agency|oliver-wyman|ollies-bargain-outlet|olly|olsson|olympus-property|omada-health|omnicom|omnicom-media-group-netherlands-omg|omnilex|omniscient|on-board-experiential|on-energy|on-running|onbe|onboard|onboardmeetings|one-acre-fund|one-acre-fund-kenya|oneapp|onebrief|onecrew|oneimaging|oneleet|onemci|onit-inc|only-stores-germany|onoshealth|onrobot|onsemi|ontic|ontra|onward-medical|onx|ooma|oos-management-corp|oosto|opal|open-cosmos|open-farm|openai|opendoor|openeye|opengov|openly|openrouter|opensea|opensesame|opentable|openwork|openworks|openx|ophelia|ophelos|oplabs|oportun|oppfi|optibus|optics11|optimal-care|optimal-dynamics|optiver|optiver-private-jobs|optiver-trading-academy|opto-investments|opus|oq-technology|oracle|orakl-oncology|orange-group|orange-quantum-systems|orasio|orb|orbem|orbit|orbital|orca|orca-computing|orca-security|orchard|orchard-therapeutics|orderchamp|orderly|oreilly-auto-parts|origis-services-utility-solar-o-m|oriola|orion-confectionery|orion-group|orion-innovation-naukri|orior|orkes|ororatech|oros-energy-europe|orqa|ortho-neo|osano|osapiens|osaro|osbra-einhaus-gmbh|oscar-health|oscilar|oshi-health|osl-retail-services|osmo|oso|ost|otter|otter-ai|otto-aerospace|ouihelp|our-group|outfit7|output|outreach|outrider|outschool|outset-medical|outtake|overland-ai|overstory|overtime|overwolf|ovhcloud|oviva|owkin|ox-security|oxford-instruments|oxford-ionics|oxford-nanopore-technologies|oxford-photovoltaics|oxford-quantum-circuits|oxio|oxyle|oyster|twaice|twelve|twenty|twentyfour-industries|twilio|twin-health|twist-bioscience|twitch|two-dots|two-sigma|two-six-technologies|u-blox|u-haul|u-s-bank|uber|uberall|ubs|ubs-digital-art-museum|ucb|udacity|udemy|udio|uefa|uipath|ukg|ultima-genomics|ultra|ultraviolet-cyber|uma-education|umass-memorial-health|unaids|unchained-labs|uncountable|underdog|understood-care|undp|unhcr|uni-president-china|unicef|unifi|unify|unifyid-acquired-by-prove|unilever|union|union-bancaire-privee|union-cycliste-internationale|uniqlo|unique|unisante|unispace|uniswap|unit|unit8|unitar|unite-us|united-airlines|united-nations-secretariat|united-rentals|unitedhealth-group|unitedmasters-translation|unitree-robotics|unity|universal|universal-music|universal-quantum|university-of-arkansas-system|university-of-basel|university-of-bern|university-of-fribourg|university-of-geneva|university-of-kansas-health-system|university-of-lausanne|university-of-miami|university-of-neuchatel|university-of-new-mexico|university-of-rochester|university-of-southern-california|univity|unlikelyai|unlimit|unlock-health|unops|unreal-snacks|unseenlabs|unsloth-ai|unto-labs|unwrap|unybrands|upbound|updater|upflow|upgrade|upkeep|uprite-construction|ups|upshop|upside|upstart|upstox|upstream-security|uptalent-io|upvest|upwork|ura|urban-outfitters|urban-sports-club|ursa-major|urschel-laboratories-inc|us-conec-ltd|us-foods|us-physical-therapy|usa-mechanical-energy-services|utonomy|uvcyber|uw-health|uzh)(?:/|$))(?:o[^/]*|u[^/]*|tw[^/]*))",
    "/:lang(en|de|fr|it)/company/:slug((?!(?:k-health|k-id|k-water|k2-space|kaedim|kagi|kaib-galldiks-partner-mbb|kaiko-ai|kairos-power|kaiser-permanente|kaizen-labs|kalepa|kalles-group|kalshi|kandou-ai|kapa-ai|kapitus|karat|karbon|kargo|karya|kasa|katapult-amsterdam-b-v|kaufland|kaufland-cz|kaumarie|kavak|kayak|kb-cz|kbr|kbra|kearney|keeper-security|keepit|kela-technologies|keller-postman|kelluu|kemaro|kentik|kepler-communications|kepler-group|kering|kernal-biologics-inc|kernel|kesko|kestra|ketryx|kettle|keycard-labs|keyloop|keystone|kfc|khan-academy|ki-insurance|kiavi|kickstarter-pbc|kiddom|kilocode|kimberly-clark|kimley-horn|kin|kinder-s|kindred|kineis|kinexon|kinexus-group|king|kion-group|kipu-quantum|kira|kit|kitchenpark|kittl|kiutra|kiwi|kizen|kjp-steuerberater-gbr|kla|klarna|klaus-meyer-gmbh-co-kg|klaviyo|klaviyo-campus|klearly|kluvo|knitwell-group|knoetic|knot|knowbe4|knowlix|known|knownwell|knupfer-lebensmittel-gmbh|koalafi|kobold-metals|kobold-metals-drc|kobold-metals-zambia|koch|koddi|kodiak|kodiak-solutions|kodland|kofi-annan-foundation|kognic|kojima-productions|koley-jessen-p-c-l-l-o|koleyjessen|koller-lode|kolmac-integrated-behavioral-health|kolmar-group|kolmar-korea|komax|kombo|komodo-health|kompuestos|kone|kong|konovo|konux|korean-air|korn-ferry|kotak-mahindra-bank|kp-group|kpmg|kr-ger-consulting-gmbh|kraft-heinz|krafton-americas|krafton-montr-al-studio|kraken|kraken-energy|krea|krea-ai|krg-technologies|kroger|kroll|kronans-apotek|kronos-research|kt|kubra-gmbh-industrie-und-kunststofftechnik|kuda|kudelski|kuehne-nagel|kulfi-collective|kunai|kura-oncology|kustomer|kyndryl|kyo|kyowa-kirin-north-america|pf-changs|pfasuiki-gmbh|pfizer|pflegehelden-franchise-gmbh|w7-managementberatung-gmbh|wabtec|wachtell|wake-county-public-school-system|walgreens|wall-street-prep|wallapop|walleye-capital-full-time|walmart|walrusfi|waltz-health|wandelbots|wandercraft|warburg-pincus-llc|warby-parker|wargaming|warner-music|warp|wasabi-technologies|waste-connections|waters-corporation|watershed|watershed-informatics|wavenet|wavestone|wawa|wayflyer|waymark|waymo|wayve|wayvia|wbs-legal|we-communications|we-love-x-gmbh|we-singapore|wealthfront|weaviate|webai|webchart|webflow|weedmaps|weee-inc|weekend|wefix-gmbh|weflow|weinstein-properties|weis-markets|weiss-asset-management|welbehealth|welcome-to-the-jungle|wellpower-all-jobs|wells-fargo|wellspan-health|weploy|weride|wesco|wesort-ai|westhafen-leipzig|westinghouse-electric-company|wettermark-keith|whalar-group|what3words|whataburger|whatnot|wheel|wheelhouse|whereby|white-circle|who-gives-a-crap|whoop|wikimedia-foundation|williams-sonoma|wilson-elser-business-legal-professionals|win-home-inspection|windmill|windranger|windsor-fashions|wingcopter|wingspan|wingtra|wipo|wipro|wireless-logic|wirescreen|wise|withclutch|withcoverage|withdaydream|within|witty-machines|wix|wiz-inc|wizard|wm|woflow|wolt|wolters-kluwer|wolve|wonder-studios|wonderflow|wonderful|wonderschool|woo-x|wood|woolpert|wordware-ai|workato|workboard|workday|workera-ai|workhelix|workleap-en|workos|workstream|workwize|world-anti-doping-agency|world-aquatics|world-council-of-churches|world-economic-forum|world-health-organization|world-labs|world-meteorological-organization|world-trade-organization|world-triathlon|world-vision-international|worldcoin|worldly|worldpay|worldquant|woven-care|wpp|wpromote|wrapbook|wrike|writer|wsc-sports|wsp|wtw|wunder|wunder-mobility|wustl|wvu-medicine|wynd-labs|wynd-labs-x-hiring)(?:/|$))(?:w[^/]*|k[^/]*|pf[^/]*))",
    "/:lang(en|de|fr|it)/company/:slug((?!(?:axa-switzerland|axelera-ai|axi|axicom|axiom|axiom-co|axios|axis-bank|axle|axle-careers|axmed|axon|axonius|axpo|axs|co-op|co-star|coalesce|coalition|coast|cobalt|cobalt-service-partners|cobase|cobblestone-energy-dubai-uae|cobot|cobre|coca-cola-hbc|cockroach-labs|codal|codat|code3|codepath|coder|coefficient|cofco-international|cofertility|cofra-holding|cognition|cognizant|cogstate|cohere|cohere-health|cohort|coinbase|coindcx|coinswitch|cointracker|colab-software|colgate-palmolive|colisee-france|collabera|collective|collibra|colonist|color|column|comand-ai|comarch|comet|comity|commerceiq|commercetools|common-thread-collective|commonroom|commonwealth-bank|commonwealth-medical-services|commonwealth-of-virginia|community-health-systems|commvault|comparis|compass-group|compass-pathways|compeer-financial|compliancy-group-llc|complyadvantage|compound|compunet-inc|comstock|comulate|concentric|concentrix|conductor|conductor-ai|conduit|conextivity|confluence|confluent|conga|connected-careers-page|connectwise|conocophillips|consensys|console|constant-contact|constellr|construction-resources|constructor-tech|consumer-edge|contact-government-services|contentful|contentsquare|contentstack|context-labs|contextual-ai|continental|continue|convene|convera|conversion|convex-dev|convious|cook-systems|cookunity|coop|coop-sverige|cooper-university-hospital|copenhagen-atomics|copper|copper-co|cordance|core-power|coreflow|coreweave|corintis|corning|corporate-synergies|corpower-ocean|correlation-one-expert-network|cortex|cortica|cortica-neurodevelopmental|corvascular|corvias-corporate-services-llc|cosmax|cosmos|cosuno|cote-vegas|cotulla-education|coty|couchbase-inc|counsel-health|counterpart|coupa-software-inc|coupang|coursera|court-of-arbitration-for-sport|covar|cove|covenant-health|covera-health|coverdash|covergenius|cox|v7labs-com|vacasa|vaco-llc|vail-health-hospital|vail-resorts|vale|valeo|valera-health|valiant|validio|valo-health|valon|valon-labs|valonvm|valov-bau-gmbh|valtech|vam-systems|van-metre-companies|vancouver-coastal-health|vanilla|vanna-health|vannevar|vanta|vantage|vapi|varda-space-industries|varicent|varo-bank|vast|vastspace|vat-group|vatic-labs|vaudoise|vaxcyte|vay|vayyar|vcluster|vdk-groep|vectara|vector|vectra|veeam-software|veepee|veesion|veeva|vega|velir|venn|veo|veo-corporate-careers|vera-institute-of-justice|vera-therapeutics-inc|veracode|veracyte|verantos|vercel|verda|verein-f-r-erziehungshilfen|veriff|verifone|verily|verimatrix|verisign|verista-inc|veritas|verity|verkada|verkor|verra-mobility|versaterm|versatile|verse|versicherungsagentur-rathje-gmbh-co-kg|verstela|vertiv|very-good-security|vestas|vestiaire-collective|vestmark-inc|vestwell|vetcove|veterinary-emergency-group|veza-technologies-inc|vf-corporation|vgw|vhc-health|via|viam|vibe|vicarius|viggle|viking-global-investors|viktor|vinted|vir-biotechnology|viral-nation-inc|virgin-atlantic|virtahealth|virtru|virtu-financial|virtua-health|virtual-preparatory-academy-of-florida|visa|visana|visasq-coleman|vise|visier-solutions-inc|vista-global|visual-concepts|visus-one-holding-gmbh|vitable-health|vitalize|vitas-healthcare|vitestro|vitol|vivid-money|vivo-defence-services|vodafone|vohra-physicians|voi|voladynamics|voldex|voliro|volkswagen-group|volocopter|volta-medical|volumental|vonage|vontobel|vorto|vow|vox-ai|vox-media-llc|voyager-technologies-inc|voyah|vsco|vtex|vts|vulcan-elements|vulncheck|vultr|vumc|vyntra)(?:/|$))(?:co[^/]*|v[^/]*|ax[^/]*))",
    "/:lang(en|de|fr|it)/company/:slug((?!(?:bracebridge-capital|brainco|brainpop|brainrocket|brainstation|braintrust|branch|branchinsurance|brand-new-day|brandtech|brave|bravehealth|bravo|bravo-a-cooperative-company|braze|breeze|breeze-airways|breezeway|breitling|brennan-industries|brex|bridge-to-enter-advanced-mathematics-beam|bridgebio-pharma|bridgefund|bridgepoint|bridgestone|bridgewater-associates|bright|bright-horizons|brightai-corporation|brightcore-energy|brightflag|brightnetwork|brightspring-health-services|brigit|brilliant|bring-labs-ag|bringg|brinker-international|brinqa|bristol-myers-squibb|brite-payments|british-american-tobacco|broadcom|broadsign-careers|broadway-ventures|broeder-ruckh-consulting-gmbh|brookdale-senior-living|brooklinen|brookshires|broward-county-public-schools|brown-university-health|browser-use|browserbase|brunswick-group|bryter|cabify|cabrillo-hospice|caceis|caci|caddell-construction|cadence-solutions|cadwell|caesars-entertainment|caffeine-ai|cailabs|cais|caisse-epargne-cossonay|calendly|california-academy-of-sciences|california-autism-center|california-state-university|callista|callsign|calm-com|calyxo|camber|cambium|cambly|cambridge-aerospace|cambridge-mobile-telematics|camp|campfire|camping-world|canals|candidly|cannabis-glass|cannondesign|canonical|canopy|cantina|canto|canton-neuchatel|canton-of-fribourg|canton-of-geneva|canton-of-valais|canton-of-vaud|canva|capco|cape|capgemini|capintel|capital-farm-credit|capital-group|capital-on-tap|capital-one|capital-technology-group|capstone|capsule|caran-dache|carbon-direct|carbonchain|carbonfuture|cardinal-health|care-access|care-com|care-international|caredx-inc|career-team|careers-at-eucalyptus|careers-at-libra-group|careers-at-nlc|careers-at-tide|carefeed|carepay-international|cargill|cargomatic|cargurus|caribou|caribou-financial|carilion-clinic|carmasec-gmbh-co-kg|carmax|carniceria-la-caba-a|caronsale|carrefour-france|carrier|carrot|carta|cartier|cartwheel|cartwheelcare|carv|carvana|casechek|caseguard|casetify|cast-ai|castelion|catawiki|category-labs|catena-clearing|caterpillar|catholic-health|catl|cato-networks|cattaneo-zanetto-pomposo-co|causalens|caylent|re-build-manufacturing|reach|reach3-insights|reactivate|read-ai|real|real-chemistry|real-time-innovations|rebag|rebtel|recharge|recidiviz|reckitt|recora-inc|recorded-future|recraft|recruitaero|recruitis|rectangle-health|recursion|red-6|red-bull|red-cell-partners|red-hat|red-lobster|red-robin|reddit|redis|redpanda-data|redpin|redstone-residential|redwood-materials|redwood-software|reema-health|reface|reflect-orbital|reflection-ai|reflex-aerospace|reformation|reframesystems|regency-integrated-health-services|regrello|regscale|relai|relais-chateaux|relativity-space|relay|relay-graduate-school-of-education|relay-payments|relay-therapeutics|relex-solutions|reliable-robotics|reliance-industries|reliant-rehabilitation|reltio|relx|relyance-ai|remedio|remedyproductstudio|remora|remotasks|remote|remote-people|renaissance-fusion|renaissance-learning-north-america|renault-group|render|renesas-electronics|rent-the-runway|rentokil-initial|reorbit|replit|replo|reply|repower|reprisk|republic-services|resend|resident|resilience|resolve-to-save-lives|resortpass|resource-environmental-solutions-llc|results-physiotherapy|retail-insights|retraites-populaires|reunion-marketing|rev|revenuecat|revero|revisa-gmbh-co-kg-steuerberatungsgesellschaft|revolut|rewards-network|rewe-group|rewind|rexel)(?:/|$))(?:ca[^/]*|re[^/]*|br[^/]*))",
    "/:lang(en|de|fr|it)/company/:slug((?!(?:babylist|bacardi|bachem|backbase|backblaze-external-website|backflip-ai|backmarket|bae-systems|baidu|bain-and-company|baincapital|bajaj-finserv|balgrist|balyasny-asset-management|bamboohr|banco-bradesco|bandai-namco-entertainment-america-inc|bandwidth|bank-of-america|bank-of-china|bank-of-ireland|banking-talent|banner-health|banque-cantonale-de-fribourg|banque-cantonale-du-jura|banque-cantonale-du-valais|banque-cantonale-neuchateloise|banque-de-commerce-et-de-placements|banque-du-leman|banque-heritage|banyan-software|baptist-health|baptist-health-south-florida|baptist-memorial-health-care|barcelona-activa|barclays|barfer-s|bark|barkbox|barkbus|barkley|baron-capital|barry-callebaut|base|base-power-company|baselayer|baseload-capital|baseten|basf|basquevolt|bass-pro-shops|bastion|basware|bath-concepts-independent-dealers|bauer-hockey-cascade-maverik-lacrosse|bauvira-gmbh|baya-systems|bayer|bayesian-health|bayreuther-brauhaus-frankfurt|baywa|hoist-finance|holcim|hologic|home-depot|home-instead|homebase|homelight|hometap|homevision|homeward|honda|hone-health|honeycomb-io|honeywell|honeywell-aerospace|honor|hook|hookmusic|hootsuite|hopital-de-la-tour|hopital-riviera-chablais|hopper|hoppr|hopskipdrive|horace-mann|horizon-industries|hornbach|hoshii|hospital-del-mar|hospital-sant-joan-de-deu|houseaccount|housecall-pro|housemarque|housinganywhere-group|houston-methodist|hover|howden|hoyoverse|mabl|mach-industries|macis-gmbh|macquarie-group|macys|madano|madison-energy-infrastructure|maersk|magic|magic-leap|magiceden|magna|magnolia|mahindra-group|mainstay|maintainx|maintea-gmbh|majestic-labs-ai|majority|make-a-wish-america|make-god-known|makersite|maki-people|mako|maltego-technologies|mamata-betreuungs-und-pflegedienst|mambu|mammoth|mammoth-brands|man-group|manna-drone-delivery|manomano|manor|manscaped|mantra-health|manufact|manukai|manulife-and-john-hancock|manychat|manypets|maple|maplight-therapeutics|marble-aerospace|maria-kersjes|mariana-minerals|mark-spain-real-estate|mark43|marketaxess|marksandspencer|marmon|marqeta|marqvision|marriott|mars|marsh|marshmallow|martell-ventures|maruti-suzuki|marvel-fusion|mass-general-brigham|massar-capital|mastercard|masterclass|material-bank|materialize|materialsecurity|mather-headquarters|matrix|matte-projects|mattermost|matternet|maurices|maven|maven-clinic|maven-emerging-talent|mavenoid|may-mobility|mayflower|mayo-clinic|maze-therapeutics|thales|thanx|that-s-no-moon-entertainment|thatch|thatgamecompany|the-ad-council|the-ai-education-project|the-brattle-group|the-city-of-fort-worth|the-daily-beast|the-doctor-catalunya-s-l|the-durst-organization|the-economist-group|the-exploration-company|the-farmer-s-dog|the-flex|the-florida-panthers|the-fork|the-global-fund|the-iconic|the-jewish-federations-of-north-america|the-joint-chiropractic|the-knot-worldwide|the-mj-companies|the-n2-company|the-national-football-league|the-new-york-times|the-nuclear-company|the-pharmacy-hub|the-pok-mon-company-international|the-quality-group|the-rec-hub|the-trade-desk|the-united-firm-la-liga-defensora-apc|the-virtus-solution|the-voleon-group|the-weather-company|theker|thermo-fisher|thesis|thetaray|theydo|thiess|think-academy-us|think-cell|thinkific|thinking-machines-lab|thirdchannel|thndr|thomson-reuters|thorizon|thought-machine|thoughtworks-new|threataware|threatlocker|thredd|thrive|thrivecart|thriveworks|thumbtack|thunderchild-fusion|thyme-care)(?:/|$))(?:ma[^/]*|th[^/]*|ba[^/]*|ho[^/]*))",
    "/:lang(en|de|fr|it)/company/:slug((?!(?:bl-mlein-ai-automation-gmbh|blablacar|black-and-veatch|black-canyon-consulting|black-duck-software-inc|black-forest-labs|black-ore|blackbird-health|blacklane|blackrock|blacksky|blank-street|blastpoint|blend|blenheim-chalcot-india|bling|blink|blink-ag|bliro|block|block-labs|blockchain-com|blockdaemon|blockit|blockworks|bloom|bloom-biorenewables|bloom-diy|bloomberg|bloomerang|bloomreach|bls|blue-dot|blue-energy|blue-ocean-robotics|blue-origin|blue-rose-research|blue-sky-innovators|blue-water-thinking|blueberrypediatrics|bluebird|bluecrest-capital-management|bluedot|bluelight-consulting|blueprint-technologies|blueprint-test-prep-tutors-instructors|bluesky-telepsych|bluevine-india|bluevine-us|bluevoyant|blushark-digital|blykalla|blytheco|li-fi|liberate|liberis|lidl|liebherr|life-skills-autism-academy|life-trading|life360|lifepoint-health|lifestance-health|lifetime|liftoff|light|lightbringer|lightdash|lightfeather-io-llc|lightforce-orthodontics|lightfully-behavioral-health|lighthouse|lightly|lightmatter|lightning|lightning-ai|lightpanda|lightricks|lightspark|lightspeed-commerce|lightspeed-commerce-fr|lightspeed-dms|lightspeed-systems|lightspeedhq|like-it-media-gmbh|lila-sciences|limb-cher-limb-cher-gmbh|lime|limula|lincoln-property-company|lincoln-property-company-through-linkedin|lindner-parkhotel-oberstaufen-betriebs-gmbh|lindt-spruengli|lindushealth|linear|link|linkedin|linklaters|linkup|linq|lio|liquid-ai|liquid-death|liquid-i-v|liquid-personnel|lirio|lithic|litify|litmus-automation|little-people-s-landing|littlepay|live-nation|liveeo|livekit|livescore-group|living-infinitely|mechanize|medal|medecins-sans-frontieres-doctors-without-borders-field|medecins-sans-frontieres-doctors-without-borders-united-states|medeloop|medely|medflex-gmbh|mediamarktsaturn|mediatek|medical-informatics-engineering|medicines-for-malaria-venture|medicines-patent-pool|mediengr-nderzentrum-mgz-nrw-gmbh|medier|medsien|medtronic|megazone|meijer|meinian-onehealth|meituan|mejuri|melio|melotech|meltplan|mem-protocol|membion-gmbh|memed-diagnostics|memx|mena-consultant|mend-io|mentiora-ai|mento|mercado-libre|mercari|mercedes-benz|mercer-advisors|merch-my-day-gmbh|merck|mercuria|mercury|merge|merge-api|meridian|meridian-partners|merit|merit-america|meriton|merqube-inc|mesh|meshy|met-group|meta|metabase|metabit-technology-llc|metacore|metalab|metalysis|metamorfosis-energ-tica-s-l|method|method-security|meticulous|metos|metox-international-inc|metrasens|metrikflow|metro-bank|metronome|metropolis|metropolitan-commercial-bank|mews|socar-trading|soci|social-discovery-group|socialpoint|societe-generale|socket|socotec|socure|sofi|softbank|softengine-holding-gmbh|soho-house-co|sojern|sol-de-janeiro|sola|solana-foundation|solar-foods|solaris|solera-health|solid-power|solidroad|solink|sollis-health|solution-sft|solveai|sona|sonarsource|sonatus|sonatype|sonder|sonepar|sonicwall|sono-bello|sonova|sony|sony-interactive-entertainment-inc|sony-music-careers-asia-middle-east|sony-music-entertainment-germany|sony-music-entertainment-netherlands|sony-music-entertainment-poland|sony-music-global-job-board|sony-pictures-animation|sony-pictures-imageworks|sopg-consulting|sophia-genetics|sophos|sopra-steria|soros-fund-management|sosafe|sotheby-s|source-ag|source-multiplier|sourcegraph|south-columbus-preparatory-academy-german-village|south-pole|south-star-software-private-limited|southwest-airlines|southworks)(?:/|$))(?:me[^/]*|so[^/]*|li[^/]*|bl[^/]*))",
    "/:lang(en|de|fr|it)/company/:slug((?!(?:gr-n-confections|gr-ns|gr8-tech|gradial|gradient-ai|gradium|gradyent|grafana-labs|graham-capital-management-l-p|grail|gram-games|grand|grand-games|grant-thornton|graphax|graphcore|graphite|gravis-robotics|gravityclimate|graymatter-robotics|grayscale-investments|greatquestion|green-thumb|greeneking|greenlight-financial-technology|greenpeace-usa|greenworks|greiner-engineering-gmbh|grepr|greptile|greystar|griffin|griffis-residential|grifols|grone-bildungszentrum-f-r-gesundheits-und-sozialberufe-gmbh-gemeinn-tzig|gronover-elektrotechnik-gmbh|groome-industrial-service-group|groove-quantum|gropyus|groq|group14-technologies|groupe-e|groupe-mutuel|groupon|grove-collaborative|grover|grow|grow-therapy|growe|groww|grundconsult-immobilien-gesellschaft-mbh|grupo-ole-restauracion|grupo-quintoandar|grupo-urgatzi|pac-nyc|pacific-bells|pacific-legal-foundation|pacs|pacvue|paddle|pagaya|pagbank|pagerduty|pair-team|palabra-ai|palantir|pallet|palmetto-clean-technology|palmstreet|palo-alto-networks|palo-it|palta|pam-health|panasonic|pandadoc|panera-bread|panoptyc|panthalassa|pantheon-systems-inc|pantheon-ventures-careers|panther|papa|papa-johns|paperless-parts|paqato-gmbh|parabola-io|parachute-health|paradigm|parafin|paragon|parallel|paratek-pharmaceuticals|pareto-ai|parity|parker|parker-hannifin|parkosecure-gmbh|parloa|parsley-health|particle41|partiful|partners-group|pasqal|passage|patch-io|patek-philippe|path-robotics|pathai|pathward-n-a|patientpoint|patreon|patrimonium|pattern-data|pavago|pave|pax-historia|pax-labs|paxos|paxoslabs|payfit|payoneer|paypal|paypay|paypay-card|paypay-india|paysafe|paystack|payt-software|paytient|paytm|paytm-payments-services|practice-better|prada-group|praxent|praxis-precision-medicines-inc|precision-aq|precision-for-medicine|precisionmedicinegroup|prefect|prelude|premier-care-dental-management|premier-truck-rental|preply|presence|presidents-institute|presidents-institute-sweden|prevail|prezzee|pricefox|pricefx|primary|prime|prime-healthcare|primeintellect|primer|prior-labs|prisma|prisma-health|private-equity-insights|private-job-board|procter-gamble|procurify|prodigal|productschool|profluent|project-expedition|project44|projective-group|prolaio|prolific|prometheus-real-estate-group|prompt|proofofplay|propel|prophecy|prophesee|propublica|proqura-gmbh|prosek-partners|proshares|prosidian|prosper-health|prosus|protege|prothesen-orthesenmanufaktur|protolabs|proton|prove|providence|proxima-fusion|pryzm|saas-group|saber-interactive|saber-tech|sable|saeki|safari-ai|safaricom|safe|safe-security|safe-superintelligence|safesize|safetyculture|safran|sage|sail-research|saildrone|sakana-ai|saks|salesforce|salient|sally-beauty-holdings|salsify|salt|salt-security|sama|sambanova|samlino-group|samotics|samsara|samsung|samsung-research-america-internship|samsung-semiconductor|san-francisco-aids-foundation|san-francisco-campus-for-jewish-living|sana|sanctuary-ai|sand-tech-holdings-limited|sandbox-vr|sandboxaq|sandoz|sandoz-foundation-hotels|sandstone-care|sanford-health|sanitas|sanity|sanofi|sanovio|santander|sap|sardine|sargent-lundy|saronic|saskatchewan-health-authority|sateliot|satispay|satrev|sattler-media-gmbh|saturn|satvu|sauce-labs-inc|saudi-aramco|save-the-children-international|saviynt|savr|savvy|sax-advisory-group|saxo-bank|saxotherm|sayari|snafu-records|snap|snap-fusion|snap-mobile-inc|snappy|sncf|snorkel-ai|snow-companies|snowflake|snyk)(?:/|$))(?:pa[^/]*|sa[^/]*|gr[^/]*|pr[^/]*|sn[^/]*))",
    "/:lang(en|de|fr|it)/company/:slug((?!(?:cec-entertainment|cedar|cedars-sinai|celebal-technologies|celestia|celestica|celigo|cellcentric|cellular-sales|celonis|cencora|censys|center-for-employment-opportunities|centessa-pharmaceuticals-llc|centralreach|centre-for-humanitarian-dialogue|centria-autism|centric-software|centrum-health|centurion-health|cequr|cerebras-systems|ceribell-inc|cern|cerrion|ceva-logistics|clara|clari|clari-salesloft|claritev|clariti-cloud-inc|clarity|clarity-innovations|clarium|clark-germany-gmbh|classdojo|classen-industries-gmbh|classpass|clay|clean-harbors|clear-corporate|clear-street|clearbank|clearscore-technology-limited|clearview-healthcare-partners|clearway-energy|clece|cleo-india|cleo-us|cleric|clerk|clerk-chat|cleveland-clinic|cleveland-preparatory-academy|clickhouse|clickup|clifford-chance|climate-finance-solutions|climate-x|climateview|climeworks|clinchoice|clinique-la-prairie|clinomic|clinton-health-access-initiative|close|close-consulting|cloud-chamber-montreal|cloudbeds|cloudflare|cloudkitchens|cloudsek|cloudsmith|cloudtrucks|clove|clover-health|club-monaco|clubhouse|clutch-technologies-inc|debiopharm|debtbook|debut-biotech|decagon|decathlon|decathlon-digital-fr|decima-international|decimal|deel|deepgram|deepintent|deepjudge|deepl|deepmind|deepnote|deepseek|deepsense-ai|deepset|defense-unicorns|definitive-healthcare-us|degreed|delair|delft-circuits|delian-alliance-industries|delinea|deliveroo|delivery-associates|delivery-hero|dell|deloitte|deloitte-india|deloitte-southeast-asia|delphi|deltacapita|dema|demandbase|dental365|dentsply-sirona|dentsu|depict|depoly|dept|descript|designed-conveyor-systems|desmos|destinus|detroit-lions|deutsche-bank|deutsches-feingoldhaus-gmbh|dev-technology|development-partners-international|devoteam|devoted-health|devrev|deweylearn|dexcom|dexis|dexory|dexter-energy|dexterity|j-crew|j-d-irving|j-safra-sarasin|jabil|jade-biosciences|jane-street|jane-street-events|janea-systems|january|jasper|jazzx-ai|jbs-dev|jbs-usa|jd-com|jd-finish-line|jd-sports|jeeves|jefferson-health|jeil-pharmaceutical|jeju-air|jellyfish|jellyfishcareers|jensen-hughes|jeronimo-martins|jerry|jet-aviation|jetbrains|jetzero|jfrog|jimmy|jll|job-board|jobandtalent|jobber|jobhive-ag|joe-nimble-gmbh|johns-hopkins-health-system|johnson-and-johnson|johnson-controls|johnson-law-group|join|join-gmbh|join-our-talent-community|join-the-folx-team|jomboy-media|josko-services|jpmorgan|jti|jua|judge|judi-health|juicebox|jukebox-health|julius|julius-baer|jumia|jumio|jump|jump-app|jump-crypto|jump-trading|jungle-scout|juni|juno|just-4-veterans-enterprise|just-eat-takeaway|justworks|juul-labs|jysk|tr-fr|traba|trace3|tracebit|tracelabs|track-omc|trade-republic|tradeshift|tradesmen-international|trading212|tradingview|trafigura|trailer-park-group|trailofbits|trainline|trane-technologies|transcarent|transcend-inc|transmarket-group|transmit-security|transmutex|transports-publics-fribourgeois|transports-publics-genevois|transports-publics-lausannois|transunion|trapeze-group|trase-systems|travel-leisure-co|travelcenters|travelperk|traversal|trella-health|trexon|treyd|trieye|trigo|trigon-gruppe-gmbh|trilon-group|trine|trinity-health|trip-com-group|tripadvisor|triple-whale|triton-systems|triumvirate-environmental|trucksmarter|true-anomaly|true-classic|truecaller|truelayer|truemed|truffle-security|truist|trulioo|trunk|trust-bank|trust-wallet|trust-will|trustly|trustpilot|truveta)(?:/|$))(?:tr[^/]*|j[^/]*|cl[^/]*|de[^/]*|ce[^/]*))",
    "/:lang(en|de|fr|it)/company/:slug((?!(?:9-mothers|chaidiscovery|chainguard|chainlink-labs|chalk|champions-group-holdings|chan-zuckerberg-initiative|change|changins|changxin-memory|chaos-industries|character|charge-robotics|chargepoint|chariot-defense|charles-river-associates|charterup|chatham-financial|chauffeurcenter-ch-ag|checkbook|checkers-rallys|checkly|checkout-com|checkr|checkr-chile|chenmoore|cheplapharm|cherry-ventures|chery|chess-com|chestnut|chevron|chicago-public-media|chief|childrens-place|chime-financial-inc|china-construction-bank|china-life-insurance|china-mobile|china-railway-group|china-state-construction|china-telecom|chip-city|chipmind|chiquita|chomps|chopard|chowbus|christies|chromatic|chromaway|chs-inc|chubb|churchs-texas-chicken|chuv|ge-healthcare|ge-vernova|gearset|gecko-mbh|gecko-robotics|geely-holding-group|geisinger|gelato|gelber-group|gelber-group-handshake|gemini|genea|general-assembly|general-atlantic|general-dynamics|general-matter|general-mills|general-motors|generate-biomedicines|generative-bionics|genertec|genesis-ai|genesis-digital-assets|genesis-molecular-ai|genestack|genesys|genetix-biotherapeutics|geneva-airport|geneva-call|geneva-centre-for-security-policy|geneva-graduate-institute|geneva-trading|genies|genius-sports|genomics|genpact|genpeach-ai|genscript-probio|gensyn|gentiva|genuine-parts-company|georg-fischer|geotab|gerald-group|get-well-network|getnet|getresponse|getspecialfasteners-com|gett|getwhy|getyourguide|peak-design|pearl|pearlhealth|pecan|peec-ai|peloton|pendo|penn-entertainment|penn-interactive|penn-state-university|penny-cz|pennylane|penske|penske-media-corp|pentera|penumbrainc|people-ai|people-can-fly|pep|pepsico|per-scholas|peraton|percepta|percepto|peregrine-technologies|perella-weinberg|perfectserve|pergolux|periodic-labs|perion-network-ltd|pernat-emile|perplexity-ai|perry-ellis-international|perry-ellis-international-retail|persistent-systems|persona|personalis-inc|personio|pet-supplies-plus|petrobras|petrochina|se3|seamless|seatgeek|seattle-sounders-fc-seattle-reign-fc|secfix|secheron-hasler-group|second-front|secretariat|sectra|secureframe|securitas|securitas-ag|securitize|security-bank|securityscorecard|seed|seeing-systems|seesaw|sei-labs|sekoia-io|select-management-group|select-medical|self-financial|selini-capital|semafor|semgrep|semiqon|semrush|sendbird|senior-doc|senra-systems|sensirion|sensofusion|sentara-health|sentilink|sentry|seo-sponsors-for-educational-opportunity|seon|seoul-robotics|sephora|seprify|septerna|sequence|sequoia|sequra|serco|sereact|serhant|sertis|sertrading|servers-com|service-corporation-international|servicenow|servimo|sesame|sesamm|seso-inc|setpoint|seven-research|severin-hotels|seyond|sezzle|te-connectivity|teachable|teague|tealium|team-car-care|team-skalieren|tebi|tebra|tecan|tech-holding|tech-mahindra|techland|technology|techtorch|tecovas|tegna-inc|tehtris|tekever|tekion|tekmetric|telefonica-tech|teleperformance|tellius|telnyx|telstra|tem|temenos|tempo|temporal|temporal-technologies|temus|tenable-inc|tencent|teneo-ai|teneo-external-feed-for-linkedin|tenex-ai|tenjin|tennr|tensorops|tensorwave|tenstorrent|tenstorrent-university-jobs|tenstorrent-unlisted-referral-jobs|tenzai|teravision-technologies|terra-quantum|terraai|terrabis|terran-orbital-corporation|terranova|terveystalo|tesco|tesla|testgorilla|tether|tethys-robotics|tetra-pak|teveo-gmbh|texas-am-university-system|texas-instruments|teya)(?:/|$))(?:se[^/]*|ch[^/]*|te[^/]*|ge[^/]*|pe[^/]*|9[^/]*))",
    "/:lang(en|de|fr|it)/company/:slug((?!(?:be-our-guest|beacon-biosignals|beacon-software|beam|beam-therapeutics|beamery|bearingpoint|beautiful-ai|beautybarrage|bedi-partnerships|bedrock|bedrock-robotics|beewise|behavox|beiersdorf|believe|belimo|belk|belong|belvedere-trading|belvo|benchling|benchmark-physical-therapy|benchprep|benevolentai|beqom|berlin-brands-group|berlin-city-auto-group|berlin-institute-of-health|berlin-metropolitan-school|berlin-packaging|berlinrosen|berner-kantonalbank|bers|bertelsmann|bertram-capital-management|bertrandt|bertschi|besi|beside|best-version-media|bestow|bestseller|beta-technologies|beth-israel-lahey-health|betsson-group|better|betterhelp|betterment|betterup|bevi|beyond-finance|beyondtrust|bp|he-arc|headlands-research|headout-li|headspace|headway|healthcare-services-group|healtheconnections|healthforce|healthpartners|healthpro-heritage|healthverity|healthy-io|hear-com-in|hear-com-us|heart-aerospace|heartflow|hebbia|hedra|heds-la-source|hedvig|heidelberg-materials|heidrick-struggles|heig-vd|heim-marketing|heizm-ller-gmbh|helium-10|hello-heart|hellofresh|help-scout|helsana|helsing|helvetia-baloise|hemab-therapeutics|hemnet|hemu|hengrui-pharmaceuticals|henkel|herald|here|hermes|hermeus|heron-power|hertz|hes-so|hes-so-fribourg|hes-so-geneve|hes-so-valais-wallis|hesav|hex-technologies|hexagon-bio|hexagon-robotics|hexagone-ai|hexarmor|hexaware|heygen|heylogin-gmbh|heytea|michael-bonsby-hvac-plumbing-electrical|michaels|michelin|michels-corporation|micron|microsoft|microsure|microtech-global|midea-group|midi-health|midstream|migros|mill|millennium-management|millers-ale-house|million-dollar-baby-co|milomed-gmbh|mimecast|mimetas|mimic|mind-robotics|mindbody|mindlance|mineralys-therapeutics|minio|minitab|minnesota-cannabis-services|mintcode-solutions-gmbh|mintlify|mio-partners|miq-digital|mirabaud-group|mirage|mirai-power|mirai-tech|mirakl|mirakl-labs|mirelo-ai|miro|miromind-ai|mirum-pharmaceuticals|misfits-market|miso|mission-lane|mistral-ai|mithril|mithrl|mitigram|mitratech|mitsogo-inc|mitsubishi-corp|mitsubishi-motors-north-america-inc|mixpanel|mob-entertainment|mobilelink|mobileye|mobilityware|mochi-health|modal|modern-animal|modern-health|moderna|modernfi|modernizing-medicine-inc|moderntreasury|modulr|modus-create|moelis|moia-gmbh|molecubes|molg|mollie|moloco|momence|moment|momentum|momentum-financial-services-group|monad-foundation|mondelez|moneyboxapp|moneyhero-group|moneysmart-group|mongodb|moniepoint|monro|monroe-tractor|monumental|monumental-sports-entertainment|monzo|moon-surgical|moonlite|moralis|morgan-morgan-p-a|morgan-stanley|morning-brew-inc|morse-micro|mortenson|mosaic|moss-new-york-llc|motherduck|motion|motional|motive|motorola-solutions|moulin-a-miel|mount-sinai-health-system|movement-strategy|mozilla|q-ant|q-centrix|q-ctrl|qai-ventures-ag|qblox|qdrant|qilimanjaro-quantum-tech|qima|qinetiq|qnb-group|qodo|qonto|qphox|qred|qts|qualcomm|qualia|qualified|qualified-digital|qualified-health|qualifyze|qualio|qualtrics|quanata|quandela|quanta-dialysis-technologies|quanta-services|quantcast|quantexa|quanthealth|quantinuum|quantis|quantori|quantrolox|quantum|quantum-coffee|quantum-motion|quantum-si|quantumdiamonds|quantware|quartr|quartz-bio|quatt|qube-rt|qubit-pharmaceuticals|quera-computing-inc|quest-diagnostics|quick|quick-green-rapid-health-gmbh|quicknode|quiktrip|quillbot|quin|quince|quisitive|quiver-ai|quix-quantum|quobly|quora|qutwo|qventus)(?:/|$))(?:be[^/]*|mi[^/]*|he[^/]*|mo[^/]*|q[^/]*|bp[^/]*))",
    "/:lang(en|de|fr|it)/company/:slug((?!(?:ac-immune|academia|academy-sports-outdoors|acadia-healthcare|acadia-pharmaceuticals-inc|accel-club|accel-schools|accela|acceleration-partners|accelercomm|accenture|accenture-federal-services|accenture-federal-services-careers-marketplace|access-healthcare-associates|accesso|accor|accord|accrue|accuracy|accuray|accuweather-careers|aci-learning|aciner-geb-udereinigung|aclu-internships|aclu-national-office|aclu-of-new-jersey|acne-studios|acommerce|acorns|acosta-group|acquia|acquisition|acquism|acrisure|acrisure-innovation|acronis|activecampaign|activision-blizzard|acumen|acurus-solutions-private-limited|fia|fictiv|fidelity-international|fidelity-investments|fieldwire|fielmann|fifa|fifth-third-bank|figma|figure|figure-lending|files-com|filigran|filmhub|filson|fim|fin|financial-technology-partners|financial-times|finanzwerk-hamburg|finch|find|finix|finma|finn|finom|finster-ai|fintechos|fireblocks|firecrawl|firehawk-aerospace|firestorm|firetiger|fireworks-ai|firmus|firmus-technologies|first-abu-dhabi-bank|first-connect-insurance|first-light-fusion|first-momentum-ventures|first-student|firstmind|firstprinciples|fis|fis-amount|fiserv|fisu|fit2go-gmbh|five-below|five-rings-llc-careers|five-rings-llc-events|five9|fivetran|fixposition|imagen-technologies|imagination|imagine-pediatrics|imagine-worldwide|imago-stock-people-gmbh|imbibe|imc|imd|immatics|immersivelabs|immobilien-hausverwaltung-lessmann-gmbh|immunocore|impact-com|impinj|impiricus|implement-consulting-group|implenia|imply|imprint|improbable|improvado|imubit|sib-solutions|sicpa|sicredi|sidoun-international-gmbh|siemens|siemens-healthineers|sierra|sieve|sift|sift-healthcare|sig-group|sightline-media-group|sigma-computing|sigmoid|signal-iduna|signers-national|signet-jewelers|signifyd|signoz|sika|sila-services|silicon-ranch-corporation|siloam-hospitals-group|silverado|silverflow|silverfort|silvus-technologies|similarweb|simon-kucher|simpleclosure|simplesense|simplex-trading|simplifynext|simplypayments|simtra-biopharma-solutions|simulamet|sinclair|singapore-public-service|singlestore|singular|sinopec|sipfront-gmbh|siren|sirona-medical|sisense|sita|siteline|siteminder|sitoo|sixfold|space-forge|space-kinetic|spacex|spacex-global|spacial|spade|span|spar-inc|spare|spark|spark-advisors|sparkland|sparksoft-corporation|sparrow|sparrow-quantum|spaulding-ridge|spc-group|speakeasy|specitec|specterops|spector-ai|speechify|speechmatics|spekit|spektr|spektrum|spencer-stuart|spendesk|sphere|sphinx|sphinx-defense|spiegel-media-gmbh|spin-brands|spin-careers|spire|splice|spothopper|spotify|spotlight|spotme|spotter|sprengnetter|sprig|spring|spring-health|springboard|springboard-roles|sprinter-health|spruce-systems|spruceid|sps-north-america|sps-north-america-opportunities-not-externally-posted-board|spycloud|zalando|zam|zama|zapier|zara|zayzoon|zebra|zed|zeffy|zeiss-group|zello|zenbusiness-inc|zendesk|zengrc|zenline-ai|zenni-optical|zeno-power|zenobe|zenoti|zenty-lp|zeotap|zephyr|zepz|zerion|zero|zero-networks|zeromark|zettabyte-space|zevia|zf|zhaw|zhengda-group|zillow|zimmer-biomet|zimmermann-brase-partner-steuerberatungsgesellschaft-mbb|zimpler|zinnia|zinnia-employee-referral|zip|zip-co-limited|zipline|ziprecruiter|zocdoc|zomato|zone-5-technologies|zone-co|zoominfo-technologies-llc|zoox|zopa|zscaler|zte|zuehlke|zulu-alpha-kilo|zuma|zuora|zup-innovation|zurich-airport|zurich-insurance|zus-health|zwift|zynga)(?:/|$))(?:fi[^/]*|si[^/]*|sp[^/]*|z[^/]*|ac[^/]*|im[^/]*))",
    "/:lang(en|de|fr|it)/company/:slug((?!(?:arab-bank-switzerland|arb-interactive|arbe-robotics|arbital-health|arbor|arc-boat-company|arc-institute|arcade|arcadia|arcadis|arcana-analytics|arcee-ai|arcelormittal|arcesium-llc|arch-co|archangel-autonomy|archer|archera|architect|archrival|arena-ai|arenanet|arine|arize-ai|arkestro|arkose-labs|arlo-solutions-llc|arm|armada|armis-security|arnold-kl-mpen-gmbh-co-kg|arondite|arqit|array-education|artefact|artera|arthur-d-little|arthur-j-gallagher|artie|artifact|artisan|artisan-partners|artsy|arx-robotics|aryzta|escribers|esh-medias|esm-personalservice-gmbh|espace|espresso|esri|ess|essentia-health|foca-bazl|focus|focus-financial-partners|focus-partners-australia-escala-partners|focused-energy|foley-hoag-llp|folio|follett-software-llc|fora-financial|forbes|ford|forerunner|foretellix|forever-families|forge|forge-biologics|form|form-health|forma|formance|formation-bio|formenergy|formlabs|forsight-robotics|forte|fortem-technologies|forter|forterra|fortinet|forto|forum-ventures|forward-networks|fospha|fossa|fotokite|found|foundation-risk-partners|founders-green-animal-hospital|foundry-robotics|four-hands|four-seasons|fourkites|foursquare|fourthline|fox-rehabilitation|foxconn|foxglove|haast|habitat-health|hack-the-box|hackerone|hackerrank-careers|hadean|hadrian|haier-group|hailo|haize-labs|hala|halcyon|haleon|handelsbanken|hang|hanmi-pharm|hanwha-renewables|happy-money|happyrobot|harbinger-motors-inc|harbor|harmattan-ai|harmonic|harmony|harness|harper-group|harris-associates|harrison-ai|harrow-inc|harry-s|harvey|hasbro|hatch|hatch-ltd|hauskrankenpflege-stolley-gmbh|havas|havells|haven-interactive-studios|havenhub|havocai|hawk|hawkeye360|hawthorne-machinery-co|hays|haystack-news|hazel-health|sc-johnson|scalable-capital|scalapay|scale-ai|scaled-cognition|scalemath|scaleops|scandic-hotels|scandit|scania|scenic-biotech|scewo|schaeffler|schaffmann-consultants-executive-search|schindler|schmelzle-partner|schollmaier-schollmaier-partmbb-steuerberatungsgesellschaft|schonfeld|schonlau-werke-geseke|schr-dinger|schroders|schwarzman-animal-medical-center|schwertfels-consulting|sciforium|scopely|scor|scorpion-enterprises-llc|scotch|scotiabank|scout-ai|scout-motors|scout24|screenpoint-medical|scribe|scw-systems|substack|sucafina|sucden|suez|sugarcrm|sui|suind|suki|sullivan-cromwell|sulzer|sulzer-schmid|summer|summerwood|summit-one-vanderbilt|sumo-logic|sumup|sun-pharmaceutical|sunbelt-rentals|sunfire|sunflower-labs|sunnyside|suno|sunrise|sunrise-management|sunrun|sunstar|sunwoda|supabase|super-com|super-technologies|superblocks|supercell|supergaming|superhuman|supermicro|supernovacompanies|supporting-strategies|sureify|surgery-partners|surrealdb|surveymonkey|susquehanna-international-group|sustainable-ag-unternehmensberatung|sustainable-talent|sustainment|sutter-health|tabapay|tabby|taco-bell-luihn-vantedge-partners|tacto|tacton-systems|tadaweb|tado|tag-aviation|tailorcare|tailored-brands|tailscale|tailwind|take-two-interactive-software-inc|takealot-com|takealot-group|takeda|tako|taktile|talent-trader-group|talentful|talkdesk|talkiatry|talkspace-remote-psychiatric-nurse-practitioner-roles|talkspace-remote-therapist-roles|talon-one|talos|tandem|tandem-bank|tandem-health|tandemlaunch|tangible|tango-gameworks|tanium|tanius-technology|tapblaze|tapestry|tappz-gmbh|target|target-rwe|taskrabbit|tastytrade|tata-capital|tata-motors|tatari|taurus|tavus|taxbit)(?:/|$))(?:fo[^/]*|ta[^/]*|su[^/]*|sc[^/]*|ha[^/]*|ar[^/]*|es[^/]*))",
    "/:lang(en|de|fr|it)/company/:slug((?!(?:amada-senior-care-north-shore|amae-health|amazon|amber|ambiencehealthcare|ambient-ai|ambient-enterprises|ambrosia-qsr|amca|amcor|amd|amend-consulting|amenitiz|american-college-of-obstetricians-and-gynecologists|american-express|american-housing|american-institute|american-residential-services|american-veterinary-group|ametek|amgen|ami|amina-bank|ammann|amo|amoria-group|amp-ai-powered-sortation-for-waste-and-recycling|ampact|amperity|amperos|amplemarket|amplifon|amplitude|amrest|amundi|amwell|apaleo|apartmentiq|apco-technologies|apera-ai-inc|aperia|apex|apex-space|apheros|apify|apiiro|apiphani|apiro-entertainment-gmbh-co-kg|aplazo|apollo|apollo-education-systems|apollo-graphql|apollo-io|appdirect|appian-corporation|appier|apple|appletree-prep|applied|applied-engineering|applied-intuition|applied-materials|appliedlabs|applike-group-gmbh|applovin|apply|appnovation-technologies|appodeal|appomni|appquantum|appsflyer|appspace|apptronik|appviewx|apron|aptiv|aptos|at-bay|at-home|ataibeckley|ataraxis-ai|atdp-company|athletics-baseball-operations|athletics-business-operations|ati-physical-therapy|atkinsrealis|atlan|atlantic-health|atlas|atlas-agro|atlas-copco-group|atlas-hxm|atlassian|atlys|atmos-cholet|atom-bank|atom-computing|atomic-cartoons|atomicwork-inc|atoms-careers-page|atos|atria-senior-living|atropos|att|attain|attain-partners|attentive|attentivemobile|atticus|attio|attivo-partners|atu-auto-teile-unger|atwell-llc|enable|enavate|encoura|encube|endava|endeavor-health|endor-labs|endress-hauser|endurosat|energie-360|energy-dome|energy-exemplar|energy-solutions-usa|energy-vault|energyhub|energytec-ai|engelhart|engflow|engie|engine|engineers-gate|enhabit|enhesa|enigma|ennoble-care|enova-international|enpal|enpulsion|ens-dynamics|ensco-inc|ensemble|ensemble-hospitalier-de-la-cote|entalpic|entera|enterpret|enterprise-mobility|entersekt|entrepreneurs-first|entrust|enveritas|envision-consulting|enviva|envoy|envoy-global-inc|framer|franciscan-health|frankenburg-technologies|fraunhofer-gesellschaft|freed|freedom-together-foundation|freeform|freenome|freenow|freeplay|freetrade|freewill|fresenius-kabi|fresenius-medical-care|fresh-prints|freshfields|freshpaint|fressnapf-maxizoo|freudenberg|friedenberger-rudnick-steuerberatungsgesellschaft-mbh|fries-gruppe|friss|froda|froid-climatisation-assistance|frontcareers|frontier-dermatology-provider-careers|frontiers|frontify|le-temps|leading-educators-careers|league-inc|leap|leapmotor|leapsome|leapwork|learneo|learning-care-group|learning-commons|learnlux|learnupon|leclanche|ledger|ledgy|legal-services-nyc|legalzoom|legend-biotech-us|legends-global|legion|legion-intelligence|legionhealth|legit-security|legora|leidos|lek|lemlist|lemonade|lendingtree|lendo|lenovo|lens|leona-health|leonardo|leonteq|les-fermes-debout|letta|levanta|level|level-access|levi-strauss|levio|levitate|lexly|leyden-labs|leydenjar-technologies|ms-amlin|msc|msd|msf|ro|roadie|roadrunner-recycling-inc|robco|robinhood|roblox|roboa|roboforce|robovision|roboyo|roche|rock-flow-dynamics|rocket-chat|rocket-factory-augsburg|rocket-lab-corporation|rocket-lawyer|rocket-money|rocket-travel-inc|rockstar-games|roemer-capital-gmbh|roke|roku|roland-berger|rolex|roljobs-technology-services|roller|rolls-royce|romande-energie|rondo-energy|roo|roofr|roofstock|root-access|ropes|rothesay|rothschild-and-co|routine-labs|rover|rovop|rowden-technologies)(?:/|$))(?:en[^/]*|le[^/]*|am[^/]*|ap[^/]*|ro[^/]*|at[^/]*|fr[^/]*|ms[^/]*))",
    "/:lang(en|de|fr|it)/company/:slug((?!(?:asana|asbury-automotive-group|ascend-analytics|ascension|ascent|ascento|asg|ashby|asian-paints|asm|asml|asos|aspect-biosystems|aspen-dental|aspire-living-learning|aspora|assa-abloy|assai-atacadista|asseco-poland|assembly|assemblyai|asset-living|assetwatch-inc|association-for-prevention-torture|assura|assured-guaranty|assyst-inc|assystem|astera-labs|astera-labs-early-career|astra|astranis|astrazeneca|astro-mechanica|astronomer|bicycle-therapeutics|big-d1-gmbh|bighat-biosciences|bigid|bike-business-hub-slu|bilfinger|bill|billa-cz|billiontoone|billogram|billups|bilt-rewards|binabik-ai|binance|binance-us|bio|bio-techne|biocartis|biocatch|biogen|biograph|biohub|biontech|biorce|bird|birzer-neumann-wirtschafts-und-steuerberatungsgesellschaft-partnerschaftsgesellschaft|bishop-fox|bitcoin-depot|bitdefender|bitfarms|bitfinex|bitgo|bitmex|bitpanda|bits-technology|bitso|bobbie|bobst|boddie-noell-enterprises|boehringer-ingelheim|boeing|bokio|bol|bold-ag|bold-business|bolster|bolt|bolt-new|boltz|bombardier|bombas|bon-secours-mercy-health|bonfire-studios|bonnier-news|booking-com|booksy|boom|boom-entertainment|boomi|booz-allen-hamilton|bordier|bosch|boschung|boston-scientific|bot-auto|bota-systems|bots|bottomline|boulder-care|boulevard|bounce|bound|box|boxlunch-hot-topic|boyd-gaming|cr-fitness-holdings|cracker-barrel|cradle|craftdocs|cranial-technologies|cravath|createch-engineering-gmbh|creative-fabrica|creativex|cred|credible|credit-agricole-next-bank|credit-karma|credit-union-of-colorado|creditaccess-india|creditgenie|crescent-hotels-resorts|cresco-labs|cresta|crexi|crh|cribl|crisp|crisp-recruit|criteo|cro-metrics|cross-river|crowdstrike|crunchyroll-llc|crusoe|crux-climate|cryptio|crypto-com|cryptonext-security|daedalean|dagster-labs|daiichi-sankyo|dailymotion|damora-therapeutics|danaher|dandelion|danfoss|dares|dark-wolf-solutions|darktrace|dash0|dashlane|data-praxis|databento|databricks|datacamp|datacor|datadog|datadome|dataguard|datahub|dataiku|datarails|datasmart-point-gmbh|datasnipper|datologyai|dave|dave-and-busters|davey-tree|david-zwirner|davidson-hospitality-group|davis-development|davis-polk|davita|daylight|daymark-health|dialectic|dialogueai|dialpad|diana-health|dicks-sporting-goods|didi-global|die-pflegeunion-gruppe|dig-inn-restaurant-teams|digible|digital-asset|digital-ops-tech-centre-dis-dotc|digitale-leute-school|digitalplatforms|diligent-corporation|diligent-robotics|diligent-services|dine-brands|direct-demo|disco|discord|disney|dispatch|disruptive-industries|distalmotion|distantjob|divergent|flaglerhealth|flagship-pioneering-inc|flagstone-group-ltd|flanigans-enterprises|flare-bright|flatiron-health|fleek|fleetio|fleetworks|fleetworthy|fletcher-jones-automotive-group|flex|flex-ltd|flexion-robotics|flexport|flighthub|flint|flip|flipkart|flir|flix|flo-health|float|flock-homes|flock-safety|flora|florence|floryn|flow-traders|flowfuse|flutterflow|flux|fluxon|flyability|flycatcher|flyr|flytxt|swan|swap|swarm-aero|swarm-biotactics|swatch-group|sweden-ballistics|sweep|sweetgreen|swibeco|swiggy|swile|swishfund|swiss-confederation|swiss-football-association|swiss-international-air-lines|swiss-life|swiss-medical-network|swiss-mobiliar|swiss-national-bank|swiss-national-science-foundation|swiss-olympic|swiss-post|swiss-re|swisscom|swissdrones|swissgrid|swissmedic|swissport|swissquote-bank|swissto12|swoboda|sword-group|sword-health)(?:/|$))(?:bi[^/]*|sw[^/]*|cr[^/]*|as[^/]*|da[^/]*|bo[^/]*|fl[^/]*|di[^/]*))",
    "/:lang(en|de|fr|it)/company/:slug((?!(?:anagram|analog-devices|anaplan|anchanto|anchorage-digital|andela|anduril-industries|anea-sante|anevo-ag|angeheuert-gmbh-personalberatung|angel-city|angi|angitia-incorporated-limited|anima|anine-bing|ankerplatz-mea-vita-neum-nster-gmbh|anodize|anomali|anon|anrok|ans|ansa|answerrocket|ant-group|antenna|anteriad|anthropic|antithesis|antonie|antora-energy|anybotics|anyfin|anyscale|anywherenow|au-small-finance-bank|au10tix|auctane|audax-group|audemars-piguet|audibene-hear-com|auditax-steuerberatungs-gmbh|auditdata|auditless|augment-code|augury|august-health|aura-aero|auralis-group|aureliussystems|aurora-innovation|aurorasolar|auros|auterion|authentic-brands-group|auto1|autobrains|automata|automattic-careers|autonation|autopilot|autoproff|autoscout24|autozone|autura|bubble|bubble-skincare|bucher-industries|bucherer|bug-bounty-switzerland|bugcrowd|buhler-group|build|builder-io|buildkite|buildops|built|built-in|built-robotics|built-technologies|bumble|bunge|bunq|bupa|burckhardt-compression|bureau-veritas|business-insider|butternut-box|bux|buyers-edge-platform-llc|buynomics|buzz-solutions|ci-azumano|cic-energigune|cintas|cinven|circle|circle-k|circle-so|circleci|circuithub|cisco|cision|citadel|citadel-securities|cite-gestion|citema-systems-gmbh|citian|citigroup|citizen-watch-group|citrix|citrus-health-group-inc|city-and-county-healthcare-group|city-of-geneva|city-of-lausanne|city-of-montreux|city-of-new-york|city-of-nyon|city-of-vevey|city-of-zurich|civil-science|goals|goat-group|gocardless|godaddy|gofundme|goguardian|goldbeck|golden-apple-foundation-careers|golden-state|goldman-sachs|golinks|gonet|gong-io|good-job-games|gooddata|goodfire|goodnotes|goodway-group|goodweek|goody|google|goop|gopuff|gore-mutual-insurance|gorgias|gorjana|gorman-bunch-orthodontics|gostudent|gotham-enterprises|gotion-inc|gousto|govini|govsignals|govtech-barbados|hiber|hibu|hidden-events|hidden-level|higgsfield-ai|highdive|higher-logic|highmark-health|highnote|hightouch|highview-power|hiive|hike-medical|hill-house-home|hillel-international|hilton|hilton-grand-vacations|hippo-insurance|hippocratic-ai|hirslanden|hiry-agency|hit-haus-industrietechnik-gmbh|hitachi|hitachi-energy|hive|hive-financial-systems|hivemq|hivestack|hiya|la-senza|la28|la28-web|labcorp|labelbox|lakera|lam-research|lambda|lambertus-apotheke|lancedb|landis-gyr|langchain|langdock|langfuse|lantern|lantmannen|larkin-street-youth-services|larsen-toubro|lasko-products|lassie|last-app|lastpass|later|latitude-ai|lattice|latticeflow|launch-potato|launchdarkly|launchpad-technologies|laurel|lavendo|lawdepot|lawzero|layer-health|layerfi|layerzero-labs|lazard|loadsmart|lob|loblaw|local-initiatives-support-corporation|localiza|loccitane-group|lockheed-martin|locus-robotics|lodestar|lodestar-space|lodgify|logicgate|logicmanager|logitech|logmind|logop-dische-praxis-kuhnle-gmbh|logos|lojas-renner|loka-inc|lombard-odier|long-lake-management|lonza|look-up|lookout-inc|loop|loreal|lorikeet|lottie|louis-dreyfus-company|louis-vuitton|lovable|placements-io|placer-ai|plaid|plain|plainid|plane|planet|planet-a-foods-gmbh|planet-pharma|planetscale|planhat|planner5d|planqc|planradar-gmbh|planzer|plata|platform-science|platform9|playground|playkot|playnvoice|playrix|playstation-global|pld-space|plentific|pletschacher-holzbau-gmbh|plexus-co|plexus-worldwide|plinth|plot|pls|plumettaz|pluralfinance|plus-power)(?:/|$))(?:la[^/]*|an[^/]*|go[^/]*|ci[^/]*|lo[^/]*|au[^/]*|hi[^/]*|pl[^/]*|bu[^/]*))",
    "/:lang(en|de|fr|it)/company/:slug((?!(?:ada|ada-health-gmbh|adani-group|adapt|adaption-labs|adaptive|adaptive-security|adarga|adc-therapeutics|addepar|addi|addionics|adf-international|adfinis-ag|adidas|adjust|adm|admatis|adnovum|adobe|adonis|adswerve-inc|adt|advance-auto-parts|advanced-space|advantest|adventhealth|advocate-health|adyen|ai-squared|ai2|ai21-labs|ai4i|aiendoscopic|aift|aig|aignostics|aikido-security|aim|ain-group|ainavio-gmbh|aios|aiphoria|air-liquide|air-space-intelligence|aira|airbnb|airbus|airbyte|aircall|airforestry|airgarage|airia|airmo-gmbh|airnxt|airobotics|airship|airslate|airspace|airtable|airtrunk|aisle|aists|aiven|aizer-health|elastic|elca-group|elcogen|electra|electrolux|electronic-arts|electronx|eleos-health|eleqtron|elevance-health|elevations-credit-union|eleven|elevenlabs|elfbeauty|eli-lilly|eliot-community-human-services|elite-dental-partners|elite-technology|elligint-health|ellipsislabs|elmi-power-gmbh|gaetan-data-gmbh|gaia-ag|galaxus|galaxy|galderma|galileo|galileo-financial-technologies|galileo-global-education|galliker|gallup|gam-investments|game-seven|gametime-united|gamma|garda-capital-partners|gardp|garmin|garner-health|gartner|gas-south|gate|gather-ai|gatik-ai|gauss-fusion|gavi|gaznat|glance|glasswall|glean|glencore|glia|glide|glimpse|global-accelerator|global-alliance-for-improved-nutrition|global-elite-empire-consultants|global-energy-alliance-for-people-and-planet-global-energy-alliance-llc|global-partners|globalfoundries|globalli|globant|globus|glossgenius|glossier|glovo|glydways|mu-group|muck-rack|mufg|multiplier|multiply|multiverse|multiverse-computing|munson-healthcare|muon-space|mural|muse-group|museum-of-science|mutable-tactics|muwave|mux|muxon|picc|picnic|picnic-delivery|pico|pictet-group|pie-insurance|piedmont-healthcare|piermont-bank|pierson-ferdinand|pigment|pika|pilatus|pilot-com|pilot-company|pimco|pindrop|pinduoduo|pine-park-health|pinecone|ping-an-insurance|ping-identity|pink-moon-studios|pinterest|pipe17|pipedrive|pit|pitch|pitchbook-data|pivot|pivot-a2e|pivotal|pix4d|pixellot|pocus|pod-network|podium|point|point-c|point-one-navigation|point72|poka-en|poke-and-wiggle|polar|polestar|polyai|polychain-capital|polygon-labs|polymarket|pomelo-care|pont-connects-e-k|pontera|poolside|popl|poppulo|portswigger|posh|poshmark|possible-finance|post|postfinance|posthog|postman|postscript|power-digital|powercell-sweden|rackner|radai|radar|radiance-technologies|radiant-nuclear|radiantsecurity|radical-numerics|radicle-health|radix-trading-experienced-job-board|raft-company-website|rai-institute|raiffeisen-switzerland|railway|raisin|raleys|rally-house|ramboll|ramp|range|rapidsos|rappi|rapyd|rapyuta-robotics|rasa|raycast|razorpay|razorpay-software-private-limited|shakepay|shardeum-foundation|sharebite|sharegate-en|shark-robotics|sharkninja|sharp-performance|shef|shein|shell|sherwin-williams|shield-ai|shields-health-solutions|shift|shift-technology|shift4|shift5|shiftsmart|shimizu-north-america|shipbob-inc|shippo|shipwell|shopfully|shopify|shopmy|showami|showpad|toast|together-ai|toka|tokamak-energy|tokyo-electron|toloka|tomofun-furbo-pet-camera|tomorrow-io|tomtom|tonal|too-good-to-go|top-closers|topcompare|topkey|topsort|topstep|toradex|torc-robotics|torq|toshiba-global-commerce-solutions-external|toss|total-wine|totalenergies|touchbistro|towardjobs|tower-peak-partners|tower-research-capital|toyota)(?:/|$))(?:pi[^/]*|ra[^/]*|to[^/]*|po[^/]*|ai[^/]*|gl[^/]*|sh[^/]*|ad[^/]*|ga[^/]*|el[^/]*|mu[^/]*))",
    "/:lang(en|de|fr|it)/company/:slug((?!(?:doc|docker|doconomy|docplanner|doctolib|doctrin|doherty-enterprises|doit|dollar-tree|dome-construction-corporation|domestika|domino-data-lab|dominos|domyn|done-berlin|donhauser-partner-mbb|donorbox|doodle|doordash|doppel|dormakaba|dorsia|doss|dott|double|doubleverify|dovetail|doximity|dr-dental|dr-reddys|dr-squatch|dragos|drata|drayer-physical-therapy|dream-security|dreamsports|dreem-health|drees-sommer|dressmann|drivenets|drivetrain|drivewealth|drixler-energietechnik-gmbh|dronamics|drone-defence|dronedeploy|dropbox|druva|drw|drw-montreal|dryft|emag|emarketer|embark|embrace|emcor-group|emergent-labs|emerging-travel-group|emerson|emerton|emirates-group|emnify|emory-healthcare|empa|empirical|employment-opportunities-at-buzzfeed-inc|emrich-wangler-herrmann-partg-mbb|eve-legal|ever|everbridge|evercore|everdriven|evergreen-nephrology|evergreen-services-group|everlane|everlaw|everlywell|everphone-gmbh|everpure|evervault|everway|every-io|everything-to-gain|evestia-clinical|evismart|evolution|evolutionary-scale|evolutioniq|evolve|evyd-technology|exa|exa-ai|exadel-inc-website|exante|excel-sports-management|excellent-go4|execujet|exeger|exein|exl|exodus-movement-inc|exotec|exotrail|exowatt|exp|expedia-group|explorium|express|express-oil-change|expressvpn|extend|extenteam-client-roles|extrahop|exxonmobil|fable|fabrion|factfinder|factor|factored|factorial|factorial-energy|factris|fae-beauty|faircom-new-york|faire|fairlife|fal|falconx|fam-brands|family-of-kidz|familywell|fanvue-com|far-ai|far-inspections|faraday-future|farfetch|farther|fashion-nova|fastino-labs|fastly|fathom-video|huawei|hub-international|hubbell|hubspot|hubstaff|hudl|hudson-river-trading|hug|hugeinc|huggingface|hugo-boss|humaans|human-agency|human-elevation-gmbh|human-interest|human-rights-watch|humana|humansignal|hume-ai|hungryroot|hunters|huntress|hut-8|lucid-bots|lucid-motors|lucid-software|lucidya|lucky-strike-entertainment|lufthansa-group|luma-ai|luma-health|luma-vision|lumana|lumapps|lumera|lumiform|lumimeds|luminance|lumindigital|lumos|lumos-identity|lunar|lunar-energy|luno|lush|lush-handmade-cosmetics|luxoft|luxor|luzia|phamily|phantom|pharmacann|pharo-management|phasecraft|phasev|philadelphia-phillies-baseball-operations|philip-morris-international|philips|philo|philz-coffee|phiture-gmbh|phizenix|phoebe-work|phoenix-contact|phonepe|phota-labs|photon|phylo|phyron|physical-intelligence|physicsx|richemont|ridgway-machines|rieter|rift|rigetti-computing|right-search|rigup|rillet|rimac-group|riot-games|ripple|rippling|rise8|riskified|risktec|rithum|rithum-linkedin-board|ritual|rival-technologies|rive|riverflex|riverlane|riverside-health-system|rivia|rivian|rivr|sk-hynix-america|sk-hynix-memory-solutions-america-inc|skadden|skechers|skeleton-technologies|skild-ai|skillshare|skin-clique|skin-laundry|sky-mavis|skydio|skyflow|skyguide|skylight|skylo-technologies|skyports|skyral|skyscanner|small-arms-survey|smallpdf|smarsh|smart-energy-link-ag|smartasset|smartbear|smarterdx|smartling|smartly|smartrent|smartsheet|smava-gmbh|smcp-north-america|smcp-north-america-us-canada|smic|smith-nephew|smithrx|sygnum-bank|sylogist|sylvera|symbolica-ai|symetra|symphogen|synack|synapse-medicine|syncron|syndica|syndigo|syneos-health|syner-g|syngenta|synhelion|synopsys|synthace|synthesia|synthesis-health|synthflow|sysco|system|systemiq|syz-group)(?:/|$))(?:do[^/]*|ev[^/]*|fa[^/]*|ph[^/]*|lu[^/]*|ri[^/]*|dr[^/]*|ex[^/]*|hu[^/]*|sy[^/]*|sk[^/]*|em[^/]*|sm[^/]*))",
    "/:lang(en|de|fr|it)/company/:slug((?!(?:1-800-contacts|100ms|10a-labs|10x-genomics|11-bit-studios|11x|12twenty|15five|1global|1komma5|1mind|1password|1x|ab-inbev-growth-group|abacum|abacus-insights|abb|abbott|abbvie|abbyy|abcellera|abercrombie|abilitypath|ably-uk|abm|abnormal|abound|abridge|absci|age-bold|age-solutions|agecare|agent|agentur-k-hnen|agicap|agile-robots|agilisys|agility-robotics|agiloft|agoda|agomab|agricultural-bank-of-china|agwest-farm-credit|ava-labs|avala|avaloq|avant|avantium|aven|avera|aviatrix|avid4|avidxchange-inc|avientus|avolta|avra|avride|cyacomb|cyberbit|cybereason|cyberhaven|cyberpeace-institute|cybersheath|cybret|cybrid|cycode|cye|cyera|cylib|cylus|cymulate|cyngn|cyolo|cyrebro|cyted|cytoreason|cyware|duatic|duck-duck-go|duckworks-millwork-solutions|dude-perfect|duetto-research|dufour-aerospace|dukascopy-bank|duke-health|duna|dune|dunnhumby|duolingo|dupont|durable|dusk|dust|dustyrobotics|dutch-bros-coffee|dutchie|ecal|ecential-robotics|echion-technologies|echo|echodyne-corp|eclinical-solutions|eclipse-trading|eclypsium|ecoatm-gazelle|ecodrop|ecolab|ecom-agroindustrial|ecomsky-gmbh|ecorobotix|ecovadis|euclid-power|eudia|euroairport-basel-mulhouse-freiburg|euronext|european-aquatics|european-athletics|european-broadcasting-union|european-free-trade-association|european-professional-club-rugby|eutelsat|federato|fedex|feedzai|fei|fels-trader-gmbh|fenaco|fenbi|fender|ferrero|ferring-pharmaceuticals|fetch|fetcherr|feverup|fueled|fuku|fulenwider-enterprises|fulfil-solutions|fullstory|function-health|fundingcircle|fundraise-up|funga-pbc|funnelfox|further-ai|fuse|fusion-worldwide|future-energy-ventures|fuze-health|gibson-robotics|gichd|gierth-partner-steuerkanzlei|giga-energy|gilead|gilion|gillig|ginkgo-bioworks-inc|gitbook|gitguardian|github|gitlab|givaudan|givecampus|givedirectly|givewell|guardio|guardz|guerrilla-games|guidehouse|guidelight-health|guidepoint|guidepoint-security|guidepost-montessori|guidewheel|guild|gunvor|gusto-inc|guthrie|guzman-y-gomez|hy-vee|hydrogenious-lohc-technologies|hydrosat|hyimpulse-technologies|hyperbolic|hyperexponential|hyperiondev|hypermasters|hypernative|hyperskill|hypersonica|hyphen-connect-limited|hypori|hystar|hyundai-motor|it-s-prodigy|itau-unibanco|itc-limited|itd-tech|iten|iterable|iteration-one-gmbh|iterative-health|itm-power|itm-radiopharma|its-logistics-llc|itu|itw|lnfusedinnovations|public|public-library-of-science|public-storage|publicis|pubmatic|pubnub|pulley|pulse|pulumi|puma|pump-co|pure|ruag|rubrik-job-board|ruby-tuesday|ruf-it-gmbh|ruggable|rula|rune-technologies|runpod-inc|runway|runwise|rural-king|rush-street-interactive|russell-reynolds-associates|ti-and-m|tia|tidio|tiger|tigergraph|tight|tiktok|tilt|tilthq|tines|tint|tinybird|tipalti|tirlan|titan|titan-ai|tubi|tubulis|tucows|tucows-inc|tudor-investment-corporation|tulip-interfaces|tune-insight|turbineone|turbotenant|turing|turner-and-townsend|turner-construction|turnkey|turquoise-health|x-ai|x-bow-systems|xaira-therapeutics|xantium|xapo-bank|xbowcareers|xcmg|xdof|xealth|xendit|xenon|xensam|xero|xiaomi|xion|xm|xocean|xometry|xometry-europe|xpeng|xtx-markets|xund|y-soft|yahoo|ycombinator|yelp|yepoda|yes-bank|yes-energy|yext|yipitdata|yipitdata-alternative|ylopo|yondr|yotpo|you-com|yougov|yousician|ypsomed|yttp|yubico|yugabytedb|yum-china|yuno)(?:/|$))(?:du[^/]*|hy[^/]*|eu[^/]*|fu[^/]*|ec[^/]*|tu[^/]*|y[^/]*|gi[^/]*|x[^/]*|gu[^/]*|cy[^/]*|ru[^/]*|ag[^/]*|it[^/]*|ab[^/]*|fe[^/]*|1[^/]*|pu[^/]*|ti[^/]*|av[^/]*|ln[^/]*))",
    "/:lang(en|de|fr|it)/company/:slug((?!(?:30mpc|3b-pharmaceuticals|3cloud|3m|3red-partners|a-lign-external|a-team|a-thinking-ape|ae-studio|aechelon-technology|aecom|aeo|aerones|aerospacelab|aerospike|aerovect|aeva-inc|aevex|affect|affinidi|affirm|affirmedrx-pbc|afresh|afry|akasa|aker-systems|akeyless|akido|akko|akkuro|aktos|akuity|akuna-capital|ao-garcia-agency|ao-globe-life|ao-shearman|aqemia|aqr|aquatic-capital-management|azuki|azurity-pharmaceuticals-india|azurity-pharmaceuticals-us|b-b-immo-gmbh|b-g-projects-gmbh|b-riley-securities|by-the-bay-health|bybit|byd-north-america|byggmax|bystronic|bytedance|cb-insights|cbh-bank|cbre-global-workplace-solutions-data-center-solutions|cm|cma-cgm|cmblu-energy-ag|cmr-surgical|ctc-lateral-website-linkedin|cti|ctl-gmbh|cttq-pharma|d-e-shaw-group|d-matrix|d-orbit|db-e-c-o-north-america|dbs|dbt-capital|dbt-labs|dhg-deutsche-h-rakustik-gmbh|dhi-group-inc|dhl|dycom-industries|dyna-robotics|dynamis-inc|dynamite-games|dyopath|eam-l-eveil-du-scarabee|earnin|easygo|easyjet|easymile|easypost|easyship|eaton|eawag|editasmedicine|edmentum|edmond-de-rothschild|edo|educate|edwards-lifesciences|effectual|efficient-computer|efg-international|egc-energie-und-geb-udetechnik-gmbh|egis-group|egon-zehnder|egym|eiffage|eigen-labs|eight-advisory|eikon-therapeutics|einride|epfl|epic-brokers|epic-games|epic-kids-inc|epirus|episode-six|episode-six-us|eqt-corporation|eqt-group|equal-experts|equal1|equinox|eqvilent|eraneos|ergon|ericsson|erl-pflege-gmbh|ernest|ernst-hasselbring-gmbh-co-kg|g-h-isolierung|g-in-gmbh|g-n-gruppe|g-p|g-research|hm-group|hmg-systems-engineering-gmbh|hmnc-brain-health|icapital|icbc|ice-miller|iceye|icici-bank|icon|iconiq|icrc|icsd|id-me|id-quantique|ideo|ideogram|idiap|ion-group|ionity|ionos-de|ionos-se|ionq|iovance-biotherapeutics|iowa-cannabis-company|isaac|isar-aerospace|isardsat|iso|isomorphic-labs|ispot|iss-stoxx|lg|lg-energy-solution-arizona|lgt-group|llamaindex|lloyds|lloyds-register|llr-partners|lyceum|lydech-thermal-acoustic-solutions-tas|lyft|lyko|lynx-analytics|lyra-health|lysa|mcadams|mccullough-robertson|mcdonalds|mcg-health|mckinsey|mcmaster-carr|mco|mr-apple|mr-apple-careers-site|mrbeast|mrbeast-contract-jobs|mt-bank|mthree-recruiting-portal|mtn-group|mvz-medizinische-labore-dessau-kassel-gmbh|myers-holum|myfitnesspal|myfunded-futures|mynt|myriad360|myrspoven|myshell|mystenlabs|myvillage|psi|psibufet|psiquantum|pst-professional-support-technologies-gmbh|rgt-geb-udemanagement-und-technologie-gmbh|rhenus|rho|rhombus-power-inc|rhythm-software-inc|rv-tech-gmbh|rvi-planning-landscape-architecture|slash-financial|slate|slaughter-and-may|sleeper|slice|slingshot-aerospace|sluhn|squarepoint-capital|squarespace|squint-ai|squircle-it-consulting-services|typeface|typeform|typewise|tyson-foods|tytan-technologies)(?:/|$))(?:my[^/]*|ae[^/]*|ly[^/]*|io[^/]*|ea[^/]*|sl[^/]*|ed[^/]*|ep[^/]*|mc[^/]*|cb[^/]*|er[^/]*|sq[^/]*|ak[^/]*|by[^/]*|ps[^/]*|dy[^/]*|is[^/]*|eg[^/]*|eq[^/]*|ic[^/]*|az[^/]*|ei[^/]*|mr[^/]*|ty[^/]*|hm[^/]*|ct[^/]*|b-[^/]*|g-[^/]*|af[^/]*|rh[^/]*|rv[^/]*|3[^/]*|a(?!(?:-|1|2|b|c|d|e|f|g|h|i|j|k|l|m|n|o|p|q|r|s|t|u|v|w|x|y|z))[^/]*|db[^/]*|dh[^/]*|ef[^/]*|ll[^/]*|e(?!(?:-|2|a|b|c|d|e|f|g|h|i|k|l|m|n|o|p|q|r|s|t|u|v|x|y|z))[^/]*|ao[^/]*|mt[^/]*|mv[^/]*|rg[^/]*|b(?!(?:-|1|a|b|c|d|e|g|h|i|j|k|l|m|n|o|p|r|s|t|u|v|w|y))[^/]*|cm[^/]*|lg[^/]*|m(?!(?:-|1|3|9|a|b|c|d|e|g|h|i|k|l|n|o|p|q|r|s|t|u|v|y))[^/]*|id[^/]*|a-[^/]*|aq[^/]*|s(?!(?:-|a|b|c|d|e|f|h|i|k|l|m|n|o|p|q|r|s|t|u|v|w|y))[^/]*|d-[^/]*))",
    "/:lang(en|de|fr|it)/company/:slug((?!(?:2020-companies|2k|66degrees|6sense|7shifts|8am|8fleet-inc|a11|a16z|ahti-interiors|ajax|ajax-systems|away|awin|awl|aypa-power|ayuda-en-accion|bb-energy|bbpos-limited|bcg|bcge|bcs|bcv|bd|bda|bdo|bge-inc|bgis|bhhc|bhp|bjak|bjs-wholesale-club|bmc|bmo|bmw|bnp-paribas|bny|bswift|bswift-india|btg-pactual|btig|bwe-energiesysteme-gmbh-co-kg|c-and-a|c6-bank|cd-projekt-red|cdds-ag|cfmoto|cfo-insights|cgi|cgs-group|csb-bank|csem|csl|csob|css|cvent|cvs-health|cvx-ventures|d2-technical-services|d2l|d2x|d3|dkatalis|dlh|dlr-group|dmg-events|dna-script|dnata|dndi|dnv|dphi-space|dsm-firmenich|dss-plus|dsv|dv-trading|dv01|dxc-technology|e-s-fitness|e-star-trading-gmbh|ebanx|ebay|ehl-group|ekimetrics|ekkiden|ekn-engineering|ey|eye-security|ezcater-inc|f-e-gmbh|f-schumacher-co|f2-ai|f2g|ffg-finanzcheck-finanzportale-gmbh|fgs-global|fjallraven|fmol-health|fp-robotics|fti-consulting|gbfoods|ghost|ghx|gpa|gpm-investments|gptzero|gs-retail|gsa-capital|gsk|gymshark|h-company|h-moser-cie|h2-powercell-gmbh|h3-dynamics|h3x-technologies|hca-healthcare|hcltech|hdfc-bank|hhm-hotels|hp|hp-hood|hp-inc|hp-iq|hpe|hpr|hqs-quantum-simulations|hr-block|hr-werkstatt-gmbh|httpie|hw-gr-nderkapital-gmbh|iamfluidics|ians|iata|ibex-medical-analytics|ibm|ieq-capital|iflytek|ifood|ifrc|ift|iherb|ihg|ikea-cz|ilg-au-enwerbung-gmbh|ilitch|illumio|iproov|ipsy|ipx-power-usa-llc|iq|iqm|iqvia|irisity|ixl-learning|ixm|l-wen-apotheke|l3harris-technologies|lcmc-health|lmarena|ltimindtree|ltk-usa|ltse|m-booth|m9-solutions|mb-f|mbc|mdclone|mgt-insurance|mks-pamp|mlb-job-board-only|mphasis|mq-referrals-only|p2p-org|pdf-net|pdt-partners|pdw|pjt-partners|pmaconsultants|pmg|pt-solutions|ptc|rf-smart|rwa-xyz|rxr|rxsense|rzr-global-inc|s-l-connect-gmbh|sbb|sbm-management-services|sfcompute|sfox|sfs-inc|srg-ssr|srs-acquiom|ssm-health|sveriges-radio|svetness|svix|t-mobile|t-mobile-cz|t-rowe-price|t1-energy|tbhc-delivers|td|td-international|td-synnex|tnt-ventures-gmbh|tpr-education-llc|tsmc|tsmg|tx-group)(?:/|$))(?:il[^/]*|c(?!(?:-|1|3|6|a|b|d|e|f|g|h|i|l|m|o|r|s|t|u|v|x|y))[^/]*|ek[^/]*|ff[^/]*|d(?!(?:-|2|3|a|b|c|e|h|i|k|l|m|n|o|p|r|s|u|v|x|y))[^/]*|i(?!(?:2|a|b|c|d|e|f|h|k|l|m|n|o|p|q|r|s|t|u|v|x))[^/]*|t-[^/]*|e-[^/]*|hp[^/]*|p(?!(?:2|a|d|e|f|h|i|j|l|m|n|o|p|r|s|t|u|v|w|y))[^/]*|bw[^/]*|cv[^/]*|d2[^/]*|h(?!(?:-|2|3|a|c|d|e|h|i|m|o|p|q|r|s|t|u|w|y))[^/]*|ip[^/]*|td[^/]*|t(?!(?:-|1|a|b|d|e|h|i|j|n|o|p|r|s|u|w|x|y|z))[^/]*|h3[^/]*|sv[^/]*|gp[^/]*|sb[^/]*|ay[^/]*|cs[^/]*|ds[^/]*|hr[^/]*|ib[^/]*|dn[^/]*|f(?!(?:-|2|a|e|f|g|h|i|j|l|m|n|o|p|r|t|u))[^/]*|gs[^/]*|l(?!(?:-|3|a|c|e|g|i|l|m|n|o|s|t|u|v|x|y))[^/]*|f-[^/]*|lt[^/]*|pd[^/]*|bb[^/]*|bj[^/]*|g(?!(?:-|2|a|b|e|h|i|l|o|p|r|s|u|w|x|y))[^/]*|hq[^/]*|(?![01236789abcdefghijklmnopqrstuvwxyz])[^/]+|cd[^/]*|hc[^/]*|hw[^/]*|if[^/]*|sf[^/]*|h-[^/]*|ia[^/]*|l3[^/]*|r(?!(?:a|b|e|f|g|h|i|o|s|t|u|v|w|x|z))[^/]*|bs[^/]*|cf[^/]*|sr[^/]*|ml[^/]*|pm[^/]*|aj[^/]*|h2[^/]*|mq[^/]*|tn[^/]*|tp[^/]*|2[^/]*|bc[^/]*|bt[^/]*|ix[^/]*|pt[^/]*|s-[^/]*|6[^/]*|bn[^/]*|dv[^/]*|ey[^/]*|ah[^/]*|dx[^/]*|ft[^/]*|l-[^/]*|rz[^/]*|8[^/]*|aw[^/]*|cg[^/]*|dl[^/]*|mg[^/]*|tb[^/]*|bg[^/]*|iq[^/]*|m9[^/]*|pj[^/]*|bm[^/]*|ez[^/]*|fm[^/]*|fp[^/]*|ie[^/]*|lc[^/]*|rx[^/]*|bd[^/]*|dm[^/]*|dp[^/]*|eb[^/]*|fg[^/]*|fj[^/]*|hh[^/]*|ss[^/]*|eh[^/]*|f2[^/]*|gh[^/]*|hd[^/]*|ih[^/]*|t1[^/]*|ts[^/]*|a1[^/]*|bh[^/]*|dk[^/]*|gy[^/]*|mb[^/]*|mk[^/]*|rf[^/]*|tx[^/]*|c-[^/]*|c6[^/]*|gb[^/]*|ik[^/]*|ir[^/]*|lm[^/]*|m-[^/]*|md[^/]*|mp[^/]*|p2[^/]*|rw[^/]*|7[^/]*|ht[^/]*|d3[^/]*))",
    "/:lang(en|de|fr|it)/company/:slug((?!(?:0x|a24|b12|bkw|bvnk|c12|c3-ai|cx2|dcaf|e2b|eei|eos|fhgr|fnz|g2it|gwi|gxo|hsbc|i2cat|iucn|ivalua|lseg|lvmh|lxt|m1|m3|mhi|mntn|pnc|ppro|pvh|pwc|pylon|rbc|rsm|rtx|sdsc|tjx|tzdc)(?:/|$))(?:iv[^/]*|c3[^/]*|i2[^/]*|py[^/]*|bv[^/]*|dc[^/]*|fh[^/]*|g2[^/]*|hs[^/]*|iu[^/]*|ls[^/]*|lv[^/]*|mn[^/]*|pp[^/]*|sd[^/]*|tz[^/]*|a2[^/]*|b1[^/]*|bk[^/]*|c1[^/]*|cx[^/]*|e2[^/]*|ee[^/]*|eo[^/]*|fn[^/]*|gw[^/]*|gx[^/]*|lx[^/]*|mh[^/]*|pn[^/]*|pv[^/]*|pw[^/]*|rb[^/]*|rs[^/]*|rt[^/]*|tj[^/]*|m1[^/]*|m3[^/]*|0[^/]*))",
    // END GENERATED COMPANY MISS MATCHERS
    // BEGIN GENERATED WATCHLIST USER EXCLUSIONS
    // Reserved application/user prefixes must bypass the generic
    // watchlist boundary before Proxy so explicit app routes win.
    "/:lang(en|de|fr|it)/:userSlug((?!(?:about|abuse|account|admin|administrator|anonymous|api|app|billing|blog|careers|check-email|companies|company|contact|dashboard|demo|docs|example|explore|false|faq|feed|forgot-password|help|home|how-we-index|info|job-seek|jobs|jobseek|legal|license|login|logout|mailer-daemon|mod|moderator|my-jobs|news|no-reply|noreply|null|postmaster|pricing|privacy|privacy-policy|profile|progress|register|reset-password|root|saved|search|security|settings|sign-in|sign-up|signin|signup|staff|status|support|system|team|terms|test|true|undefined|unknown|verify-email|watchlists|webmaster)(?:/|$))[^/]+)/:watchlistSlug",
    // END GENERATED WATCHLIST USER EXCLUSIONS
    {
      source: "/:lang(en|de|fr|it)/explore",
      has: [
        {
          type: "header",
          key: "next-action",
          value: ".+",
        },
      ],
    },
    {
      source: "/:lang(en|de|fr|it)/watchlists",
      has: [{ type: "header", key: "next-action", value: ".+" }],
    },
    "/:lang(en|de|fr|it)/watchlists/:watchlistId([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89aAbB][0-9a-fA-F]{3}-[0-9a-fA-F]{12})",
    {
      source: "/:lang(en|de|fr|it)/company/:slug",
      has: [{ type: "header", key: "next-action", value: ".+" }],
    },
    {
      source: "/:lang(en|de|fr|it)/explore",
      has: [
        { type: "header", key: "accept", value: ".*text/html.*" },
      ],
      missing: [
        { type: "header", key: "rsc", value: "1" },
        { type: "header", key: "next-action" },
      ],
    },
    "/:lang(en|de|fr|it)/:probe(adminer|cgi-bin|phpmyadmin|wp-admin|wp-content|wp-includes|wp-json|xmlrpc|\\.env|\\.git)/:path*",
    "/:lang(en|de|fr|it)/:user/:probe(\\.env|\\.git)/:path*",
  ],
};
