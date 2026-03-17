"use client";

import { useState, useEffect } from "react";

type RunState = "idle" | "profile" | "running" | "SUCCEEDED" | "FAILED" | "ABORTED" | "TIMED-OUT" | "error";

const PROFILE_KEY = "discovery_user_profile";

interface UserProfile {
  skills: string;
  background: string;
  pastWins: string;
}

function loadProfile(): UserProfile | null {
  try {
    const raw = localStorage.getItem(PROFILE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function saveProfile(p: UserProfile) {
  localStorage.setItem(PROFILE_KEY, JSON.stringify(p));
}

export default function RunDiscoveryButton() {
  const [state, setState] = useState<RunState>("idle");
  const [runId, setRunId] = useState<string | null>(null);
  const [profile, setProfile] = useState<UserProfile>({ skills: "", background: "", pastWins: "" });
  const [hasProfile, setHasProfile] = useState(false);

  useEffect(() => {
    const p = loadProfile();
    if (p) {
      setProfile(p);
      setHasProfile(true);
    }
  }, []);

  function handleClick() {
    if (!hasProfile) {
      setState("profile");
    } else {
      triggerRun(profile);
    }
  }

  async function handleProfileSubmit(e: React.FormEvent) {
    e.preventDefault();
    saveProfile(profile);
    setHasProfile(true);
    setState("idle");
    await triggerRun(profile);
  }

  async function triggerRun(p: UserProfile) {
    setState("running");
    try {
      const body = {
        userProfile: {
          skills: p.skills.split(",").map((s) => s.trim()).filter(Boolean),
          background: p.background,
          pastWins: p.pastWins.split("\n").map((s) => s.trim()).filter(Boolean),
        },
      };
      const res = await fetch("/api/apify/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.error || "Failed to trigger run");
      }
      const data = await res.json();
      setRunId(data.runId);
      pollStatus(data.runId);
    } catch {
      setState("error");
    }
  }

  async function pollStatus(id: string) {
    const interval = setInterval(async () => {
      try {
        const res = await fetch(`/api/apify/status/${id}`);
        if (!res.ok) return;
        const data = await res.json();
        const terminal = ["SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"];
        if (terminal.includes(data.status)) {
          setState(data.status as RunState);
          clearInterval(interval);
        }
      } catch {
        // keep polling
      }
    }, 3000);
  }

  if (state === "profile") {
    return (
      <form
        onSubmit={handleProfileSubmit}
        style={{
          background: "var(--surface)",
          border: "1px solid var(--border)",
          borderRadius: 8,
          padding: "1rem",
          width: 340,
          display: "flex",
          flexDirection: "column",
          gap: 8,
        }}
      >
        <span style={{ fontSize: 12, fontWeight: 600, color: "var(--text)" }}>Your profile (sent to discovery actor)</span>
        <input
          required
          placeholder="Skills (comma-separated, e.g. TypeScript, Go, React)"
          value={profile.skills}
          onChange={(e) => setProfile({ ...profile, skills: e.target.value })}
          style={inputStyle}
        />
        <input
          required
          placeholder="Background (e.g. Full-stack engineer, 5 yrs experience)"
          value={profile.background}
          onChange={(e) => setProfile({ ...profile, background: e.target.value })}
          style={inputStyle}
        />
        <textarea
          placeholder="Past wins, one per line (optional)"
          value={profile.pastWins}
          onChange={(e) => setProfile({ ...profile, pastWins: e.target.value })}
          rows={2}
          style={{ ...inputStyle, resize: "vertical" }}
        />
        <div style={{ display: "flex", gap: 6, justifyContent: "flex-end" }}>
          <button type="button" onClick={() => setState("idle")} style={cancelBtnStyle}>Cancel</button>
          <button type="submit" style={submitBtnStyle}>Run Discovery</button>
        </div>
      </form>
    );
  }

  const label =
    state === "idle" ? "Run Discovery"
    : state === "running" ? "Running…"
    : state === "SUCCEEDED" ? "Done ✓"
    : state === "FAILED" || state === "ABORTED" || state === "TIMED-OUT" ? `${state} ✗`
    : "Error";

  const color =
    state === "SUCCEEDED" ? "#4ade80"
    : state === "FAILED" || state === "ABORTED" || state === "TIMED-OUT" || state === "error" ? "#f87171"
    : "var(--text)";

  return (
    <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
      <button
        onClick={handleClick}
        disabled={state === "running"}
        style={{
          background: "var(--accent)",
          border: "none",
          borderRadius: 6,
          padding: "0.4rem 1rem",
          color: color,
          cursor: state === "running" ? "not-allowed" : "pointer",
          fontSize: 13,
          opacity: state === "running" ? 0.7 : 1,
        }}
      >
        {label}
        {runId && state === "running" && (
          <span style={{ marginLeft: 6, fontSize: 11, opacity: 0.6 }}>{runId.slice(0, 8)}</span>
        )}
      </button>
      {hasProfile && state === "idle" && (
        <button
          onClick={() => setState("profile")}
          title="Edit profile"
          style={{ background: "none", border: "none", cursor: "pointer", color: "var(--text-muted)", fontSize: 13, padding: "0.4rem" }}
        >
          ✎
        </button>
      )}
    </div>
  );
}

const inputStyle: React.CSSProperties = {
  background: "var(--surface-2, #1a1a1a)",
  border: "1px solid var(--border)",
  borderRadius: 4,
  padding: "0.4rem 0.6rem",
  color: "var(--text)",
  fontSize: 12,
  width: "100%",
  outline: "none",
};

const cancelBtnStyle: React.CSSProperties = {
  background: "none",
  border: "1px solid var(--border)",
  borderRadius: 4,
  padding: "0.3rem 0.75rem",
  color: "var(--text-muted)",
  cursor: "pointer",
  fontSize: 12,
};

const submitBtnStyle: React.CSSProperties = {
  background: "var(--accent)",
  border: "none",
  borderRadius: 4,
  padding: "0.3rem 0.75rem",
  color: "var(--text)",
  cursor: "pointer",
  fontSize: 12,
};
