import {
  pgTable,
  uuid,
  text,
  real,
  timestamp,
  index,
  jsonb,
  smallint,
} from "drizzle-orm/pg-core";

// Minimal subset of the main schema — signals app reads/writes only these tables.
// apps/web owns all migrations; do NOT run drizzle-kit push/migrate from here.

export const company = pgTable("company", {
  id: uuid("id").defaultRandom().primaryKey(),
  name: text("name").notNull(),
  slug: text("slug").unique().notNull(),
  logo: text("logo"),
  icon: text("icon"),
  website: text("website"),
  description: text("description"),
  industry: smallint("industry"),
  extras: jsonb("extras").default({}),
  createdAt: timestamp("created_at").defaultNow().notNull(),
  updatedAt: timestamp("updated_at").defaultNow().notNull(),
});

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
