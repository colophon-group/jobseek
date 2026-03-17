"use client";

import { LogOut } from "lucide-react";
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
        display: "flex",
        alignItems: "center",
        gap: 8,
        width: "100%",
        padding: "0.45rem 0.65rem",
        borderRadius: 7,
        border: "none",
        background: "transparent",
        color: "var(--text-muted)",
        cursor: "pointer",
        fontSize: 13.5,
        textAlign: "left",
      }}
    >
      <LogOut size={15} />
      Sign out
    </button>
  );
}
