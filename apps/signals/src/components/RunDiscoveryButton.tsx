"use client";

import { useState, useEffect } from "react";
import { Play, Settings, Loader2, CheckCircle, XCircle } from "lucide-react";

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
      <div
        style={{
          position: "fixed",
          inset: 0,
          background: "rgba(0,0,0,0.25)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          zIndex: 50,
        }}
      >
        <form
          onSubmit={handleProfileSubmit}
          style={{
            background: "var(--surface)",
            border: "1px solid var(--border)",
            borderRadius: 14,
            padding: "1.5rem",
            width: 400,
            boxShadow: "0 8px 32px rgba(0,0,0,0.12)",
            display: "flex",
            flexDirection: "column",
            gap: 12,
          }}
        >
          <div style={{ marginBottom: 4 }}>
            <div style={{ fontWeight: 700, fontSize: 15, color: "var(--text)", marginBottom: 4 }}>
              Your profile
            </div>
            <div style={{ fontSize: 12.5, color: "var(--text-muted)" }}>
              Used by the AI to find the most relevant signals for you.
            </div>
          </div>
          <div>
            <label style={labelStyle}>Skills</label>
            <input
              required
              placeholder="TypeScript, Go, React, ML…"
              value={profile.skills}
              onChange={(e) => setProfile({ ...profile, skills: e.target.value })}
              style={inputStyle}
            />
          </div>
          <div>
            <label style={labelStyle}>Background</label>
            <input
              required
              placeholder="e.g. Full-stack engineer, 5 yrs experience"
              value={profile.background}
              onChange={(e) => setProfile({ ...profile, background: e.target.value })}
              style={inputStyle}
            />
          </div>
          <div>
            <label style={labelStyle}>Past wins (optional, one per line)</label>
            <textarea
              placeholder="Led migration to microservices at Acme…"
              value={profile.pastWins}
              onChange={(e) => setProfile({ ...profile, pastWins: e.target.value })}
              rows={3}
              style={{ ...inputStyle, resize: "vertical" }}
            />
          </div>
          <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", paddingTop: 4 }}>
            <button type="button" onClick={() => setState("idle")} style={cancelBtnStyle}>
              Cancel
            </button>
            <button type="submit" style={primaryBtnStyle}>
              <Play size={13} />
              Run Discovery
            </button>
          </div>
        </form>
      </div>
    );
  }

  const isSuccess = state === "SUCCEEDED";
  const isError = ["FAILED", "ABORTED", "TIMED-OUT", "error"].includes(state);
  const isRunning = state === "running";

  return (
    <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
      <button
        onClick={handleClick}
        disabled={isRunning}
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 7,
          background: isSuccess ? "#dcfce7" : isError ? "#fee2e2" : "var(--accent)",
          border: isSuccess ? "1px solid #bbf7d0" : isError ? "1px solid #fecaca" : "none",
          borderRadius: 8,
          padding: "0.5rem 1.1rem",
          color: isSuccess ? "#15803d" : isError ? "#991b1b" : "#fff",
          cursor: isRunning ? "not-allowed" : "pointer",
          fontSize: 13.5,
          fontWeight: 600,
          opacity: isRunning ? 0.85 : 1,
          transition: "background 0.15s",
        }}
      >
        {isRunning ? (
          <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} />
        ) : isSuccess ? (
          <CheckCircle size={14} />
        ) : isError ? (
          <XCircle size={14} />
        ) : (
          <Play size={13} fill="white" />
        )}
        {isRunning
          ? "Discovering…"
          : isSuccess
          ? "Done"
          : isError
          ? "Failed — retry"
          : "Run Discovery"}
        {runId && isRunning && (
          <span style={{ fontSize: 10, opacity: 0.6 }}>{runId.slice(0, 6)}</span>
        )}
      </button>
      {hasProfile && state === "idle" && (
        <button
          onClick={() => setState("profile")}
          title="Edit profile"
          style={{
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            width: 34,
            height: 34,
            background: "var(--surface)",
            border: "1px solid var(--border)",
            borderRadius: 8,
            cursor: "pointer",
            color: "var(--text-muted)",
          }}
        >
          <Settings size={14} />
        </button>
      )}
      <style>{`@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }`}</style>
    </div>
  );
}

const labelStyle: React.CSSProperties = {
  display: "block",
  fontSize: 11,
  fontWeight: 600,
  color: "var(--text-muted)",
  marginBottom: 5,
  textTransform: "uppercase",
  letterSpacing: 0.5,
};

const inputStyle: React.CSSProperties = {
  background: "var(--surface-2)",
  border: "1.5px solid var(--border)",
  borderRadius: 7,
  padding: "0.5rem 0.7rem",
  color: "var(--text)",
  fontSize: 13,
  width: "100%",
  outline: "none",
};

const cancelBtnStyle: React.CSSProperties = {
  background: "transparent",
  border: "1px solid var(--border)",
  borderRadius: 7,
  padding: "0.45rem 0.9rem",
  color: "var(--text-muted)",
  cursor: "pointer",
  fontSize: 13,
  fontWeight: 500,
};

const primaryBtnStyle: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 6,
  background: "var(--accent)",
  border: "none",
  borderRadius: 7,
  padding: "0.45rem 0.9rem",
  color: "#fff",
  cursor: "pointer",
  fontSize: 13,
  fontWeight: 600,
};
