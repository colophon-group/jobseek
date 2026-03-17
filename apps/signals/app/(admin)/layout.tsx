"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import LogoutButton from "@/components/LogoutButton";
import { Zap, Mail, BarChart3, LogOut } from "lucide-react";

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
          width: 240,
          background: "var(--surface)",
          borderRight: "1px solid var(--border)",
          display: "flex",
          flexDirection: "column",
          flexShrink: 0,
          position: "sticky",
          top: 0,
          height: "100vh",
        }}
      >
        {/* Logo */}
        <div
          style={{
            padding: "1.25rem 1.25rem 1rem",
            borderBottom: "1px solid var(--border)",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <div
              style={{
                width: 30,
                height: 30,
                background: "var(--accent)",
                borderRadius: 8,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                flexShrink: 0,
              }}
            >
              <Zap size={16} color="white" fill="white" />
            </div>
            <div>
              <div style={{ fontWeight: 700, fontSize: 15, color: "var(--text)", letterSpacing: -0.3 }}>
                Signals
              </div>
              <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 1 }}>
                AI hiring intelligence
              </div>
            </div>
          </div>
        </div>

        {/* Nav */}
        <nav style={{ padding: "0.75rem 0.75rem", flex: 1, display: "flex", flexDirection: "column", gap: 2 }}>
          <div style={{ fontSize: 10, fontWeight: 600, color: "var(--text-muted)", letterSpacing: 1, textTransform: "uppercase", padding: "0.25rem 0.5rem", marginBottom: 4 }}>
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
                  padding: "0.45rem 0.65rem",
                  borderRadius: 7,
                  color: active ? "var(--accent-text)" : "var(--text-muted)",
                  background: active ? "var(--accent-light)" : "transparent",
                  textDecoration: "none",
                  fontSize: 13.5,
                  fontWeight: active ? 600 : 400,
                  transition: "background 0.1s, color 0.1s",
                }}
              >
                <Icon size={15} strokeWidth={active ? 2.5 : 2} />
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
      <main style={{ flex: 1, padding: "2rem 2.5rem", overflowY: "auto", maxWidth: "calc(100vw - 240px)" }}>
        {children}
      </main>
    </div>
  );
}
