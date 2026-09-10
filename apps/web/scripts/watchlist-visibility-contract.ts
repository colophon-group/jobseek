export const watchlistPathLocales = ["en", "de", "fr", "it"] as const;

export type WatchlistPathLocale = (typeof watchlistPathLocales)[number];
export type OwnerSlugKind = "username" | "display_username";

export type WatchlistPathVariant = {
  locale: WatchlistPathLocale;
  ownerSlugKind: OwnerSlugKind;
  ownerSlug: string;
  pagePath: string;
  ogPath: string;
  legacyOgPathPattern: string;
  legacyOgPurgePattern: string;
};

type ApplyEnvironment = Readonly<Record<string, string | undefined>>;

type InventoryIdentity = {
  publicCount: number;
  publicInventoryDigest: string;
};

function invariant(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message);
}

function pathVariant(
  ownerSlugKind: OwnerSlugKind,
  ownerSlug: string,
  locale: WatchlistPathLocale,
  watchlistSlug: string,
): WatchlistPathVariant {
  const legacyPrefix = `/${locale}/${ownerSlug}/${watchlistSlug}/opengraph-image-`;
  return {
    locale,
    ownerSlugKind,
    ownerSlug,
    pagePath: `/${locale}/${ownerSlug}/${watchlistSlug}`,
    ogPath: `/og/watchlist/${locale}/${ownerSlug}/${watchlistSlug}`,
    legacyOgPathPattern: `${legacyPrefix}:hash`,
    legacyOgPurgePattern: `${legacyPrefix}*`,
  };
}

export function expectedWatchlistPathVariants(params: {
  ownerUsername: string | null;
  ownerDisplayUsername: string | null;
  watchlistSlug: string;
}): WatchlistPathVariant[] {
  const ownerSlugs: Array<[OwnerSlugKind, string | null]> = [
    ["username", params.ownerUsername],
    ["display_username", params.ownerDisplayUsername],
  ];
  return ownerSlugs.flatMap(([kind, value]) =>
    value === null
      ? []
      : watchlistPathLocales.map((locale) =>
          pathVariant(kind, value, locale, params.watchlistSlug),
        ),
  );
}

/**
 * Validate both views of the removal manifest:
 *
 * - every non-null source column keeps its labelled `(kind, locale)` entries;
 * - the distinct concrete URL and legacy-pattern sets contain every URL form.
 *
 * Mirrored `username`/`display_username` values therefore retain eight labelled
 * provenance entries while correctly representing four distinct URLs per form.
 */
export function assertWatchlistPathVariantMatrix(
  params: {
    watchlistId: string;
    ownerUsername: string | null;
    ownerDisplayUsername: string | null;
    watchlistSlug: string;
  },
  actual: WatchlistPathVariant[],
): void {
  const expected = expectedWatchlistPathVariants(params);
  const key = (variant: WatchlistPathVariant) =>
    `${variant.ownerSlugKind}\u0000${variant.locale}`;
  const expectedByLabel = new Map(expected.map((variant) => [key(variant), variant]));
  const actualByLabel = new Map(actual.map((variant) => [key(variant), variant]));

  invariant(
    actual.length === expected.length && actualByLabel.size === actual.length,
    `Localized labelled path inventory is incomplete or duplicated for watchlist ${params.watchlistId}`,
  );
  invariant(
    expectedByLabel.size === actualByLabel.size,
    `Localized labelled path inventory has the wrong matrix for watchlist ${params.watchlistId}`,
  );
  for (const [label, expectedVariant] of expectedByLabel) {
    const actualVariant = actualByLabel.get(label);
    invariant(
      actualVariant !== undefined &&
        actualVariant.locale === expectedVariant.locale &&
        actualVariant.ownerSlugKind === expectedVariant.ownerSlugKind &&
        actualVariant.ownerSlug === expectedVariant.ownerSlug &&
        actualVariant.pagePath === expectedVariant.pagePath &&
        actualVariant.ogPath === expectedVariant.ogPath &&
        actualVariant.legacyOgPathPattern ===
          expectedVariant.legacyOgPathPattern &&
        actualVariant.legacyOgPurgePattern ===
          expectedVariant.legacyOgPurgePattern,
      `Localized path inventory differs for ${label} on watchlist ${params.watchlistId}`,
    );
  }

  for (const field of [
    "pagePath",
    "ogPath",
    "legacyOgPathPattern",
    "legacyOgPurgePattern",
  ] as const) {
    const expectedDistinct = new Set(expected.map((variant) => variant[field]));
    const actualDistinct = new Set(actual.map((variant) => variant[field]));
    invariant(
      expectedDistinct.size === actualDistinct.size &&
        [...expectedDistinct].every((value) => actualDistinct.has(value)),
      `Distinct ${field} inventory is incomplete for watchlist ${params.watchlistId}`,
    );
  }
}

function positiveRunId(environment: ApplyEnvironment, name: string): number {
  const raw = environment[name];
  const parsed = Number(raw);
  invariant(
    raw && /^\d+$/.test(raw) && Number.isSafeInteger(parsed) && parsed > 0,
    `${name} must be a positive immutable run ID`,
  );
  return parsed;
}

function deployedSha(environment: ApplyEnvironment, name: string): string {
  const value = environment[name];
  invariant(
    value && /^[0-9a-f]{40}$/.test(value),
    `${name} must be a deployed 40-hex SHA`,
  );
  return value;
}

function requiredNonBlankString(
  environment: ApplyEnvironment,
  name: string,
): string {
  const value = environment[name];
  invariant(
    typeof value === "string" && value.trim().length > 0,
    `${name} must be a non-empty string`,
  );
  return value.trim();
}

export function requiredWatchlistApplyEvidence(
  environment: ApplyEnvironment,
  inventory: InventoryIdentity,
) {
  invariant(
    environment.WATCHLIST_PRIVACY_CONFIRMATION === "PRIVATE-WATCHLISTS-0089",
    "Apply confirmation is invalid",
  );
  const routeCutoverApprovedBy = requiredNonBlankString(
    environment,
    "WATCHLIST_ROUTE_CUTOVER_APPROVED_BY",
  );

  return {
    confirmation: environment.WATCHLIST_PRIVACY_CONFIRMATION,
    backupRestoreRunId: positiveRunId(
      environment,
      "WATCHLIST_PRIVACY_BACKUP_RESTORE_RUN_ID",
    ),
    privateMutationsDeploySha: deployedSha(
      environment,
      "WATCHLIST_PRIVATE_MUTATIONS_DEPLOY_SHA",
    ),
    routeCutoverDeploySha: deployedSha(
      environment,
      "WATCHLIST_ROUTE_CUTOVER_DEPLOY_SHA",
    ),
    routeCutoverApprovedBy,
    publicApiCutoverDeploySha: deployedSha(
      environment,
      "WATCHLIST_PUBLIC_API_CUTOVER_DEPLOY_SHA",
    ),
    publicApiCutoverVerificationRunId: positiveRunId(
      environment,
      "WATCHLIST_PUBLIC_API_CUTOVER_VERIFICATION_RUN_ID",
    ),
    expectedPublicCount: inventory.publicCount,
    expectedPublicDigest: inventory.publicInventoryDigest,
  };
}
