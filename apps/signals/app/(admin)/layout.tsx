import Link from "next/link";
import LogoutButton from "@/components/LogoutButton";

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen" style={{ background: "var(--background)" }}>
      {/* Sidebar */}
      <aside
        style={{
          width: 220,
          background: "var(--surface)",
          borderRight: "1px solid var(--border)",
          padding: "1.5rem 1rem",
          display: "flex",
          flexDirection: "column",
          gap: "0.25rem",
          flexShrink: 0,
        }}
      >
        <div style={{ color: "var(--accent)", fontWeight: 700, fontSize: 15, marginBottom: "1.5rem", letterSpacing: 1 }}>
          SIGNALS
        </div>
        <NavLink href="/signals">Signals</NavLink>
        <NavLink href="/outreach">Outreach</NavLink>
        <div style={{ flexGrow: 1 }} />
        <LogoutButton />
      </aside>

      {/* Main content */}
      <main style={{ flex: 1, padding: "2rem", overflowY: "auto" }}>
        {children}
      </main>
    </div>
  );
}

function NavLink({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <Link
      href={href}
      style={{
        display: "block",
        padding: "0.4rem 0.75rem",
        borderRadius: 6,
        color: "var(--text-muted)",
        textDecoration: "none",
        fontSize: 13,
        transition: "background 0.1s",
      }}
      className="hover:bg-white/5"
    >
      {children}
    </Link>
  );
}
