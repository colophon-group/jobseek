"use client";

import { useEffect, useState, useMemo } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { Check, Search } from "lucide-react";
import { Trans, useLingui } from "@lingui/react/macro";
import { allLanguages } from "@/lib/job-languages";
import { CountryFlag } from "@/components/country-flag";
import { useSearchableDialogFocus } from "@/components/search/use-searchable-dialog-focus";
import { SettingsDialog } from "./SettingsDialog";

interface JobLanguageModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  selected: Set<string>;
  onToggle?: (code: string) => void;
  onApply?: (codes: string[]) => void;
  availableCodes: Set<string>;
  locale?: string;
}

export function JobLanguageModal({
  open,
  onOpenChange,
  selected,
  onToggle,
  onApply,
  availableCodes,
  locale,
}: JobLanguageModalProps) {
  const { t, i18n } = useLingui();
  const [search, setSearch] = useState("");
  const [draft, setDraft] = useState(new Set(selected));
  const [all, setAll] = useState(!selected.size || selected.has("*"));
  const { searchInputRef, focusSearchInputOnOpen, restoreTriggerFocusOnClose } =
    useSearchableDialogFocus();
  const language = locale ?? i18n.locale;
  const names = useMemo(
    () => new Intl.DisplayNames([language], { type: "language" }),
    [language],
  );
  const english = useMemo(
    () => new Intl.DisplayNames(["en"], { type: "language" }),
    [],
  );
  // Languages remain selectable even when there are no current postings.
  const available = allLanguages;
  const query = search.trim().toLocaleLowerCase(language);
  const aliases = (code: string, label: string) =>
    [code, label, names.of(code), english.of(code)].map(
      (value) => value?.toLocaleLowerCase(language) ?? "",
    );
  const filtered = available
    .filter((lang) =>
      aliases(lang.code, lang.label).some((value) => value.includes(query)),
    )
    .sort(
      (a, b) =>
        Number(aliases(b.code, b.label).includes(query)) -
          Number(aliases(a.code, a.label).includes(query)) ||
        Number(availableCodes.has(b.code)) - Number(availableCodes.has(a.code)),
    );
  // Open is the edit-session boundary; subsequent parent renders must not discard a draft.
  useEffect(() => {
    if (open) {
      setSearch("");
      setDraft(new Set(selected));
      setAll(!selected.size || selected.has("*"));
    }
  }, [open]);
  const searchLabel = t({
    id: "settings.jobLanguages.modal.searchPlaceholder",
    comment: "Placeholder for search input in all-languages modal",
    message: "Search languages...",
  });
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <SettingsDialog
        onOpenAutoFocus={focusSearchInputOnOpen}
        onCloseAutoFocus={restoreTriggerFocusOnClose}
        title={t({
          id: "settings.jobLanguages.title",
          comment: "Job posting language preferences heading",
          message: "Job languages",
        })}
      >
        {onApply && (
          <fieldset className="mb-4 grid gap-1">
            <legend className="sr-only">
              <Trans
                id="settings.jobLanguages.title"
                comment="Job posting language preferences heading"
              >
                Job languages
              </Trans>
            </legend>
            <label className="flex min-h-11 items-center gap-3 text-xs">
              <input
                type="radio"
                name="job-language-mode"
                checked={all}
                onChange={() => setAll(true)}
              />
              <Trans
                id="settings.jobLanguages.all"
                comment="All job languages option"
              >
                All languages
              </Trans>
            </label>
            <label className="flex min-h-11 items-center gap-3 text-xs">
              <input
                type="radio"
                name="job-language-mode"
                checked={!all}
                onChange={() => setAll(false)}
              />
              <Trans
                id="settings.jobLanguages.specific"
                comment="Select specific job posting languages"
              >
                Choose languages
              </Trans>
            </label>
          </fieldset>
        )}
        {(!all || !onApply) && (
          <>
            <label className="mb-3 flex items-center gap-2 rounded-lg border border-divider px-3 focus-within:ring-2 focus-within:ring-primary">
              <Search
                size={14}
                aria-hidden="true"
                className="shrink-0 text-muted"
              />
              <input
                ref={searchInputRef}
                type="text"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                aria-label={searchLabel}
                placeholder={searchLabel}
                className="min-h-11 w-full min-w-0 bg-transparent text-base outline-none sm:text-sm"
              />
            </label>
            <div className="max-h-[36dvh] overflow-y-auto overscroll-contain">
              {!filtered.length && (
                <p className="py-8 text-sm text-muted">
                  <Trans
                    id="settings.jobLanguages.modal.noResults"
                    comment="No languages match search in all-languages modal"
                  >
                    No languages match your search.
                  </Trans>
                </p>
              )}
              {filtered.map((lang) => (
                <button
                  key={lang.code}
                  type="button"
                  aria-label={lang.label}
                  aria-pressed={draft.has(lang.code)}
                  onClick={() => {
                    const next = new Set(draft);
                    if (next.has(lang.code)) next.delete(lang.code);
                    else next.add(lang.code);
                    setDraft(next);
                    onToggle?.(lang.code);
                  }}
                  className="flex min-h-12 w-full items-center gap-3 rounded-md px-2 py-3 text-left text-xs hover:bg-border-soft focus-visible:ring-2 focus-visible:ring-primary"
                >
                  <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded border border-divider">
                    {draft.has(lang.code) && (
                      <Check size={12} aria-hidden="true" />
                    )}
                  </span>
                  {lang.flag && (
                    <CountryFlag
                      iso={lang.flag}
                      size={18}
                      className="shrink-0"
                    />
                  )}
                  <span className="min-w-0 flex-1">
                    <span>{lang.label}</span>
                    {names.of(lang.code) !== lang.label && (
                      <span className="mt-1 block text-[10px] text-muted">
                        {names.of(lang.code)}
                      </span>
                    )}
                  </span>
                  <span className="text-[10px] text-muted">{lang.code}</span>
                </button>
              ))}
            </div>
          </>
        )}
        {onApply && (
          <div className="mt-5 flex justify-end gap-2">
            <Dialog.Close className="min-h-11 rounded-full border border-divider px-4 text-xs">
              <Trans id="common.actions.cancel" comment="Cancel an edit dialog">
                Cancel
              </Trans>
            </Dialog.Close>
            <button
              className="min-h-11 rounded-full bg-primary px-4 text-xs text-primary-contrast"
              onClick={() => {
                const codes = [...draft].filter((code) => code !== "*");
                onApply(all || !codes.length ? ["*"] : codes);
                onOpenChange(false);
              }}
            >
              <Trans id="common.actions.save" comment="Save settings changes">
                Save
              </Trans>
            </button>
          </div>
        )}
        {/* Focus handling is provided by the searchable input and Radix's trigger restoration. */}
      </SettingsDialog>
    </Dialog.Root>
  );
}
