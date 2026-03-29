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

const inputCls = "w-full rounded-md border border-divider bg-background px-3 py-2 text-sm text-foreground outline-none focus:border-foreground transition-colors placeholder:text-muted";
const labelCls = "block text-[10.5px] font-semibold uppercase tracking-widest text-muted mb-1.5";

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
      const res = await fetch("/agentic/api/apify/run", {
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
        const res = await fetch(`/agentic/api/apify/status/${id}`);
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
        className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm"
        onClick={(e) => e.target === e.currentTarget && setState("idle")}
      >
        <form
          onSubmit={handleProfileSubmit}
          className="w-[400px] rounded-lg border border-divider bg-surface p-6 shadow-xl flex flex-col gap-4"
        >
          <div>
            <p className="text-[10.5px] font-semibold uppercase tracking-widest text-muted mb-1">Discovery</p>
            <h2 className="text-lg font-bold text-foreground tracking-tight">Your profile</h2>
            <p className="text-sm text-muted mt-1">Tells the AI what signals matter most to you.</p>
          </div>

          <div>
            <label className={labelCls}>Skills</label>
            <input required placeholder="TypeScript, Go, React, ML…" value={profile.skills}
              onChange={(e) => setProfile({ ...profile, skills: e.target.value })}
              className={inputCls} />
          </div>

          <div>
            <label className={labelCls}>Background</label>
            <input required placeholder="e.g. Full-stack engineer, 5 yrs experience" value={profile.background}
              onChange={(e) => setProfile({ ...profile, background: e.target.value })}
              className={inputCls} />
          </div>

          <div>
            <label className={labelCls}>Past wins (optional)</label>
            <textarea placeholder="One per line…" value={profile.pastWins} rows={3}
              onChange={(e) => setProfile({ ...profile, pastWins: e.target.value })}
              className={`${inputCls} resize-y`} />
          </div>

          <div className="flex gap-2 justify-end">
            <button type="button" onClick={() => setState("idle")}
              className="rounded-md border border-divider px-3 py-1.5 text-sm text-muted hover:text-foreground transition-colors cursor-pointer">
              Cancel
            </button>
            <button type="submit"
              className="rounded-md bg-primary px-3 py-1.5 text-sm font-semibold text-primary-contrast hover:opacity-90 transition-opacity cursor-pointer">
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
    <div className="flex items-center gap-2">
      <button
        onClick={handleClick}
        disabled={isRunning}
        className={`inline-flex items-center gap-2 rounded-md px-3 py-1.5 text-sm font-semibold transition-colors cursor-pointer disabled:opacity-70 disabled:cursor-not-allowed ${
          isSuccess ? "bg-success-bg text-success"
          : isError ? "bg-error-bg text-error"
          : "bg-primary text-primary-contrast hover:opacity-90"
        }`}
      >
        {isRunning && (
          <span className="size-1.5 rounded-full bg-current opacity-60 animate-pulse" />
        )}
        {isRunning ? "Discovering…"
          : isSuccess ? "Done"
          : isError ? "Failed · Retry"
          : "Run Discovery"}
        {runId && isRunning && (
          <span className="text-[10px] opacity-50">{runId.slice(0, 6)}</span>
        )}
      </button>

      {hasProfile && state === "idle" && (
        <button
          onClick={() => setState("profile")}
          title="Edit profile"
          className="inline-flex size-8 items-center justify-center rounded-md border border-divider bg-surface text-muted hover:text-foreground transition-colors cursor-pointer"
        >
          ⚙
        </button>
      )}
    </div>
  );
}
