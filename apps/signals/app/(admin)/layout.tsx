"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import LogoutButton from "@/components/LogoutButton";
import { BarChart3, Mail } from "lucide-react";

const NAV = [
  { href: "/signals", label: "Signals", icon: BarChart3 },
  { href: "/outreach", label: "Outreach", icon: Mail },
];

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();

  return (
    <div style={{ display: "flex", minHeight: "100vh", background: "var(--background)" }}>
      {/* Sidebar */}
      <aside
        style={{
          width: 220,
          background: "rgba(255,255,255,0.72)",
          backdropFilter: "blur(20px)",
          WebkitBackdropFilter: "blur(20px)",
          borderRight: "1px solid rgba(0,0,0,0.06)",
          display: "flex",
          flexDirection: "column",
          flexShrink: 0,
          position: "sticky",
          top: 0,
          height: "100vh",
        }}
      >
        {/* Brand */}
        <div style={{ padding: "1.5rem 1.25rem 1rem" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <div
              style={{
                width: 28,
                height: 28,
                background: "linear-gradient(145deg, #0071e3 0%, #5e5ce6 100%)",
                borderRadius: 8,
                flexShrink: 0,
                boxShadow: "0 2px 8px rgba(0,113,227,0.35)",
              }}
            />
            <span
              style={{
                fontWeight: 600,
                fontSize: 15,
                color: "var(--text)",
                letterSpacing: -0.3,
              }}
            >
              Signals
            </span>
          </div>
        </div>

        {/* Nav */}
        <nav style={{ padding: "0 0.75rem", flex: 1, display: "flex", flexDirection: "column", gap: 2 }}>
          <div
            style={{
              fontSize: 10,
              fontWeight: 600,
              color: "var(--text-subtle)",
              letterSpacing: 1.2,
              textTransform: "uppercase",
              padding: "0.5rem 0.5rem 0.4rem",
              marginBottom: 2,
            }}
          >
            Workspace
          </div>
          {NAV.map(({ href, label, icon: Icon }) => {
            const active = pathname.startsWith(href);
            return (
              <Link
                key={href}
                href={href}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 9,
                  padding: "0.5rem 0.6rem",
                  borderRadius: 9,
                  color: active ? "var(--accent)" : "var(--text-muted)",
                  background: active ? "rgba(0,113,227,0.08)" : "transparent",
                  textDecoration: "none",
                  fontSize: 13.5,
                  fontWeight: active ? 600 : 400,
                  letterSpacing: -0.1,
                  transition: "background 0.15s, color 0.15s",
                }}
              >
                <Icon size={15} strokeWidth={active ? 2.2 : 1.8} />
                {label}
              </Link>
            );
          })}
        </nav>

        {/* Footer */}
        <div style={{ padding: "0.75rem", borderTop: "1px solid var(--border)" }}>
          <LogoutButton />
        </div>
      </aside>

      {/* Main */}
      <main
        style={{
          flex: 1,
          padding: "2.5rem 3rem",
          overflowY: "auto",
        }}
      >
        {children}
      </main>
    </div>
  );
}
