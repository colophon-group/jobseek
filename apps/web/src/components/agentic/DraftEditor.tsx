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
    const res = await fetch(`/agentic/api/outreach/${id}`, {
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
      router.push("/agentic/outreach");
    } finally {
      setActionLoading(null);
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div>
        <label style={labelStyle}>Subject line</label>
        <input
          type="text"
          value={editSubject}
          onChange={(e) => setEditSubject(e.target.value)}
          style={inputStyle}
          placeholder="Email subject…"
        />
      </div>

      <div>
        <label style={labelStyle}>Email body</label>
        <textarea
          value={editBody}
          onChange={(e) => setEditBody(e.target.value)}
          rows={16}
          style={{ ...inputStyle, resize: "vertical", lineHeight: 1.65 }}
          placeholder="Email body…"
        />
      </div>

      <div style={{ display: "flex", gap: 8, paddingTop: 4, flexWrap: "wrap" }}>
        <button onClick={save} disabled={saving} style={secondaryBtnStyle(saving)}>
          {saving ? "Saving…" : "Save draft"}
        </button>

        {status !== "sent" && (
          <button
            onClick={() => changeStatus("sent")}
            disabled={!!actionLoading}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 6,
              background: "rgba(52,199,89,0.12)",
              border: "none",
              borderRadius: 9,
              padding: "0.55rem 1.1rem",
              color: "#1a8c3f",
              cursor: actionLoading ? "not-allowed" : "pointer",
              fontSize: 13.5,
              fontWeight: 600,
              letterSpacing: -0.1,
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
              background: "rgba(0,0,0,0.05)",
              border: "none",
              borderRadius: 9,
              padding: "0.55rem 1.1rem",
              color: "var(--text-muted)",
              cursor: actionLoading ? "not-allowed" : "pointer",
              fontSize: 13.5,
              fontWeight: 500,
              letterSpacing: -0.1,
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

const labelStyle: React.CSSProperties = {
  display: "block",
  fontSize: 10.5,
  fontWeight: 600,
  color: "var(--text-muted)",
  marginBottom: 6,
  textTransform: "uppercase",
  letterSpacing: 1,
};

const inputStyle: React.CSSProperties = {
  background: "var(--background)",
  border: "none",
  borderRadius: 10,
  padding: "0.65rem 0.9rem",
  color: "var(--text)",
  fontSize: 14,
  width: "100%",
  outline: "none",
  fontFamily: "inherit",
  letterSpacing: -0.1,
};

function secondaryBtnStyle(disabled: boolean): React.CSSProperties {
  return {
    background: "rgba(0,0,0,0.05)",
    border: "none",
    borderRadius: 9,
    padding: "0.55rem 1.1rem",
    color: "var(--text-secondary)",
    cursor: disabled ? "not-allowed" : "pointer",
    fontSize: 13.5,
    fontWeight: 500,
    letterSpacing: -0.1,
    opacity: disabled ? 0.6 : 1,
  };
}
