import { db } from "@/db";
import { hiringSignal, company } from "@/db/schema";
import { desc, eq } from "drizzle-orm";
import RunDiscoveryButton from "@/components/RunDiscoveryButton";
import SignalTypeBadge from "@/components/SignalTypeBadge";
import ScoreBadge from "@/components/ScoreBadge";
import { TrendingUp, Building2, Zap, Star } from "lucide-react";

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

  const highPriority = signals.filter((s) => s.score >= 8).length;
  const uniqueCompanies = new Set(signals.map((s) => s.companySlug ?? s.companyName)).size;
  const avgScore = signals.length > 0
    ? (signals.reduce((a, b) => a + b.score, 0) / signals.length).toFixed(1)
    : "—";

  return (
    <div>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", marginBottom: "1.75rem" }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, color: "var(--text)", letterSpacing: -0.5, marginBottom: 4 }}>
            Signal Feed
          </h1>
          <p style={{ color: "var(--text-muted)", fontSize: 13.5 }}>
            AI-detected hiring signals from funding rounds, headcount changes, and market activity.
          </p>
        </div>
        <RunDiscoveryButton />
      </div>

      {/* Stats */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12, marginBottom: "1.75rem" }}>
        <StatCard
          icon={<Zap size={16} color="#6366f1" />}
          iconBg="#eef2ff"
          label="Total Signals"
          value={signals.length.toString()}
        />
        <StatCard
          icon={<Star size={16} color="#d97706" />}
          iconBg="#fef3c7"
          label="High Priority"
          value={highPriority.toString()}
          sub="score ≥ 8.0"
        />
        <StatCard
          icon={<Building2 size={16} color="#16a34a" />}
          iconBg="#dcfce7"
          label="Companies"
          value={uniqueCompanies.toString()}
          sub="monitored"
        />
        <StatCard
          icon={<TrendingUp size={16} color="#0284c7" />}
          iconBg="#e0f2fe"
          label="Avg Score"
          value={avgScore}
          sub="out of 10"
        />
      </div>

      {/* Table */}
      {signals.length === 0 ? (
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
          <Zap size={32} color="#e2e8f0" style={{ marginBottom: 12 }} />
          <div style={{ fontWeight: 600, color: "var(--text)", marginBottom: 6 }}>No signals yet</div>
          <div style={{ fontSize: 13 }}>Run the discovery pipeline to detect hiring signals.</div>
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
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead>
              <tr style={{ background: "var(--surface-2)", borderBottom: "1px solid var(--border)" }}>
                <Th>Company</Th>
                <Th>Type</Th>
                <Th>Score</Th>
                <Th>Signal</Th>
                <Th>Date</Th>
                <Th>Links</Th>
              </tr>
            </thead>
            <tbody>
              {signals.map((s, i) => {
                const meta = (s.metadata ?? {}) as Record<string, string>;
                const initials = (s.companyName ?? meta.company_name ?? "?")
                  .split(" ")
                  .slice(0, 2)
                  .map((w: string) => w[0])
                  .join("")
                  .toUpperCase();

                return (
                  <tr
                    key={s.id}
                    style={{
                      borderBottom: i < signals.length - 1 ? "1px solid var(--border)" : "none",
                      transition: "background 0.1s",
                    }}
                    className="hover:bg-slate-50"
                  >
                    <Td>
                      <div style={{ display: "flex", alignItems: "center", gap: 9 }}>
                        <div
                          style={{
                            width: 28,
                            height: 28,
                            borderRadius: 7,
                            background: "var(--accent-light)",
                            color: "var(--accent-text)",
                            fontSize: 10,
                            fontWeight: 700,
                            display: "flex",
                            alignItems: "center",
                            justifyContent: "center",
                            flexShrink: 0,
                          }}
                        >
                          {initials}
                        </div>
                        <span style={{ color: "var(--text)", fontWeight: 600, whiteSpace: "nowrap" }}>
                          {s.companyName ?? meta.company_name ?? "—"}
                        </span>
                      </div>
                    </Td>
                    <Td>
                      <SignalTypeBadge type={s.signalType} />
                    </Td>
                    <Td>
                      <ScoreBadge score={s.score} />
                    </Td>
                    <Td style={{ maxWidth: 380 }}>
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
                      <div style={{ display: "flex", gap: 8 }}>
                        {meta.source_url ? (
                          <a
                            href={meta.source_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            style={{
                              color: "var(--accent-text)",
                              textDecoration: "none",
                              fontSize: 12,
                              fontWeight: 500,
                              background: "var(--accent-light)",
                              padding: "2px 8px",
                              borderRadius: 6,
                              whiteSpace: "nowrap",
                            }}
                          >
                            News ↗
                          </a>
                        ) : null}
                        {meta.careers_url ? (
                          <a
                            href={meta.careers_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            style={{
                              color: "#15803d",
                              textDecoration: "none",
                              fontSize: 12,
                              fontWeight: 500,
                              background: "#dcfce7",
                              padding: "2px 8px",
                              borderRadius: 6,
                              whiteSpace: "nowrap",
                            }}
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

function StatCard({
  icon,
  iconBg,
  label,
  value,
  sub,
}: {
  icon: React.ReactNode;
  iconBg: string;
  label: string;
  value: string;
  sub?: string;
}) {
  return (
    <div
      style={{
        background: "var(--surface)",
        border: "1px solid var(--border)",
        borderRadius: 10,
        padding: "1rem 1.1rem",
        display: "flex",
        alignItems: "flex-start",
        gap: 10,
      }}
    >
      <div
        style={{
          width: 34,
          height: 34,
          borderRadius: 8,
          background: iconBg,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          flexShrink: 0,
        }}
      >
        {icon}
      </div>
      <div>
        <div style={{ fontSize: 12, color: "var(--text-muted)", marginBottom: 3 }}>{label}</div>
        <div style={{ fontSize: 22, fontWeight: 700, color: "var(--text)", lineHeight: 1 }}>{value}</div>
        {sub && <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 3 }}>{sub}</div>}
      </div>
    </div>
  );
}

function Th({ children }: { children: React.ReactNode }) {
  return (
    <th
      style={{
        textAlign: "left",
        padding: "0.625rem 1rem",
        fontWeight: 600,
        fontSize: 11,
        textTransform: "uppercase",
        letterSpacing: 0.6,
        color: "var(--text-muted)",
      }}
    >
      {children}
    </th>
  );
}

function Td({ children, style }: { children: React.ReactNode; style?: React.CSSProperties }) {
  return (
    <td style={{ padding: "0.75rem 1rem", ...style }}>{children}</td>
  );
}
