"use client";

import { useRouter } from "next/navigation";

export default function LogoutButton() {
  const router = useRouter();

  async function logout() {
    await fetch("/agentic/api/auth/logout", { method: "POST" });
    router.push("/agentic/login");
  }

  return (
    <button
      onClick={logout}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        width: "100%",
        padding: "0.45rem 0.6rem",
        borderRadius: 8,
        border: "none",
        background: "transparent",
        color: "var(--text-muted)",
        cursor: "pointer",
        fontSize: 13.5,
        letterSpacing: -0.1,
        textAlign: "left",
      }}
    >
      Sign out
    </button>
  );
}
