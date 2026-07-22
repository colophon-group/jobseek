"use client";

import { useRef, useState } from "react";
import { Loader2, Plus, Trash2 } from "lucide-react";
import { Trans, useLingui } from "@lingui/react/macro";
import * as AlertDialog from "@radix-ui/react-alert-dialog";
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { ErrorAlert } from "@/components/ui/ErrorAlert";
import {
  INTERVIEW_TYPES,
  type InterviewEntry,
  type InterviewType,
} from "@/lib/actions/my-jobs-types";

function useTypeLabels(): Record<InterviewType, string> {
  const { t } = useLingui();
  return {
    interview: t({ id: "myJobs.interviewType.interview", comment: "Interview type label: general interview", message: "Interview" }),
    phone_screen: t({ id: "myJobs.interviewType.phoneScreen", comment: "Interview type label: phone screen", message: "Phone Screen" }),
    video_call: t({ id: "myJobs.interviewType.videoCall", comment: "Interview type label: video call", message: "Video Call" }),
    technical: t({ id: "myJobs.interviewType.technical", comment: "Interview type label: technical interview", message: "Technical" }),
    coding: t({ id: "myJobs.interviewType.coding", comment: "Interview type label: coding challenge", message: "Coding" }),
    system_design: t({ id: "myJobs.interviewType.systemDesign", comment: "Interview type label: system design", message: "System Design" }),
    behavioral: t({ id: "myJobs.interviewType.behavioral", comment: "Interview type label: behavioral interview", message: "Behavioral" }),
    onsite: t({ id: "myJobs.interviewType.onsite", comment: "Interview type label: onsite interview", message: "Onsite" }),
    panel: t({ id: "myJobs.interviewType.panel", comment: "Interview type label: panel interview", message: "Panel" }),
    hiring_manager: t({ id: "myJobs.interviewType.hiringManager", comment: "Interview type label: hiring manager round", message: "Hiring Manager" }),
    other: t({ id: "myJobs.interviewType.other", comment: "Interview type label: other/unspecified", message: "Other" }),
  };
}

interface InterviewListProps {
  interviews: InterviewEntry[];
  onAdd: (type: InterviewType) => Promise<boolean>;
  onUpdate: (
    id: string,
    updates: { type?: InterviewType; scheduledAt?: string | null },
  ) => Promise<boolean>;
  onDelete: (id: string) => Promise<boolean>;
}

export function InterviewList({
  interviews,
  onAdd,
  onUpdate,
  onDelete,
}: InterviewListProps) {
  const typeLabels = useTypeLabels();
  const { t } = useLingui();
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState("");
  const [feedback, setFeedback] = useState("");

  function reportError(message: string) {
    setFeedback("");
    setError(message);
  }

  function reportSuccess(message: string) {
    setError("");
    setFeedback(message);
  }

  async function handleAdd() {
    if (adding) return;
    setAdding(true);
    setError("");
    setFeedback("");

    let ok = false;
    try {
      ok = await onAdd("interview");
    } catch {
      ok = false;
    } finally {
      setAdding(false);
    }

    if (ok) {
      reportSuccess(t({ id: "myJobs.interview.added", comment: "Screen-reader confirmation after adding an interview round", message: "Interview added." }));
    } else {
      reportError(t({ id: "myJobs.interview.addError", comment: "Error shown when an interview round cannot be added", message: "Couldn't add the interview. Try again." }));
    }
  }

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
              typeLabels={typeLabels}
              onUpdate={onUpdate}
              onDelete={onDelete}
              onError={reportError}
              onSuccess={reportSuccess}
            />
          ))}
        </ul>
      )}

      <ErrorAlert
        message={error}
        focusOnRender
        className="mb-1 px-2 py-1.5 text-xs"
      />
      <p role="status" aria-live="polite" className="sr-only">
        {feedback}
      </p>
      <button
        type="button"
        onClick={handleAdd}
        disabled={adding}
        aria-busy={adding}
        className="inline-flex cursor-pointer items-center gap-1 rounded px-1.5 py-0.5 text-[11px] text-primary transition-colors hover:bg-primary/10 disabled:cursor-wait disabled:opacity-60"
      >
        {adding ? <Loader2 size={12} className="animate-spin" aria-hidden="true" /> : <Plus size={12} aria-hidden="true" />}
        {adding ? (
          <Trans id="myJobs.detail.addingInterview" comment="Button label while an interview round is being added">
            Adding…
          </Trans>
        ) : (
          <Trans
            id="myJobs.detail.addInterview"
            comment="Button to add an interview round"
          >
            Add interview
          </Trans>
        )}
      </button>
    </div>
  );
}

function InterviewRow({
  interview,
  typeLabels,
  onUpdate,
  onDelete,
  onError,
  onSuccess,
}: {
  interview: InterviewEntry;
  typeLabels: Record<InterviewType, string>;
  onUpdate: InterviewListProps["onUpdate"];
  onDelete: InterviewListProps["onDelete"];
  onError: (message: string) => void;
  onSuccess: (message: string) => void;
}) {
  // `i18n.locale` is the viewer's active language. Without it,
  // `toLocaleDateString(undefined, ...)` renders the Node default
  // (en-US) on the server and the browser locale on the client,
  // producing a hydration mismatch for non-en viewers. See #3221.
  const { i18n, t } = useLingui();
  const triggerRef = useRef<HTMLButtonElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const [updating, setUpdating] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState("");

  const updateError = t({ id: "myJobs.interview.updateError", comment: "Error shown when an interview round cannot be updated", message: "Couldn't update the interview. Try again." });
  const deleteErrorMessage = t({ id: "myJobs.interview.deleteError", comment: "Error shown when an interview round cannot be deleted", message: "Couldn't delete the interview. Try again." });

  async function handleTypeChange(type: InterviewType) {
    if (type === interview.type || updating) return;
    setUpdating(true);
    let ok = false;
    try {
      ok = await onUpdate(interview.id, { type });
    } catch {
      ok = false;
    } finally {
      setUpdating(false);
    }

    if (ok) {
      onSuccess(t({ id: "myJobs.interview.updated", comment: "Screen-reader confirmation after updating an interview round", message: "Interview updated." }));
    } else {
      onError(updateError);
    }
  }

  async function handleDelete() {
    if (deleting) return;
    setDeleting(true);
    setDeleteError("");
    let ok = false;
    try {
      ok = await onDelete(interview.id);
    } catch {
      ok = false;
    } finally {
      setDeleting(false);
    }

    if (ok) {
      setDeleteOpen(false);
      onSuccess(t({ id: "myJobs.interview.deleted", comment: "Screen-reader confirmation after deleting an interview round", message: "Interview deleted." }));
    } else {
      setDeleteError(deleteErrorMessage);
    }
  }

  function handleDeleteOpenChange(open: boolean) {
    if (!open && deleting) return;
    setDeleteOpen(open);
    if (!open) setDeleteError("");
  }

  return (
    <li>
      <DropdownMenu.Root>
        <DropdownMenu.Trigger asChild>
          <button
            ref={triggerRef}
            type="button"
            disabled={updating}
            className="flex w-full cursor-pointer items-center gap-2 rounded px-2 py-1 text-left transition-colors hover:bg-border-soft/50 disabled:cursor-wait disabled:opacity-60"
          >
            <span className="text-[11px] font-medium text-muted">
              #{interview.round}
            </span>
            <span className="text-xs">{typeLabels[interview.type]}</span>
            {interview.scheduledAt && (
              <span className="text-[11px] text-muted">
                {new Date(interview.scheduledAt).toLocaleDateString(i18n.locale, {
                  month: "short",
                  day: "numeric",
                  hour: "2-digit",
                  minute: "2-digit",
                })}
              </span>
            )}
          </button>
        </DropdownMenu.Trigger>
        <DropdownMenu.Portal>
          <DropdownMenu.Content
            align="start"
            sideOffset={4}
            collisionPadding={12}
            className="z-50 max-h-[var(--radix-dropdown-menu-content-available-height)] w-48 overflow-y-auto rounded-md border border-border-soft bg-surface p-1 shadow-lg"
          >
            <DropdownMenu.RadioGroup
              value={interview.type}
              onValueChange={(value) => void handleTypeChange(value as InterviewType)}
            >
              {INTERVIEW_TYPES.map((type) => (
                <DropdownMenu.RadioItem
                  key={type}
                  value={type}
                  disabled={updating}
                  className={`flex cursor-pointer items-center rounded-sm px-2 py-1.5 text-xs outline-none data-[highlighted]:bg-border-soft ${
                    type === interview.type ? "font-medium text-primary" : "text-foreground"
                  }`}
                >
                  {typeLabels[type]}
                </DropdownMenu.RadioItem>
              ))}
            </DropdownMenu.RadioGroup>
            <DropdownMenu.Separator className="my-1 h-px bg-divider" />
            <DropdownMenu.Item
              className="flex cursor-pointer items-center gap-2 rounded-sm px-2 py-1.5 text-xs text-rose-500 outline-none data-[highlighted]:bg-rose-50 dark:data-[highlighted]:bg-rose-900/20"
              onSelect={() => {
                globalThis.setTimeout(() => setDeleteOpen(true), 0);
              }}
            >
              <Trash2 size={11} aria-hidden="true" />
              <Trans id="myJobs.interview.delete" comment="Delete interview button">Delete</Trans>
            </DropdownMenu.Item>
          </DropdownMenu.Content>
        </DropdownMenu.Portal>
      </DropdownMenu.Root>

      <AlertDialog.Root open={deleteOpen} onOpenChange={handleDeleteOpenChange}>
        <AlertDialog.Portal>
          <AlertDialog.Overlay className="fixed inset-0 z-50 bg-black/40 data-[state=open]:animate-in data-[state=open]:fade-in-0" />
          <AlertDialog.Content
            className="fixed left-1/2 top-1/2 z-50 w-[calc(100%-2rem)] max-w-sm -translate-x-1/2 -translate-y-1/2 rounded-xl border border-border-soft bg-surface p-6 shadow-xl data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95"
            onOpenAutoFocus={(event) => {
              event.preventDefault();
              cancelRef.current?.focus();
            }}
            onCloseAutoFocus={(event) => {
              event.preventDefault();
              triggerRef.current?.focus();
            }}
            onEscapeKeyDown={(event) => {
              if (deleting) event.preventDefault();
            }}
          >
            <AlertDialog.Title className="text-base font-semibold">
              <Trans id="myJobs.interview.deleteTitle" comment="Delete interview confirmation title">Delete interview?</Trans>
            </AlertDialog.Title>
            <AlertDialog.Description className="mt-2 text-sm text-muted">
              <Trans id="myJobs.interview.deleteDescription" comment="Delete interview confirmation description">
                This permanently deletes this interview round. If it is the last round, the application moves back to Applied.
              </Trans>
            </AlertDialog.Description>
            <div className="mt-4">
              <ErrorAlert message={deleteError} focusOnRender />
            </div>
            <div className="mt-5 flex justify-end gap-2">
              <AlertDialog.Cancel asChild>
                <button
                  ref={cancelRef}
                  type="button"
                  disabled={deleting}
                  className="cursor-pointer rounded-md border border-border-soft px-4 py-2 text-sm font-medium transition-colors hover:bg-border-soft disabled:cursor-wait disabled:opacity-60"
                >
                  <Trans id="myJobs.interview.deleteCancel" comment="Cancel deleting an interview round">Cancel</Trans>
                </button>
              </AlertDialog.Cancel>
              <button
                type="button"
                onClick={handleDelete}
                disabled={deleting}
                className="inline-flex cursor-pointer items-center gap-2 rounded-md border border-warning-border bg-warning-bg px-4 py-2 text-sm font-medium text-warning transition-opacity hover:opacity-80 disabled:cursor-wait disabled:opacity-60"
              >
                {deleting && <Loader2 size={14} className="animate-spin" aria-hidden="true" />}
                {deleting ? (
                  <Trans id="myJobs.interview.deleting" comment="Delete interview button while deletion is pending">Deleting…</Trans>
                ) : (
                  <Trans id="myJobs.interview.deleteConfirm" comment="Confirm deleting an interview round">Delete</Trans>
                )}
              </button>
            </div>
          </AlertDialog.Content>
        </AlertDialog.Portal>
      </AlertDialog.Root>
    </li>
  );
}
