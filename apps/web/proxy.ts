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

  const actionSurface = publicReadActionSurface(request);
  if (actionSurface) {
    const rateLimited = await publicReadRateLimitResponse(
      request,
      actionSurface,
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
    "/:lang(en|de|fr|it)/company/:slug((?!(?:cabify|cabrillo-hospice|caceis|caddell-construction|cadence-solutions|cadwell|caffeine-ai|cailabs|cais|caisse-epargne-cossonay|calendly|california-academy-of-sciences|california-autism-center|callista|callsign|calm-com|calyxo|camber|cambium|cambly|cambridge-aerospace|cambridge-mobile-telematics|camp|campfire|canals|candidly|cannabis-glass|cannondesign|canonical|canopy|cantina|canto|canva|capco|cape|capintel|capital-farm-credit|capital-group|capital-on-tap|capital-one|capital-technology-group|capstone|capsule|caran-dache|carbon-direct|carbonchain|carbonfuture|care-access|care-com|caredx-inc|career-team|careers-at-eucalyptus|careers-at-libra-group|careers-at-nlc|careers-at-tide|carefeed|carepay-international|cargill|cargomatic|cargurus|caribou|caribou-financial|carmasec-gmbh-co-kg|carniceria-la-caba-a|caronsale|carrot|carta|cartier|cartwheel|cartwheelcare|carv|casechek|caseguard|casetify|cast-ai|castelion|catawiki|category-labs|catena-clearing|caterpillar|catl|cato-networks|cattaneo-zanetto-pomposo-co|causalens|caylent|editasmedicine|edmentum|edmond-de-rothschild|edo|educate|edwards-lifesciences|h-company|h-moser-cie|h2-powercell-gmbh|h3-dynamics|h3x-technologies|haast|habitat-health|hack-the-box|hackerone|hackerrank-careers|hadean|hadrian|haier-group|hailo|haize-labs|hala|halcyon|haleon|handelsbanken|hang|hanmi-pharm|hanwha-renewables|happy-money|happyrobot|harbinger-motors-inc|harbor|harmattan-ai|harmonic|harmony|harness|harper-group|harris-associates|harrison-ai|harrow-inc|harry-s|harvey|hasbro|hatch|hauskrankenpflege-stolley-gmbh|havas|haven-interactive-studios|havenhub|havocai|hawk|hawkeye360|hawthorne-machinery-co|hays|haystack-news|hazel-health|hca-healthcare|hcltech|hdfc-bank|headlands-research|headout-li|headspace|headway|healthcare-services-group|healtheconnections|healthverity|healthy-io|hear-com-in|hear-com-us|heart-aerospace|heartflow|hebbia|hedra|hedvig|heidrick-struggles|heim-marketing|heizm-ller-gmbh|helium-10|hello-heart|hellofresh|helsana|helsing|helvetia-baloise|hemab-therapeutics|hemnet|herald|here|hermes|hermeus|heron-power|hex-technologies|hexagon-bio|hexagon-robotics|hexagone-ai|hexarmor|hexaware|heygen|heylogin-gmbh|heytea|hiber|hibu|hidden-events|hidden-level|higgsfield-ai|highdive|higher-logic|highnote|hightouch|highview-power|hiive|hike-medical|hill-house-home|hillel-international|hilton|hippo-insurance|hippocratic-ai|hiry-agency|hit-haus-industrietechnik-gmbh|hitachi|hitachi-energy|hive|hive-financial-systems|hivemq|hivestack|hiya|hmg-systems-engineering-gmbh|hmnc-brain-health|hoist-finance|holcim|hologic|home-depot|home-instead|homebase|homelight|hometap|homevision|homeward|honda|hone-health|honeycomb-io|honeywell|honor|hook|hookmusic|hootsuite|hopper|hoppr|hopskipdrive|horace-mann|horizon-industries|hoshii|hospital-del-mar|hospital-sant-joan-de-deu|houseaccount|housecall-pro|housemarque|housinganywhere-group|hover|howden|hoyoverse|hp|hp-hood|hp-iq|hpe|hpr|hqs-quantum-simulations|hr-werkstatt-gmbh|hsbc|httpie|huawei|hubspot|hubstaff|hudl|hudson-river-trading|hug|hugeinc|huggingface|humaans|human-agency|human-elevation-gmbh|human-interest|human-rights-watch|humansignal|hume-ai|hungryroot|hunters|huntress|hut-8|hw-gr-nderkapital-gmbh|hydrogenious-lohc-technologies|hydrosat|hyimpulse-technologies|hyperbolic|hyperexponential|hyperiondev|hypermasters|hypernative|hyperskill|hypersonica|hyphen-connect-limited|hypori|hystar|hyundai-motor)(?:/|$))(?:h[^/]*|ca[^/]*|ed[^/]*))",
    "/:lang(en|de|fr|it)/company/:slug((?!(?:d-e-shaw-group|d-matrix|d-orbit|n-ix|n1|n26|n8n|nabis|nabla|nachhilfeunterricht|namespace|nanonets|nansen|nansen-ai|nanuq-gmbh|narvar|nas-company|nash|natera|national-design-build-services|national-life-insurance-company|national-science-center-kharkiv-institute-of-physics-and-technology|nationwide|naturalmotion|nature-s-bakery|naughty-dog|nauticus-robotics|nava-pbc|navan|naver-vietnam|navier|navvis|nawah|nayya|nccgroup|near-space-labs|nearfield-instruments|nearform|nebius|nec-laboratories|neighbor|neighbors-bank|neko-health|neo4j|neon|neon-health|neon-pagamentos|neoris|neptune-ai|nerdwallet|nerdy|neros-technologies|nestai|nestle|netapp|netdocuments|netease-games|netflix|netjets|netlify|netskope|netwealth|neuehealth|neura-robotics|neuraflash-part-of-accenture|neural-concept|neural-frames|neuralink|new-era-technology|new-leaf-energy-inc|new-relic|new-york-city-economic-development-corporation|new-york-iso|newcleo|newco-communications|newcore|newfront|newlab-careers|newlimit|newsbreak|newsela|newstel-gmbh|newsweek|nex|nexgen-cloud|nexos|next|next-insurance|next-sense|nexthink|nexthop-ai|nexwafe|neysa-networks-careers-page|nfon|ng-cash|ngrok-inc|nhl|nhoa|nhs|nice|nicoll-curtin|nielseniq|nift|nike|nikon|nimble-gravity|ninjatrader|nintendo|nira-energy|nissan|nitricity|nivoda|nmi|no-limits-academy-b-v|noise-labs|nokia|noma-security|nomad|nomagic|nomiso|nonprofit-finance-fund|nord-ostsee-automobile-se-co-kg|nord-ostsee-sparkasse|nord-security|nordason|nordstrom|norm-ai|normative|norsepower|norsk-titanium|nortal|north-america|northbeam|northmarq|northmill|northrop-grumman|northwest-pipe-fittings|northwood-space|notabene|notability|notable|nothing|notion|noto|notraffic|nourish|nova-credit|nova-founders-capital|novartis|novatron-fusion-group|novel|novo|novo-nordisk|novocure|novogene|novu|noxon|noxtua|nozomi-networks|npr|nscale|nt-concepts|ntt-data-europe-latam-branch-in-usa-inc|nu-quantum|nubank|numa|numeral|numerix|numeus|nunu-ai|nuro|nutanix|nutrafol|nutrisense|nvidia|nvision-quantum|nviso|nw|nxp|nyobolt|v7labs-com|vacasa|vaco-llc|vail-health-hospital|vale|valera-health|valiant|validio|valo-health|valon|valon-labs|valonvm|valov-bau-gmbh|valtech|van-metre-companies|vanilla|vanna-health|vannevar|vanta|vantage|vapi|varda-space-industries|varicent|varo-bank|vast|vastspace|vatic-labs|vaudoise|vaxcyte|vay|vayyar|vcluster|vectara|vector|vectra|veeam-software|veepee|veesion|veeva|vega|velir|venn|veo|veo-corporate-careers|vera-institute-of-justice|vera-therapeutics-inc|veracode|veracyte|verantos|vercel|verda|verein-f-r-erziehungshilfen|veriff|verifone|verily|verimatrix|verisign|verista-inc|veritas|verity|verkada|verkor|verra-mobility|versaterm|versatile|verse|versicherungsagentur-rathje-gmbh-co-kg|verstela|very-good-security|vestiaire-collective|vestmark-inc|vestwell|vetcove|veza-technologies-inc|vgw|via|viam|vibe|vicarius|viggle|viking-global-investors|viktor|vinted|vir-biotechnology|viral-nation-inc|virgin-atlantic|virtahealth|virtru|virtu-financial|virtual-preparatory-academy-of-florida|visa|visana|visasq-coleman|vise|visier-solutions-inc|vista-global|visual-concepts|visus-one-holding-gmbh|vitable-health|vitalize|vitestro|vitol|vivid-money|vivo-defence-services|vodafone|voi|voladynamics|voldex|voliro|volkswagen-group|volocopter|volta-medical|volumental|vonage|vontobel|vorto|vow|vox-ai|vox-media-llc|voyager-technologies-inc|vsco|vtex|vts|vulcan-elements|vulncheck|vultr|vyntra)(?:/|$))(?:n[^/]*|v[^/]*|d-[^/]*))",
    "/:lang(en|de|fr|it)/company/:slug((?!(?:1-800-contacts|100ms|10a-labs|10x-genomics|11-bit-studios|11x|12twenty|15five|1global|1komma5|1mind|1password|1x|in-the-pocket|in3|inc-innovation-center-gmbh|inceptive|incepto-medical|incharge-energy|incident|incident-iq|includedhealth|incode|incyte|indeed|indent|index-ventures|inditex-tech|indosuez-wealth-management|inductive-bio|industrial-electric-manufacturing|industrious-labs|ineffable-intelligence|infarm|infineon|infinidat|infinite-machine|infinite-orbits|infinitus-systems|infinity-constellation|infisical|inflection-ai|influxdata|infosys|infuse|ing|ingenieurb-ro-dr-petry-partner-mbb|inhome-therapy|inizio|inizio-ignite|inizio-ignite-putnam|inizio-ignite-research-partnership|inizio-ignite-stem|inizio-ignite-vynamic|inizio-medical|inkhouse|inkind|inmobi|innatera|inngest|innok-robotics|innotec-gmbh|innovafeed|innoviz-technologies|insify|insightsoftware|insitro|insomniac-games|inspira-education|inspiration-commerce-group|inspire-medical-systems-inc|inspiren|instabase|instacart|instawork|instead|institut-f-r-rehabilitation|instories|instride|instructure|instrumental|insurello|insurely|insurify|insurtech-insights|integrafec|integral-services-gmbh|integrated-specialty-coverages-llc|integrity-rehab-group|intel|intelligent-energy|intellum-inc|interactive-brokers|interactive-brokers-external|intercom|interface-ai|intermex-wire-transfer|internal-job-board|interplay|interstellar-lab|intertek|interview-engineering|interview-kickstart|interworks|intesa-sanpaolo|intradiem|intrinsic|intro|intuit|intus|inuru|inversion|invert|invesco|investors-community-bank|invgate|invisible-technologies|invivyd|inworld-ai|o2-cz|oak|oaknorth|oboe|obsidian|obsidian-security|obviant|ocado|ocrolus-inc|octa-steuerberater-ralf-sommer|octave|octopus-energy|octopus-robots|octus|oddball|odeko|odle-sales|odoo|odyssey|odysseyhotelgroup|oerlikon|offerup|offerzen|office-hours|officespace-software|offshore-launch|ofi|ogilvy|ogilvy-australia|ogilvy-social-lab|ogt|oh-io|ohalo|ohpen|oklo|okta|okx|olam-agri|olam-group|olema-oncology|olipop|oliv-ai|oliver-agency|oliver-wyman|olly|olsson|olympus-property|omada-health|omnicom|omnicom-media-group-netherlands-omg|omnilex|omniscient|on-board-experiential|on-energy|on-running|onbe|onboard|onboardmeetings|one-acre-fund|one-acre-fund-kenya|oneapp|onebrief|onecrew|oneimaging|oneleet|onit-inc|onoshealth|onrobot|ontic|ontra|onward-medical|onx|ooma|oosto|opal|open-cosmos|open-farm|openai|opendoor|openeye|opengov|openly|openrouter|opensea|opensesame|opentable|openwork|openworks|openx|ophelia|ophelos|oplabs|oportun|oppfi|optibus|optics11|optimal-care|optimal-dynamics|optiver|optiver-private-jobs|optiver-trading-academy|opto-investments|opus|oq-technology|oracle|orakl-oncology|orange-group|orange-quantum-systems|orasio|orb|orbem|orbit|orbital|orca|orca-computing|orca-security|orchard|orchard-therapeutics|orderchamp|orderly|oreilly-auto-parts|origis-services-utility-solar-o-m|oriola|orion-confectionery|orion-group|orion-innovation-naukri|orior|orkes|ororatech|oros-energy-europe|orqa|ortho-neo|osano|osapiens|osaro|osbra-einhaus-gmbh|oscar-health|oscilar|oshi-health|osmo|oso|ost|otter|otter-ai|otto-aerospace|our-group|outfit7|output|outreach|outrider|outschool|outset-medical|outtake|overland-ai|overstory|overtime|overwolf|ovhcloud|oviva|owkin|ox-security|oxford-instruments|oxford-ionics|oxford-nanopore-technologies|oxford-photovoltaics|oxford-quantum-circuits|oxio|oxyle|oyster)(?:/|$))(?:o[^/]*|in[^/]*|1[^/]*))",
    "/:lang(en|de|fr|it)/company/:slug((?!(?:co-star|coalesce|coalition|coast|cobalt|cobalt-service-partners|cobase|cobblestone-energy-dubai-uae|cobot|cobre|coca-cola-hbc|cockroach-labs|codal|codat|code3|codepath|coder|coefficient|cofco-international|cofertility|cofra-holding|cognition|cognizant|cogstate|cohere|cohere-health|cohort|coinbase|coindcx|coinswitch|cointracker|colab-software|colgate-palmolive|collective|collibra|colonist|color|column|comand-ai|comarch|comet|comity|commerceiq|commercetools|common-thread-collective|commonroom|commonwealth-bank|commvault|comparis|compass-group|compass-pathways|compeer-financial|compliancy-group-llc|complyadvantage|compound|compunet-inc|comstock|comulate|concentric|conductor|conductor-ai|conduit|conextivity|confluence|confluent|conga|connected-careers-page|connectwise|conocophillips|consensys|console|constant-contact|constellr|construction-resources|constructor-tech|consumer-edge|contentful|contentsquare|contentstack|context-labs|contextual-ai|continental|continue|convene|convera|conversion|convex-dev|convious|cook-systems|cookunity|coop|coop-sverige|copenhagen-atomics|copper|copper-co|cordance|core-power|coreflow|coreweave|corintis|corporate-synergies|corpower-ocean|correlation-one-expert-network|cortex|cortica|cortica-neurodevelopmental|corvascular|corvias-corporate-services-llc|cosmax|cosmos|cosuno|cote-vegas|cotulla-education|coty|couchbase-inc|counsel-health|counterpart|coupa-software-inc|coupang|coursera|covar|cove|covera-health|coverdash|covergenius|k-health|k-id|k-water|k2-space|kaedim|kagi|kaib-galldiks-partner-mbb|kaiko-ai|kairos-power|kaiser-permanente|kaizen-labs|kalepa|kalles-group|kalshi|kandou-ai|kapa-ai|kapitus|karat|karbon|kargo|karya|kasa|katapult-amsterdam-b-v|kaufland|kaufland-cz|kaumarie|kavak|kayak|kb-cz|kbra|kearney|keeper-security|keepit|kela-technologies|keller-postman|kelluu|kemaro|kentik|kepler-communications|kepler-group|kering|kernal-biologics-inc|kernel|kesko|kestra|ketryx|kettle|keycard-labs|keyloop|keystone|kfc|khan-academy|ki-insurance|kiavi|kickstarter-pbc|kiddom|kilocode|kimberly-clark|kin|kinder-s|kindred|kineis|kinexon|kinexus-group|king|kipu-quantum|kira|kit|kitchenpark|kittl|kiutra|kiwi|kizen|kjp-steuerberater-gbr|kla|klarna|klaus-meyer-gmbh-co-kg|klaviyo|klaviyo-campus|klearly|kluvo|knoetic|knot|knowbe4|knowlix|known|knownwell|knupfer-lebensmittel-gmbh|koalafi|kobold-metals|kobold-metals-drc|kobold-metals-zambia|koch|koddi|kodiak|kodiak-solutions|kodland|kognic|kojima-productions|koley-jessen-p-c-l-l-o|koleyjessen|koller-lode|kolmac-integrated-behavioral-health|kolmar-group|kolmar-korea|kombo|komodo-health|kompuestos|kong|konovo|konux|korean-air|korn-ferry|kotak-mahindra-bank|kpmg|kr-ger-consulting-gmbh|krafton-americas|krafton-montr-al-studio|kraken|kraken-energy|krea|krea-ai|kroger|kroll|kronans-apotek|kronos-research|kt|kubra-gmbh-industrie-und-kunststofftechnik|kuda|kudelski|kuehne-nagel|kulfi-collective|kunai|kura-oncology|kustomer|kyo|kyowa-kirin-north-america|sc-johnson|scalable-capital|scalapay|scale-ai|scaled-cognition|scalemath|scaleops|scandic-hotels|scandit|scenic-biotech|scewo|schaffmann-consultants-executive-search|schindler|schmelzle-partner|schollmaier-schollmaier-partmbb-steuerberatungsgesellschaft|schonfeld|schonlau-werke-geseke|schr-dinger|schroders|schwarzman-animal-medical-center|sciforium|scopely|scor|scorpion-enterprises-llc|scotch|scout-ai|scout-motors|scout24|screenpoint-medical|scribe|scw-systems)(?:/|$))(?:co[^/]*|k[^/]*|sc[^/]*))",
    "/:lang(en|de|fr|it)/company/:slug((?!(?:alacris|alamar-biosciences|alan|alarm-com|alaska-airlines|albert-cz|albert-mackenzie-llp|albertsons-companies|alchemy|alcon|alector|aledade|alembic|alentis-therapeutics|aleph|aleph-alpha|alertmedia|alexander-shunnarah-trial-attorneys|alexandra-lozano-immigration-law-pllc|algo1|algolia|algorized|alibaba|alice-and-bob|alight|alika-personal-gmbh|alivedx|alixpartners|all-space|allarahealth|allbirds|allegro|allen-control-systems|alliance|alliance-defending-freedom|allianz-suisse|allica-bank|allied-universal|allium|alloy|alloy-ai|alloyenterprises|allps|allspice|alltrails|alluxio|alma|alo|alpaca|alpenlabs|alpha|alpha-financial-markets-consulting|alpha-fmc-insurance-consulting|alphagrep-securities|alphalion|alphasense|alphasense-india|alpian|alpine-investors|alt|alta-ares|altana|alten-technology-usa|altilium-metals|altium|altos-labs|altris|altscore|alu|alumni-ventures|alvean|alvotech|alvys|alx-africa|re-build-manufacturing|reach|reach3-insights|reactivate|read-ai|real|real-chemistry|real-time-innovations|rebag|rebtel|recharge|recidiviz|reckitt|recora-inc|recorded-future|recraft|recruitaero|recruitis|rectangle-health|recursion|red-6|red-bull|red-cell-partners|red-hat|red-lobster|reddit|redis|redpanda-data|redpin|redstone-residential|redwood-materials|redwood-software|reema-health|reface|reflect-orbital|reflection-ai|reflex-aerospace|reformation|reframesystems|regrello|regscale|relai|relativity-space|relay|relay-graduate-school-of-education|relay-payments|relay-therapeutics|relex-solutions|reliable-robotics|reliance-industries|reltio|relyance-ai|remedio|remedyproductstudio|remora|remotasks|remote|remote-people|renaissance-fusion|renaissance-learning-north-america|renault-group|render|rent-the-runway|reorbit|replit|replo|reply|reprisk|resend|resident|resilience|resolve-to-save-lives|resortpass|resource-environmental-solutions-llc|results-physiotherapy|retail-insights|retraites-populaires|reunion-marketing|rev|revenuecat|revero|revisa-gmbh-co-kg-steuerberatungsgesellschaft|revolut|rewards-network|rewe-group|rewind|w7-managementberatung-gmbh|wachtell|walgreens|wall-street-prep|wallapop|walleye-capital-full-time|walmart|walrusfi|waltz-health|wandelbots|wandercraft|warburg-pincus-llc|wargaming|warner-music|warp|wasabi-technologies|waters-corporation|watershed|watershed-informatics|wavenet|wavestone|wayflyer|waymark|waymo|wayve|wayvia|wbs-legal|we-communications|we-love-x-gmbh|we-singapore|wealthfront|weaviate|webai|webchart|webflow|weedmaps|weee-inc|weekend|wefix-gmbh|weflow|weinstein-properties|weiss-asset-management|welbehealth|welcome-to-the-jungle|wellpower-all-jobs|wells-fargo|weploy|weride|wesort-ai|westhafen-leipzig|wettermark-keith|whalar-group|what3words|whataburger|whatnot|wheel|wheelhouse|whereby|white-circle|who-gives-a-crap|whoop|wikimedia-foundation|wilson-elser-business-legal-professionals|win-home-inspection|windmill|windranger|wingcopter|wingspan|wingtra|wipro|wireless-logic|wirescreen|wise|withclutch|withcoverage|withdaydream|within|witty-machines|wix|wiz-inc|wizard|woflow|wolt|wolters-kluwer|wolve|wonder-studios|wonderflow|wonderful|wonderschool|woo-x|woolpert|wordware-ai|workato|workboard|workday|workera-ai|workhelix|workleap-en|workos|workstream|workwize|world-health-organization|world-labs|worldcoin|worldly|worldpay|worldquant|woven-care|wpp|wpromote|wrapbook|wrike|writer|wsc-sports|wsp|wtw|wunder|wunder-mobility|wvu-medicine|wynd-labs|wynd-labs-x-hiring)(?:/|$))(?:w[^/]*|re[^/]*|al[^/]*))",
    "/:lang(en|de|fr|it)/company/:slug((?!(?:fictiv|fidelity-international|fidelity-investments|fieldwire|figma|figure|figure-lending|files-com|filigran|filmhub|filson|fin|financial-technology-partners|financial-times|finanzwerk-hamburg|finch|finix|finn|finom|finster-ai|fintechos|fireblocks|firecrawl|firehawk-aerospace|firestorm|firetiger|fireworks-ai|firmus|firmus-technologies|first-abu-dhabi-bank|first-connect-insurance|first-light-fusion|first-momentum-ventures|firstmind|firstprinciples|fis-amount|fiserv|fit2go-gmbh|five-below|five-rings-llc-careers|five-rings-llc-events|five9|fivetran|fixposition|mabl|mach-industries|macis-gmbh|macquarie-group|macys|madano|madison-energy-infrastructure|maersk|magic|magic-leap|magiceden|magnolia|mahindra-group|mainstay|maintainx|maintea-gmbh|majestic-labs-ai|majority|make-a-wish-america|make-god-known|makersite|maki-people|mako|maltego-technologies|mamata-betreuungs-und-pflegedienst|mambu|mammoth|mammoth-brands|man-group|manna-drone-delivery|manomano|manscaped|mantra-health|manufact|manukai|manychat|manypets|maple|maplight-therapeutics|marble-aerospace|maria-kersjes|mariana-minerals|mark-spain-real-estate|mark43|marketaxess|marksandspencer|marqeta|marqvision|marriott|mars|marsh|marshmallow|martell-ventures|maruti-suzuki|marvel-fusion|massar-capital|mastercard|masterclass|material-bank|materialize|materialsecurity|mather-headquarters|matrix|matte-projects|mattermost|matternet|maven|maven-clinic|maven-emerging-talent|mavenoid|may-mobility|mayflower|maze-therapeutics|stability-ai|stable|stack-av|stack-overflow|stackadapt|stackblitz|stackgini-gmbh|stackhawk|stackline|stacks|stadler-rail|stahlwerk-annah-tte-max-aicher-gmbh-co-kg|stainless|stam-holding-gmbh|stambaugh-ness|standard-nuclear|standardfleet|stanley-1913|starbridge|starbucks|starbucks-china|starburst|starcloud|stark|starling-bank|starrez|start-campus|startree|startup-team|state-bank-of-india|state-street|statsig|staubli|stayai|steady-energy|stealth-start-up-mobility-berlin|stedi|steer|stegra|stela-laxhuber|stellantis|stellar|sterlington-pllc|stitch-fix|stmicroelectronics|stockx|stoik|stoiximan|stone-linkedin|store-space-self-storage|storiogroup|story-cannabis|str|straight-arrow-news|strand-therapeutics|strata-decision-technology|strata-information-group|strategic-hr-client-job-openings|strategy-and|straumann|strava|stream|striim-inc|strike|stripe|strive-health|striveworks|stronghold|stronghold-investment-management|stryker|stubhub|studapart|study-com-c|studyflash|stuut-technologies|stytch|thanx|that-s-no-moon-entertainment|thatch|thatgamecompany|the-ad-council|the-ai-education-project|the-brattle-group|the-city-of-fort-worth|the-daily-beast|the-doctor-catalunya-s-l|the-durst-organization|the-economist-group|the-exploration-company|the-farmer-s-dog|the-flex|the-florida-panthers|the-fork|the-iconic|the-jewish-federations-of-north-america|the-knot-worldwide|the-mj-companies|the-n2-company|the-national-football-league|the-new-york-times|the-nuclear-company|the-pharmacy-hub|the-pok-mon-company-international|the-quality-group|the-rec-hub|the-trade-desk|the-united-firm-la-liga-defensora-apc|the-virtus-solution|the-voleon-group|the-weather-company|theker|thermo-fisher|thesis|thetaray|theydo|thiess|think-academy-us|think-cell|thinkific|thinking-machines-lab|thndr|thomson-reuters|thorizon|thought-machine|thoughtworks-new|threataware|threatlocker|thredd|thrive|thrivecart|thumbtack|thunderchild-fusion|thyme-care)(?:/|$))(?:st[^/]*|th[^/]*|ma[^/]*|fi[^/]*))",
    "/:lang(en|de|fr|it)/company/:slug((?!(?:babylist|bacardi|bachem|backbase|backblaze-external-website|backflip-ai|backmarket|bae-systems|baidu|bain-and-company|baincapital|bajaj-finserv|balgrist|balyasny-asset-management|bamboohr|banco-bradesco|bandai-namco-entertainment-america-inc|bandwidth|bank-of-america|bank-of-china|bank-of-ireland|banking-talent|banque-cantonale-de-fribourg|banque-cantonale-du-jura|banque-cantonale-du-valais|banque-cantonale-neuchateloise|banque-de-commerce-et-de-placements|banque-du-leman|banque-heritage|banyan-software|barcelona-activa|barclays|barfer-s|bark|barkbox|barkbus|barkley|baron-capital|barry-callebaut|base|base-power-company|baselayer|baseload-capital|baseten|basf|basquevolt|bastion|basware|bauer-hockey-cascade-maverik-lacrosse|bauvira-gmbh|baya-systems|bayer|bayesian-health|bayreuther-brauhaus-frankfurt|li-fi|liberate|liberis|lidl|liebherr|life-skills-autism-academy|life-trading|life360|lifepoint-health|lifetime|liftoff|light|lightbringer|lightdash|lightfeather-io-llc|lightforce-orthodontics|lightfully-behavioral-health|lighthouse|lightly|lightmatter|lightning|lightning-ai|lightpanda|lightricks|lightspark|lightspeed-commerce|lightspeed-commerce-fr|lightspeed-dms|lightspeed-systems|lightspeedhq|like-it-media-gmbh|lila-sciences|limb-cher-limb-cher-gmbh|lime|limula|lincoln-property-company|lincoln-property-company-through-linkedin|lindner-parkhotel-oberstaufen-betriebs-gmbh|lindt-spruengli|lindushealth|linear|link|linkedin|linklaters|linkup|linq|lio|liquid-ai|liquid-death|liquid-i-v|lirio|lithic|litify|litmus-automation|little-people-s-landing|littlepay|liveeo|livekit|livescore-group|living-infinitely|mcadams|mccullough-robertson|mcdonalds|mcg-health|mckinsey|mcmaster-carr|mco|mechanize|medal|medecins-sans-frontieres-doctors-without-borders-field|medecins-sans-frontieres-doctors-without-borders-united-states|medeloop|medely|medflex-gmbh|mediatek|medical-informatics-engineering|mediengr-nderzentrum-mgz-nrw-gmbh|medier|medsien|medtronic|megazone|meinian-onehealth|meituan|mejuri|melio|melotech|meltplan|mem-protocol|membion-gmbh|memed-diagnostics|memx|mena-consultant|mend-io|mentiora-ai|mento|mercado-libre|mercari|mercedes-benz|mercer-advisors|merch-my-day-gmbh|merck|mercuria|mercury|merge|merge-api|meridian|meridian-partners|merit|merit-america|meriton|merqube-inc|mesh|meshy|met-group|meta|metabase|metabit-technology-llc|metacore|metalab|metalysis|metamorfosis-energ-tica-s-l|method|method-security|meticulous|metos|metox-international-inc|metrasens|metrikflow|metro-bank|metronome|metropolis|metropolitan-commercial-bank|mews|socar-trading|soci|social-discovery-group|socialpoint|societe-generale|socket|socure|sofi|softbank|softengine-holding-gmbh|soho-house-co|sojern|sol-de-janeiro|sola|solana-foundation|solar-foods|solaris|solera-health|solid-power|solidroad|solink|sollis-health|solveai|sona|sonarsource|sonatus|sonatype|sonder|sonicwall|sono-bello|sonova|sony|sony-interactive-entertainment-inc|sony-music-careers-asia-middle-east|sony-music-entertainment-germany|sony-music-entertainment-netherlands|sony-music-entertainment-poland|sony-music-global-job-board|sony-pictures-animation|sony-pictures-imageworks|sopg-consulting|sophia-genetics|sophos|sopra-steria|soros-fund-management|sosafe|sotheby-s|source-ag|source-multiplier|sourcegraph|south-columbus-preparatory-academy-german-village|south-pole|south-star-software-private-limited|southwest-airlines|southworks)(?:/|$))(?:me[^/]*|so[^/]*|li[^/]*|ba[^/]*|mc[^/]*))",
    "/:lang(en|de|fr|it)/company/:slug((?!(?:ada|ada-health-gmbh|adani-group|adapt|adaption-labs|adaptive|adaptive-security|adarga|adc-therapeutics|addepar|addi|addionics|adf-international|adfinis-ag|adjust|adm|admatis|adnovum|adobe|adonis|adswerve-inc|advance-auto-parts|advanced-space|advantest|adventhealth|advocate-health|adyen|bp|gr-n-confections|gr-ns|gr8-tech|gradial|gradient-ai|gradium|gradyent|grafana-labs|graham-capital-management-l-p|grail|gram-games|grand|grand-games|grant-thornton|graphax|graphcore|graphite|gravis-robotics|gravityclimate|graymatter-robotics|grayscale-investments|greatquestion|green-thumb|greeneking|greenlight-financial-technology|greenpeace-usa|greenworks|greiner-engineering-gmbh|grepr|greptile|griffin|griffis-residential|grone-bildungszentrum-f-r-gesundheits-und-sozialberufe-gmbh-gemeinn-tzig|gronover-elektrotechnik-gmbh|groome-industrial-service-group|groove-quantum|gropyus|groq|group14-technologies|groupe-mutuel|groupon|grove-collaborative|grover|grow|grow-therapy|growe|groww|grundconsult-immobilien-gesellschaft-mbh|grupo-ole-restauracion|grupo-quintoandar|grupo-urgatzi|pac-nyc|pacific-legal-foundation|pacvue|paddle|pagaya|pagerduty|pair-team|palabra-ai|palantir|pallet|palmetto-clean-technology|palmstreet|palo-alto-networks|palo-it|palta|panasonic|pandadoc|panera-bread|panoptyc|panthalassa|pantheon-systems-inc|pantheon-ventures-careers|panther|papa|paperless-parts|paqato-gmbh|parabola-io|parachute-health|paradigm|parafin|paragon|parallel|paratek-pharmaceuticals|pareto-ai|parity|parker|parker-hannifin|parkosecure-gmbh|parloa|parsley-health|particle41|partiful|partners-group|pasqal|passage|patch-io|patek-philippe|path-robotics|pathai|pathward-n-a|patientpoint|patreon|pattern-data|pave|pax-historia|pax-labs|paxos|paxoslabs|payfit|payoneer|paypal|paypay|paypay-card|paypay-india|paysafe|paystack|payt-software|paytient|paytm|practice-better|prada-group|praxent|praxis-precision-medicines-inc|precision-aq|precision-for-medicine|precisionmedicinegroup|prefect|prelude|premier-care-dental-management|premier-truck-rental|preply|presence|presidents-institute|presidents-institute-sweden|prevail|prezzee|pricefox|pricefx|primary|prime|prime-healthcare|primeintellect|primer|prior-labs|prisma|private-equity-insights|private-job-board|procter-gamble|procurify|prodigal|productschool|profluent|project-expedition|project44|projective-group|prolaio|prolific|prometheus-real-estate-group|prompt|proofofplay|propel|prophecy|prophesee|propublica|proqura-gmbh|prosek-partners|proshares|prosidian|prosper-health|prosus|protege|prothesen-orthesenmanufaktur|protolabs|proton|prove|proxima-fusion|pryzm|u-blox|u-haul|u-s-bank|uber|uberall|ubs|ubs-digital-art-museum|ucb|udacity|udemy|udio|uefa|uipath|ultima-genomics|ultra|ultraviolet-cyber|uma-education|unchained-labs|uncountable|underdog|understood-care|unify|unifyid-acquired-by-prove|unilever|union|union-bancaire-privee|uniqlo|unique|unispace|uniswap|unit|unit8|unite-us|united-airlines|unitedhealth-group|unitedmasters-translation|unitree-robotics|unity|universal|universal-music|universal-quantum|univity|unlikelyai|unlimit|unlock-health|unreal-snacks|unseenlabs|unsloth-ai|unto-labs|unwrap|unybrands|upbound|updater|upflow|upgrade|upkeep|uprite-construction|ups|upshop|upside|upstart|upstox|upstream-security|upvest|upwork|ura|urban-sports-club|ursa-major|urschel-laboratories-inc|us-conec-ltd|usa-mechanical-energy-services|utonomy|uvcyber|uzh)(?:/|$))(?:u[^/]*|gr[^/]*|pa[^/]*|pr[^/]*|ad[^/]*|bp[^/]*))",
    "/:lang(en|de|fr|it)/company/:slug((?!(?:be-our-guest|beacon-biosignals|beacon-software|beam|beam-therapeutics|beamery|bearingpoint|beautiful-ai|beautybarrage|bedi-partnerships|bedrock|bedrock-robotics|beewise|behavox|beiersdorf|believe|belong|belvedere-trading|belvo|benchling|benchmark-physical-therapy|benchprep|benevolentai|beqom|berlin-brands-group|berlin-city-auto-group|berlin-institute-of-health|berlin-metropolitan-school|berlin-packaging|berlinrosen|berner-kantonalbank|bers|bertram-capital-management|bertschi|besi|beside|bestow|beta-technologies|betsson-group|better|betterhelp|betterment|betterup|bevi|beyond-finance|beyondtrust|bl-mlein-ai-automation-gmbh|blablacar|black-canyon-consulting|black-duck-software-inc|black-forest-labs|black-ore|blackbird-health|blacklane|blackrock|blacksky|blank-street|blastpoint|blend|blenheim-chalcot-india|bling|blink|blink-ag|bliro|block|block-labs|blockchain-com|blockdaemon|blockit|blockworks|bloom|bloom-biorenewables|bloom-diy|bloomberg|bloomerang|bloomreach|bls|blue-dot|blue-energy|blue-ocean-robotics|blue-rose-research|blue-sky-innovators|blue-water-thinking|blueberrypediatrics|bluebird|bluecrest-capital-management|bluedot|blueprint-technologies|blueprint-test-prep-tutors-instructors|bluesky-telepsych|bluevine-india|bluevine-us|bluevoyant|blushark-digital|blykalla|blytheco|bracebridge-capital|brainco|brainpop|brainrocket|brainstation|braintrust|branch|branchinsurance|brand-new-day|brandtech|brave|bravehealth|bravo|bravo-a-cooperative-company|braze|breeze|breeze-airways|breezeway|breitling|brennan-industries|brex|bridge-to-enter-advanced-mathematics-beam|bridgebio-pharma|bridgefund|bridgepoint|bridgestone|bridgewater-associates|bright|brightai-corporation|brightcore-energy|brightflag|brightnetwork|brigit|brilliant|bring-labs-ag|bringg|brinker-international|brinqa|bristol-myers-squibb|brite-payments|british-american-tobacco|broadcom|broadsign-careers|broadway-ventures|broeder-ruckh-consulting-gmbh|brooklinen|browser-use|browserbase|brunswick-group|bryter|j-safra-sarasin|jade-biosciences|jane-street|jane-street-events|janea-systems|january|jasper|jazzx-ai|jbs-dev|jd-com|jd-sports|jeeves|jeil-pharmaceutical|jeju-air|jellyfish|jellyfishcareers|jensen-hughes|jerry|jet-aviation|jetbrains|jetzero|jfrog|jimmy|jll|job-board|jobandtalent|jobber|jobhive-ag|joe-nimble-gmbh|johnson-and-johnson|johnson-controls|johnson-law-group|join|join-gmbh|join-our-talent-community|join-the-folx-team|jomboy-media|josko-services|jpmorgan|jti|jua|judge|judi-health|juicebox|jukebox-health|julius|julius-baer|jumia|jumio|jump|jump-app|jump-crypto|jump-trading|jungle-scout|juni|juno|just-4-veterans-enterprise|just-eat-takeaway|justworks|juul-labs|jysk|saas-group|saber-interactive|saber-tech|sable|saeki|safari-ai|safaricom|safe|safe-security|safe-superintelligence|safesize|safetyculture|safran|sage|sail-research|saildrone|sakana-ai|salesforce|salient|sally-beauty-holdings|salsify|salt|salt-security|sama|sambanova|samlino-group|samotics|samsara|samsung|samsung-research-america-internship|samsung-semiconductor|san-francisco-aids-foundation|san-francisco-campus-for-jewish-living|sana|sanctuary-ai|sand-tech-holdings-limited|sandbox-vr|sandboxaq|sandoz|sandstone-care|sanford-health|sanitas|sanity|sanofi|sanovio|sap|sardine|saronic|sateliot|satispay|satrev|sattler-media-gmbh|saturn|satvu|sauce-labs-inc|saudi-aramco|saviynt|savr|savvy|sax-advisory-group|saxo-bank|saxotherm|sayari)(?:/|$))(?:sa[^/]*|bl[^/]*|br[^/]*|j[^/]*|be[^/]*))",
    "/:lang(en|de|fr|it)/company/:slug((?!(?:chaidiscovery|chainguard|chainlink-labs|chalk|champions-group-holdings|chan-zuckerberg-initiative|change|chaos-industries|character|charge-robotics|chargepoint|chariot-defense|charles-river-associates|charterup|chatham-financial|chauffeurcenter-ch-ag|checkbook|checkers-rallys|checkly|checkout-com|checkr|checkr-chile|chenmoore|cheplapharm|cherry-ventures|chery|chess-com|chestnut|chevron|chicago-public-media|chief|chime-financial-inc|china-construction-bank|china-life-insurance|china-mobile|china-railway-group|china-state-construction|china-telecom|chip-city|chipmind|chiquita|chomps|chopard|chowbus|christies|chromatic|chromaway|chs-inc|chubb|clara|clari|clari-salesloft|claritev|clariti-cloud-inc|clarity|clarity-innovations|clarium|clark-germany-gmbh|classdojo|classen-industries-gmbh|classpass|clay|clear-corporate|clear-street|clearbank|clearscore-technology-limited|clearview-healthcare-partners|clearway-energy|cleo-india|cleo-us|cleric|clerk|clerk-chat|cleveland-preparatory-academy|clickhouse|clickup|clifford-chance|climate-finance-solutions|climate-x|climateview|climeworks|clinchoice|clinomic|close|close-consulting|cloud-chamber-montreal|cloudbeds|cloudflare|cloudkitchens|cloudsek|cloudsmith|cloudtrucks|clove|clover-health|club-monaco|clubhouse|clutch-technologies-inc|debiopharm|debtbook|debut-biotech|decagon|decathlon|decathlon-digital-fr|decima-international|decimal|deel|deepgram|deepintent|deepjudge|deepl|deepmind|deepnote|deepseek|deepsense-ai|deepset|defense-unicorns|definitive-healthcare-us|degreed|delair|delft-circuits|delian-alliance-industries|delinea|deliveroo|delivery-associates|delivery-hero|dell|deloitte|delphi|deltacapita|dema|demandbase|dental365|dentsply-sirona|dentsu|depict|depoly|dept|descript|designed-conveyor-systems|desmos|destinus|detroit-lions|deutsche-bank|deutsches-feingoldhaus-gmbh|dev-technology|development-partners-international|devoted-health|devrev|deweylearn|dexcom|dexis|dexory|dexter-energy|dexterity|snafu-records|snap|snap-fusion|snap-mobile-inc|snappy|sncf|snorkel-ai|snow-companies|snowflake|snyk|te-connectivity|teachable|teague|tealium|team-skalieren|tebi|tebra|tecan|tech-holding|tech-mahindra|techland|technology|techtorch|tecovas|tegna-inc|tehtris|tekever|tekion|tekmetric|telefonica-tech|tellius|telnyx|telstra|tem|temenos|tempo|temporal|temporal-technologies|temus|tenable-inc|tencent|teneo-ai|teneo-external-feed-for-linkedin|tenex-ai|tenjin|tennr|tensorops|tensorwave|tenstorrent|tenstorrent-university-jobs|tenstorrent-unlisted-referral-jobs|tenzai|teravision-technologies|terra-quantum|terraai|terrabis|terran-orbital-corporation|terranova|terveystalo|tesco|tesla|testgorilla|tether|tethys-robotics|tetra-pak|teveo-gmbh|texas-instruments|teya|tr-fr|traba|trace3|tracebit|tracelabs|track-omc|trade-republic|tradeshift|trading212|tradingview|trafigura|trailer-park-group|trailofbits|trainline|transcarent|transcend-inc|transmarket-group|transmit-security|transmutex|transports-publics-fribourgeois|transports-publics-genevois|transports-publics-lausannois|transunion|trapeze-group|trase-systems|travelperk|traversal|trella-health|trexon|treyd|trieye|trigo|trigon-gruppe-gmbh|trine|trinity-health|tripadvisor|triple-whale|triton-systems|triumvirate-environmental|trucksmarter|true-anomaly|true-classic|truecaller|truelayer|truemed|truffle-security|trulioo|trunk|trust-bank|trust-wallet|trust-will|trustly|trustpilot|truveta)(?:/|$))(?:tr[^/]*|de[^/]*|te[^/]*|ch[^/]*|cl[^/]*|sn[^/]*))",
    "/:lang(en|de|fr|it)/company/:slug((?!(?:elastic|elca-group|elcogen|electra|electrolux|electronic-arts|electronx|eleos-health|eleqtron|elevance-health|elevations-credit-union|eleven|elevenlabs|elfbeauty|eli-lilly|eliot-community-human-services|elite-dental-partners|elite-technology|elligint-health|ellipsislabs|elmi-power-gmbh|michael-bonsby-hvac-plumbing-electrical|michaels|micron|microsoft|microsure|microtech-global|midea-group|midi-health|midstream|migros|mill|millennium-management|million-dollar-baby-co|milomed-gmbh|mimecast|mimetas|mimic|mind-robotics|mindbody|mineralys-therapeutics|minio|minitab|minnesota-cannabis-services|mintcode-solutions-gmbh|mintlify|mio-partners|miq-digital|mirabaud-group|mirage|mirai-power|mirai-tech|mirakl|mirakl-labs|mirelo-ai|miro|miromind-ai|mirum-pharmaceuticals|misfits-market|miso|mission-lane|mistral-ai|mithril|mithrl|mitigram|mitratech|mitsogo-inc|mitsubishi-corp|mitsubishi-motors-north-america-inc|mixpanel|mob-entertainment|mobileye|mobilityware|mochi-health|modal|modern-animal|modern-health|moderna|modernfi|modernizing-medicine-inc|moderntreasury|modulr|modus-create|moelis|moia-gmbh|molecubes|molg|mollie|moloco|momence|moment|momentum|momentum-financial-services-group|monad-foundation|mondelez|moneyboxapp|moneyhero-group|moneysmart-group|mongodb|moniepoint|monroe-tractor|monumental|monumental-sports-entertainment|monzo|moon-surgical|moonlite|moralis|morgan-morgan-p-a|morgan-stanley|morning-brew-inc|morse-micro|mosaic|moss-new-york-llc|motherduck|motion|motional|motive|motorola-solutions|moulin-a-miel|movement-strategy|mozilla|q-ant|q-centrix|q-ctrl|qai-ventures-ag|qblox|qdrant|qilimanjaro-quantum-tech|qinetiq|qnb-group|qodo|qonto|qphox|qred|qts|qualcomm|qualia|qualified|qualified-digital|qualified-health|qualifyze|qualio|qualtrics|quanata|quandela|quanta-dialysis-technologies|quantcast|quantexa|quanthealth|quantinuum|quantis|quantori|quantrolox|quantum|quantum-coffee|quantum-motion|quantum-si|quantumdiamonds|quantware|quartr|quartz-bio|quatt|qube-rt|qubit-pharmaceuticals|quera-computing-inc|quick|quick-green-rapid-health-gmbh|quicknode|quillbot|quin|quince|quisitive|quiver-ai|quix-quantum|quobly|quora|qutwo|qventus|se3|seamless|seatgeek|seattle-sounders-fc-seattle-reign-fc|secfix|secheron-hasler-group|second-front|secretariat|sectra|secureframe|securitas-ag|securitize|securityscorecard|seed|seeing-systems|seesaw|sei-labs|sekoia-io|select-management-group|select-medical|self-financial|selini-capital|semafor|semgrep|semiqon|semrush|sendbird|senior-doc|senra-systems|sensirion|sensofusion|sentilink|sentry|seo-sponsors-for-educational-opportunity|seon|seoul-robotics|seprify|septerna|sequence|sequoia|sequra|sereact|serhant|sertis|sertrading|servers-com|servicenow|servimo|sesame|sesamm|seso-inc|setpoint|seven-research|severin-hotels|seyond|sezzle|space-forge|space-kinetic|spacex|spacex-global|spacial|spade|span|spare|spark|spark-advisors|sparkland|sparksoft-corporation|sparrow|sparrow-quantum|spaulding-ridge|spc-group|speakeasy|specitec|specterops|spector-ai|speechify|speechmatics|spekit|spektr|spektrum|spencer-stuart|spendesk|sphere|sphinx|sphinx-defense|spiegel-media-gmbh|spin-brands|spin-careers|spire|splice|spothopper|spotify|spotlight|spotme|spotter|sprengnetter|sprig|spring|spring-health|springboard|springboard-roles|sprinter-health|spruce-systems|spruceid|sps-north-america|sps-north-america-opportunities-not-externally-posted-board|spycloud)(?:/|$))(?:se[^/]*|mo[^/]*|mi[^/]*|sp[^/]*|q[^/]*|el[^/]*))",
    "/:lang(en|de|fr|it)/company/:slug((?!(?:ac-immune|academia|acadia-pharmaceuticals-inc|accel-club|accel-schools|accela|acceleration-partners|accelercomm|accenture|accenture-federal-services|accenture-federal-services-careers-marketplace|access-healthcare-associates|accesso|accor|accord|accrue|accuracy|accuray|accuweather-careers|aci-learning|aciner-geb-udereinigung|aclu-internships|aclu-national-office|aclu-of-new-jersey|acne-studios|acommerce|acorns|acquia|acquisition|acquism|acrisure|acrisure-innovation|acronis|activecampaign|activision-blizzard|acumen|acurus-solutions-private-limited|ae-studio|aechelon-technology|aecom|aeo|aerones|aerospacelab|aerospike|aerovect|aeva-inc|aevex|foca-bazl|focus|focus-financial-partners|focus-partners-australia-escala-partners|focused-energy|foley-hoag-llp|folio|follett-software-llc|fora-financial|forbes|ford|forerunner|foretellix|forever-families|forge|forge-biologics|form|form-health|forma|formance|formation-bio|formenergy|formlabs|forsight-robotics|forte|fortem-technologies|forter|forterra|forto|forum-ventures|forward-networks|fospha|fossa|fotokite|found|foundation-risk-partners|founders-green-animal-hospital|foundry-robotics|four-hands|fourkites|foursquare|fourthline|foxconn|foxglove|gearset|gecko-mbh|gecko-robotics|gelato|gelber-group|gelber-group-handshake|gemini|genea|general-assembly|general-atlantic|general-dynamics|general-matter|general-mills|general-motors|generate-biomedicines|generative-bionics|genesis-ai|genesis-digital-assets|genesis-molecular-ai|genestack|genesys|genetix-biotherapeutics|geneva-airport|geneva-trading|genies|genius-sports|genomics|genpact|genpeach-ai|genscript-probio|gensyn|georg-fischer|geotab|gerald-group|get-well-network|getnet|getresponse|getspecialfasteners-com|gett|getwhy|getyourguide|sib-solutions|sicpa|sidoun-international-gmbh|siemens|siemens-healthineers|sierra|sieve|sift|sift-healthcare|sightline-media-group|sigma-computing|sigmoid|signers-national|signifyd|signoz|sika|sila-services|silicon-ranch-corporation|silverado|silverflow|silverfort|silvus-technologies|similarweb|simon-kucher|simpleclosure|simplesense|simplex-trading|simplifynext|simplypayments|simtra-biopharma-solutions|simulamet|sinclair|singlestore|singular|sinopec|sipfront-gmbh|siren|sirona-medical|sisense|sita|siteline|siteminder|sitoo|sixfold|tabapay|tabby|tacto|tacton-systems|tadaweb|tado|tag-aviation|tailorcare|tailored-brands|tailscale|tailwind|take-two-interactive-software-inc|takealot-com|takealot-group|takeda|tako|taktile|talentful|talkdesk|talkiatry|talkspace-remote-psychiatric-nurse-practitioner-roles|talkspace-remote-therapist-roles|talon-one|talos|tandem|tandem-bank|tandem-health|tandemlaunch|tangible|tango-gameworks|tanium|tanius-technology|tapblaze|tappz-gmbh|target|target-rwe|taskrabbit|tastytrade|tata-capital|tata-motors|tatari|taurus|tavus|taxbit|zalando|zam|zama|zapier|zara|zayzoon|zebra|zed|zeffy|zello|zenbusiness-inc|zendesk|zengrc|zenline-ai|zenni-optical|zeno-power|zenobe|zenoti|zenty-lp|zeotap|zephyr|zepz|zerion|zero|zero-networks|zeromark|zettabyte-space|zevia|zhaw|zillow|zimmer-biomet|zimmermann-brase-partner-steuerberatungsgesellschaft-mbb|zimpler|zinnia|zinnia-employee-referral|zip|zip-co-limited|zipline|ziprecruiter|zocdoc|zomato|zone-5-technologies|zone-co|zoominfo-technologies-llc|zoox|zopa|zscaler|zte|zuehlke|zulu-alpha-kilo|zuma|zuora|zup-innovation|zurich-airport|zurich-insurance|zus-health|zwift|zynga)(?:/|$))(?:z[^/]*|ac[^/]*|fo[^/]*|ge[^/]*|si[^/]*|ta[^/]*|ae[^/]*))",
    "/:lang(en|de|fr|it)/company/:slug((?!(?:apaleo|apartmentiq|apco-technologies|apera-ai-inc|aperia|apex|apex-space|apheros|apify|apiiro|apiphani|apiro-entertainment-gmbh-co-kg|aplazo|apollo|apollo-education-systems|apollo-graphql|apollo-io|appdirect|appian-corporation|appier|apple|appletree-prep|applied|applied-engineering|applied-intuition|applied-materials|appliedlabs|applike-group-gmbh|applovin|apply|appnovation-technologies|appodeal|appomni|appquantum|appsflyer|appspace|apptronik|appviewx|apron|aptiv|aptos|arab-bank-switzerland|arb-interactive|arbe-robotics|arbital-health|arbor|arc-boat-company|arc-institute|arcade|arcadia|arcana-analytics|arcee-ai|arcesium-llc|arch-co|archangel-autonomy|archer|archera|architect|archrival|arena-ai|arenanet|arine|arize-ai|arkestro|arkose-labs|arlo-solutions-llc|arm|armada|armis-security|arnold-kl-mpen-gmbh-co-kg|arondite|arqit|array-education|artefact|artera|arthur-d-little|arthur-j-gallagher|artie|artifact|artisan|artisan-partners|artsy|arx-robotics|aryzta|bicycle-therapeutics|big-d1-gmbh|bighat-biosciences|bigid|bike-business-hub-slu|bill|billa-cz|billiontoone|billogram|billups|bilt-rewards|binabik-ai|binance|binance-us|bio|bio-techne|biocartis|biocatch|biogen|biograph|biohub|biontech|biorce|bird|birzer-neumann-wirtschafts-und-steuerberatungsgesellschaft-partnerschaftsgesellschaft|bishop-fox|bitcoin-depot|bitdefender|bitfarms|bitfinex|bitgo|bitmex|bitpanda|bits-technology|bitso|glance|glasswall|glean|glencore|glia|glide|glimpse|global-accelerator|global-energy-alliance-for-people-and-planet-global-energy-alliance-llc|globalli|globant|glossgenius|glossier|glovo|glydways|leading-educators-careers|league-inc|leap|leapsome|leapwork|learneo|learning-commons|learnlux|learnupon|leclanche|ledger|ledgy|legal-services-nyc|legalzoom|legend-biotech-us|legion|legion-intelligence|legionhealth|legit-security|legora|lek|lemlist|lemonade|lendingtree|lendo|lenovo|lens|leona-health|leonardo|leonteq|les-fermes-debout|letta|levanta|level|level-access|levio|levitate|lexly|leyden-labs|leydenjar-technologies|peak-design|pearl|pearlhealth|pecan|peec-ai|peloton|pendo|penn-interactive|penny-cz|pennylane|penske-media-corp|pentera|penumbrainc|people-ai|people-can-fly|pep|per-scholas|percepta|percepto|peregrine-technologies|perella-weinberg|perfectserve|pergolux|periodic-labs|perion-network-ltd|pernat-emile|perplexity-ai|perry-ellis-international|perry-ellis-international-retail|persistent-systems|persona|personalis-inc|personio|petrobras|petrochina|ro|roadie|roadrunner-recycling-inc|robco|robinhood|roblox|roboa|roboforce|robovision|roboyo|roche|rock-flow-dynamics|rocket-chat|rocket-factory-augsburg|rocket-lab-corporation|rocket-lawyer|rocket-money|rocket-travel-inc|rockstar-games|roemer-capital-gmbh|roke|roku|roland-berger|rolex|roller|rolls-royce|rondo-energy|roo|roofr|roofstock|root-access|ropes|rothesay|rothschild-and-co|routine-labs|rover|rovop|rowden-technologies|substack|sucafina|sucden|sugarcrm|sui|suind|suki|sullivan-cromwell|sulzer|sulzer-schmid|summer|summit-one-vanderbilt|sumo-logic|sumup|sun-pharmaceutical|sunfire|sunflower-labs|sunnyside|suno|sunrise|sunrise-management|sunrun|sunstar|supabase|super-com|super-technologies|superblocks|supercell|supergaming|superhuman|supernovacompanies|supporting-strategies|sureify|surrealdb|surveymonkey|susquehanna-international-group|sustainable-ag-unternehmensberatung|sustainable-talent|sustainment)(?:/|$))(?:ar[^/]*|su[^/]*|ap[^/]*|pe[^/]*|bi[^/]*|ro[^/]*|le[^/]*|gl[^/]*))",
    "/:lang(en|de|fr|it)/company/:slug((?!(?:anagram|analog-devices|anaplan|anchanto|anchorage-digital|andela|anduril-industries|anea-sante|anevo-ag|angeheuert-gmbh-personalberatung|angel-city|angi|angitia-incorporated-limited|anima|anine-bing|ankerplatz-mea-vita-neum-nster-gmbh|anodize|anomali|anon|anrok|ans|ansa|answerrocket|ant-group|antenna|anteriad|anthropic|antithesis|antonie|antora-energy|anybotics|anyfin|anyscale|anywherenow|cradle|craftdocs|cranial-technologies|cravath|createch-engineering-gmbh|creative-fabrica|creativex|cred|credible|credit-agricole-next-bank|credit-karma|credit-union-of-colorado|creditaccess-india|creditgenie|cresco-labs|cresta|crexi|cribl|crisp|crisp-recruit|criteo|cro-metrics|cross-river|crowdstrike|crunchyroll-llc|crusoe|crux-climate|cryptio|crypto-com|cryptonext-security|daedalean|dagster-labs|daiichi-sankyo|dailymotion|damora-therapeutics|danaher|dandelion|dares|dark-wolf-solutions|darktrace|dash0|dashlane|data-praxis|databento|databricks|datacamp|datacor|datadog|datadome|dataguard|datahub|dataiku|datarails|datasmart-point-gmbh|datasnipper|datologyai|dave|david-zwirner|davis-development|davis-polk|davita|daylight|daymark-health|enable|enavate|encoura|encube|endava|endor-labs|endress-hauser|endurosat|energy-dome|energy-exemplar|energy-solutions-usa|energy-vault|energyhub|energytec-ai|engelhart|engflow|engie|engine|engineers-gate|enhesa|enigma|ennoble-care|enova-international|enpal|enpulsion|ens-dynamics|ensco-inc|ensemble|entalpic|entera|enterpret|entersekt|entrepreneurs-first|entrust|enveritas|envision-consulting|enviva|envoy|envoy-global-inc|flaglerhealth|flagship-pioneering-inc|flagstone-group-ltd|flare-bright|flatiron-health|fleek|fleetio|fleetworks|fleetworthy|fletcher-jones-automotive-group|flex|flexion-robotics|flexport|flighthub|flint|flip|flipkart|flix|flo-health|float|flock-homes|flock-safety|flora|florence|floryn|flow-traders|flowfuse|flutterflow|flux|fluxon|flyability|flycatcher|flyr|flytxt|framer|frankenburg-technologies|freed|freedom-together-foundation|freeform|freenome|freenow|freeplay|freetrade|freewill|fresenius-kabi|fresenius-medical-care|fresh-prints|freshfields|freshpaint|friedenberger-rudnick-steuerberatungsgesellschaft-mbh|fries-gruppe|friss|froda|froid-climatisation-assistance|frontcareers|frontier-dermatology-provider-careers|frontiers|frontify|la-senza|la28|la28-web|labelbox|lakera|lam-research|lambda|lambertus-apotheke|lancedb|langchain|langdock|langfuse|lantern|lantmannen|larkin-street-youth-services|larsen-toubro|lasko-products|lassie|last-app|lastpass|later|latitude-ai|lattice|latticeflow|launch-potato|launchdarkly|launchpad-technologies|laurel|lavendo|lawdepot|lawzero|layer-health|layerfi|layerzero-labs|lazard|placements-io|placer-ai|plaid|plain|plainid|plane|planet|planet-a-foods-gmbh|planet-pharma|planetscale|planhat|planner5d|planqc|planradar-gmbh|planzer|plata|platform-science|platform9|playground|playkot|playnvoice|playrix|playstation-global|pld-space|plentific|pletschacher-holzbau-gmbh|plexus-co|plexus-worldwide|plinth|plot|pls|plumettaz|pluralfinance|plus-power|pocus|pod-network|podium|point|point-c|point-one-navigation|point72|poka-en|poke-and-wiggle|polar|polestar|polyai|polychain-capital|polygon-labs|polymarket|pomelo-care|pont-connects-e-k|pontera|poolside|popl|poppulo|portswigger|posh|poshmark|possible-finance|post|postfinance|posthog|postman|postscript|power-digital|powercell-sweden)(?:/|$))(?:en[^/]*|an[^/]*|la[^/]*|cr[^/]*|fr[^/]*|fl[^/]*|da[^/]*|pl[^/]*|po[^/]*))",
    "/:lang(en|de|fr|it)/company/:slug((?!(?:amae-health|amazon|amber|ambiencehealthcare|ambient-ai|ambient-enterprises|amca|amd|amend-consulting|amenitiz|american-college-of-obstetricians-and-gynecologists|american-express|american-housing|american-institute|ametek|amgen|ami|amina-bank|amo|amoria-group|amp-ai-powered-sortation-for-waste-and-recycling|amperity|amperos|amplemarket|amplitude|amundi|amwell|asana|ascend-analytics|ascension|ascent|ascento|asg|ashby|asian-paints|asm|asml|asos|aspect-biosystems|aspire-living-learning|aspora|assai-atacadista|asseco-poland|assembly|assemblyai|asset-living|assetwatch-inc|assura|assured-guaranty|assyst-inc|astera-labs|astera-labs-early-career|astra|astranis|astrazeneca|astro-mechanica|astronomer|at-bay|ataibeckley|ataraxis-ai|atdp-company|athletics-baseball-operations|athletics-business-operations|atlan|atlas|atlas-agro|atlas-hxm|atlassian|atlys|atmos-cholet|atom-bank|atom-computing|atomic-cartoons|atomicwork-inc|atoms-careers-page|atos|atropos|attain|attain-partners|attentive|attentivemobile|atticus|attio|attivo-partners|atwell-llc|au10tix|auctane|audax-group|audemars-piguet|audibene-hear-com|auditax-steuerberatungs-gmbh|auditdata|auditless|augment-code|augury|august-health|aura-aero|auralis-group|aureliussystems|aurora-innovation|aurorasolar|auros|auterion|authentic-brands-group|auto1|autobrains|automata|automattic-careers|autopilot|autoproff|autoscout24|autozone|autura|bobbie|bobst|boehringer-ingelheim|boeing|bokio|bol|bold-ag|bold-business|bolster|bolt|bolt-new|boltz|bombas|bonfire-studios|bonnier-news|booking-com|booksy|boom|boom-entertainment|boomi|bordier|bosch|boschung|boston-scientific|bot-auto|bota-systems|bots|bottomline|boulder-care|boulevard|bounce|bound|box|boxlunch-hot-topic|dialectic|dialogueai|dialpad|diana-health|dicks-sporting-goods|didi-global|die-pflegeunion-gruppe|dig-inn-restaurant-teams|digible|digital-asset|digital-ops-tech-centre-dis-dotc|digitale-leute-school|digitalplatforms|diligent-corporation|diligent-robotics|diligent-services|disco|discord|disney|dispatch|disruptive-industries|distalmotion|distantjob|divergent|goals|goat-group|gocardless|godaddy|gofundme|goguardian|golden-apple-foundation-careers|golden-state|goldman-sachs|golinks|gonet|gong-io|good-job-games|gooddata|goodfire|goodnotes|goodway-group|goodweek|goody|google|goop|gopuff|gore-mutual-insurance|gorgias|gorjana|gorman-bunch-orthodontics|gostudent|gotion-inc|gousto|govini|govsignals|govtech-barbados|loadsmart|lob|local-initiatives-support-corporation|loccitane-group|lockheed-martin|locus-robotics|lodestar|lodestar-space|lodgify|logicgate|logicmanager|logitech|logmind|logop-dische-praxis-kuhnle-gmbh|logos|loka-inc|lombard-odier|long-lake-management|lonza|look-up|lookout-inc|loop|loreal|lorikeet|lottie|louis-dreyfus-company|louis-vuitton|lovable|rackner|radai|radar|radiance-technologies|radiant-nuclear|radiantsecurity|radical-numerics|radicle-health|radix-trading-experienced-job-board|raft-company-website|rai-institute|raiffeisen-switzerland|railway|raisin|ramp|range|rapidsos|rappi|rapyd|rapyuta-robotics|rasa|raycast|razorpay|razorpay-software-private-limited|swan|swap|swarm-aero|swarm-biotactics|swatch-group|sweden-ballistics|sweep|sweetgreen|swiggy|swile|swishfund|swiss-international-air-lines|swiss-life|swiss-mobiliar|swiss-re|swisscom|swissdrones|swissport|swissquote-bank|swissto12|swoboda|sword-group|sword-health)(?:/|$))(?:am[^/]*|di[^/]*|go[^/]*|lo[^/]*|au[^/]*|at[^/]*|as[^/]*|bo[^/]*|ra[^/]*|sw[^/]*))",
    "/:lang(en|de|fr|it)/company/:slug((?!(?:ai-squared|ai2|ai21-labs|ai4i|aiendoscopic|aift|aig|aignostics|aikido-security|aim|ainavio-gmbh|aios|aiphoria|air-liquide|air-space-intelligence|aira|airbnb|airbus|airbyte|aircall|airforestry|airgarage|airia|airmo-gmbh|airnxt|airobotics|airship|airslate|airspace|airtable|airtrunk|aisle|aiven|aizer-health|bubble|bubble-skincare|bucherer|bug-bounty-switzerland|bugcrowd|buhler-group|build|builder-io|buildkite|buildops|built|built-in|built-robotics|built-technologies|bumble|bunq|bureau-veritas|business-insider|butternut-box|bux|buyers-edge-platform-llc|buynomics|buzz-solutions|c3-ai|cec-entertainment|cedar|celebal-technologies|celestia|celigo|cellcentric|celonis|censys|center-for-employment-opportunities|centessa-pharmaceuticals-llc|centralreach|centric-software|centrum-health|cequr|cerebras-systems|ceribell-inc|cern|cerrion|ceva-logistics|doc|docker|doconomy|docplanner|doctolib|doctrin|doit|dollar-tree|dome-construction-corporation|domestika|domino-data-lab|dominos|domyn|done-berlin|donhauser-partner-mbb|donorbox|doodle|doordash|doppel|dorsia|doss|dott|double|doubleverify|dovetail|doximity|dr-dental|dr-reddys|dr-squatch|dragos|drata|drayer-physical-therapy|dream-security|dreamsports|dreem-health|dressmann|drivenets|drivetrain|drivewealth|drixler-energietechnik-gmbh|dronamics|drone-defence|dronedeploy|dropbox|druva|drw|drw-montreal|dryft|eve-legal|ever|everbridge|evercore|everdriven|evergreen-nephrology|evergreen-services-group|everlane|everlaw|everlywell|everphone-gmbh|everpure|evervault|everway|every-io|everything-to-gain|evismart|evolution|evolutionary-scale|evolutioniq|evolve|evyd-technology|fable|fabrion|factfinder|factor|factored|factorial|factorial-energy|factris|fae-beauty|faircom-new-york|faire|fairlife|fal|falconx|fam-brands|family-of-kidz|familywell|fanvue-com|far-ai|far-inspections|faraday-future|farfetch|farther|fashion-nova|fastino-labs|fastly|fathom-video|imagen-technologies|imagination|imagine-pediatrics|imagine-worldwide|imago-stock-people-gmbh|imbibe|imc|immatics|immersivelabs|immobilien-hausverwaltung-lessmann-gmbh|immunocore|impact-com|impinj|impiricus|implement-consulting-group|imply|imprint|improbable|improvado|imubit|phamily|phantom|pharmacann|pharo-management|phasecraft|phasev|philadelphia-phillies-baseball-operations|philip-morris-international|philips|philo|philz-coffee|phiture-gmbh|phizenix|phoebe-work|phoenix-contact|phonepe|phota-labs|phylo|phyron|physical-intelligence|physicsx|picnic|picnic-delivery|pico|pictet-group|pie-insurance|piermont-bank|pierson-ferdinand|pigment|pika|pilatus|pilot-com|pimco|pindrop|pinduoduo|pine-park-health|pinecone|ping-an-insurance|ping-identity|pink-moon-studios|pinterest|pipe17|pipedrive|pit|pitch|pitchbook-data|pivot|pivot-a2e|pivotal|pix4d|pixellot|shakepay|shardeum-foundation|sharebite|sharegate-en|shark-robotics|sharkninja|sharp-performance|shef|shein|shell|shield-ai|shields-health-solutions|shift|shift-technology|shift4|shift5|shiftsmart|shimizu-north-america|shipbob-inc|shippo|shipwell|shopfully|shopify|shopmy|showpad|toast|together-ai|toka|tokamak-energy|tokyo-electron|toloka|tomofun-furbo-pet-camera|tomorrow-io|tomtom|tonal|too-good-to-go|topcompare|topkey|topsort|topstep|toradex|torc-robotics|torq|toshiba-global-commerce-solutions-external|toss|totalenergies|touchbistro|tower-peak-partners|tower-research-capital|toyota)(?:/|$))(?:to[^/]*|pi[^/]*|ai[^/]*|fa[^/]*|sh[^/]*|im[^/]*|bu[^/]*|ph[^/]*|ev[^/]*|ce[^/]*|do[^/]*|dr[^/]*|c3[^/]*))",
    "/:lang(en|de|fr|it)/company/:slug((?!(?:cb-insights|cbh-bank|cbre-global-workplace-solutions-data-center-solutions|ci-azumano|cic-energigune|cinven|circle|circle-k|circle-so|circleci|circuithub|cisco|cision|citadel|citadel-securities|cite-gestion|citema-systems-gmbh|citian|citigroup|citizen-watch-group|citrix|citrus-health-group-inc|civil-science|cube|cubesoftware|cubist|culinary-agency|culture-amp|curaleaf|curaponte|curi-capital|current|cursor|curtiss-wright|cuspai|custom-surgical-gmbh|customcells|customer-io|cyacomb|cyberbit|cybereason|cyberhaven|cyberpeace-institute|cybersheath|cybret|cybrid|cycode|cye|cyera|cylib|cylus|cymulate|cyngn|cyolo|cyrebro|cyted|cytoreason|cyware|duatic|duck-duck-go|duckworks-millwork-solutions|dude-perfect|duetto-research|dufour-aerospace|dukascopy-bank|duna|dune|dunnhumby|duolingo|dupont|durable|dusk|dust|dustyrobotics|dutchie|ecential-robotics|echion-technologies|echo|echodyne-corp|eclinical-solutions|eclipse-trading|eclypsium|ecoatm-gazelle|ecodrop|ecom-agroindustrial|ecomsky-gmbh|ecorobotix|ecovadis|emag|emarketer|embark|embrace|emergent-labs|emerging-travel-group|emerton|emirates-group|emnify|empa|empirical|employment-opportunities-at-buzzfeed-inc|emrich-wangler-herrmann-partg-mbb|exa|exa-ai|exadel-inc-website|exante|excel-sports-management|excellent-go4|execujet|exeger|exein|exl|exodus-movement-inc|exotec|exotrail|exowatt|exp|expedia-group|explorium|expressvpn|extend|extenteam-client-roles|extrahop|exxonmobil|gaetan-data-gmbh|gaia-ag|galaxus|galaxy|galderma|galileo|galileo-financial-technologies|galliker|gallup|gam-investments|game-seven|gametime-united|gamma|garda-capital-partners|garmin|garner-health|gartner|gas-south|gate|gather-ai|gatik-ai|gauss-fusion|gibson-robotics|gierth-partner-steuerkanzlei|giga-energy|gilead|gilion|gillig|ginkgo-bioworks-inc|gitbook|gitguardian|github|gitlab|givaudan|givecampus|givedirectly|givewell|lucid-bots|lucid-motors|lucid-software|lucidya|lufthansa-group|luma-ai|luma-health|luma-vision|lumana|lumapps|lumera|lumiform|lumimeds|luminance|lumindigital|lumos|lumos-identity|lunar|lunar-energy|luno|lush|lush-handmade-cosmetics|luxoft|luxor|luzia|richemont|ridgway-machines|rieter|rift|rigetti-computing|right-search|rigup|rillet|rimac-group|riot-games|ripple|rippling|rise8|riskified|risktec|rithum|rithum-linkedin-board|ritual|rival-technologies|rive|riverflex|riverlane|rivia|rivian|rivr|sk-hynix-america|sk-hynix-memory-solutions-america-inc|skadden|skeleton-technologies|skild-ai|skillshare|skin-clique|skin-laundry|sky-mavis|skydio|skyflow|skyguide|skylight|skylo-technologies|skyports|skyral|skyscanner|smallpdf|smarsh|smart-energy-link-ag|smartasset|smartbear|smarterdx|smartling|smartly|smartrent|smartsheet|smava-gmbh|smcp-north-america|smcp-north-america-us-canada|smic|smith-nephew|smithrx|sygnum-bank|sylogist|sylvera|symbolica-ai|symetra|symphogen|synack|synapse-medicine|syncron|syndica|syndigo|syner-g|syngenta|synopsys|synthace|synthesia|synthesis-health|synthflow|system|systemiq|syz-group|x-ai|x-bow-systems|xaira-therapeutics|xantium|xapo-bank|xbowcareers|xdof|xealth|xendit|xenon|xensam|xero|xiaomi|xion|xm|xocean|xometry|xometry-europe|xpeng|xtx-markets|xund|y-soft|yahoo|ycombinator|yelp|yepoda|yes-energy|yext|yipitdata|yipitdata-alternative|ylopo|yondr|yotpo|you-com|yougov|yousician|ypsomed|yttp|yubico|yugabytedb|yum-china|yuno)(?:/|$))(?:ga[^/]*|lu[^/]*|ri[^/]*|ci[^/]*|ex[^/]*|sk[^/]*|sy[^/]*|sm[^/]*|du[^/]*|em[^/]*|ec[^/]*|gi[^/]*|y[^/]*|x[^/]*|cy[^/]*|cu[^/]*|cb[^/]*))",
    "/:lang(en|de|fr|it)/company/:slug((?!(?:ab-inbev-growth-group|abacum|abacus-insights|abb|abbott|abbvie|abbyy|abcellera|abilitypath|ably-uk|abnormal|abound|abridge|absci|age-bold|age-solutions|agent|agentur-k-hnen|agicap|agile-robots|agilisys|agility-robotics|agiloft|agoda|agomab|agricultural-bank-of-china|agwest-farm-credit|akasa|aker-systems|akeyless|akido|akko|akkuro|aktos|akuity|akuna-capital|ava-labs|avala|avaloq|avant|avantium|aven|aviatrix|avid4|avidxchange-inc|avientus|avra|avride|axa-switzerland|axelera-ai|axi|axicom|axiom|axiom-co|axios|axis-bank|axle|axle-careers|axmed|axon|axonius|axs|azuki|azurity-pharmaceuticals-india|azurity-pharmaceuticals-us|by-the-bay-health|bybit|byd-north-america|byggmax|bytedance|eam-l-eveil-du-scarabee|earnin|easygo|easyjet|easymile|easypost|easyship|eaton|eawag|egc-energie-und-geb-udetechnik-gmbh|egon-zehnder|egym|epic-brokers|epic-games|epic-kids-inc|epirus|episode-six|episode-six-us|eqt-corporation|eqt-group|equal-experts|equal1|eqvilent|eraneos|ergon|ericsson|erl-pflege-gmbh|ernest|ernst-hasselbring-gmbh-co-kg|escribers|esm-personalservice-gmbh|espace|espresso|esri|ess|etalytics-gmbh|eth-zurich|ethereum-foundation|ethernovia-inc|ethon-ai|ethos-life|ethyca|etisalat|etoro|etsy|euclid-power|eudia|euroairport-basel-mulhouse-freiburg|euronext|eutelsat|federato|fedex|feedzai|fels-trader-gmbh|fender|ferrero|ferring-pharmaceuticals|fetch|fetcherr|feverup|fueled|fulfil-solutions|fullstory|function-health|fundingcircle|fundraise-up|funga-pbc|funnelfox|further-ai|fuse|fusion-worldwide|future-energy-ventures|fuze-health|guardio|guardz|guerrilla-games|guidelight-health|guidepoint|guidepoint-security|guidepost-montessori|guidewheel|guild|gunvor|gusto-inc|icapital|icbc|ice-miller|iceye|icici-bank|icon|iconiq|ion-group|ionity|ionos-de|ionos-se|ionq|iovance-biotherapeutics|iowa-cannabis-company|isaac|isar-aerospace|isardsat|isomorphic-labs|ispot|iss-stoxx|it-s-prodigy|itau-unibanco|itc-limited|itd-tech|iten|iterable|iteration-one-gmbh|iterative-health|itm-power|itm-radiopharma|its-logistics-llc|lyceum|lydech-thermal-acoustic-solutions-tas|lyft|lyko|lynx-analytics|lyra-health|lysa|mr-apple|mr-apple-careers-site|mrbeast|mrbeast-contract-jobs|muck-rack|multiplier|multiply|multiverse|multiverse-computing|muon-space|mural|muse-group|museum-of-science|mutable-tactics|muwave|mux|muxon|myers-holum|myfitnesspal|myfunded-futures|mynt|myriad360|myrspoven|myshell|mystenlabs|myvillage|psi|psibufet|psiquantum|pst-professional-support-technologies-gmbh|public|public-library-of-science|publicis|pubmatic|pubnub|pulley|pulse|pulumi|pump-co|pure|ruag|rubrik-job-board|ruf-it-gmbh|ruggable|rula|rune-technologies|runpod-inc|runway|runwise|rush-street-interactive|russell-reynolds-associates|slash-financial|slate|slaughter-and-may|sleeper|slice|slingshot-aerospace|ti-and-m|tia|tidio|tiger|tigergraph|tight|tiktok|tilt|tilthq|tines|tint|tinybird|tipalti|tirlan|titan|titan-ai|tubi|tubulis|tucows|tucows-inc|tudor-investment-corporation|tulip-interfaces|tune-insight|turbineone|turbotenant|turing|turner-and-townsend|turnkey|turquoise-health|twaice|twelve|twenty|twentyfour-industries|twilio|twin-health|twist-bioscience|twitch|two-dots|two-sigma|two-six-technologies)(?:/|$))(?:fu[^/]*|tu[^/]*|ag[^/]*|ru[^/]*|it[^/]*|mu[^/]*|gu[^/]*|ab[^/]*|tw[^/]*|ti[^/]*|ax[^/]*|et[^/]*|fe[^/]*|my[^/]*|av[^/]*|pu[^/]*|ly[^/]*|io[^/]*|ea[^/]*|er[^/]*|sl[^/]*|ak[^/]*|eu[^/]*|ep[^/]*|ps[^/]*|az[^/]*|is[^/]*|mr[^/]*|by[^/]*|es[^/]*|eq[^/]*|eg[^/]*|ic[^/]*))",
    "/:lang(en|de|fr|it)/company/:slug((?!(?:30mpc|3b-pharmaceuticals|3cloud|3red-partners|66degrees|6sense|8am|8fleet-inc|a-lign-external|a-team|a-thinking-ape|affect|affinidi|affirm|affirmedrx-pbc|afresh|ahti-interiors|ajax|ajax-systems|aqemia|aqr|aquatic-capital-management|away|awin|awl|aypa-power|ayuda-en-accion|b-b-immo-gmbh|b-g-projects-gmbh|b-riley-securities|bb-energy|bbpos-limited|bcg|bcge|bcs|bcv|bswift|bswift-india|btg-pactual|btig|bvnk|bwe-energiesysteme-gmbh-co-kg|cd-projekt-red|cdds-ag|cgi|cgs-group|cm|cma-cgm|cmblu-energy-ag|cmr-surgical|csem|csl|csob|css|ctc-lateral-website-linkedin|cti|ctl-gmbh|cvent|cvs-health|cvx-ventures|d2-technical-services|d2l|d2x|db-e-c-o-north-america|dbt-capital|dbt-labs|dhg-deutsche-h-rakustik-gmbh|dhi-group-inc|dhl|dna-script|dnata|dnv|dsm-firmenich|dss-plus|dsv|dv-trading|dv01|dyna-robotics|dynamis-inc|dynamite-games|dyopath|e-s-fitness|e-star-trading-gmbh|effectual|efficient-computer|efg-international|eigen-labs|eight-advisory|eikon-therapeutics|einride|ekimetrics|ekkiden|ekn-engineering|ey|eye-security|f-e-gmbh|f-schumacher-co|ffg-finanzcheck-finanzportale-gmbh|fti-consulting|g-in-gmbh|g-n-gruppe|g-p|g-research|gs-retail|gsa-capital|gsk|iamfluidics|ians|iata|ibex-medical-analytics|ibm|id-me|id-quantique|ideo|ideogram|idiap|ilg-au-enwerbung-gmbh|illumio|iproov|ipsy|ipx-power-usa-llc|ixl-learning|ixm|l-wen-apotheke|lg|lg-energy-solution-arizona|lgt-group|llamaindex|lloyds|lloyds-register|llr-partners|lnfusedinnovations|ltimindtree|ltk-usa|ltse|mlb-job-board-only|mq-referrals-only|ms-amlin|msc|msd|msf|mthree-recruiting-portal|mtn-group|mvz-medizinische-labore-dessau-kassel-gmbh|pdf-net|pdt-partners|pdw|pfasuiki-gmbh|pfizer|pflegehelden-franchise-gmbh|pmaconsultants|pmg|rgt-geb-udemanagement-und-technologie-gmbh|rhenus|rho|rhombus-power-inc|rhythm-software-inc|rv-tech-gmbh|rvi-planning-landscape-architecture|rzr-global-inc|s-l-connect-gmbh|sfcompute|sfox|squarepoint-capital|squarespace|squint-ai|sveriges-radio|svetness|svix|t-mobile-cz|t-rowe-price|td-international|tnt-ventures-gmbh|tpr-education-llc|typeface|typeform|typewise|tytan-technologies)(?:/|$))(?:ei[^/]*|b-[^/]*|dy[^/]*|pf[^/]*|rh[^/]*|rv[^/]*|a(?!(?:-|1|2|b|c|d|e|f|g|h|i|j|k|l|m|n|o|p|q|r|s|t|u|v|w|x|y|z))[^/]*|dh[^/]*|ef[^/]*|ll[^/]*|ty[^/]*|3[^/]*|af[^/]*|db[^/]*|mv[^/]*|rg[^/]*|ct[^/]*|e(?!(?:-|2|a|b|c|d|e|f|g|i|k|l|m|n|o|p|q|r|s|t|u|v|x|y|z))[^/]*|sq[^/]*|cm[^/]*|lg[^/]*|m(?!(?:-|1|3|9|a|b|c|d|e|g|h|i|k|l|n|o|p|q|r|s|t|u|v|y))[^/]*|id[^/]*|a-[^/]*|aq[^/]*|b(?!(?:-|1|a|b|c|d|e|g|h|i|l|m|n|o|p|r|s|t|u|v|w|y))[^/]*|g-[^/]*|s(?!(?:-|a|b|c|d|e|f|h|i|k|l|m|n|o|p|q|r|t|u|v|w|y))[^/]*|ek[^/]*|ff[^/]*|mt[^/]*|c(?!(?:1|3|6|a|b|d|e|f|g|h|i|l|m|o|r|s|t|u|v|x|y))[^/]*|e-[^/]*|i(?!(?:2|a|b|c|d|e|f|h|k|l|m|n|o|p|q|r|s|t|v|x))[^/]*|bw[^/]*|cv[^/]*|d2[^/]*|d(?!(?:-|2|3|a|b|e|h|i|k|l|m|n|o|p|r|s|u|v|y))[^/]*|il[^/]*|ip[^/]*|sv[^/]*|p(?!(?:2|a|d|e|f|h|i|j|l|m|o|p|r|s|t|u|w|y))[^/]*|t(?!(?:-|1|a|b|d|e|h|i|j|n|o|p|r|s|u|w|y|z))[^/]*|ay[^/]*|ds[^/]*|ib[^/]*|gs[^/]*|f-[^/]*|lt[^/]*|pd[^/]*|t-[^/]*|bb[^/]*|f(?!(?:-|2|a|e|f|g|h|i|j|l|n|o|p|r|t|u))[^/]*|(?![01236789abcdefghijklmnopqrstuvwxyz])[^/]+|cd[^/]*|g(?!(?:-|2|a|b|e|h|i|l|o|p|r|s|u|w|y))[^/]*|ia[^/]*|dn[^/]*|ms[^/]*|bs[^/]*|l(?!(?:-|a|e|g|i|l|m|n|o|t|u|v|x|y))[^/]*|r(?!(?:a|e|f|g|h|i|o|s|t|u|v|w|x|z))[^/]*|ln[^/]*|ml[^/]*|pm[^/]*|aj[^/]*|cs[^/]*|mq[^/]*|tn[^/]*|tp[^/]*|bc[^/]*|bt[^/]*|ix[^/]*|s-[^/]*|td[^/]*|6[^/]*|dv[^/]*|ey[^/]*|ah[^/]*|ft[^/]*|l-[^/]*|rz[^/]*|sf[^/]*|8[^/]*|aw[^/]*|cg[^/]*|bv[^/]*))",
    "/:lang(en|de|fr|it)/company/:slug((?!(?:0x|2k|7shifts|9-mothers|a11|a16z|a24|ao-shearman|b12|bd|bda|bdo|bge-inc|bhhc|bhp|bmw|bnp-paribas|c12|c6-bank|cfo-insights|cx2|d3|dkatalis|dlh|dlr-group|dmg-events|dphi-space|e2b|ebanx|ebay|eei|eos|ezcater-inc|f2-ai|f2g|fgs-global|fhgr|fjallraven|fnz|fp-robotics|g2it|gbfoods|ghost|ghx|gptzero|gwi|gymshark|i2cat|ieq-capital|ifood|ift|iherb|ikea-cz|iq|iqm|irisity|ivalua|lmarena|lvmh|lxt|m-booth|m1|m3|m9-solutions|mb-f|mbc|mdclone|mgt-insurance|mhi|mks-pamp|mntn|mphasis|p2p-org|pjt-partners|ppro|ptc|pwc|pylon|rf-smart|rsm|rtx|rwa-xyz|rxr|rxsense|sbb|sdsc|srs-acquiom|t1-energy|tbhc-delivers|tjx|tsmc|tsmg|tzdc)(?:/|$))(?:dl[^/]*|mg[^/]*|tb[^/]*|cf[^/]*|m9[^/]*|pj[^/]*|ao[^/]*|bn[^/]*|ez[^/]*|fp[^/]*|ie[^/]*|rx[^/]*|sr[^/]*|bd[^/]*|dm[^/]*|dp[^/]*|eb[^/]*|fg[^/]*|fj[^/]*|f2[^/]*|gh[^/]*|if[^/]*|t1[^/]*|ts[^/]*|9[^/]*|a1[^/]*|bh[^/]*|dk[^/]*|gy[^/]*|mb[^/]*|mk[^/]*|rf[^/]*|bg[^/]*|c6[^/]*|gb[^/]*|gp[^/]*|ik[^/]*|ir[^/]*|lm[^/]*|m-[^/]*|md[^/]*|mp[^/]*|p2[^/]*|rw[^/]*|7[^/]*|iq[^/]*|iv[^/]*|i2[^/]*|ih[^/]*|py[^/]*|fh[^/]*|g2[^/]*|lv[^/]*|mn[^/]*|pp[^/]*|sd[^/]*|tz[^/]*|a2[^/]*|b1[^/]*|bm[^/]*|c1[^/]*|cx[^/]*|e2[^/]*|ee[^/]*|eo[^/]*|fn[^/]*|gw[^/]*|lx[^/]*|mh[^/]*|pt[^/]*|pw[^/]*|rs[^/]*|rt[^/]*|sb[^/]*|tj[^/]*|d3[^/]*|m1[^/]*|m3[^/]*|0[^/]*|2[^/]*))",
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
