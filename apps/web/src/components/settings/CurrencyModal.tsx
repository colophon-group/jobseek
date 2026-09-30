"use client";

import { useMemo, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { Check, Search } from "lucide-react";
import { Trans, useLingui } from "@lingui/react/macro";
import { SettingsDialog } from "./SettingsDialog";

export function CurrencyModal({
  value,
  currencies,
  onSelect,
  disabled,
}: {
  value: string;
  currencies: string[];
  onSelect: (code: string) => void;
  disabled?: boolean;
}) {
  const { t, i18n } = useLingui();
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const names = useMemo(
    () => new Intl.DisplayNames([i18n.locale], { type: "currency" }),
    [i18n.locale],
  );
  const english = useMemo(
    () => new Intl.DisplayNames(["en"], { type: "currency" }),
    [],
  );
  const options = useMemo(
    () =>
      [...new Set(currencies)]
        .sort()
        .filter((code) =>
          [code, names.of(code), english.of(code)]
            .join(" ")
            .toLocaleLowerCase(i18n.locale)
            .includes(search.trim().toLocaleLowerCase(i18n.locale)),
        ),
    [currencies, names, english, search, i18n.locale],
  );
  const title = t({
    id: "settings.general.salary.currencyLabel",
    comment: "Label for currency selector",
    message: "Currency",
  });
  return (
    <Dialog.Root
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (next) setSearch("");
      }}
    >
      <Dialog.Trigger
        disabled={disabled}
        aria-label={`${title}: ${value}`}
        className="inline-flex min-h-11 w-full items-center justify-between gap-4 rounded-lg border border-divider bg-surface px-3 py-2 text-xs outline-none hover:bg-border-soft focus-visible:ring-2 focus-visible:ring-primary disabled:opacity-50"
      >
        <span>{value}</span>
        <Search size={14} aria-hidden="true" className="text-muted" />
      </Dialog.Trigger>
      <SettingsDialog
        title={title}
        description={t({
          id: "settings.currency.description",
          comment: "Description of the salary currency picker",
          message: "Choose how salaries are displayed.",
        })}
      >
        <label className="mb-4 flex items-center gap-2 rounded-lg border border-divider px-3 focus-within:ring-2 focus-within:ring-primary">
          <Search
            size={16}
            aria-hidden="true"
            className="shrink-0 text-muted"
          />
          <input
            autoFocus
            type="search"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            aria-label={t({
              id: "settings.currency.search",
              comment:
                "Search supported currencies by localized name or ISO code",
              message: "Search by currency or code",
            })}
            placeholder={t({
              id: "settings.currency.search",
              comment:
                "Search supported currencies by localized name or ISO code",
              message: "Search by currency or code",
            })}
            className="min-h-11 w-full min-w-0 bg-transparent text-base outline-none sm:text-sm"
          />
        </label>
        <div className="max-h-[45dvh] overflow-y-auto overscroll-contain">
          {options.map((code) => (
            <button
              key={code}
              type="button"
              onClick={() => {
                onSelect(code);
                setOpen(false);
              }}
              aria-pressed={code === value}
              className={`flex min-h-12 w-full items-center gap-3 border-b border-border-soft px-2 py-3 text-left text-xs outline-none hover:bg-border-soft focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary ${code === value ? "rounded-md bg-border-soft" : ""}`}
            >
              <span className="min-w-0 flex-1 break-words">
                {names.of(code)}
              </span>
              <span className="shrink-0 text-muted">{code}</span>
              <span className="w-3.5 shrink-0">
                {code === value && <Check size={14} aria-hidden="true" />}
              </span>
            </button>
          ))}
          {!options.length && (
            <p role="status" className="py-6 text-sm text-muted">
              <Trans
                id="settings.currency.empty"
                comment="Currency picker has no search matches"
              >
                No matching currencies
              </Trans>
            </p>
          )}
        </div>
      </SettingsDialog>
    </Dialog.Root>
  );
}
