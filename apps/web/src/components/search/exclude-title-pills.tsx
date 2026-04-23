"use client";

import { useState, useRef } from "react";
import { X, EyeOff } from "lucide-react";
import { useLingui } from "@lingui/react/macro";

interface ExcludeTitlePillsProps {
  keywords: string[];
  onAdd: (keyword: string) => void;
  onRemove: (keyword: string) => void;
}

export function ExcludeTitlePills({
  keywords,
  onAdd,
  onRemove,
}: ExcludeTitlePillsProps) {
  const { t } = useLingui();
  const [inputValue, setInputValue] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = inputValue.trim();
    if (!trimmed) return;
    if (keywords.some((k) => k.toLowerCase() === trimmed.toLowerCase())) return;
    onAdd(trimmed);
    setInputValue("");
  };

  const placeholder = t({
    id: "search.excludeTitles.addPlaceholder",
    comment: "Placeholder in the exclude-title input",
    message: "Hide titles with...",
  });

  const removeLabel = t({
    id: "search.excludeTitles.remove",
    comment: "Aria label for removing an exclude-title pill",
    message: "Remove excluded title",
  });

  return (
    <div className="flex flex-wrap items-center gap-2">
      {keywords.map((kw) => (
        <span
          key={kw}
          className="inline-flex items-center gap-1 rounded-full bg-muted/10 px-3 py-1 text-sm text-muted"
        >
          <EyeOff size={12} className="shrink-0" />
          {kw}
          <button
            onClick={() => onRemove(kw)}
            className="ml-0.5 rounded-full p-0.5 transition-colors hover:bg-muted/20 cursor-pointer"
            aria-label={removeLabel}
          >
            <X size={12} />
          </button>
        </span>
      ))}
      <form onSubmit={handleSubmit} className="inline-flex">
        <div className="inline-flex items-center gap-1 rounded-full border border-dashed border-border-soft px-3 py-1">
          <EyeOff size={14} className="shrink-0 text-muted" />
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
