import { sql } from "drizzle-orm";
import {
  pgTable,
  pgEnum,
  uuid,
  text,
  boolean,
  smallint,
  integer,
  bigint,
  real,
  numeric,
  timestamp,
  index,
  uniqueIndex,
  primaryKey,
  jsonb,
} from "drizzle-orm/pg-core";

// ── Better Auth core tables ──────────────────────────────────────────

export const user = pgTable("user", {
  id: text("id").primaryKey(),
  name: text("name").notNull(),
  email: text("email").notNull().unique(),
  emailVerified: boolean("email_verified").default(false).notNull(),
  image: text("image"),
  createdAt: timestamp("created_at").defaultNow().notNull(),
  updatedAt: timestamp("updated_at")
    .defaultNow()
    .$onUpdate(() => new Date())
    .notNull(),
});

export const session = pgTable(
  "session",
  {
    id: text("id").primaryKey(),
    expiresAt: timestamp("expires_at").notNull(),
    token: text("token").notNull().unique(),
    createdAt: timestamp("created_at").defaultNow().notNull(),
    updatedAt: timestamp("updated_at")
      .$onUpdate(() => new Date())
      .notNull(),
    ipAddress: text("ip_address"),
    userAgent: text("user_agent"),
    userId: text("user_id")
      .notNull()
      .references(() => user.id, { onDelete: "cascade" }),
  },
  (table) => [index("session_userId_idx").on(table.userId)],
);

export const account = pgTable(
  "account",
  {
    id: text("id").primaryKey(),
    accountId: text("account_id").notNull(),
    providerId: text("provider_id").notNull(),
    userId: text("user_id")
      .notNull()
      .references(() => user.id, { onDelete: "cascade" }),
    accessToken: text("access_token"),
    refreshToken: text("refresh_token"),
    idToken: text("id_token"),
    accessTokenExpiresAt: timestamp("access_token_expires_at"),
    refreshTokenExpiresAt: timestamp("refresh_token_expires_at"),
    scope: text("scope"),
    password: text("password"),
    createdAt: timestamp("created_at").defaultNow().notNull(),
    updatedAt: timestamp("updated_at")
      .$onUpdate(() => new Date())
      .notNull(),
  },
  (table) => [index("account_userId_idx").on(table.userId)],
);

export const verification = pgTable(
  "verification",
  {
    id: text("id").primaryKey(),
    identifier: text("identifier").notNull(),
    value: text("value").notNull(),
    expiresAt: timestamp("expires_at").notNull(),
    createdAt: timestamp("created_at").defaultNow().notNull(),
    updatedAt: timestamp("updated_at")
      .defaultNow()
      .$onUpdate(() => new Date())
      .notNull(),
  },
  (table) => [index("verification_identifier_idx").on(table.identifier)],
);

// ── User preferences (1:1 with user) ────────────────────────────────

export const userPreferences = pgTable("user_preferences", {
  id: uuid("id").defaultRandom().primaryKey(),
  userId: text("user_id")
    .notNull()
    .unique()
    .references(() => user.id, { onDelete: "cascade" }),
  theme: text("theme", { enum: ["light", "dark"] }).default("light").notNull(),
  locale: text("locale", { enum: ["en", "de", "fr", "it"] })
    .default("en")
    .notNull(),
  cookieConsent: boolean("cookie_consent").default(false).notNull(),
  themeUpdatedAt: timestamp("theme_updated_at"),
  localeUpdatedAt: timestamp("locale_updated_at"),
  lastPasswordResetAt: timestamp("last_password_reset_at"),
  updatedAt: timestamp("updated_at")
    .defaultNow()
    .$onUpdate(() => new Date())
    .notNull(),
});

// ── Location tables (GeoNames-seeded hierarchy) ─────────────────────

export const locationTypeEnum = pgEnum("location_type", [
  "macro",
  "country",
  "region",
  "city",
]);

export const location = pgTable(
  "location",
  {
    id: integer("id").primaryKey(),
    parentId: integer("parent_id"),
    type: locationTypeEnum("type").notNull(),
    population: integer("population"),
    lat: real("lat"),
    lng: real("lng"),
  },
  (table) => [
    index("idx_loc_parent").on(table.parentId),
    index("idx_loc_type").on(table.type),
  ],
);

export const locationName = pgTable(
  "location_name",
  {
    locationId: integer("location_id")
      .notNull()
      .references(() => location.id, { onDelete: "cascade" }),
    locale: text("locale").notNull(),
    name: text("name").notNull(),
    isDisplay: boolean("is_display").default(false).notNull(),
  },
  (table) => [
    primaryKey({ columns: [table.locationId, table.locale, table.name] }),
    index("idx_locname_lower").on(sql`lower(name)`, table.locale),
    index("idx_locname_display")
      .on(table.locationId, table.locale)
      .where(sql`is_display = true`),
  ],
);

export const locationMacroMember = pgTable(
  "location_macro_member",
  {
    macroId: integer("macro_id")
      .notNull()
      .references(() => location.id, { onDelete: "cascade" }),
    countryId: integer("country_id")
      .notNull()
      .references(() => location.id, { onDelete: "cascade" }),
  },
  (table) => [primaryKey({ columns: [table.macroId, table.countryId] })],
);

// ── App-specific tables ──────────────────────────────────────────────

export const subscription = pgTable("subscription", {
  id: uuid("id").defaultRandom().primaryKey(),
  userId: text("user_id")
    .notNull()
    .references(() => user.id, { onDelete: "cascade" }),
  plan: text("plan", { enum: ["free", "unlimited"] }).notNull(),
  status: text("status", { enum: ["active", "cancelled", "expired"] }).notNull(),
  startsAt: timestamp("starts_at").notNull(),
  endsAt: timestamp("ends_at"),
  createdAt: timestamp("created_at").defaultNow().notNull(),
  updatedAt: timestamp("updated_at").defaultNow().notNull(),
});

export const industry = pgTable("industry", {
  id: smallint("id").primaryKey(),
  name: text("name").notNull().unique(),
});

export const company = pgTable("company", {
  id: uuid("id").defaultRandom().primaryKey(),
  name: text("name").notNull(),
  slug: text("slug").unique().notNull(),
  logo: text("logo"),
  icon: text("icon"),
  logoType: text("logo_type", { enum: ["wordmark", "wordmark+icon", "icon"] }),
  website: text("website"),
  description: text("description"),
  industry: smallint("industry").references(() => industry.id),
  employeeCountRange: smallint("employee_count_range"),
  foundedYear: smallint("founded_year"),
  hqLocationId: integer("hq_location_id"),
  extras: jsonb("extras").default({}),
  createdAt: timestamp("created_at").defaultNow().notNull(),
  updatedAt: timestamp("updated_at").defaultNow().notNull(),
});

export const jobBoard = pgTable(
  "job_board",
  {
    id: uuid("id").defaultRandom().primaryKey(),
    companyId: uuid("company_id")
      .notNull()
      .references(() => company.id, { onDelete: "cascade" }),
    boardSlug: text("board_slug").unique(),
    crawlerType: text("crawler_type"),
    boardUrl: text("board_url").notNull().unique(),

    checkIntervalMinutes: integer("check_interval_minutes").notNull().default(60),
    nextCheckAt: timestamp("next_check_at", { withTimezone: true })
      .defaultNow()
      .notNull(),
    lastCheckedAt: timestamp("last_checked_at", { withTimezone: true }),
    lastSuccessAt: timestamp("last_success_at", { withTimezone: true }),

    consecutiveFailures: integer("consecutive_failures").default(0).notNull(),
    lastError: text("last_error"),
    isEnabled: boolean("is_enabled").default(true).notNull(),

    // ── Scheduler fields ──
    boardStatus: text("board_status", {
      enum: ["active", "suspect", "gone", "disabled"],
    })
      .default("active")
      .notNull(),
    throttleKey: text("throttle_key"),
    leaseOwner: text("lease_owner"),
    leasedUntil: timestamp("leased_until", { withTimezone: true }),
    emptyCheckCount: integer("empty_check_count").default(0).notNull(),
    lastNonEmptyAt: timestamp("last_non_empty_at", { withTimezone: true }),
    goneAt: timestamp("gone_at", { withTimezone: true }),

    metadata: jsonb("metadata").default({}),
    scrapeIntervalHours: integer("scrape_interval_hours").default(24).notNull(),

    createdAt: timestamp("created_at", { withTimezone: true }).defaultNow().notNull(),
    updatedAt: timestamp("updated_at", { withTimezone: true }).defaultNow().notNull(),
  },
  (table) => [
    index("idx_jb_company").on(table.companyId),
    index("idx_jb_due").on(table.nextCheckAt, table.throttleKey).where(
      sql`board_status IN ('active', 'suspect') AND is_enabled = true`,
    ),
    index("idx_jb_lease").on(table.leasedUntil).where(
      sql`leased_until IS NOT NULL`,
    ),
  ],
);

export const jobPosting = pgTable(
  "job_posting",
  {
    id: uuid("id").defaultRandom().primaryKey(),
    companyId: uuid("company_id")
      .notNull()
      .references(() => company.id, { onDelete: "cascade" }),
    boardId: uuid("board_id").references(() => jobBoard.id, {
      onDelete: "set null",
    }),

    // ── Core fields ──
    isActive: boolean("is_active").default(true).notNull(),
    locales: text("locales").array().notNull().default([]),
    titles: text("titles").array().notNull().default([]),
    locationIds: integer("location_ids").array(),
    locationTypes: text("location_types").array(),
    descriptionR2Hash: bigint("description_r2_hash", { mode: "bigint" }),

    employmentType: text("employment_type"),

    // ── Identity & lifecycle ──
    sourceUrl: text("source_url").unique().notNull(),
    firstSeenAt: timestamp("first_seen_at", { withTimezone: true })
      .defaultNow()
      .notNull(),
    lastSeenAt: timestamp("last_seen_at", { withTimezone: true }),

    // ── Scrape scheduler fields ──
    nextScrapeAt: timestamp("next_scrape_at", { withTimezone: true }),
    lastScrapedAt: timestamp("last_scraped_at", { withTimezone: true }),
    leasedUntil: timestamp("leased_until", { withTimezone: true }),
    scrapeFailures: integer("scrape_failures").default(0).notNull(),
    missingCount: integer("missing_count").default(0).notNull(),

    // ── Enrichment fields ──
    enrichment: jsonb("enrichment"),
    toBeEnriched: boolean("to_be_enriched").default(true).notNull(),
    enrichVersion: integer("enrich_version").default(0).notNull(),
    lastEnrichedAt: timestamp("last_enriched_at", { withTimezone: true }),
  },
  (table) => [
    index("idx_jp_company").on(table.companyId),
    index("idx_jp_board_url").on(table.boardId, table.sourceUrl),
    index("idx_jp_active").on(table.isActive).where(sql`is_active = true`),
    index("idx_jp_location_ids").using("gin", table.locationIds),
    index("idx_jp_next_scrape").on(table.nextScrapeAt).where(
      sql`is_active = true AND next_scrape_at IS NOT NULL`,
    ),
    index("idx_jp_lease").on(table.leasedUntil).where(
      sql`leased_until IS NOT NULL`,
    ),
    index("idx_jp_to_be_enriched").on(table.toBeEnriched).where(
      sql`is_active = true AND to_be_enriched = true`,
    ),
  ],
);

export const savedJob = pgTable(
  "saved_job",
  {
    id: uuid("id").defaultRandom().primaryKey(),
    userId: text("user_id")
      .notNull()
      .references(() => user.id, { onDelete: "cascade" }),
    jobPostingId: uuid("job_posting_id")
      .notNull()
      .references(() => jobPosting.id, { onDelete: "cascade" }),
    savedAt: timestamp("saved_at", { withTimezone: true }).defaultNow().notNull(),
  },
  (table) => [
    uniqueIndex("idx_sj_user_posting").on(table.userId, table.jobPostingId),
    index("idx_sj_user_saved_at").on(table.userId, table.savedAt),
  ],
);

export const companyRequest = pgTable(
  "company_request",
  {
    id: uuid("id").defaultRandom().primaryKey(),
    input: text("input").notNull().unique(),
    count: integer("count").notNull().default(1),
    lastUserHint: jsonb("last_user_hint"),
    status: text("status", {
      enum: ["pending", "processing", "screening_passed", "completed", "rejected", "failed"],
    })
      .notNull()
      .default("pending"),
    resolvedCompanyId: uuid("resolved_company_id").references(
      () => company.id,
      { onDelete: "set null" },
    ),
    resolvedJobBoardId: uuid("resolved_job_board_id").references(
      () => jobBoard.id,
      { onDelete: "set null" },
    ),
    retries: integer("retries").notNull().default(0),
    maxRetries: integer("max_retries").notNull().default(3),
    errorMessage: text("error_message"),
    githubIssueNumber: integer("github_issue_number"),
    createdAt: timestamp("created_at", { withTimezone: true })
      .defaultNow()
      .notNull(),
    updatedAt: timestamp("updated_at", { withTimezone: true })
      .defaultNow()
      .notNull(),
  },
  (table) => [
    index("idx_cr_status").on(table.status).where(sql`status = 'pending'`),
  ],
);

// ── Enrichment batch tracking ───────────────────────────────────────

export const enrichBatch = pgTable("enrich_batch", {
  id: text("id").primaryKey(),
  provider: text("provider").notNull(),
  model: text("model").notNull(),
  status: text("status", {
    enum: ["submitted", "completed", "failed", "expired"],
  })
    .default("submitted")
    .notNull(),
  itemCount: integer("item_count").notNull(),
  postingIds: uuid("posting_ids").array().notNull(),
  submittedAt: timestamp("submitted_at", { withTimezone: true })
    .defaultNow()
    .notNull(),
  completedAt: timestamp("completed_at", { withTimezone: true }),
  inputTokens: integer("input_tokens"),
  outputTokens: integer("output_tokens"),
  estimatedCostUsd: numeric("estimated_cost_usd", {
    precision: 10,
    scale: 4,
  }),
  error: text("error"),
});

