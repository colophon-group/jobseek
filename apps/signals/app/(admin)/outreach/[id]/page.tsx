import { db } from "@/db";
import { outreachDraft, hiringSignal, company } from "@/db/schema";
import { eq } from "drizzle-orm";
import { notFound } from "next/navigation";
import DraftEditor from "@/components/DraftEditor";
import SignalTypeBadge from "@/components/SignalTypeBadge";
import ScoreBadge from "@/components/ScoreBadge";

export const dynamic = "force-dynamic";

export default async function DraftPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  const rows = await db
    .select({
      draft: outreachDraft,
      signal: hiringSignal,
      companyName: company.name,
      companySlug: company.slug,
    })
    .from(outreachDraft)
    .innerJoin(hiringSignal, eq(outreachDraft.signalId, hiringSignal.id))
    .leftJoin(company, eq(hiringSignal.companyId, company.id))
    .where(eq(outreachDraft.id, id))
    .limit(1);

  if (!rows.length) notFound();

  const { draft, signal, companyName } = rows[0];
  const meta = (signal.metadata ?? {}) as Record<string, string>;

  return (
    <div style={{ maxWidth: 720 }}>
      {/* Header */}
      <div style={{ marginBottom: "1.5rem" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
          <span style={{ fontWeight: 700, fontSize: 16, color: "var(--text)" }}>
            {companyName ?? meta.company_name ?? "Unknown company"}
          </span>
          <SignalTypeBadge type={signal.signalType} />
          <ScoreBadge score={signal.score} />
          <span
            style={{
              fontSize: 11,
              padding: "2px 8px",
              borderRadius: 99,
              background:
                draft.status === "sent"
                  ? "#16a34a22"
                  : draft.status === "archived"
                  ? "#52525222"
                  : "#6366f122",
              color:
                draft.status === "sent"
                  ? "#4ade80"
                  : draft.status === "archived"
                  ? "#737373"
                  : "#a5b4fc",
            }}
          >
            {draft.status.replace("_", " ")}
          </span>
        </div>
        <div style={{ color: "var(--text-muted)", fontSize: 12, lineHeight: 1.6 }}>
          {signal.signalText}
        </div>
        {meta.source_url && (
          <a
            href={meta.source_url}
            target="_blank"
            rel="noopener noreferrer"
            style={{ color: "var(--accent)", fontSize: 12, textDecoration: "none" }}
          >
            View source ↗
          </a>
        )}
      </div>

      {/* Contact */}
      <div
        style={{
          background: "var(--surface)",
          border: "1px solid var(--border)",
          borderRadius: 8,
          padding: "0.75rem 1rem",
          marginBottom: "1.5rem",
          display: "flex",
          alignItems: "center",
          gap: "1rem",
        }}
      >
        <div style={{ flex: 1 }}>
          <div style={{ fontWeight: 600, color: "var(--text)", fontSize: 13 }}>
            {draft.contactName}
          </div>
          {draft.contactTitle && (
            <div style={{ color: "var(--text-muted)", fontSize: 12 }}>{draft.contactTitle}</div>
          )}
        </div>
        {draft.contactEmail && (
          <CopyEmailButton email={draft.contactEmail} />
        )}
      </div>

      {/* Editable draft */}
      <DraftEditor
        id={draft.id}
        subject={draft.subject}
        body={draft.body}
        status={draft.status as "pending_review" | "sent" | "archived"}
      />
    </div>
  );
}

function CopyEmailButton({ email }: { email: string }) {
  // Server-rendered but needs client for clipboard — rendered as a span with data attr,
  // handled by CopyButton client component below.
  return <CopyEmailClientButton email={email} />;
}

// Inline client component to avoid extra file
import CopyEmailClientButton from "@/components/CopyButton";
