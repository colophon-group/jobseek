"use client";

import { useState, useRef, useEffect } from "react";
import { Plus, Trash2 } from "lucide-react";
import { Trans } from "@lingui/react/macro";
import {
  INTERVIEW_TYPES,
  type InterviewEntry,
  type InterviewType,
} from "@/lib/actions/my-jobs-types";

const typeLabels: Record<InterviewType, string> = {
  interview: "Interview",
  phone_screen: "Phone Screen",
  video_call: "Video Call",
  technical: "Technical",
  coding: "Coding",
  system_design: "System Design",
  behavioral: "Behavioral",
  onsite: "Onsite",
  panel: "Panel",
  hiring_manager: "Hiring Manager",
  other: "Other",
};

interface InterviewListProps {
  interviews: InterviewEntry[];
  onAdd: (type: InterviewType) => void;
  onUpdate: (
    id: string,
    updates: { type?: InterviewType; scheduledAt?: string | null },
  ) => void;
  onDelete: (id: string) => void;
}

export function InterviewList({
  interviews,
  onAdd,
  onUpdate,
  onDelete,
}: InterviewListProps) {
  return (
    <div className="space-y-1">
      <h3 className="text-[10px] font-medium uppercase tracking-wider text-muted">
        <Trans
          id="myJobs.detail.interviews"
          comment="Interviews section heading"
        >
          Interviews
        </Trans>
      </h3>

      {interviews.length > 0 && (
        <ul className="space-y-0.5">
          {interviews.map((interview) => (
            <InterviewRow
              key={interview.id}
              interview={interview}
              onUpdate={onUpdate}
              onDelete={onDelete}
            />
          ))}
        </ul>
      )}

      <button
        onClick={() => onAdd("interview")}
        className="inline-flex cursor-pointer items-center gap-1 rounded px-1.5 py-0.5 text-[11px] text-primary transition-colors hover:bg-primary/10"
      >
        <Plus size={12} />
        <Trans
          id="myJobs.detail.addInterview"
          comment="Button to add an interview round"
        >
          Add interview
        </Trans>
      </button>
    </div>
  );
}

function InterviewRow({
  interview,
  onUpdate,
  onDelete,
}: {
  interview: InterviewEntry;
  onUpdate: InterviewListProps["onUpdate"];
  onDelete: InterviewListProps["onDelete"];
}) {
  const [open, setOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  // Close on outside click
  useEffect(() => {
    if (!open) return;
    function handleClick(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [open]);

  return (
    <li className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full cursor-pointer items-center gap-2 rounded px-2 py-1 text-left transition-colors hover:bg-border-soft/50"
      >
        <span className="text-[11px] font-medium text-muted">
          #{interview.round}
        </span>
        <span className="text-xs">{typeLabels[interview.type]}</span>
        {interview.scheduledAt && (
          <span className="text-[11px] text-muted">
            {new Date(interview.scheduledAt).toLocaleDateString(undefined, {
              month: "short",
              day: "numeric",
              hour: "2-digit",
              minute: "2-digit",
            })}
          </span>
        )}
      </button>

      {open && (
        <div
          ref={menuRef}
          className="absolute left-0 z-20 mt-0.5 w-48 rounded-md border border-border-soft bg-surface py-1 shadow-lg"
        >
          {INTERVIEW_TYPES.map((t) => (
            <button
              key={t}
              onClick={() => {
                onUpdate(interview.id, { type: t });
                setOpen(false);
              }}
              className={`flex w-full cursor-pointer items-center px-3 py-1.5 text-xs transition-colors hover:bg-border-soft ${
                t === interview.type ? "font-medium text-primary" : "text-foreground"
              }`}
            >
              {typeLabels[t]}
            </button>
          ))}
          <hr className="my-1 border-divider" />
          <button
            onClick={() => {
              onDelete(interview.id);
              setOpen(false);
            }}
            className="flex w-full cursor-pointer items-center gap-2 px-3 py-1.5 text-xs text-rose-500 transition-colors hover:bg-rose-50 dark:hover:bg-rose-900/20"
          >
            <Trash2 size={11} />
            Delete
          </button>
        </div>
      )}
    </li>
  );
}
