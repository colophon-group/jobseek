import { db } from "@/db";
import { outreachDraft, hiringSignal, company } from "@/db/schema";
import { eq, desc } from "drizzle-orm";
import Link from "next/link";
import SignalTypeBadge from "@/components/SignalTypeBadge";
import ScoreBadge from "@/components/ScoreBadge";
import { ChevronRight } from "lucide-react";

export const dynamic = "force-dynamic";

type DraftStatus = "pending_review" | "sent" | "archived";

const TABS: { label: string; value: DraftStatus; dot: string }[] = [
  { label: "Inbox", value: "pending_review", dot: "var(--dot-blue)" },
  { label: "Sent", value: "sent", dot: "var(--dot-green)" },
  { label: "Archived", value: "archived", dot: "var(--dot-gray)" },
];

const SIGNAL_DOT: Record<string, string> = {
  funding:    "var(--dot-orange)",
  sec_filing: "var(--dot-blue)",
  twitter:    "var(--dot-indigo)",
  headcount:  "var(--dot-green)",
  github:     "var(--dot-purple)",
  job_gap:    "var(--dot-red)",
};

export default async function OutreachPage({
  searchParams,
}: {
  searchParams: Promise<{ tab?: string }>;
}) {
  const { tab } = await searchParams;
  const activeTab: DraftStatus =
    tab === "sent" || tab === "archived" ? tab : "pending_review";

  const drafts = await db
    .select({
      id: outreachDraft.id,
      subject: outreachDraft.subject,
      contactName: outreachDraft.contactName,
      contactTitle: outreachDraft.contactTitle,
      contactEmail: outreachDraft.contactEmail,
      status: outreachDraft.status,
      createdAt: outreachDraft.createdAt,
      signalType: hiringSignal.signalType,
      signalScore: hiringSignal.score,
      companyName: company.name,
    })
    .from(outreachDraft)
    .innerJoin(hiringSignal, eq(outreachDraft.signalId, hiringSignal.id))
    .leftJoin(company, eq(hiringSignal.companyId, company.id))
    .where(eq(outreachDraft.status, activeTab))
    .orderBy(desc(outreachDraft.createdAt))
    .limit(200);

  return (
    <div>
      {/* Page header */}
      <div style={{ marginBottom: "2rem" }}>
        <p
          style={{
            fontSize: 11,
            fontWeight: 600,
            letterSpacing: 1.4,
            textTransform: "uppercase",
            color: "var(--text-muted)",
            marginBottom: 6,
          }}
        >
          Outreach
        </p>
        <h1
          style={{
            fontSize: 28,
            fontWeight: 700,
            color: "var(--text)",
            letterSpacing: -0.8,
            margin: 0,
          }}
        >
          Drafts
        </h1>
      </div>

      {/* Tabs — pill style */}
      <div
        style={{
          display: "inline-flex",
          gap: 4,
          background: "var(--surface)",
          borderRadius: 12,
          padding: 4,
          boxShadow: "var(--card-shadow)",
          marginBottom: "1.5rem",
        }}
      >
        {TABS.map(({ label, value, dot }) => {
          const active = activeTab === value;
          return (
            <Link
              key={value}
              href={`/outreach?tab=${value}`}
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
                padding: "0.4rem 1rem",
                borderRadius: 9,
                fontSize: 13,
                fontWeight: active ? 600 : 400,
                color: active ? "var(--text)" : "var(--text-muted)",
                background: active ? "var(--background)" : "transparent",
                boxShadow: active ? "0 1px 4px rgba(0,0,0,0.08)" : "none",
                textDecoration: "none",
                letterSpacing: -0.1,
                transition: "all 0.15s",
              }}
            >
              <span
                style={{
                  display: "inline-block",
                  width: 7,
                  height: 7,
                  borderRadius: "50%",
                  background: active ? dot : "var(--dot-gray)",
                  opacity: active ? 1 : 0.5,
                }}
              />
              {label}
            </Link>
          );
        })}
      </div>

      {/* List */}
      {drafts.length === 0 ? (
        <div
          style={{
            background: "var(--surface)",
            borderRadius: "var(--radius)",
            boxShadow: "var(--card-shadow)",
            padding: "5rem",
            textAlign: "center",
          }}
        >
          <div
            style={{
              width: 9,
              height: 9,
              borderRadius: "50%",
              background: "var(--dot-gray)",
              margin: "0 auto 1.25rem",
            }}
          />
          <div style={{ fontWeight: 600, fontSize: 16, color: "var(--text)", marginBottom: 5 }}>
            All clear
          </div>
          <div style={{ color: "var(--text-muted)", fontSize: 13.5 }}>
            No drafts in {activeTab.replace("_", " ")}.
          </div>
        </div>
      ) : (
        <div
          style={{
            background: "var(--surface)",
            borderRadius: "var(--radius)",
            boxShadow: "var(--card-shadow)",
            overflow: "hidden",
          }}
        >
          {drafts.map((d, i) => {
            const dot = SIGNAL_DOT[d.signalType] ?? "var(--dot-gray)";
            return (
              <Link
                key={d.id}
                href={`/outreach/${d.id}`}
                style={{ textDecoration: "none", display: "block" }}
              >
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: "1.25rem",
                    padding: "1rem 1.5rem",
                    borderBottom:
                      i < drafts.length - 1 ? "1px solid rgba(0,0,0,0.05)" : "none",
                    transition: "background 0.12s",
                    cursor: "pointer",
                  }}
                  className="hover:bg-black/[0.02]"
                >
                  {/* Dot */}
                  <span
                    style={{
                      display: "inline-block",
                      width: 9,
                      height: 9,
                      borderRadius: "50%",
                      background: dot,
                      flexShrink: 0,
                    }}
                  />

                  {/* Content */}
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 8,
                        marginBottom: 3,
                        flexWrap: "wrap",
                      }}
                    >
                      <span
                        style={{
                          fontWeight: 600,
                          fontSize: 14,
                          color: "var(--text)",
                          letterSpacing: -0.2,
                        }}
                      >
                        {d.companyName ?? "—"}
                      </span>
                      <SignalTypeBadge type={d.signalType} />
                      <ScoreBadge score={d.signalScore} />
                    </div>
                    <div
                      style={{
                        color: "var(--text-muted)",
                        fontSize: 13,
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                        letterSpacing: -0.1,
                      }}
                    >
                      {d.subject}
                    </div>
                  </div>

                  {/* Right */}
                  <div style={{ textAlign: "right", flexShrink: 0 }}>
                    <div style={{ fontSize: 13, fontWeight: 500, color: "var(--text-secondary)", letterSpacing: -0.1 }}>
                      {d.contactName}
                    </div>
                    {d.contactTitle && (
                      <div style={{ fontSize: 11.5, color: "var(--text-muted)", marginTop: 2 }}>
                        {d.contactTitle}
                      </div>
                    )}
                    <div style={{ fontSize: 11, color: "var(--text-subtle)", marginTop: 3 }}>
                      {new Date(d.createdAt).toLocaleDateString("en-US", {
                        month: "short",
                        day: "numeric",
                      })}
                    </div>
                  </div>

                  <ChevronRight size={14} color="var(--text-subtle)" strokeWidth={1.5} />
                </div>
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}
