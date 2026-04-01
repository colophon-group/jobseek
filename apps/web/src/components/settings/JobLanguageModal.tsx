"use client";

import { useState, useMemo } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { X, Search } from "lucide-react";
import { Trans, useLingui } from "@lingui/react/macro";
import { allLanguages } from "@/lib/job-languages";
import { CountryFlag } from "@/components/country-flag";
import { ScrollFade } from "@/components/ui/scroll-fade";

interface JobLanguageModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  selected: Set<string>;
  onToggle: (code: string) => void;
  /** Only languages with jobs are shown. */
  availableCodes: Set<string>;
}

export function JobLanguageModal({
  open,
  onOpenChange,
  selected,
  onToggle,
  availableCodes,
}: JobLanguageModalProps) {
  const { t } = useLingui();
  const [search, setSearch] = useState("");

  const available = useMemo(
    () => allLanguages.filter((l) => availableCodes.has(l.code)),
    [availableCodes],
  );

  const filtered = useMemo(() => {
    if (!search.trim()) return available;
    const q = search.trim().toLowerCase();
    return available.filter(
      (lang) =>
        lang.label.toLowerCase().includes(q) ||
        lang.code.toLowerCase().includes(q),
    );
  }, [available, search]);

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/40 data-[state=open]:animate-in data-[state=open]:fade-in-0" />
        <Dialog.Content
          className="fixed left-1/2 top-1/2 z-50 flex max-h-[85vh] w-[calc(100%-2rem)] max-w-lg -translate-x-1/2 -translate-y-1/2 flex-col rounded-xl border border-border-soft bg-surface shadow-xl data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95"
          aria-describedby={undefined}
        >
          {/* Header */}
          <div className="flex items-center justify-between border-b border-divider px-5 py-4">
            <Dialog.Title className="text-base font-semibold">
              <Trans
                id="settings.jobLanguages.modal.title"
                comment="Title for the all-languages modal in settings"
              >
                All languages
              </Trans>
            </Dialog.Title>
            <Dialog.Close asChild>
              <button className="rounded-md p-1.5 text-muted transition-colors hover:bg-border-soft hover:text-foreground cursor-pointer">
                <X size={16} />
              </button>
            </Dialog.Close>
          </div>

          {/* Search */}
          <div className="border-b border-divider px-5 py-3">
            <div className="flex items-center gap-2 rounded-md border border-border-soft px-3 py-2">
              <Search size={14} className="shrink-0 text-muted" />
              <input
                type="text"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder={t({
                  id: "settings.jobLanguages.modal.searchPlaceholder",
                  comment: "Placeholder for search input in all-languages modal",
                  message: "Search languages...",
                })}
                className="w-full min-w-0 bg-transparent text-sm outline-none placeholder:text-muted"
              />
            </div>
          </div>

          {/* Body */}
          <ScrollFade wrapperClassName="flex-1 min-h-0" className="px-5 py-4">
            {filtered.length === 0 ? (
              <p className="py-8 text-center text-sm text-muted">
                <Trans
                  id="settings.jobLanguages.modal.noResults"
                  comment="No languages match search in all-languages modal"
                >
                  No languages match your search.
                </Trans>
              </p>
            ) : (
              <div className="flex flex-wrap gap-2">
                {filtered.map((lang) => {
                  const active = selected.has(lang.code);
                  return (
                    <button
                      key={lang.code}
                      onClick={() => onToggle(lang.code)}
                      className={`inline-flex cursor-pointer items-center gap-1.5 rounded-full px-3 py-1 text-sm transition-colors ${
                        active
                          ? "bg-primary/10 text-primary font-medium"
                          : "border border-border-soft text-muted hover:border-primary/30 hover:text-foreground"
                      }`}
                    >
                      {lang.flag && <CountryFlag iso={lang.flag} size={14} className="shrink-0 rounded-[2px]" />}
                      {lang.label}
                    </button>
                  );
                })}
              </div>
            )}
          </ScrollFade>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
