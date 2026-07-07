"use client";

import { useEffect, useRef, useState } from "react";
import { Trans } from "@lingui/react/macro";
import * as Dialog from "@radix-ui/react-dialog";

import { JobDetailPanel } from "@/components/search/job-detail-dialog";

interface MobileJobDetailDialogProps {
  postingId: string | null;
  onClose: () => void;
}

export function MobileJobDetailDialog({
  postingId,
  onClose,
}: MobileJobDetailDialogProps) {
  const isMobile = useMobileDetailBreakpoint();
  const restoreFocusElementRef = useRef<HTMLElement | null>(null);

  if (!postingId || !isMobile) return null;

  return (
    <Dialog.Root
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/40 data-[state=open]:animate-in data-[state=open]:fade-in-0 lg:hidden" />
        <Dialog.Content
          aria-describedby={undefined}
          aria-modal="true"
          className="fixed inset-y-0 right-0 z-50 w-full max-w-lg bg-surface shadow-xl data-[state=open]:animate-in data-[state=open]:slide-in-from-right-4 lg:hidden"
          onOpenAutoFocus={() => {
            if (typeof document === "undefined") return;

            const activeElement = document.activeElement;
            restoreFocusElementRef.current = activeElement instanceof HTMLElement ? activeElement : null;
          }}
          onCloseAutoFocus={(event) => {
            const restoreTarget = restoreFocusElementRef.current;
            restoreFocusElementRef.current = null;

            if (!restoreTarget?.isConnected) return;

            event.preventDefault();
            restoreTarget.focus();
          }}
        >
          <Dialog.Title className="sr-only">
            <Trans id="search.detail.title" comment="Job detail panel title">
              Job Details
            </Trans>
          </Dialog.Title>
          <JobDetailPanel postingId={postingId} onClose={onClose} />
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function useMobileDetailBreakpoint() {
  const [isMobile, setIsMobile] = useState(false);

  useEffect(() => {
    if (!window.matchMedia) return;

    const query = window.matchMedia("(max-width: 1023px)");
    const update = () => setIsMobile(query.matches);
    update();
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);

  return isMobile;
}
