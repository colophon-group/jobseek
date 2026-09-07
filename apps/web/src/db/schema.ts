import { sql } from "drizzle-orm";
import {
  pgTable,
  pgEnum,
  uuid,
  text,
  boolean,
  smallint,
  integer,
  real,
  numeric,
  timestamp,
  index,
  uniqueIndex,
  check,
  foreignKey,
  primaryKey,
  jsonb,
  type AnyPgColumn,
} from "drizzle-orm/pg-core";
import {
  NOTIFICATION_CADENCE_VALUES,
  NOTIFICATION_DELIVERY_STATUS_VALUES,
} from "../lib/notifications/contracts";

// ── Better Auth core tables ──────────────────────────────────────────

export const user = pgTable("user", {
  id: text("id").primaryKey(),
  name: text("name").notNull(),
  email: text("email").notNull().unique(),
  emailVerified: boolean("email_verified").default(false).notNull(),
  image: text("image"),
  username: text("username").unique(),
  displayUsername: text("display_username"),
  createdAt: timestamp("created_at", { withTimezone: true })
    .defaultNow()
    .notNull(),
  updatedAt: timestamp("updated_at", { withTimezone: true })
    .defaultNow()
    .$onUpdate(() => new Date())
    .notNull(),
});

export const session = pgTable(
  "session",
  {
    id: text("id").primaryKey(),
    expiresAt: timestamp("expires_at", { withTimezone: true }).notNull(),
    token: text("token").notNull().unique(),
    createdAt: timestamp("created_at", { withTimezone: true })
      .defaultNow()
      .notNull(),
    updatedAt: timestamp("updated_at", { withTimezone: true })
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
    issuer: text("issuer").notNull(),
    accountId: text("account_id").notNull(),
    providerId: text("provider_id").notNull(),
    userId: text("user_id")
      .notNull()
      .references(() => user.id, { onDelete: "cascade" }),
    accessToken: text("access_token"),
    refreshToken: text("refresh_token"),
    idToken: text("id_token"),
    accessTokenExpiresAt: timestamp("access_token_expires_at", {
      withTimezone: true,
    }),
    refreshTokenExpiresAt: timestamp("refresh_token_expires_at", {
      withTimezone: true,
    }),
    scope: text("scope"),
    password: text("password"),
    createdAt: timestamp("created_at", { withTimezone: true })
      .defaultNow()
      .notNull(),
    updatedAt: timestamp("updated_at", { withTimezone: true })
      .$onUpdate(() => new Date())
      .notNull(),
  },
  (table) => [
    index("account_userId_idx").on(table.userId),
    uniqueIndex("account_issuer_account_id_uidx").on(
      table.issuer,
      table.accountId,
    ),
  ],
);

export const verification = pgTable(
  "verification",
  {
    id: text("id").primaryKey(),
    identifier: text("identifier").notNull(),
    value: text("value").notNull(),
    expiresAt: timestamp("expires_at", { withTimezone: true }).notNull(),
    createdAt: timestamp("created_at", { withTimezone: true })
      .defaultNow()
      .notNull(),
    updatedAt: timestamp("updated_at", { withTimezone: true })
      .defaultNow()
      .$onUpdate(() => new Date())
      .notNull(),
  },
  (table) => [index("verification_identifier_idx").on(table.identifier)],
);

// ── User preferences (1:1 with user) ────────────────────────────────

export const notificationCadenceEnum = pgEnum(
  "notification_cadence",
  NOTIFICATION_CADENCE_VALUES,
);

export const notificationDeliveryStatusEnum = pgEnum(
  "notification_delivery_status",
  NOTIFICATION_DELIVERY_STATUS_VALUES,
);

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
  jobLanguages: text("job_languages").array().notNull().default([]),
  displayCurrency: text("display_currency").default("EUR").notNull(),
  salaryPeriod: text("salary_period"),
  cookieConsent: boolean("cookie_consent").default(false).notNull(),
  dismissedBanners: text("dismissed_banners").array().notNull().default([]),
  notificationCadence: notificationCadenceEnum("notification_cadence")
    .default("weekly")
    .notNull(),
  notificationsPaused: boolean("notifications_paused")
    .default(false)
    .notNull(),
  notificationsStateChangedAt: timestamp(
    "notifications_state_changed_at",
    { withTimezone: true },
  )
    .defaultNow()
    .notNull(),
  themeUpdatedAt: timestamp("theme_updated_at", { withTimezone: true }),
  localeUpdatedAt: timestamp("locale_updated_at", { withTimezone: true }),
  lastPasswordResetAt: timestamp("last_password_reset_at", {
    withTimezone: true,
  }),
  updatedAt: timestamp("updated_at", { withTimezone: true })
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
    slug: text("slug").unique(),
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

// ── Occupation domain tables (taxonomy-managed) ─────────────────────

export const occupationDomain = pgTable("occupation_domain", {
  id: integer("id").primaryKey().generatedAlwaysAsIdentity(),
  slug: text("slug").notNull().unique(),
});

export const occupationDomainName = pgTable(
  "occupation_domain_name",
  {
    domainId: integer("domain_id")
      .notNull()
      .references(() => occupationDomain.id, { onDelete: "cascade" }),
    locale: text("locale").notNull(),
    name: text("name").notNull(),
    isDisplay: boolean("is_display").default(true).notNull(),
  },
  (table) => [
    primaryKey({ columns: [table.domainId, table.locale, table.name] }),
    index("idx_domname_lower").on(sql`lower(name)`, table.locale),
    index("idx_domname_display")
      .on(table.domainId, table.locale)
      .where(sql`is_display = true`),
  ],
);

// ── Occupation tables (taxonomy-managed) ─────────────────────────────

export const occupation = pgTable(
  "occupation",
  {
    id: integer("id").primaryKey().generatedAlwaysAsIdentity(),
    slug: text("slug").notNull().unique(),
    parentId: integer("parent_id"),
    domainId: integer("domain_id").references(() => occupationDomain.id),
  },
  (table) => [
    index("idx_occupation_parent")
      .on(table.parentId)
      .where(sql`parent_id IS NOT NULL`),
    index("idx_occupation_domain")
      .on(table.domainId)
      .where(sql`domain_id IS NOT NULL`),
  ],
);

export const occupationName = pgTable(
  "occupation_name",
  {
    occupationId: integer("occupation_id")
      .notNull()
      .references(() => occupation.id, { onDelete: "cascade" }),
    locale: text("locale").notNull(),
    name: text("name").notNull(),
    isDisplay: boolean("is_display").default(true).notNull(),
  },
  (table) => [
    primaryKey({ columns: [table.occupationId, table.locale, table.name] }),
    index("idx_occname_lower").on(sql`lower(name)`, table.locale),
    index("idx_occname_display")
      .on(table.occupationId, table.locale)
      .where(sql`is_display = true`),
  ],
);

// ── Seniority tables (taxonomy-managed) ──────────────────────────────

export const seniority = pgTable("seniority", {
  id: integer("id").primaryKey().generatedAlwaysAsIdentity(),
  slug: text("slug").notNull().unique(),
});

export const seniorityName = pgTable(
  "seniority_name",
  {
    seniorityId: integer("seniority_id")
      .notNull()
      .references(() => seniority.id, { onDelete: "cascade" }),
    locale: text("locale").notNull(),
    name: text("name").notNull(),
    isDisplay: boolean("is_display").default(true).notNull(),
  },
  (table) => [
    primaryKey({ columns: [table.seniorityId, table.locale, table.name] }),
    index("idx_senname_lower").on(sql`lower(name)`, table.locale),
    index("idx_senname_display")
      .on(table.seniorityId, table.locale)
      .where(sql`is_display = true`),
  ],
);

// ── Technology table (taxonomy-managed) ──────────────────────────────

export const technology = pgTable("technology", {
  id: integer("id").primaryKey().generatedAlwaysAsIdentity(),
  slug: text("slug").notNull().unique(),
  name: text("name"),
  category: text("category"),
});

// ── Taxonomy miss tracking ──────────────────────────────────────────

export const taxonomyMiss = pgTable(
  "taxonomy_miss",
  {
    id: integer("id").primaryKey().generatedAlwaysAsIdentity(),
    taxonomy: text("taxonomy").notNull(),
    rawValue: text("raw_value").notNull(),
    sampleValue: text("sample_value").notNull(),
    hitCount: integer("hit_count").notNull().default(1),
    firstSeenAt: timestamp("first_seen_at", { withTimezone: true })
      .defaultNow()
      .notNull(),
    lastSeenAt: timestamp("last_seen_at", { withTimezone: true })
      .defaultNow()
      .notNull(),
    status: text("status", { enum: ["pending", "resolved", "discarded"] })
      .default("pending")
      .notNull(),
    resolvedTo: text("resolved_to"),
  },
  (table) => [
    uniqueIndex("idx_tm_taxonomy_raw").on(table.taxonomy, table.rawValue),
    index("idx_tm_pending")
      .on(table.taxonomy, table.hitCount)
      .where(sql`status = 'pending'`),
  ],
);

// ── Currency rate table (ECB daily rates) ────────────────────────────

export const currencyRate = pgTable("currency_rate", {
  currency: text("currency").primaryKey(),
  toEur: numeric("to_eur", { precision: 10, scale: 6 }).notNull(),
  updatedAt: timestamp("updated_at", { withTimezone: true }).defaultNow().notNull(),
});

// ── App-specific tables ──────────────────────────────────────────────

export const subscription = pgTable(
  "subscription",
  {
    id: uuid("id").defaultRandom().primaryKey(),
    userId: text("user_id")
      .notNull()
      .references(() => user.id, { onDelete: "cascade" }),
    plan: text("plan", { enum: ["free", "unlimited"] }).notNull(),
    status: text("status", { enum: ["active", "cancelled", "expired"] }).notNull(),
    stripeCustomerId: text("stripe_customer_id"),
    stripeSubscriptionId: text("stripe_subscription_id"),
    startsAt: timestamp("starts_at").notNull(),
    endsAt: timestamp("ends_at"),
    createdAt: timestamp("created_at").defaultNow().notNull(),
    updatedAt: timestamp("updated_at").defaultNow().notNull(),
  },
  (table) => [
    uniqueIndex("idx_sub_user").on(table.userId),
    uniqueIndex("idx_sub_stripe_customer")
      .on(table.stripeCustomerId)
      .where(sql`stripe_customer_id IS NOT NULL`),
    uniqueIndex("idx_sub_stripe_subscription")
      .on(table.stripeSubscriptionId)
      .where(sql`stripe_subscription_id IS NOT NULL`),
  ],
);

export const industry = pgTable("industry", {
  id: smallint("id").primaryKey(),
  name: text("name").notNull().unique(),
});

export const industryName = pgTable(
  "industry_name",
  {
    industryId: smallint("industry_id")
      .notNull()
      .references(() => industry.id, { onDelete: "cascade" }),
    locale: text("locale").notNull(),
    name: text("name").notNull(),
    isDisplay: boolean("is_display").default(true).notNull(),
  },
  (table) => [
    primaryKey({ columns: [table.industryId, table.locale, table.name] }),
    index("idx_indname_lower").on(sql`lower(name)`, table.locale),
    index("idx_indname_display")
      .on(table.industryId, table.locale)
      .where(sql`is_display = true`),
  ],
);

export const companyDescription = pgTable(
  "company_description",
  {
    companyId: uuid("company_id")
      .notNull()
      .references(() => company.id, { onDelete: "cascade" }),
    locale: text("locale").notNull(),
    description: text("description").notNull(),
  },
  (table) => [primaryKey({ columns: [table.companyId, table.locale] })],
);

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

    // ── Browser flags (computed by sync from crawler_type + metadata) ──
    monitorNeedsBrowser: boolean("monitor_needs_browser").default(false).notNull(),
    scraperNeedsBrowser: boolean("scraper_needs_browser").default(false).notNull(),

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

export const savedJob = pgTable(
  "saved_job",
  {
    id: uuid("id").defaultRandom().primaryKey(),
    userId: text("user_id")
      .notNull()
      .references(() => user.id, { onDelete: "cascade" }),
    // Durable external posting identity. 0085 deliberately removes the FK to
    // the crawler-owned job_posting mirror while preserving NOT NULL and the
    // per-user unique index.
    jobPostingId: uuid("job_posting_id").notNull(),
    savedAt: timestamp("saved_at", { withTimezone: true }).defaultNow().notNull(),
    postingTitle: text("posting_title").notNull(),
    postingSourceUrl: text("posting_source_url").notNull(),
    postingFirstSeenAt: timestamp("posting_first_seen_at", { withTimezone: true })
      .notNull(),
    postingIsActive: boolean("posting_is_active").notNull(),
    postingSalaryMin: integer("posting_salary_min"),
    postingSalaryMax: integer("posting_salary_max"),
    postingSalaryCurrency: text("posting_salary_currency"),
    postingSalaryPeriod: text("posting_salary_period"),
    companyId: uuid("company_id").notNull(),
    companyName: text("company_name").notNull(),
    companySlug: text("company_slug").notNull(),
    companyIcon: text("company_icon"),

    // ── Application tracker fields ──
    status: text("status", {
      enum: ["saved", "applied", "interviewing", "offered", "rejected"],
    })
      .default("saved")
      .notNull(),
    statusChangedAt: timestamp("status_changed_at", { withTimezone: true })
      .defaultNow()
      .notNull(),
    appliedAt: timestamp("applied_at", { withTimezone: true }),
    rejectedAt: timestamp("rejected_at", { withTimezone: true }),
    offeredAt: timestamp("offered_at", { withTimezone: true }),
    salaryMinOverride: integer("salary_min_override"),
    salaryMaxOverride: integer("salary_max_override"),
    salaryCurrencyOverride: text("salary_currency_override"),
    salaryPeriodOverride: text("salary_period_override"),
  },
  (table) => [
    check(
      "saved_job_snapshot_text_nonblank_check",
      sql`NULLIF(btrim(${table.postingTitle}), '') IS NOT NULL
        AND NULLIF(btrim(${table.postingSourceUrl}), '') IS NOT NULL
        AND NULLIF(btrim(${table.companyName}), '') IS NOT NULL
        AND NULLIF(btrim(${table.companySlug}), '') IS NOT NULL`,
    ),
    uniqueIndex("idx_sj_user_posting").on(table.userId, table.jobPostingId),
    index("idx_sj_user_saved_at").on(table.userId, table.savedAt),
    index("idx_sj_user_status").on(table.userId, table.status),
    index("idx_sj_user_status_changed").on(table.userId, table.statusChangedAt),
  ],
);

export const applicationInterview = pgTable(
  "application_interview",
  {
    id: uuid("id").defaultRandom().primaryKey(),
    savedJobId: uuid("saved_job_id").notNull(),
    round: smallint("round").notNull(),
    type: text("type", {
      enum: [
        "interview",
        "phone_screen",
        "video_call",
        "technical",
        "coding",
        "system_design",
        "behavioral",
        "onsite",
        "panel",
        "hiring_manager",
        "other",
      ],
    }).notNull(),
    scheduledAt: timestamp("scheduled_at", { withTimezone: true }),
    createdAt: timestamp("created_at", { withTimezone: true }).defaultNow().notNull(),
  },
  (table) => [
    // UNIQUE on (saved_job_id, round) — closes the duplicate-round race
    // between concurrent addInterview calls and the updateJobStatus
    // auto-create branch (#3114 / #3160). Application layer pairs this
    // with ON CONFLICT DO NOTHING and a single-statement
    // INSERT … SELECT max(round)+1 …
    uniqueIndex("idx_ai_saved_job_round").on(table.savedJobId, table.round),
    foreignKey({
      columns: [table.savedJobId],
      foreignColumns: [savedJob.id],
      name: "application_interview_saved_job_id_fkey",
    }).onDelete("cascade"),
  ],
);

export const followedCompany = pgTable(
  "followed_company",
  {
    id: uuid("id").defaultRandom().primaryKey(),
    userId: text("user_id")
      .notNull()
      .references(() => user.id, { onDelete: "cascade" }),
    companyId: uuid("company_id")
      .notNull()
      .references(() => company.id, { onDelete: "cascade" }),
    followedAt: timestamp("followed_at", { withTimezone: true }).defaultNow().notNull(),
  },
  (table) => [
    uniqueIndex("idx_fc_user_company").on(table.userId, table.companyId),
    index("idx_fc_user_followed_at").on(table.userId, table.followedAt),
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

// ── Hiring Signals and Outreach ─────────────────────────────────────

export const hiringSignal = pgTable(
  "hiring_signal",
  {
    id: uuid("id").defaultRandom().primaryKey(),
    companyId: uuid("company_id")
      .notNull()
      .references(() => company.id, { onDelete: "cascade" }),
    signalType: text("signal_type").notNull(),
    signalText: text("signal_text").notNull(),
    signalDate: timestamp("signal_date", { withTimezone: true }).notNull(),
    sourceId: text("source_id").notNull(),
    score: real("score").default(0).notNull(),
    reasoning: text("reasoning"),
    metadata: jsonb("metadata").default({}),
    createdAt: timestamp("created_at", { withTimezone: true })
      .defaultNow()
      .notNull(),
    updatedAt: timestamp("updated_at", { withTimezone: true })
      .defaultNow()
      .notNull(),
  },
  (table) => [
    index("idx_hs_company").on(table.companyId),
    index("idx_hs_type").on(table.signalType),
  ],
);

export const outreachDraft = pgTable(
  "outreach_draft",
  {
    id: uuid("id").defaultRandom().primaryKey(),
    signalId: uuid("signal_id")
      .notNull()
      .references(() => hiringSignal.id, { onDelete: "cascade" }),
    contactName: text("contact_name").notNull(),
    contactTitle: text("contact_title"),
    contactEmail: text("contact_email"),
    subject: text("subject").notNull(),
    body: text("body").notNull(),
    status: text("status", {
      enum: ["pending_review", "sent", "archived"],
    })
      .default("pending_review")
      .notNull(),
    createdAt: timestamp("created_at", { withTimezone: true })
      .defaultNow()
      .notNull(),
    updatedAt: timestamp("updated_at", { withTimezone: true })
      .defaultNow()
      .notNull(),
  },
  (table) => [index("idx_od_signal").on(table.signalId)],
);

// ── Watchlist tables ────────────────────────────────────────────────

export const watchlist = pgTable(
  "watchlist",
  {
    id: uuid("id").defaultRandom().primaryKey(),
    userId: text("user_id")
      .notNull()
      .references(() => user.id, { onDelete: "cascade" }),
    slug: text("slug").notNull(),
    title: text("title").notNull(),
    description: text("description"),
    isPublic: boolean("is_public").default(false).notNull(),
    alertsEnabled: boolean("alerts_enabled").default(false).notNull(),
    alertsEnabledAt: timestamp("alerts_enabled_at", { withTimezone: true }),
    filters: jsonb("filters").default({}).notNull(),
    sourceWatchlistId: uuid("source_watchlist_id").references((): AnyPgColumn => watchlist.id, {
      onDelete: "set null",
    }),
    lastAccessedAt: timestamp("last_accessed_at", { withTimezone: true })
      .defaultNow()
      .notNull(),
    createdAt: timestamp("created_at", { withTimezone: true })
      .defaultNow()
      .notNull(),
    updatedAt: timestamp("updated_at", { withTimezone: true })
      .defaultNow()
      .$onUpdate(() => new Date())
      .notNull(),
  },
  (table) => [
    uniqueIndex("idx_wl_user_slug").on(table.userId, table.slug),
    index("idx_wl_user_accessed").on(table.userId, table.lastAccessedAt),
    index("idx_wl_public")
      .on(table.isPublic)
      .where(sql`is_public = true`),
    check(
      "watchlist_alerts_enabled_at_check",
      sql`(${table.alertsEnabled} AND ${table.alertsEnabledAt} IS NOT NULL)
        OR (NOT ${table.alertsEnabled} AND ${table.alertsEnabledAt} IS NULL)`,
    ),
  ],
);

export const notificationDelivery = pgTable(
  "notification_delivery",
  {
    id: uuid("id").defaultRandom().primaryKey(),
    userId: text("user_id")
      .notNull()
      .references(() => user.id, { onDelete: "cascade" }),
    cadence: notificationCadenceEnum("cadence").notNull(),
    scheduledFor: timestamp("scheduled_for", { withTimezone: true }).notNull(),
    windowStart: timestamp("window_start", { withTimezone: true }).notNull(),
    windowEnd: timestamp("window_end", { withTimezone: true }).notNull(),
    status: notificationDeliveryStatusEnum("status")
      .default("pending")
      .notNull(),
    matchCount: integer("match_count"),
    idempotencyKey: text("idempotency_key").notNull(),
    providerMessageId: text("provider_message_id"),
    providerAttemptCount: integer("provider_attempt_count")
      .default(0)
      .notNull(),
    lastProviderAttemptAt: timestamp("last_provider_attempt_at", {
      withTimezone: true,
    }),
    lastErrorCode: text("last_error_code"),
    deferredUntil: timestamp("deferred_until", { withTimezone: true }),
    completedAt: timestamp("completed_at", { withTimezone: true }),
    createdAt: timestamp("created_at", { withTimezone: true })
      .defaultNow()
      .notNull(),
    updatedAt: timestamp("updated_at", { withTimezone: true })
      .defaultNow()
      .$onUpdate(() => new Date())
      .notNull(),
  },
  (table) => [
    uniqueIndex("notification_delivery_period_uidx").on(
      table.userId,
      table.cadence,
      table.scheduledFor,
    ),
    uniqueIndex("notification_delivery_idempotency_key_uidx").on(
      table.idempotencyKey,
    ),
    index("notification_delivery_due_idx").on(
      table.status,
      table.scheduledFor,
    ),
    index("notification_delivery_user_window_idx").on(
      table.userId,
      table.windowEnd,
    ),
    check(
      "notification_delivery_window_check",
      sql`${table.windowStart} < ${table.windowEnd}
        AND ${table.windowEnd} <= ${table.scheduledFor}`,
    ),
    check(
      "notification_delivery_match_count_check",
      sql`${table.matchCount} IS NULL OR ${table.matchCount} >= 0`,
    ),
    check(
      "notification_delivery_attempt_count_check",
      sql`${table.providerAttemptCount} >= 0`,
    ),
    check(
      "notification_delivery_completion_check",
      sql`(
          ${table.status} IN ('sent', 'skipped')
          AND ${table.completedAt} IS NOT NULL
        ) OR (
          ${table.status} NOT IN ('sent', 'skipped')
          AND ${table.completedAt} IS NULL
        )`,
    ),
    check(
      "notification_delivery_skipped_check",
      sql`${table.status} <> 'skipped' OR (
        ${table.matchCount} IS NOT NULL
        AND ${table.matchCount} = 0
        AND ${table.providerAttemptCount} = 0
        AND ${table.lastProviderAttemptAt} IS NULL
        AND ${table.providerMessageId} IS NULL
      )`,
    ),
    check(
      "notification_delivery_sendable_match_check",
      sql`${table.status} NOT IN ('sent', 'unknown', 'quota_deferred')
        OR (${table.matchCount} IS NOT NULL AND ${table.matchCount} > 0)`,
    ),
    check(
      "notification_delivery_provider_attempt_check",
      sql`${table.status} NOT IN ('sent', 'unknown') OR (
        ${table.providerAttemptCount} > 0
        AND ${table.lastProviderAttemptAt} IS NOT NULL
      )`,
    ),
    check(
      "notification_delivery_sent_provider_check",
      sql`${table.status} <> 'sent'
        OR NULLIF(BTRIM(${table.providerMessageId}), '') IS NOT NULL`,
    ),
    check(
      "notification_delivery_deferred_check",
      sql`(
          ${table.status} = 'quota_deferred'
          AND ${table.deferredUntil} IS NOT NULL
        ) OR (
          ${table.status} <> 'quota_deferred'
          AND ${table.deferredUntil} IS NULL
        )`,
    ),
  ],
);

export const watchlistCompany = pgTable(
  "watchlist_company",
  {
    id: uuid("id").defaultRandom().primaryKey(),
    watchlistId: uuid("watchlist_id")
      .notNull()
      .references(() => watchlist.id, { onDelete: "cascade" }),
    companyId: uuid("company_id")
      .notNull()
      .references(() => company.id, { onDelete: "cascade" }),
    addedAt: timestamp("added_at", { withTimezone: true })
      .defaultNow()
      .notNull(),
  },
  (table) => [
    uniqueIndex("idx_wlc_watchlist_company").on(
      table.watchlistId,
      table.companyId,
    ),
    index("idx_wlc_company").on(table.companyId),
  ],
);

// ── Murmur per-claim KV (named-config state, jobseek#2757) ───────────

export const murmurClaimKv = pgTable(
  "murmur_claim_kv",
  {
    claimToken: text("claim_token").notNull(),
    name: text("name").notNull(),
    value: jsonb("value").notNull(),
    createdAt: timestamp("created_at", { withTimezone: true })
      .defaultNow()
      .notNull(),
    updatedAt: timestamp("updated_at", { withTimezone: true })
      .defaultNow()
      .notNull(),
  },
  (table) => [
    primaryKey({ columns: [table.claimToken, table.name] }),
    index("murmur_claim_kv_token_idx").on(table.claimToken),
  ],
);

// ── Murmur webhook idempotency ledger (jobseek#2763) ─────────────────
//
// One row per accepted Murmur run. `runId` is the PRIMARY KEY — that is
// the UNIQUE constraint the accept handler relies on per Murmur
// DESIGN.md §4.2. `bodySha256` lets the handler distinguish "same body
// re-fire" (idempotent success) from "different body re-fire"
// (operator anomaly: warn and discard).
export const murmurAcceptLog = pgTable(
  "murmur_accept_log",
  {
    runId: text("run_id").primaryKey(),
    bodySha256: text("body_sha256").notNull(),
    appliedAt: timestamp("applied_at", { withTimezone: true })
      .defaultNow()
      .notNull(),
    companyId: uuid("company_id").references(() => company.id, {
      onDelete: "set null",
    }),
    boardCount: integer("board_count").notNull().default(0),
    target: text("target").notNull(),
  },
  (table) => [index("murmur_accept_log_applied_idx").on(table.appliedAt)],
);
