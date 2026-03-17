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
    if (p) { setProfile(p); setHasProfile(true); }
  }, []);

  function handleClick() {
    if (!hasProfile) setState("profile");
    else triggerRun(profile);
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
      if (!res.ok) throw new Error();
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
        if (["SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"].includes(data.status)) {
          setState(data.status as RunState);
          clearInterval(interval);
        }
      } catch { /* keep polling */ }
    }, 3000);
  }

  if (state === "profile") {
    return (
      <div
        style={{
          position: "fixed",
          inset: 0,
          background: "rgba(0,0,0,0.2)",
          backdropFilter: "blur(8px)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          zIndex: 50,
        }}
        onClick={(e) => e.target === e.currentTarget && setState("idle")}
      >
        <form
          onSubmit={handleProfileSubmit}
          style={{
            background: "var(--surface)",
            borderRadius: 20,
            padding: "1.75rem",
            width: 400,
            boxShadow: "0 12px 48px rgba(0,0,0,0.18)",
            display: "flex",
            flexDirection: "column",
            gap: 14,
          }}
        >
          <div style={{ marginBottom: 4 }}>
            <p style={{ fontSize: 11, fontWeight: 600, letterSpacing: 1.2, textTransform: "uppercase", color: "var(--text-muted)", margin: "0 0 6px" }}>
              Discovery
            </p>
            <div style={{ fontWeight: 700, fontSize: 18, color: "var(--text)", letterSpacing: -0.4 }}>
              Your profile
            </div>
            <div style={{ fontSize: 13, color: "var(--text-muted)", marginTop: 4 }}>
              Tells the AI what signals matter most to you.
            </div>
          </div>
          <div>
            <label style={labelStyle}>Skills</label>
            <input required placeholder="TypeScript, Go, React, ML…" value={profile.skills}
              onChange={(e) => setProfile({ ...profile, skills: e.target.value })}
              style={inputStyle} />
          </div>
          <div>
            <label style={labelStyle}>Background</label>
            <input required placeholder="e.g. Full-stack engineer, 5 yrs experience" value={profile.background}
              onChange={(e) => setProfile({ ...profile, background: e.target.value })}
              style={inputStyle} />
          </div>
          <div>
            <label style={labelStyle}>Past wins (optional)</label>
            <textarea placeholder="One per line…" value={profile.pastWins} rows={3}
              onChange={(e) => setProfile({ ...profile, pastWins: e.target.value })}
              style={{ ...inputStyle, resize: "vertical" }} />
          </div>
          <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
            <button type="button" onClick={() => setState("idle")} style={cancelStyle}>Cancel</button>
            <button type="submit" style={primaryStyle}>Run Discovery</button>
          </div>
        </form>
      </div>
    );
  }

  const isSuccess = state === "SUCCEEDED";
  const isError = ["FAILED", "ABORTED", "TIMED-OUT", "error"].includes(state);
  const isRunning = state === "running";

  return (
    <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
      <button
        onClick={handleClick}
        disabled={isRunning}
        style={{
          background: isSuccess
            ? "rgba(52,199,89,0.12)"
            : isError
            ? "rgba(255,59,48,0.1)"
            : "var(--accent)",
          color: isSuccess ? "#1a8c3f" : isError ? "#cc2a22" : "#fff",
          border: "none",
          borderRadius: 10,
          padding: "0.55rem 1.25rem",
          fontSize: 13.5,
          fontWeight: 600,
          letterSpacing: -0.2,
          cursor: isRunning ? "not-allowed" : "pointer",
          opacity: isRunning ? 0.8 : 1,
          display: "inline-flex",
          alignItems: "center",
          gap: 7,
          transition: "background 0.15s",
        }}
      >
        {isRunning && (
          <span
            style={{
              display: "inline-block",
              width: 8,
              height: 8,
              borderRadius: "50%",
              background: "rgba(255,255,255,0.6)",
              animation: "pulse 1.2s ease-in-out infinite",
            }}
          />
        )}
        {isRunning ? "Discovering…"
          : isSuccess ? "Done"
          : isError ? "Failed · Retry"
          : "Run Discovery"}
        {runId && isRunning && (
          <span style={{ fontSize: 10, opacity: 0.55 }}>{runId.slice(0, 6)}</span>
        )}
      </button>

      {hasProfile && state === "idle" && (
        <button
          onClick={() => setState("profile")}
          title="Edit profile"
          style={{
            background: "var(--surface)",
            border: "none",
            borderRadius: 8,
            width: 34,
            height: 34,
            cursor: "pointer",
            color: "var(--text-muted)",
            fontSize: 15,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            boxShadow: "var(--card-shadow)",
          }}
        >
          ⚙
        </button>
      )}
      <style>{`@keyframes pulse { 0%,100%{opacity:0.4} 50%{opacity:1} }`}</style>
    </div>
  );
}

const labelStyle: React.CSSProperties = {
  display: "block",
  fontSize: 10.5,
  fontWeight: 600,
  color: "var(--text-muted)",
  marginBottom: 5,
  textTransform: "uppercase",
  letterSpacing: 1,
};

const inputStyle: React.CSSProperties = {
  background: "var(--background)",
  border: "none",
  borderRadius: 9,
  padding: "0.6rem 0.8rem",
  color: "var(--text)",
  fontSize: 13.5,
  width: "100%",
  outline: "none",
  letterSpacing: -0.1,
};

const cancelStyle: React.CSSProperties = {
  background: "rgba(0,0,0,0.06)",
  border: "none",
  borderRadius: 9,
  padding: "0.5rem 1rem",
  color: "var(--text-muted)",
  cursor: "pointer",
  fontSize: 13.5,
  fontWeight: 500,
};

const primaryStyle: React.CSSProperties = {
  background: "var(--accent)",
  border: "none",
  borderRadius: 9,
  padding: "0.5rem 1.1rem",
  color: "#fff",
  cursor: "pointer",
  fontSize: 13.5,
  fontWeight: 600,
  letterSpacing: -0.2,
};
