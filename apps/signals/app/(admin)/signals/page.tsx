import { db } from "@/db";
import { hiringSignal, company } from "@/db/schema";
import { desc, eq } from "drizzle-orm";
import RunDiscoveryButton from "@/components/RunDiscoveryButton";
import SignalTypeBadge from "@/components/SignalTypeBadge";
import ScoreBadge from "@/components/ScoreBadge";

export const dynamic = "force-dynamic";

export default async function SignalsPage() {
  const signals = await db
    .select({
      id: hiringSignal.id,
      signalType: hiringSignal.signalType,
      signalText: hiringSignal.signalText,
      signalDate: hiringSignal.signalDate,
      score: hiringSignal.score,
      reasoning: hiringSignal.reasoning,
      metadata: hiringSignal.metadata,
      companyName: company.name,
      companySlug: company.slug,
    })
    .from(hiringSignal)
    .leftJoin(company, eq(hiringSignal.companyId, company.id))
    .orderBy(desc(hiringSignal.signalDate))
    .limit(200);

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 style={{ fontSize: 18, fontWeight: 600, color: "var(--text)" }}>
          Hiring Signals
          <span style={{ marginLeft: 8, fontSize: 13, color: "var(--text-muted)", fontWeight: 400 }}>
            {signals.length} total
          </span>
        </h1>
        <RunDiscoveryButton />
      </div>

      {signals.length === 0 ? (
        <div style={{ color: "var(--text-muted)", marginTop: "4rem", textAlign: "center" }}>
          No signals yet. Run the discovery pipeline to get started.
        </div>
      ) : (
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead>
              <tr style={{ borderBottom: "1px solid var(--border)", color: "var(--text-muted)" }}>
                <Th>Company</Th>
                <Th>Type</Th>
                <Th>Score</Th>
                <Th>Signal</Th>
                <Th>Date</Th>
                <Th>Links</Th>
              </tr>
            </thead>
            <tbody>
              {signals.map((s) => {
                const meta = (s.metadata ?? {}) as Record<string, string>;
                return (
                  <tr
                    key={s.id}
                    style={{ borderBottom: "1px solid var(--border)" }}
                    className="hover:bg-white/3"
                  >
                    <Td>
                      <span style={{ color: "var(--text)", fontWeight: 500 }}>
                        {s.companyName ?? meta.company_name ?? "—"}
                      </span>
                    </Td>
                    <Td>
                      <SignalTypeBadge type={s.signalType} />
                    </Td>
                    <Td>
                      <ScoreBadge score={s.score} />
                    </Td>
                    <Td style={{ maxWidth: 360 }}>
                      <span
                        title={s.signalText}
                        style={{
                          display: "block",
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                          color: "var(--text-muted)",
                        }}
                      >
                        {s.signalText}
                      </span>
                    </Td>
                    <Td>
                      <span style={{ color: "var(--text-muted)", whiteSpace: "nowrap" }}>
                        {s.signalDate
                          ? new Date(s.signalDate).toLocaleDateString("en-US", {
                              month: "short",
                              day: "numeric",
                            })
                          : "—"}
                      </span>
                    </Td>
                    <Td>
                      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                        {meta.source_url ? (
                          <a
                            href={meta.source_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            title="News source"
                            style={{ color: "var(--accent)", textDecoration: "none", fontSize: 12 }}
                          >
                            News ↗
                          </a>
                        ) : null}
                        {meta.careers_url ? (
                          <a
                            href={meta.careers_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            title="Careers page"
                            style={{ color: "#4ade80", textDecoration: "none", fontSize: 12 }}
                          >
                            Careers ↗
                          </a>
                        ) : null}
                        {!meta.source_url && !meta.careers_url && (
                          <span style={{ color: "var(--border)" }}>—</span>
                        )}
                      </div>
                    </Td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function Th({ children }: { children: React.ReactNode }) {
  return (
    <th style={{ textAlign: "left", padding: "0.5rem 0.75rem", fontWeight: 500, fontSize: 12, textTransform: "uppercase", letterSpacing: 0.5 }}>
      {children}
    </th>
  );
}

function Td({ children, style }: { children: React.ReactNode; style?: React.CSSProperties }) {
  return (
    <td style={{ padding: "0.6rem 0.75rem", ...style }}>{children}</td>
  );
}
