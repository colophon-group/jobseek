import { pgTable, text, integer, uuid, timestamp, numeric, uniqueIndex, index, foreignKey, unique, serial, boolean, jsonb, smallint, real, check, bigint, primaryKey, pgEnum } from "drizzle-orm/pg-core"
import { sql } from "drizzle-orm"

export const locationType = pgEnum("location_type", ['macro', 'country', 'region', 'city'])


export const enrichBatch = pgTable("enrich_batch", {
	id: text().primaryKey().notNull(),
	provider: text().notNull(),
	model: text().notNull(),
	status: text().default('submitted').notNull(),
	itemCount: integer("item_count").notNull(),
	postingIds: uuid("posting_ids").array().notNull(),
	submittedAt: timestamp("submitted_at", { withTimezone: true, mode: 'string' }).defaultNow().notNull(),
	completedAt: timestamp("completed_at", { withTimezone: true, mode: 'string' }),
	inputTokens: integer("input_tokens"),
	outputTokens: integer("output_tokens"),
	estimatedCostUsd: numeric("estimated_cost_usd", { precision: 10, scale:  4 }),
	error: text(),
});

export const followedCompany = pgTable("followed_company", {
	id: uuid().defaultRandom().primaryKey().notNull(),
	userId: text("user_id").notNull(),
	companyId: uuid("company_id").notNull(),
	followedAt: timestamp("followed_at", { withTimezone: true, mode: 'string' }).defaultNow().notNull(),
}, (table) => [
	uniqueIndex("idx_fc_user_company").using("btree", table.userId.asc().nullsLast().op("text_ops"), table.companyId.asc().nullsLast().op("text_ops")),
	index("idx_fc_user_followed_at").using("btree", table.userId.asc().nullsLast().op("text_ops"), table.followedAt.asc().nullsLast().op("text_ops")),
	foreignKey({
			columns: [table.companyId],
			foreignColumns: [company.id],
			name: "followed_company_company_id_fkey"
		}).onDelete("cascade"),
	foreignKey({
			columns: [table.userId],
			foreignColumns: [user.id],
			name: "followed_company_user_id_fkey"
		}).onDelete("cascade"),
]);

export const technology = pgTable("technology", {
	id: serial().primaryKey().notNull(),
	slug: text().notNull(),
	name: text(),
	category: text(),
}, (table) => [
	unique("technology_slug_key").on(table.slug),
]);

export const taxonomyMiss = pgTable("taxonomy_miss", {
	id: serial().primaryKey().notNull(),
	taxonomy: text().notNull(),
	rawValue: text("raw_value").notNull(),
	sampleValue: text("sample_value").notNull(),
	hitCount: integer("hit_count").default(1).notNull(),
	firstSeenAt: timestamp("first_seen_at", { withTimezone: true, mode: 'string' }).defaultNow().notNull(),
	lastSeenAt: timestamp("last_seen_at", { withTimezone: true, mode: 'string' }).defaultNow().notNull(),
	status: text().default('pending').notNull(),
	resolvedTo: text("resolved_to"),
}, (table) => [
	index("idx_tm_pending").using("btree", table.taxonomy.asc().nullsLast().op("int4_ops"), table.hitCount.desc().nullsFirst().op("text_ops")).where(sql`(status = 'pending'::text)`),
	unique("taxonomy_miss_taxonomy_raw_value_key").on(table.taxonomy, table.rawValue),
]);

export const seniority = pgTable("seniority", {
	id: serial().primaryKey().notNull(),
	slug: text().notNull(),
}, (table) => [
	unique("seniority_slug_key").on(table.slug),
]);

export const occupationDomain = pgTable("occupation_domain", {
	id: serial().primaryKey().notNull(),
	slug: text().notNull(),
}, (table) => [
	unique("occupation_domain_slug_key").on(table.slug),
]);

export const occupation = pgTable("occupation", {
	id: serial().primaryKey().notNull(),
	slug: text().notNull(),
	parentId: integer("parent_id"),
	domainId: integer("domain_id"),
}, (table) => [
	index("idx_occupation_domain").using("btree", table.domainId.asc().nullsLast().op("int4_ops")).where(sql`(domain_id IS NOT NULL)`),
	index("idx_occupation_parent").using("btree", table.parentId.asc().nullsLast().op("int4_ops")).where(sql`(parent_id IS NOT NULL)`),
	foreignKey({
			columns: [table.domainId],
			foreignColumns: [occupationDomain.id],
			name: "occupation_domain_id_fkey"
		}),
	foreignKey({
			columns: [table.parentId],
			foreignColumns: [table.id],
			name: "occupation_parent_id_fkey"
		}),
	unique("occupation_slug_key").on(table.slug),
]);

export const subscription = pgTable("subscription", {
	id: uuid().defaultRandom().primaryKey().notNull(),
	userId: text("user_id").notNull(),
	plan: text().notNull(),
	status: text().notNull(),
	startsAt: timestamp("starts_at", { mode: 'string' }).notNull(),
	endsAt: timestamp("ends_at", { mode: 'string' }),
	createdAt: timestamp("created_at", { mode: 'string' }).defaultNow().notNull(),
	updatedAt: timestamp("updated_at", { mode: 'string' }).defaultNow().notNull(),
	stripeCustomerId: text("stripe_customer_id"),
	stripeSubscriptionId: text("stripe_subscription_id"),
}, (table) => [
	uniqueIndex("idx_sub_stripe_customer").using("btree", table.stripeCustomerId.asc().nullsLast().op("text_ops")).where(sql`(stripe_customer_id IS NOT NULL)`),
	uniqueIndex("idx_sub_stripe_subscription").using("btree", table.stripeSubscriptionId.asc().nullsLast().op("text_ops")).where(sql`(stripe_subscription_id IS NOT NULL)`),
	uniqueIndex("idx_sub_user").using("btree", table.userId.asc().nullsLast().op("text_ops")),
	foreignKey({
			columns: [table.userId],
			foreignColumns: [user.id],
			name: "subscription_user_id_user_id_fk"
		}).onDelete("cascade"),
]);

export const jobBoard = pgTable("job_board", {
	id: uuid().defaultRandom().primaryKey().notNull(),
	companyId: uuid("company_id").notNull(),
	crawlerType: text("crawler_type"),
	boardUrl: text("board_url").notNull(),
	checkIntervalMinutes: integer("check_interval_minutes").default(60).notNull(),
	nextCheckAt: timestamp("next_check_at", { withTimezone: true, mode: 'string' }).defaultNow().notNull(),
	lastCheckedAt: timestamp("last_checked_at", { withTimezone: true, mode: 'string' }),
	lastSuccessAt: timestamp("last_success_at", { withTimezone: true, mode: 'string' }),
	consecutiveFailures: integer("consecutive_failures").default(0).notNull(),
	lastError: text("last_error"),
	isEnabled: boolean("is_enabled").default(true).notNull(),
	createdAt: timestamp("created_at", { withTimezone: true, mode: 'string' }).defaultNow().notNull(),
	updatedAt: timestamp("updated_at", { withTimezone: true, mode: 'string' }).defaultNow().notNull(),
	metadata: jsonb().default({}),
	boardSlug: text("board_slug"),
	boardStatus: text("board_status").default('active').notNull(),
	throttleKey: text("throttle_key"),
	leaseOwner: text("lease_owner"),
	leasedUntil: timestamp("leased_until", { withTimezone: true, mode: 'string' }),
	emptyCheckCount: integer("empty_check_count").default(0).notNull(),
	lastNonEmptyAt: timestamp("last_non_empty_at", { withTimezone: true, mode: 'string' }),
	goneAt: timestamp("gone_at", { withTimezone: true, mode: 'string' }),
	scrapeIntervalHours: integer("scrape_interval_hours").default(24).notNull(),
}, (table) => [
	index("idx_jb_company").using("btree", table.companyId.asc().nullsLast().op("uuid_ops")),
	index("idx_jb_due").using("btree", table.nextCheckAt.asc().nullsLast().op("timestamptz_ops"), table.throttleKey.asc().nullsLast().op("timestamptz_ops")).where(sql`((board_status = ANY (ARRAY['active'::text, 'suspect'::text])) AND (is_enabled = true))`),
	index("idx_jb_lease").using("btree", table.leasedUntil.asc().nullsLast().op("timestamptz_ops")).where(sql`(leased_until IS NOT NULL)`),
	foreignKey({
			columns: [table.companyId],
			foreignColumns: [company.id],
			name: "job_board_company_id_company_id_fk"
		}).onDelete("cascade"),
	unique("job_board_board_url_unique").on(table.boardUrl),
	unique("job_board_board_slug_key").on(table.boardSlug),
]);

export const savedJob = pgTable("saved_job", {
	id: uuid().defaultRandom().primaryKey().notNull(),
	userId: text("user_id").notNull(),
	jobPostingId: uuid("job_posting_id").notNull(),
	savedAt: timestamp("saved_at", { withTimezone: true, mode: 'string' }).defaultNow().notNull(),
	status: text().default('saved').notNull(),
	statusChangedAt: timestamp("status_changed_at", { withTimezone: true, mode: 'string' }).defaultNow().notNull(),
	appliedAt: timestamp("applied_at", { withTimezone: true, mode: 'string' }),
	rejectedAt: timestamp("rejected_at", { withTimezone: true, mode: 'string' }),
	offeredAt: timestamp("offered_at", { withTimezone: true, mode: 'string' }),
	salaryMinOverride: integer("salary_min_override"),
	salaryMaxOverride: integer("salary_max_override"),
	salaryCurrencyOverride: text("salary_currency_override"),
	salaryPeriodOverride: text("salary_period_override"),
}, (table) => [
	uniqueIndex("idx_sj_user_posting").using("btree", table.userId.asc().nullsLast().op("text_ops"), table.jobPostingId.asc().nullsLast().op("text_ops")),
	index("idx_sj_user_saved_at").using("btree", table.userId.asc().nullsLast().op("timestamptz_ops"), table.savedAt.asc().nullsLast().op("timestamptz_ops")),
	index("idx_sj_user_status").using("btree", table.userId.asc().nullsLast(), table.status.asc().nullsLast()),
	index("idx_sj_user_status_changed").using("btree", table.userId.asc().nullsLast(), table.statusChangedAt.asc().nullsLast()),
	foreignKey({
			columns: [table.jobPostingId],
			foreignColumns: [jobPosting.id],
			name: "saved_job_job_posting_id_job_posting_id_fk"
		}).onDelete("cascade"),
	foreignKey({
			columns: [table.userId],
			foreignColumns: [user.id],
			name: "saved_job_user_id_user_id_fk"
		}).onDelete("cascade"),
]);

export const applicationInterview = pgTable("application_interview", {
	id: uuid().defaultRandom().primaryKey().notNull(),
	savedJobId: uuid("saved_job_id").notNull(),
	round: smallint().notNull(),
	type: text().notNull(),
	scheduledAt: timestamp("scheduled_at", { withTimezone: true, mode: 'string' }),
	createdAt: timestamp("created_at", { withTimezone: true, mode: 'string' }).defaultNow().notNull(),
}, (table) => [
	uniqueIndex("idx_ai_saved_job_round").using("btree", table.savedJobId.asc().nullsLast(), table.round.asc().nullsLast()),
	foreignKey({
			columns: [table.savedJobId],
			foreignColumns: [savedJob.id],
			name: "application_interview_saved_job_id_saved_job_id_fk"
		}).onDelete("cascade"),
]);

export const session = pgTable("session", {
	id: text().primaryKey().notNull(),
	expiresAt: timestamp("expires_at", { mode: 'string' }).notNull(),
	token: text().notNull(),
	createdAt: timestamp("created_at", { mode: 'string' }).defaultNow().notNull(),
	updatedAt: timestamp("updated_at", { mode: 'string' }).notNull(),
	ipAddress: text("ip_address"),
	userAgent: text("user_agent"),
	userId: text("user_id").notNull(),
}, (table) => [
	index("session_userId_idx").using("btree", table.userId.asc().nullsLast().op("text_ops")),
	foreignKey({
			columns: [table.userId],
			foreignColumns: [user.id],
			name: "session_user_id_user_id_fk"
		}).onDelete("cascade"),
	unique("session_token_unique").on(table.token),
]);

export const user = pgTable("user", {
	id: text().primaryKey().notNull(),
	name: text().notNull(),
	email: text().notNull(),
	emailVerified: boolean("email_verified").default(false).notNull(),
	image: text(),
	createdAt: timestamp("created_at", { mode: 'string' }).defaultNow().notNull(),
	updatedAt: timestamp("updated_at", { mode: 'string' }).defaultNow().notNull(),
}, (table) => [
	unique("user_email_unique").on(table.email),
]);

export const companyRequest = pgTable("company_request", {
	id: uuid().defaultRandom().primaryKey().notNull(),
	input: text().notNull(),
	count: integer().default(1).notNull(),
	resolvedCompanyId: uuid("resolved_company_id"),
	createdAt: timestamp("created_at", { withTimezone: true, mode: 'string' }).defaultNow().notNull(),
	updatedAt: timestamp("updated_at", { withTimezone: true, mode: 'string' }).defaultNow().notNull(),
	lastUserHint: jsonb("last_user_hint"),
	status: text().default('pending').notNull(),
	resolvedJobBoardId: uuid("resolved_job_board_id"),
	retries: integer().default(0).notNull(),
	maxRetries: integer("max_retries").default(3).notNull(),
	errorMessage: text("error_message"),
	githubIssueNumber: integer("github_issue_number"),
}, (table) => [
	index("idx_cr_status").using("btree", table.status.asc().nullsLast().op("text_ops")).where(sql`(status = 'pending'::text)`),
	foreignKey({
			columns: [table.resolvedCompanyId],
			foreignColumns: [company.id],
			name: "company_request_resolved_company_id_company_id_fk"
		}).onDelete("set null"),
	foreignKey({
			columns: [table.resolvedJobBoardId],
			foreignColumns: [jobBoard.id],
			name: "company_request_resolved_job_board_id_job_board_id_fk"
		}).onDelete("set null"),
	unique("company_request_input_unique").on(table.input),
]);

export const account = pgTable("account", {
	id: text().primaryKey().notNull(),
	accountId: text("account_id").notNull(),
	providerId: text("provider_id").notNull(),
	userId: text("user_id").notNull(),
	accessToken: text("access_token"),
	refreshToken: text("refresh_token"),
	idToken: text("id_token"),
	accessTokenExpiresAt: timestamp("access_token_expires_at", { mode: 'string' }),
	refreshTokenExpiresAt: timestamp("refresh_token_expires_at", { mode: 'string' }),
	scope: text(),
	password: text(),
	createdAt: timestamp("created_at", { mode: 'string' }).defaultNow().notNull(),
	updatedAt: timestamp("updated_at", { mode: 'string' }).notNull(),
}, (table) => [
	index("account_userId_idx").using("btree", table.userId.asc().nullsLast().op("text_ops")),
	foreignKey({
			columns: [table.userId],
			foreignColumns: [user.id],
			name: "account_user_id_user_id_fk"
		}).onDelete("cascade"),
]);

export const industry = pgTable("industry", {
	id: smallint().primaryKey().notNull(),
	name: text().notNull(),
}, (table) => [
	unique("industry_name_key").on(table.name),
]);

export const company = pgTable("company", {
	id: uuid().defaultRandom().primaryKey().notNull(),
	name: text().notNull(),
	slug: text().notNull(),
	website: text(),
	description: text(),
	createdAt: timestamp("created_at", { mode: 'string' }).defaultNow().notNull(),
	updatedAt: timestamp("updated_at", { mode: 'string' }).defaultNow().notNull(),
	logo: text(),
	icon: text(),
	logoType: text("logo_type"),
	industry: smallint(),
	employeeCountRange: smallint("employee_count_range"),
	foundedYear: smallint("founded_year"),
	extras: jsonb().default({}),
}, (table) => [
	index("idx_company_industry").using("btree", table.industry.asc().nullsLast().op("int2_ops")).where(sql`(industry IS NOT NULL)`),
	foreignKey({
			columns: [table.industry],
			foreignColumns: [industry.id],
			name: "company_industry_fkey"
		}),
	unique("company_slug_unique").on(table.slug),
]);

export const userPreferences = pgTable("user_preferences", {
	id: uuid().defaultRandom().primaryKey().notNull(),
	userId: text("user_id").notNull(),
	theme: text().default('light').notNull(),
	locale: text().default('en').notNull(),
	updatedAt: timestamp("updated_at", { mode: 'string' }).defaultNow().notNull(),
	cookieConsent: boolean("cookie_consent").default(false).notNull(),
	lastPasswordResetAt: timestamp("last_password_reset_at", { mode: 'string' }),
	themeUpdatedAt: timestamp("theme_updated_at", { mode: 'string' }),
	localeUpdatedAt: timestamp("locale_updated_at", { mode: 'string' }),
	jobLanguages: text("job_languages").array().default([""]).notNull(),
	displayCurrency: text("display_currency").default('EUR').notNull(),
}, (table) => [
	foreignKey({
			columns: [table.userId],
			foreignColumns: [user.id],
			name: "user_preferences_user_id_user_id_fk"
		}).onDelete("cascade"),
	unique("user_preferences_user_id_unique").on(table.userId),
]);

export const location = pgTable("location", {
	id: integer().primaryKey().notNull(),
	parentId: integer("parent_id"),
	type: locationType().notNull(),
	population: integer(),
	lat: real(),
	lng: real(),
	languages: text().array(),
	slug: text(),
}, (table) => [
	index("idx_loc_parent").using("btree", table.parentId.asc().nullsLast().op("int4_ops")),
	uniqueIndex("idx_loc_slug").using("btree", table.slug.asc().nullsLast().op("text_ops")),
	index("idx_loc_type").using("btree", table.type.asc().nullsLast().op("enum_ops")),
	foreignKey({
			columns: [table.parentId],
			foreignColumns: [table.id],
			name: "location_parent_id_fkey"
		}),
]);

export const currencyRate = pgTable("currency_rate", {
	currency: text().primaryKey().notNull(),
	toEur: numeric("to_eur", { precision: 10, scale:  6 }).notNull(),
	updatedAt: timestamp("updated_at", { withTimezone: true, mode: 'string' }).defaultNow().notNull(),
});

export const jobPosting = pgTable("job_posting", {
	id: uuid().defaultRandom().primaryKey().notNull(),
	companyId: uuid("company_id").notNull(),
	boardId: uuid("board_id"),
	sourceUrl: text("source_url").notNull(),
	firstSeenAt: timestamp("first_seen_at", { withTimezone: true, mode: 'string' }).defaultNow().notNull(),
	lastSeenAt: timestamp("last_seen_at", { withTimezone: true, mode: 'string' }),
	employmentType: text("employment_type"),
	nextScrapeAt: timestamp("next_scrape_at", { withTimezone: true, mode: 'string' }),
	lastScrapedAt: timestamp("last_scraped_at", { withTimezone: true, mode: 'string' }),
	leasedUntil: timestamp("leased_until", { withTimezone: true, mode: 'string' }),
	scrapeFailures: smallint("scrape_failures").default(0).notNull(),
	missingCount: smallint("missing_count").default(0).notNull(),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	descriptionR2Hash: bigint("description_r2_hash", { mode: "number" }),
	isActive: boolean("is_active").default(true).notNull(),
	locales: text().array().default([""]).notNull(),
	titles: text().array().default([""]).notNull(),
	locationIds: integer("location_ids").array(),
	locationTypes: text("location_types").array(),
	enrichment: jsonb(),
	toBeEnriched: boolean("to_be_enriched").default(true).notNull(),
	enrichVersion: smallint("enrich_version").default(0).notNull(),
	lastEnrichedAt: timestamp("last_enriched_at", { withTimezone: true, mode: 'string' }),
	leaseOwner: text("lease_owner"),
	occupationId: integer("occupation_id"),
	seniorityId: integer("seniority_id"),
	technologyIds: integer("technology_ids").array(),
	salaryMin: integer("salary_min"),
	salaryMax: integer("salary_max"),
	salaryCurrency: text("salary_currency"),
	salaryPeriod: text("salary_period"),
	salaryEur: integer("salary_eur"),
	experienceMin: numeric("experience_min", { precision: 3, scale: 1 }),
	experienceMax: numeric("experience_max", { precision: 3, scale: 1 }),
}, (table) => [
	index("idx_jp_active").using("btree", table.isActive.asc().nullsLast().op("bool_ops")).where(sql`(is_active = true)`),
	index("idx_jp_board_url").using("btree", table.boardId.asc().nullsLast().op("uuid_ops"), table.sourceUrl.asc().nullsLast().op("text_ops")),
	index("idx_jp_company").using("btree", table.companyId.asc().nullsLast().op("uuid_ops")),
	index("idx_jp_experience_min").using("btree", table.experienceMin.asc().nullsLast().op("numeric_ops")).where(sql`(experience_min IS NOT NULL)`),
	index("idx_jp_lease").using("btree", table.leasedUntil.asc().nullsLast().op("timestamptz_ops")).where(sql`(leased_until IS NOT NULL)`),
	index("idx_jp_location_ids").using("gin", table.locationIds.asc().nullsLast().op("array_ops")),
	index("idx_jp_next_scrape").using("btree", table.nextScrapeAt.asc().nullsLast().op("timestamptz_ops")).where(sql`((is_active = true) AND (next_scrape_at IS NOT NULL))`),
	index("idx_jp_occupation").using("btree", table.occupationId.asc().nullsLast().op("int4_ops")).where(sql`(occupation_id IS NOT NULL)`),
	index("idx_jp_salary_eur").using("btree", table.salaryEur.asc().nullsLast().op("int4_ops")).where(sql`(salary_eur IS NOT NULL)`),
	index("idx_jp_search_vector").using("gin", sql`((setweight(to_tsvector('simple'::regconfig, COALESCE(titles[1]`),
	index("idx_jp_seniority").using("btree", table.seniorityId.asc().nullsLast().op("int4_ops")).where(sql`(seniority_id IS NOT NULL)`),
	index("idx_jp_technology_ids").using("gin", table.technologyIds.asc().nullsLast().op("array_ops")).where(sql`(technology_ids IS NOT NULL)`),
	index("idx_jp_to_be_enriched").using("btree", table.toBeEnriched.asc().nullsLast().op("bool_ops")).where(sql`((is_active = true) AND (to_be_enriched = true))`),
	foreignKey({
			columns: [table.boardId],
			foreignColumns: [jobBoard.id],
			name: "job_posting_board_id_job_board_id_fk"
		}).onDelete("set null"),
	foreignKey({
			columns: [table.companyId],
			foreignColumns: [company.id],
			name: "job_posting_company_id_company_id_fk"
		}).onDelete("cascade"),
	foreignKey({
			columns: [table.occupationId],
			foreignColumns: [occupation.id],
			name: "job_posting_occupation_id_fkey"
		}),
	foreignKey({
			columns: [table.seniorityId],
			foreignColumns: [seniority.id],
			name: "job_posting_seniority_id_fkey"
		}),
	unique("job_posting_source_url_unique").on(table.sourceUrl),
	check("chk_employment_type", sql`(employment_type IS NULL) OR (employment_type = ANY (ARRAY['full_time'::text, 'part_time'::text, 'contract'::text, 'internship'::text, 'temporary'::text, 'volunteer'::text, 'full_or_part'::text]))`),
	check("chk_location_arrays_length", sql`((location_ids IS NULL) AND (location_types IS NULL)) OR (array_length(location_ids, 1) = array_length(location_types, 1))`),
	check("chk_location_types", sql`location_types <@ ARRAY['onsite'::text, 'remote'::text, 'hybrid'::text]`),
]);

export const verification = pgTable("verification", {
	id: text().primaryKey().notNull(),
	identifier: text().notNull(),
	value: text().notNull(),
	expiresAt: timestamp("expires_at", { mode: 'string' }).notNull(),
	createdAt: timestamp("created_at", { mode: 'string' }).defaultNow().notNull(),
	updatedAt: timestamp("updated_at", { mode: 'string' }).defaultNow().notNull(),
}, (table) => [
	index("verification_identifier_idx").using("btree", table.identifier.asc().nullsLast().op("text_ops")),
]);

export const locationMacroMember = pgTable("location_macro_member", {
	macroId: integer("macro_id").notNull(),
	countryId: integer("country_id").notNull(),
}, (table) => [
	foreignKey({
			columns: [table.countryId],
			foreignColumns: [location.id],
			name: "location_macro_member_country_id_fkey"
		}).onDelete("cascade"),
	foreignKey({
			columns: [table.macroId],
			foreignColumns: [location.id],
			name: "location_macro_member_macro_id_fkey"
		}).onDelete("cascade"),
	primaryKey({ columns: [table.macroId, table.countryId], name: "location_macro_member_pkey"}),
]);

export const companyDescription = pgTable("company_description", {
	companyId: uuid("company_id").notNull(),
	locale: text().notNull(),
	description: text().notNull(),
}, (table) => [
	foreignKey({
			columns: [table.companyId],
			foreignColumns: [company.id],
			name: "company_description_company_id_fkey"
		}).onDelete("cascade"),
	primaryKey({ columns: [table.companyId, table.locale], name: "company_description_pkey"}),
]);

export const occupationName = pgTable("occupation_name", {
	occupationId: integer("occupation_id").notNull(),
	locale: text().notNull(),
	name: text().notNull(),
	isDisplay: boolean("is_display").default(true).notNull(),
}, (table) => [
	index("idx_occname_display").using("btree", table.occupationId.asc().nullsLast().op("text_ops"), table.locale.asc().nullsLast().op("text_ops")).where(sql`is_display`),
	index("idx_occname_lower").using("btree", sql`lower(name)`, sql`locale`),
	index("idx_occname_search").using("btree", sql`lower(name)`),
	foreignKey({
			columns: [table.occupationId],
			foreignColumns: [occupation.id],
			name: "occupation_name_occupation_id_fkey"
		}).onDelete("cascade"),
	primaryKey({ columns: [table.occupationId, table.locale, table.name], name: "occupation_name_pkey"}),
]);

export const seniorityName = pgTable("seniority_name", {
	seniorityId: integer("seniority_id").notNull(),
	locale: text().notNull(),
	name: text().notNull(),
	isDisplay: boolean("is_display").default(true).notNull(),
}, (table) => [
	index("idx_senname_display").using("btree", table.seniorityId.asc().nullsLast().op("int4_ops"), table.locale.asc().nullsLast().op("int4_ops")).where(sql`is_display`),
	index("idx_senname_lower").using("btree", sql`lower(name)`, sql`locale`),
	index("idx_senname_search").using("btree", sql`lower(name)`),
	foreignKey({
			columns: [table.seniorityId],
			foreignColumns: [seniority.id],
			name: "seniority_name_seniority_id_fkey"
		}).onDelete("cascade"),
	primaryKey({ columns: [table.seniorityId, table.locale, table.name], name: "seniority_name_pkey"}),
]);

export const occupationDomainName = pgTable("occupation_domain_name", {
	domainId: integer("domain_id").notNull(),
	locale: text().notNull(),
	name: text().notNull(),
	isDisplay: boolean("is_display").default(true).notNull(),
}, (table) => [
	index("idx_domname_display").using("btree", table.domainId.asc().nullsLast().op("int4_ops"), table.locale.asc().nullsLast().op("text_ops")).where(sql`is_display`),
	index("idx_domname_lower").using("btree", sql`lower(name)`, sql`locale`),
	foreignKey({
			columns: [table.domainId],
			foreignColumns: [occupationDomain.id],
			name: "occupation_domain_name_domain_id_fkey"
		}).onDelete("cascade"),
	primaryKey({ columns: [table.domainId, table.locale, table.name], name: "occupation_domain_name_pkey"}),
]);

export const locationName = pgTable("location_name", {
	locationId: integer("location_id").notNull(),
	locale: text().notNull(),
	name: text().notNull(),
	isDisplay: boolean("is_display").default(false).notNull(),
}, (table) => [
	index("idx_locname_display").using("btree", table.locationId.asc().nullsLast().op("int4_ops"), table.locale.asc().nullsLast().op("text_ops")).where(sql`(is_display = true)`),
	index("idx_locname_lower").using("btree", sql`lower(name)`, sql`locale`),
	index("idx_locname_trgm").using("gin", sql`lower(name)`),
	foreignKey({
			columns: [table.locationId],
			foreignColumns: [location.id],
			name: "location_name_location_id_fkey"
		}).onDelete("cascade"),
	primaryKey({ columns: [table.locationId, table.locale, table.name], name: "location_name_pkey"}),
]);

export const industryName = pgTable("industry_name", {
	industryId: smallint("industry_id").notNull(),
	locale: text().notNull(),
	name: text().notNull(),
	isDisplay: boolean("is_display").default(true).notNull(),
}, (table) => [
	index("idx_indname_display").using("btree", table.industryId.asc().nullsLast().op("int2_ops"), table.locale.asc().nullsLast().op("text_ops")).where(sql`is_display`),
	index("idx_indname_lower").using("btree", sql`lower(name)`, sql`locale`),
	foreignKey({
			columns: [table.industryId],
			foreignColumns: [industry.id],
			name: "industry_name_industry_id_fkey"
		}).onDelete("cascade"),
	primaryKey({ columns: [table.industryId, table.locale, table.name], name: "industry_name_pkey"}),
]);
