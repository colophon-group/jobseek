import { db } from "@/db";
import { hiringSignal, company } from "@/db/schema";
import { desc, eq } from "drizzle-orm";
import RunDiscoveryButton from "@/components/RunDiscoveryButton";
import SignalTypeBadge from "@/components/SignalTypeBadge";
import ScoreBadge from "@/components/ScoreBadge";

export const dynamic = "force-dynamic";

const SIGNAL_DOT: Record<string, string> = {
  funding:    "var(--dot-orange)",
  sec_filing: "var(--dot-blue)",
  twitter:    "var(--dot-indigo)",
  headcount:  "var(--dot-green)",
  github:     "var(--dot-purple)",
  job_gap:    "var(--dot-red)",
};

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
  const avgScore =
    signals.length > 0
      ? (signals.reduce((a, b) => a + b.score, 0) / signals.length).toFixed(1)
      : "—";

  return (
    <div>
      {/* Page header */}
      <div
        style={{
          display: "flex",
          alignItems: "flex-start",
          justifyContent: "space-between",
          marginBottom: "2rem",
        }}
      >
        <div>
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
            Intelligence
          </p>
          <h1
            style={{
              fontSize: 28,
              fontWeight: 700,
              color: "var(--text)",
              letterSpacing: -0.8,
              margin: 0,
              lineHeight: 1.1,
            }}
          >
            Signal Feed
          </h1>
        </div>
        <div style={{ paddingTop: 8 }}>
          <RunDiscoveryButton />
        </div>
      </div>

      {/* Stat cards — Apple architecture card style */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(4, 1fr)",
          gap: 14,
          marginBottom: "2rem",
        }}
      >
        <AppleStatCard dot="var(--dot-blue)" label="Total Signals" value={signals.length.toString()} sub="detected" />
        <AppleStatCard dot="var(--dot-orange)" label="High Priority" value={highPriority.toString()} sub="score ≥ 8.0" />
        <AppleStatCard dot="var(--dot-green)" label="Companies" value={uniqueCompanies.toString()} sub="monitored" />
        <AppleStatCard dot="var(--dot-purple)" label="Avg Score" value={avgScore} sub="out of 10" />
      </div>

      {/* Signal table */}
      {signals.length === 0 ? (
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
              width: 10,
              height: 10,
              borderRadius: "50%",
              background: "var(--dot-gray)",
              margin: "0 auto 1.25rem",
            }}
          />
          <div style={{ fontWeight: 600, fontSize: 16, color: "var(--text)", marginBottom: 6 }}>
            No signals yet
          </div>
          <div style={{ color: "var(--text-muted)", fontSize: 13.5 }}>
            Run discovery to detect hiring signals from funding rounds and market activity.
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
          {/* Table header */}
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "200px 110px 70px 1fr 80px 120px",
              padding: "0.75rem 1.5rem",
              borderBottom: "1px solid rgba(0,0,0,0.06)",
              background: "rgba(0,0,0,0.015)",
            }}
          >
            {["Company", "Type", "Score", "Signal", "Date", "Links"].map((h) => (
              <span
                key={h}
                style={{
                  fontSize: 10.5,
                  fontWeight: 600,
                  letterSpacing: 1,
                  textTransform: "uppercase",
                  color: "var(--text-muted)",
                }}
              >
                {h}
              </span>
            ))}
          </div>

          {/* Rows */}
          {signals.map((s, i) => {
            const meta = (s.metadata ?? {}) as Record<string, string>;
            const name = s.companyName ?? meta.company_name ?? "—";
            const dot = SIGNAL_DOT[s.signalType] ?? "var(--dot-gray)";

            return (
              <div
                key={s.id}
                style={{
                  display: "grid",
                  gridTemplateColumns: "200px 110px 70px 1fr 80px 120px",
                  padding: "0.85rem 1.5rem",
                  borderBottom:
                    i < signals.length - 1 ? "1px solid rgba(0,0,0,0.05)" : "none",
                  alignItems: "center",
                  transition: "background 0.12s",
                }}
                className="hover:bg-black/[0.02]"
              >
                {/* Company */}
                <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
                  <span
                    style={{
                      display: "inline-block",
                      width: 8,
                      height: 8,
                      borderRadius: "50%",
                      background: dot,
                      flexShrink: 0,
                    }}
                  />
                  <span
                    style={{
                      fontWeight: 600,
                      fontSize: 13.5,
                      color: "var(--text)",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                      letterSpacing: -0.1,
                    }}
                  >
                    {name}
                  </span>
                </div>

                {/* Type */}
                <div>
                  <SignalTypeBadge type={s.signalType} />
                </div>

                {/* Score */}
                <div>
                  <ScoreBadge score={s.score} />
                </div>

                {/* Signal text */}
                <span
                  title={s.signalText}
                  style={{
                    color: "var(--text-muted)",
                    fontSize: 13,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                    display: "block",
                    letterSpacing: -0.1,
                  }}
                >
                  {s.signalText}
                </span>

                {/* Date */}
                <span style={{ color: "var(--text-subtle)", fontSize: 12.5, whiteSpace: "nowrap" }}>
                  {s.signalDate
                    ? new Date(s.signalDate).toLocaleDateString("en-US", {
                        month: "short",
                        day: "numeric",
                      })
                    : "—"}
                </span>

                {/* Links */}
                <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                  {meta.source_url && (
                    <a
                      href={meta.source_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      style={{
                        color: "var(--accent)",
                        fontSize: 12,
                        fontWeight: 500,
                        textDecoration: "none",
                        letterSpacing: -0.1,
                      }}
                    >
                      News ↗
                    </a>
                  )}
                  {meta.careers_url && (
                    <a
                      href={meta.careers_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      style={{
                        color: "var(--dot-green)",
                        fontSize: 12,
                        fontWeight: 500,
                        textDecoration: "none",
                        letterSpacing: -0.1,
                      }}
                    >
                      Jobs ↗
                    </a>
                  )}
                  {!meta.source_url && !meta.careers_url && (
                    <span style={{ color: "var(--text-subtle)" }}>—</span>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function AppleStatCard({
  dot,
  label,
  value,
  sub,
}: {
  dot: string;
  label: string;
  value: string;
  sub: string;
}) {
  return (
    <div
      style={{
        background: "var(--surface)",
        borderRadius: "var(--radius)",
        boxShadow: "var(--card-shadow)",
        padding: "1.25rem 1.25rem 1.1rem",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        textAlign: "center",
        gap: 8,
      }}
    >
      <span
        style={{
          display: "inline-block",
          width: 9,
          height: 9,
          borderRadius: "50%",
          background: dot,
        }}
      />
      <div
        style={{
          fontSize: 26,
          fontWeight: 700,
          color: "var(--text)",
          letterSpacing: -1,
          lineHeight: 1,
        }}
      >
        {value}
      </div>
      <div>
        <div style={{ fontWeight: 600, fontSize: 13, color: "var(--text)", letterSpacing: -0.2 }}>
          {label}
        </div>
        <div style={{ fontSize: 11.5, color: "var(--text-muted)", marginTop: 2 }}>{sub}</div>
      </div>
    </div>
  );
}
