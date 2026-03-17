"use client";

import { useState, useEffect, useMemo, useRef } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { X, Loader2, Code2, Search } from "lucide-react";
import { Trans } from "@lingui/react/macro";
import { getAllTechnologiesGrouped } from "@/lib/actions/taxonomy";
import type { TechnologyGroup } from "@/lib/actions/taxonomy";

interface TechnologyModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  selected: { id: number; slug: string; name: string }[];
  onToggle: (tech: { id: number; slug: string; name: string }) => void;
  filters?: { companyId?: string; keywords?: string[]; locationIds?: number[]; occupationIds?: number[]; seniorityIds?: number[]; languages?: string[] };
}

export function TechnologyModal({
  open,
  onOpenChange,
  selected,
  onToggle,
  filters,
}: TechnologyModalProps) {
  const [groups, setGroups] = useState<TechnologyGroup[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState("");

  const selectedIds = useMemo(() => new Set(selected.map((s) => s.id)), [selected]);

  const filtersKey = filters ? JSON.stringify(filters) : "";
  const prevFiltersKeyRef = useRef(filtersKey);

  useEffect(() => {
    if (open && (!groups || filtersKey !== prevFiltersKeyRef.current)) {
      prevFiltersKeyRef.current = filtersKey;
      setLoading(true);
      getAllTechnologiesGrouped(filters)
        .then(setGroups)
        .finally(() => setLoading(false));
    }
  }, [open, groups, filtersKey]);

  // Reset search when modal closes
  useEffect(() => {
    if (!open) setSearch("");
  }, [open]);

  const filteredGroups = useMemo(() => {
    if (!groups) return null;
    const q = search.trim().toLowerCase();
    if (!q) return groups;

    const result: TechnologyGroup[] = [];
    for (const group of groups) {
      const filtered = group.technologies.filter((t) =>
        t.name.toLowerCase().includes(q) || t.slug.toLowerCase().includes(q),
      );
      if (filtered.length > 0) {
        result.push({ category: group.category, technologies: filtered });
      }
    }
    return result;
  }, [groups, search]);

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
            <Dialog.Title className="flex items-center gap-2 text-base font-semibold">
              <Code2 size={18} className="text-muted" />
              <Trans id="search.technologyModal.title" comment="Title for the technology selection modal">
                Select technologies
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
            <div className="relative">
              <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
              <input
                type="text"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Filter technologies..."
                className="w-full rounded-lg border border-border-soft bg-transparent py-2 pl-9 pr-3 text-sm placeholder:text-muted focus:border-primary/40 focus:outline-none"
              />
            </div>
          </div>

          {/* Body */}
          <div className="flex-1 overflow-y-auto px-5 py-4">
            {loading ? (
              <div className="flex items-center justify-center py-12">
                <Loader2 size={20} className="animate-spin text-muted" />
              </div>
            ) : !filteredGroups || filteredGroups.length === 0 ? (
              <p className="py-8 text-center text-sm text-muted">
                <Trans id="search.technologyModal.noResults" comment="No technologies available or matching search">
                  No technologies found.
                </Trans>
              </p>
            ) : (
              <div className="flex flex-col gap-5">
                {filteredGroups.map((group) => (
                  <div key={group.category}>
                    <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">
                      {group.category}
                    </h3>
                    <div className="flex flex-wrap gap-2">
                      {group.technologies.map((tech) => {
                        const active = selectedIds.has(tech.id);
                        return (
                          <button
                            key={tech.id}
                            onClick={() => onToggle({ id: tech.id, slug: tech.slug, name: tech.name })}
                            className={`flex cursor-pointer items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm transition-colors ${
                              active
                                ? "bg-primary/10 text-primary"
                                : "border border-border-soft text-muted hover:border-primary/30 hover:text-foreground"
                            }`}
                          >
                            <span className="font-medium">{tech.name}</span>
                            <span className={`text-xs ${active ? "text-primary/70" : "text-muted"}`}>
                              ({tech.count})
                            </span>
                          </button>
                        );
                      })}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
