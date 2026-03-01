import { sql } from "drizzle-orm";
import {
  pgTable,
  uuid,
  text,
  boolean,
  integer,
  timestamp,
  index,
  uniqueIndex,
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

export const company = pgTable("company", {
  id: uuid("id").defaultRandom().primaryKey(),
  name: text("name").notNull(),
  slug: text("slug").unique().notNull(),
  logo: text("logo"),
  icon: text("icon"),
  website: text("website"),
  description: text("description"),
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
    metadata: jsonb("metadata").default({}),

    createdAt: timestamp("created_at", { withTimezone: true }).defaultNow().notNull(),
    updatedAt: timestamp("updated_at", { withTimezone: true }).defaultNow().notNull(),
  },
  (table) => [
    index("idx_jb_next_check").on(table.nextCheckAt),
    index("idx_jb_company").on(table.companyId),
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
    title: text("title"),
    description: text("description"),
    locations: text("locations").array(),
    employmentType: text("employment_type"),
    jobLocationType: text("job_location_type"),
    baseSalary: jsonb("base_salary"),
    skills: text("skills").array(),
    datePosted: timestamp("date_posted", { withTimezone: true }),
    validThrough: timestamp("valid_through", { withTimezone: true }),
    responsibilities: text("responsibilities").array(),
    qualifications: text("qualifications").array(),
    metadata: jsonb("metadata").default({}),
    fetchMethod: text("fetch_method"),
    latestVersionId: uuid("latest_version_id"),
    sourceUrl: text("source_url").unique(),
    status: text("status").default("active").notNull(),
    firstSeenAt: timestamp("first_seen_at", { withTimezone: true }).defaultNow(),
    lastSeenAt: timestamp("last_seen_at", { withTimezone: true }),
    delistedAt: timestamp("delisted_at", { withTimezone: true }),
    createdAt: timestamp("created_at").defaultNow().notNull(),
    updatedAt: timestamp("updated_at").defaultNow().notNull(),
  },
  (table) => [
    index("idx_jp_locations").using("gin", table.locations),
    index("idx_jp_skills").using("gin", table.skills),
    index("idx_jp_employment_type").on(table.employmentType),
    index("idx_jp_status_active").on(table.status).where(sql`status = 'active'`),
    index("idx_jp_last_seen_active").on(table.lastSeenAt).where(sql`status = 'active'`),
    index("idx_jp_valid_through").on(table.validThrough).where(sql`valid_through IS NOT NULL`),
  ],
);

export const jobPostingVersion = pgTable(
  "job_posting_version",
  {
    id: uuid("id").defaultRandom().primaryKey(),
    jobPostingId: uuid("job_posting_id")
      .notNull()
      .references(() => jobPosting.id, { onDelete: "cascade" }),
    content: jsonb("content").notNull(),
    contentHash: text("content_hash").notNull(),
    fetchMethod: text("fetch_method").notNull(),
    fetchedAt: timestamp("fetched_at", { withTimezone: true }).defaultNow().notNull(),
  },
  (table) => [
    index("idx_jpv_posting_fetched").on(table.jobPostingId, table.fetchedAt),
    uniqueIndex("idx_jpv_posting_hash").on(table.jobPostingId, table.contentHash),
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
    ),
    resolvedJobBoardId: uuid("resolved_job_board_id").references(
      () => jobBoard.id,
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

export const jobUrlQueue = pgTable(
  "job_url_queue",
  {
    id: uuid("id").defaultRandom().primaryKey(),
    jobPostingId: uuid("job_posting_id")
      .notNull()
      .references(() => jobPosting.id, { onDelete: "cascade" }),
    url: text("url").notNull().unique(),
    status: text("status").notNull().default("pending"),
    retries: integer("retries").default(0),
    maxRetries: integer("max_retries").default(3),
    errorMessage: text("error_message"),
    lockedUntil: timestamp("locked_until", { withTimezone: true }),
    createdAt: timestamp("created_at", { withTimezone: true }).defaultNow().notNull(),
    updatedAt: timestamp("updated_at", { withTimezone: true }).defaultNow().notNull(),
  },
  (table) => [
    index("idx_juq_pending").on(table.status, table.createdAt).where(sql`status = 'pending'`),
  ],
);
