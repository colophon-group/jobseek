"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

interface Props {
  id: string;
  subject: string;
  body: string;
  status: "pending_review" | "sent" | "archived";
}

export default function DraftEditor({ id, subject, body, status }: Props) {
  const router = useRouter();
  const [editSubject, setEditSubject] = useState(subject);
  const [editBody, setEditBody] = useState(body);
  const [saving, setSaving] = useState(false);
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  async function patch(updates: Record<string, string>) {
    const res = await fetch(`/api/outreach/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(updates),
    });
    if (!res.ok) throw new Error("Failed to update");
  }

  async function save() {
    setSaving(true);
    try {
      await patch({ subject: editSubject, body: editBody });
      router.refresh();
    } finally {
      setSaving(false);
    }
  }

  async function changeStatus(newStatus: string) {
    setActionLoading(newStatus);
    try {
      await patch({ status: newStatus });
      router.push("/outreach");
    } finally {
      setActionLoading(null);
    }
  }

  const inputStyle: React.CSSProperties = {
    background: "var(--surface)",
    border: "1px solid var(--border)",
    borderRadius: 6,
    padding: "0.5rem 0.75rem",
    color: "var(--text)",
    fontSize: 13,
    width: "100%",
    outline: "none",
    fontFamily: "inherit",
    resize: "vertical" as const,
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div>
        <label style={{ display: "block", fontSize: 11, color: "var(--text-muted)", marginBottom: 4, textTransform: "uppercase", letterSpacing: 0.5 }}>
          Subject
        </label>
        <input
          type="text"
          value={editSubject}
          onChange={(e) => setEditSubject(e.target.value)}
          style={{ ...inputStyle, resize: undefined }}
        />
      </div>

      <div>
        <label style={{ display: "block", fontSize: 11, color: "var(--text-muted)", marginBottom: 4, textTransform: "uppercase", letterSpacing: 0.5 }}>
          Body
        </label>
        <textarea
          value={editBody}
          onChange={(e) => setEditBody(e.target.value)}
          rows={14}
          style={inputStyle}
        />
      </div>

      <div style={{ display: "flex", gap: 8, paddingTop: 4 }}>
        <button
          onClick={save}
          disabled={saving}
          style={{
            background: "var(--surface-2)",
            border: "1px solid var(--border)",
            borderRadius: 6,
            padding: "0.4rem 1rem",
            color: "var(--text)",
            cursor: saving ? "not-allowed" : "pointer",
            fontSize: 13,
            opacity: saving ? 0.6 : 1,
          }}
        >
          {saving ? "Saving…" : "Save"}
        </button>

        {status !== "sent" && (
          <button
            onClick={() => changeStatus("sent")}
            disabled={!!actionLoading}
            style={{
              background: "#16a34a22",
              border: "1px solid #16a34a44",
              borderRadius: 6,
              padding: "0.4rem 1rem",
              color: "#4ade80",
              cursor: actionLoading ? "not-allowed" : "pointer",
              fontSize: 13,
              opacity: actionLoading ? 0.6 : 1,
            }}
          >
            {actionLoading === "sent" ? "Marking…" : "Mark as Sent"}
          </button>
        )}

        {status !== "archived" && (
          <button
            onClick={() => changeStatus("archived")}
            disabled={!!actionLoading}
            style={{
              background: "transparent",
              border: "1px solid var(--border)",
              borderRadius: 6,
              padding: "0.4rem 1rem",
              color: "var(--text-muted)",
              cursor: actionLoading ? "not-allowed" : "pointer",
              fontSize: 13,
              opacity: actionLoading ? 0.6 : 1,
            }}
          >
            {actionLoading === "archived" ? "Archiving…" : "Archive"}
          </button>
        )}
      </div>
    </div>
  );
}
