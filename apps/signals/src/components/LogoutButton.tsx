"use client";

import { useRouter } from "next/navigation";

export default function LogoutButton() {
  const router = useRouter();

  async function logout() {
    await fetch("/api/auth/logout", { method: "POST" });
    router.push("/login");
  }

  return (
    <button
      onClick={logout}
      style={{
        background: "transparent",
        border: "none",
        color: "var(--text-muted)",
        cursor: "pointer",
        fontSize: 13,
        padding: "0.4rem 0.75rem",
        textAlign: "left",
        borderRadius: 6,
        width: "100%",
      }}
      className="hover:bg-white/5"
    >
      Sign out
    </button>
  );
}
