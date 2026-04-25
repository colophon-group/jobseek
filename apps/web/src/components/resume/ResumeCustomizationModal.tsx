"use client";

import { useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { X, Loader2 } from "lucide-react";
import { ResumeDiffPreview } from "./ResumeDiffPreview";

interface ResumeCustomizationModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  original: string;
  customized: string;
  insertedKeywords: string[];
  loading?: boolean;
  onAccept: () => void | Promise<void>;
  onCancel: () => void;
}

export function ResumeCustomizationModal({
  open,
  onOpenChange,
  original,
  customized,
  insertedKeywords,
  loading = false,
  onAccept,
  onCancel,
}: ResumeCustomizationModalProps) {
  const [accepting, setAccepting] = useState(false);

  const handleAccept = async () => {
    setAccepting(true);
    try {
      await onAccept();
    } finally {
      setAccepting(false);
    }
  };

  const handleCancel = () => {
    onCancel();
    onOpenChange(false);
  };

  const isLoading = loading || accepting;

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/40 data-[state=open]:animate-in data-[state=open]:fade-in-0" />
        <Dialog.Content
          className="fixed left-1/2 top-1/2 z-50 flex max-h-[90vh] w-[calc(100%-2rem)] max-w-4xl -translate-x-1/2 -translate-y-1/2 flex-col rounded-xl border border-border-soft bg-surface shadow-xl data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95"
          aria-describedby={undefined}
        >
          {/* Header */}
          <div className="flex items-center justify-between border-b border-divider px-6 py-4">
            <Dialog.Title className="text-base font-semibold">
              Customize Resume Preview
            </Dialog.Title>
            <Dialog.Close asChild>
              <button
                className="rounded-md p-1.5 text-muted transition-colors hover:bg-border-soft hover:text-foreground cursor-pointer"
                disabled={isLoading}
              >
                <X size={16} />
              </button>
            </Dialog.Close>
          </div>

          {/* Body */}
          <div className="flex-1 min-h-0 overflow-y-auto px-6 py-4">
            {customized && original ? (
              <ResumeDiffPreview
                original={original}
                customized={customized}
                insertedKeywords={insertedKeywords}
              />
            ) : (
              <div className="flex items-center justify-center py-12">
                <div className="text-center text-muted space-y-2">
                  <Loader2 className="mx-auto h-8 w-8 animate-spin" />
                  <p className="text-sm">Customizing resume...</p>
                </div>
              </div>
            )}
          </div>

          {/* Footer */}
          <div className="flex items-center justify-between gap-3 border-t border-divider px-6 py-4">
            <button
              onClick={handleCancel}
              disabled={isLoading}
              className="px-4 py-2 rounded-md border border-border text-sm font-medium transition-colors hover:bg-border-soft disabled:opacity-50 disabled:cursor-not-allowed"
            >
              Cancel
            </button>
            <button
              onClick={handleAccept}
              disabled={isLoading || !customized}
              className="px-4 py-2 rounded-md bg-primary text-primary-contrast text-sm font-medium transition-colors hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
            >
              {isLoading && <Loader2 size={14} className="animate-spin" />}
              {isLoading ? "Saving..." : "Accept & Save"}
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
