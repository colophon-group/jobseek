"use client";

import { useState } from "react";
import { Plus, Pencil, Trash2, Check, X } from "lucide-react";
import { Trans } from "@lingui/react/macro";
import {
  INTERVIEW_TYPES,
  type InterviewEntry,
  type InterviewType,
} from "@/lib/actions/my-jobs";

const typeLabels: Record<InterviewType, string> = {
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
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <h3 className="text-[10px] font-medium uppercase tracking-wider text-muted">
          <Trans
            id="myJobs.detail.interviews"
            comment="Interviews section heading"
          >
            Interviews
          </Trans>
        </h3>
        <button
          onClick={() => onAdd("phone_screen")}
          className="inline-flex cursor-pointer items-center gap-1 rounded px-1.5 py-0.5 text-[11px] text-primary transition-colors hover:bg-primary/10"
        >
          <Plus size={12} />
          <Trans
            id="myJobs.detail.addInterview"
            comment="Button to add an interview round"
          >
            Add
          </Trans>
        </button>
      </div>

      {interviews.length === 0 ? (
        <p className="text-xs text-muted">
          <Trans
            id="myJobs.detail.noInterviews"
            comment="Empty state when no interviews"
          >
            No interviews yet.
          </Trans>
        </p>
      ) : (
        <ul className="space-y-1">
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
  const [editing, setEditing] = useState(false);
  const [editType, setEditType] = useState<InterviewType>(interview.type);
  const [editDate, setEditDate] = useState(
    interview.scheduledAt
      ? new Date(interview.scheduledAt).toISOString().slice(0, 16)
      : "",
  );

  function handleSave() {
    onUpdate(interview.id, {
      type: editType,
      scheduledAt: editDate || null,
    });
    setEditing(false);
  }

  function handleCancel() {
    setEditType(interview.type);
    setEditDate(
      interview.scheduledAt
        ? new Date(interview.scheduledAt).toISOString().slice(0, 16)
        : "",
    );
    setEditing(false);
  }

  if (editing) {
    return (
      <li className="flex flex-wrap items-center gap-1.5 rounded bg-border-soft/50 px-2 py-1.5">
        <span className="text-[11px] font-medium text-muted">
          #{interview.round}
        </span>
        <select
          value={editType}
          onChange={(e) => setEditType(e.target.value as InterviewType)}
          className="rounded border border-border-soft bg-surface px-1.5 py-0.5 text-xs"
        >
          {INTERVIEW_TYPES.map((t) => (
            <option key={t} value={t}>
              {typeLabels[t]}
            </option>
          ))}
        </select>
        <input
          type="datetime-local"
          value={editDate}
          onChange={(e) => setEditDate(e.target.value)}
          className="rounded border border-border-soft bg-surface px-1.5 py-0.5 text-xs"
        />
        <button
          onClick={handleSave}
          className="cursor-pointer rounded p-0.5 text-green-600 hover:bg-green-100 dark:hover:bg-green-900/40"
        >
          <Check size={12} />
        </button>
        <button
          onClick={handleCancel}
          className="cursor-pointer rounded p-0.5 text-muted hover:bg-border-soft"
        >
          <X size={12} />
        </button>
      </li>
    );
  }

  return (
    <li className="group flex items-center gap-2 rounded px-2 py-1 transition-colors hover:bg-border-soft/50">
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
      <span className="ml-auto flex items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100">
        <button
          onClick={() => setEditing(true)}
          className="cursor-pointer rounded p-0.5 text-muted hover:bg-border-soft hover:text-foreground"
        >
          <Pencil size={11} />
        </button>
        <button
          onClick={() => onDelete(interview.id)}
          className="cursor-pointer rounded p-0.5 text-muted hover:bg-red-100 hover:text-red-600 dark:hover:bg-red-900/40"
        >
          <Trash2 size={11} />
        </button>
      </span>
    </li>
  );
}
