"use client";

import { useState, useRef } from "react";
import { X, Plus } from "lucide-react";
import { useLingui } from "@lingui/react/macro";

interface KeywordPillsProps {
  keywords: string[];
  onAdd: (keyword: string) => void;
  onRemove: (keyword: string) => void;
}

export function KeywordPills({ keywords, onAdd, onRemove }: KeywordPillsProps) {
  const { t } = useLingui();
  const [inputValue, setInputValue] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = inputValue.trim();
    if (trimmed && !keywords.some((k) => k.toLowerCase() === trimmed.toLowerCase())) {
      onAdd(trimmed);
      setInputValue("");
    }
  };

  const placeholder = t({
    id: "search.keywords.addPlaceholder",
    comment: "Placeholder in the add keyword input",
    message: "Add keyword...",
  });

  return (
    <div className="flex flex-wrap items-center gap-2">
      {keywords.map((kw) => (
        <span
          key={kw}
          className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-3 py-1 text-sm text-primary"
        >
          {kw}
          <button
            onClick={() => onRemove(kw)}
            className="ml-0.5 rounded-full p-0.5 transition-colors hover:bg-primary/20 cursor-pointer"
            aria-label={t({
              id: "search.keywords.remove",
              comment: "Aria label for removing a keyword pill",
              message: "Remove keyword",
            })}
          >
            <X size={12} />
          </button>
        </span>
      ))}
      <form onSubmit={handleSubmit} className="inline-flex">
        <div className="inline-flex items-center gap-1 rounded-full border border-dashed border-border-soft px-3 py-1">
          <Plus size={14} className="shrink-0 text-muted" />
          <div className="relative inline-grid items-center">
            <span className="invisible col-start-1 row-start-1 whitespace-pre text-sm">
              {inputValue || placeholder}
            </span>
            <input
              ref={inputRef}
              type="text"
              size={1}
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              placeholder={placeholder}
              className="col-start-1 row-start-1 w-full min-w-0 bg-transparent text-sm outline-none placeholder:text-muted"
            />
          </div>
        </div>
      </form>
    </div>
  );
}
