"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Save, Send, Archive } from "lucide-react";

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

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
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
          style={{ ...inputStyle, resize: "vertical", lineHeight: 1.6 }}
          placeholder="Email body…"
        />
      </div>

      <div style={{ display: "flex", gap: 8, paddingTop: 4, flexWrap: "wrap" }}>
        <button
          onClick={save}
          disabled={saving}
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
            background: "var(--surface-2)",
            border: "1px solid var(--border)",
            borderRadius: 8,
            padding: "0.5rem 1rem",
            color: "var(--text)",
            cursor: saving ? "not-allowed" : "pointer",
            fontSize: 13,
            fontWeight: 500,
            opacity: saving ? 0.6 : 1,
          }}
        >
          <Save size={13} />
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
              background: "#dcfce7",
              border: "1px solid #bbf7d0",
              borderRadius: 8,
              padding: "0.5rem 1rem",
              color: "#15803d",
              cursor: actionLoading ? "not-allowed" : "pointer",
              fontSize: 13,
              fontWeight: 600,
              opacity: actionLoading ? 0.6 : 1,
            }}
          >
            <Send size={13} />
            {actionLoading === "sent" ? "Marking…" : "Mark as Sent"}
          </button>
        )}

        {status !== "archived" && (
          <button
            onClick={() => changeStatus("archived")}
            disabled={!!actionLoading}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 6,
              background: "transparent",
              border: "1px solid var(--border)",
              borderRadius: 8,
              padding: "0.5rem 1rem",
              color: "var(--text-muted)",
              cursor: actionLoading ? "not-allowed" : "pointer",
              fontSize: 13,
              fontWeight: 500,
              opacity: actionLoading ? 0.6 : 1,
            }}
          >
            <Archive size={13} />
            {actionLoading === "archived" ? "Archiving…" : "Archive"}
          </button>
        )}
      </div>
    </div>
  );
}

const labelStyle: React.CSSProperties = {
  display: "block",
  fontSize: 11,
  fontWeight: 600,
  color: "var(--text-muted)",
  marginBottom: 6,
  textTransform: "uppercase",
  letterSpacing: 0.5,
};

const inputStyle: React.CSSProperties = {
  background: "var(--surface-2)",
  border: "1.5px solid var(--border)",
  borderRadius: 8,
  padding: "0.6rem 0.8rem",
  color: "var(--text)",
  fontSize: 13.5,
  width: "100%",
  outline: "none",
  fontFamily: "inherit",
};
