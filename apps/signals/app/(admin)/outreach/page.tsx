import { db } from "@/db";
import { outreachDraft, hiringSignal, company } from "@/db/schema";
import { eq, desc } from "drizzle-orm";
import Link from "next/link";
import SignalTypeBadge from "@/components/SignalTypeBadge";
import ScoreBadge from "@/components/ScoreBadge";
import { Inbox, Send, Archive, ChevronRight } from "lucide-react";

export const dynamic = "force-dynamic";

type DraftStatus = "pending_review" | "sent" | "archived";

const TABS: { label: string; value: DraftStatus; icon: React.ComponentType<{ size?: number; strokeWidth?: number }> }[] = [
  { label: "Inbox", value: "pending_review", icon: Inbox },
  { label: "Sent", value: "sent", icon: Send },
  { label: "Archived", value: "archived", icon: Archive },
];

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
      {/* Header */}
      <div style={{ marginBottom: "1.75rem" }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, color: "var(--text)", letterSpacing: -0.5, marginBottom: 4 }}>
          Outreach
        </h1>
        <p style={{ color: "var(--text-muted)", fontSize: 13.5 }}>
          AI-drafted emails ready to send. Review, personalise, and reach out.
        </p>
      </div>

      {/* Tabs */}
      <div
        style={{
          display: "flex",
          gap: 4,
          marginBottom: "1.25rem",
          background: "var(--surface)",
          border: "1px solid var(--border)",
          borderRadius: 10,
          padding: 4,
          width: "fit-content",
        }}
      >
        {TABS.map(({ label, value, icon: Icon }) => {
          const active = activeTab === value;
          return (
            <Link
              key={value}
              href={`/outreach?tab=${value}`}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 6,
                padding: "0.4rem 0.9rem",
                fontSize: 13,
                fontWeight: active ? 600 : 400,
                color: active ? "var(--text)" : "var(--text-muted)",
                background: active ? "var(--surface-2)" : "transparent",
                borderRadius: 7,
                textDecoration: "none",
                transition: "background 0.1s",
              }}
            >
              <Icon size={13} strokeWidth={active ? 2.5 : 2} />
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
            border: "1px solid var(--border)",
            borderRadius: 12,
            padding: "4rem",
            textAlign: "center",
            color: "var(--text-muted)",
          }}
        >
          <Inbox size={32} color="#e2e8f0" style={{ marginBottom: 12 }} />
          <div style={{ fontWeight: 600, color: "var(--text)", marginBottom: 6 }}>All clear</div>
          <div style={{ fontSize: 13 }}>No drafts in {activeTab.replace("_", " ")}.</div>
        </div>
      ) : (
        <div
          style={{
            background: "var(--surface)",
            border: "1px solid var(--border)",
            borderRadius: 12,
            overflow: "hidden",
          }}
        >
          {drafts.map((d, i) => {
            const initials = (d.companyName ?? "?")
              .split(" ")
              .slice(0, 2)
              .map((w: string) => w[0])
              .join("")
              .toUpperCase();

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
                    gap: "1rem",
                    padding: "0.9rem 1.1rem",
                    borderBottom: i < drafts.length - 1 ? "1px solid var(--border)" : "none",
                    cursor: "pointer",
                    transition: "background 0.1s",
                  }}
                  className="hover:bg-slate-50"
                >
                  {/* Company avatar */}
                  <div
                    style={{
                      width: 36,
                      height: 36,
                      borderRadius: 9,
                      background: "var(--accent-light)",
                      color: "var(--accent-text)",
                      fontSize: 12,
                      fontWeight: 700,
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      flexShrink: 0,
                    }}
                  >
                    {initials}
                  </div>

                  {/* Main content */}
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 7, marginBottom: 3 }}>
                      <span style={{ fontWeight: 600, color: "var(--text)", fontSize: 13.5 }}>
                        {d.companyName ?? "—"}
                      </span>
                      <SignalTypeBadge type={d.signalType} />
                      <ScoreBadge score={d.signalScore} />
                    </div>
                    <div
                      style={{
                        color: "var(--text-muted)",
                        fontSize: 12.5,
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {d.subject}
                    </div>
                  </div>

                  {/* Right side */}
                  <div style={{ textAlign: "right", flexShrink: 0, display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 3 }}>
                    <div style={{ color: "var(--text)", fontSize: 12.5, fontWeight: 500 }}>
                      {d.contactName}
                    </div>
                    {d.contactTitle && (
                      <div style={{ color: "var(--text-muted)", fontSize: 11.5 }}>{d.contactTitle}</div>
                    )}
                    <div style={{ color: "var(--text-muted)", fontSize: 11 }}>
                      {new Date(d.createdAt).toLocaleDateString("en-US", { month: "short", day: "numeric" })}
                    </div>
                  </div>

                  <ChevronRight size={14} color="var(--border-strong)" />
                </div>
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}
