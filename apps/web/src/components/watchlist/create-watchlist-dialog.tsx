"use client";

import { useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { X, Loader2 } from "lucide-react";
import { Trans, useLingui } from "@lingui/react/macro";
import { useRouter } from "next/navigation";
import { useLocalePath } from "@/lib/useLocalePath";
import { useAuth } from "@/lib/useAuth";
import { createWatchlist } from "@/lib/actions/watchlists";
import { CompanySelector } from "./company-selector";
import { ScrollFade } from "@/components/ui/scroll-fade";

type SelectedCompany = { id: string; name: string; slug: string; icon: string | null };

export function CreateWatchlistDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useLingui();
  const router = useRouter();
  const lp = useLocalePath();
  const { user } = useAuth();
  const [title, setTitle] = useState("");
  const [companies, setCompanies] = useState<SelectedCompany[]>([]);
  const [isPublic, setIsPublic] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!title.trim()) return;

    setSaving(true);
    setError(null);

    try {
      const result = await createWatchlist({
        title: title.trim(),
        companyIds: companies.map((c) => c.id),
        isPublic,
      });

      if ("error" in result) {
        setError(result.error === "limit_reached" ? "limit_reached" : "unknown");
        return;
      }

      onOpenChange(false);
      setTitle("");
      setCompanies([]);

      if (user?.username) {
        router.push(lp(`/${user.username}/${result.slug}`));
      } else {
        router.refresh();
      }
    } catch {
      setError("unknown");
    } finally {
      setSaving(false);
    }
  }

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
              <Trans id="watchlists.create.title" comment="Title of the create watchlist dialog">
                Create watchlist
              </Trans>
            </Dialog.Title>
            <Dialog.Close asChild>
              <button className="rounded-md p-1.5 text-muted transition-colors hover:bg-border-soft hover:text-foreground cursor-pointer">
                <X size={16} />
              </button>
            </Dialog.Close>
          </div>

          {/* Body */}
          <ScrollFade wrapperClassName="flex-1 min-h-0" className="">
            <form onSubmit={handleSubmit} className="px-5 py-4">
            <div className="space-y-4">
              {/* Title input */}
              <div>
                <label className="mb-1.5 block text-sm font-medium">
                  <Trans id="watchlists.create.nameLabel" comment="Label for watchlist name input">
                    Name
                  </Trans>
                </label>
                <input
                  type="text"
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  placeholder={t({
                    id: "watchlists.create.namePlaceholder",
                    comment: "Placeholder for watchlist name input",
                    message: "My watchlist",
                  })}
                  className="w-full rounded-md border border-border-soft px-3 py-2 text-sm outline-none transition-colors focus:border-primary placeholder:text-muted bg-transparent"
                  maxLength={100}
                  autoFocus
                />
              </div>

              {/* Company selector */}
              <div>
                <label className="mb-1.5 block text-sm font-medium">
                  <Trans id="watchlists.create.companiesLabel" comment="Label for company selector in watchlist creation">
                    Companies
                  </Trans>
                </label>
                <CompanySelector selected={companies} onChange={setCompanies} />
              </div>

              {/* Public toggle */}
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={isPublic}
                  onChange={(e) => setIsPublic(e.target.checked)}
                  className="size-4 rounded border-border-soft accent-primary"
                />
                <span className="text-sm">
                  <Trans id="watchlists.create.publicLabel" comment="Checkbox label to make watchlist public">
                    Public (discoverable by others)
                  </Trans>
                </span>
              </label>

              {error === "limit_reached" && (
                <p className="text-sm text-red-500">
                  <Trans id="watchlists.create.limitReached" comment="Error when free plan watchlist limit is reached">
                    You've reached your watchlist limit. Upgrade to create more.
                  </Trans>
                </p>
              )}
              {error === "unknown" && (
                <p className="text-sm text-red-500">
                  <Trans id="watchlists.create.error" comment="Generic error creating watchlist">
                    Something went wrong. Please try again.
                  </Trans>
                </p>
              )}
            </div>

            {/* Footer */}
            <div className="mt-6 flex justify-end gap-2">
              <Dialog.Close asChild>
                <button
                  type="button"
                  className="rounded-md px-4 py-2 text-sm text-muted transition-colors hover:text-foreground cursor-pointer"
                >
                  <Trans id="watchlists.create.cancel" comment="Cancel button in create watchlist dialog">
                    Cancel
                  </Trans>
                </button>
              </Dialog.Close>
              <button
                type="submit"
                disabled={!title.trim() || saving}
                className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-contrast transition-opacity hover:opacity-90 disabled:opacity-50 cursor-pointer"
              >
                {saving && <Loader2 size={14} className="animate-spin" />}
                <Trans id="watchlists.create.submit" comment="Submit button in create watchlist dialog">
                  Create
                </Trans>
              </button>
            </div>
          </form>
          </ScrollFade>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
