import { db } from "@/db";
import { outreachDraft, hiringSignal, company } from "@/db/schema";
import { eq, desc } from "drizzle-orm";
import Link from "next/link";
import SignalTypeBadge from "@/components/SignalTypeBadge";
import ScoreBadge from "@/components/ScoreBadge";

export const dynamic = "force-dynamic";

type DraftStatus = "pending_review" | "sent" | "archived";

const TABS: { label: string; value: DraftStatus }[] = [
  { label: "Inbox", value: "pending_review" },
  { label: "Sent", value: "sent" },
  { label: "Archived", value: "archived" },
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
      <div className="flex items-center justify-between mb-6">
        <h1 style={{ fontSize: 18, fontWeight: 600, color: "var(--text)" }}>Outreach</h1>
      </div>

      {/* Tabs */}
      <div style={{ display: "flex", gap: 4, marginBottom: "1.5rem", borderBottom: "1px solid var(--border)" }}>
        {TABS.map((t) => (
          <Link
            key={t.value}
            href={`/outreach?tab=${t.value}`}
            style={{
              padding: "0.4rem 1rem",
              fontSize: 13,
              color: activeTab === t.value ? "var(--text)" : "var(--text-muted)",
              borderBottom: activeTab === t.value ? "2px solid var(--accent)" : "2px solid transparent",
              textDecoration: "none",
              marginBottom: -1,
            }}
          >
            {t.label}
          </Link>
        ))}
      </div>

      {drafts.length === 0 ? (
        <div style={{ color: "var(--text-muted)", textAlign: "center", marginTop: "3rem" }}>
          No drafts in {activeTab.replace("_", " ")}.
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {drafts.map((d) => (
            <Link
              key={d.id}
              href={`/outreach/${d.id}`}
              style={{ textDecoration: "none" }}
            >
              <div
                style={{
                  background: "var(--surface)",
                  border: "1px solid var(--border)",
                  borderRadius: 8,
                  padding: "0.875rem 1rem",
                  display: "flex",
                  alignItems: "center",
                  gap: "1rem",
                  cursor: "pointer",
                }}
                className="hover:bg-white/3"
              >
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
                    <span style={{ fontWeight: 600, color: "var(--text)", fontSize: 13 }}>
                      {d.companyName ?? "—"}
                    </span>
                    <SignalTypeBadge type={d.signalType} />
                    <ScoreBadge score={d.signalScore} />
                  </div>
                  <div style={{ color: "var(--text-muted)", fontSize: 12, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {d.subject}
                  </div>
                </div>
                <div style={{ textAlign: "right", flexShrink: 0 }}>
                  <div style={{ color: "var(--text-muted)", fontSize: 12 }}>
                    {d.contactName}
                    {d.contactTitle ? ` · ${d.contactTitle}` : ""}
                  </div>
                  <div style={{ color: "var(--border)", fontSize: 11, marginTop: 2 }}>
                    {new Date(d.createdAt).toLocaleDateString("en-US", { month: "short", day: "numeric" })}
                  </div>
                </div>
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
