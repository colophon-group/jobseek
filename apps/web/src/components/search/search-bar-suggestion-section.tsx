"use client";

import type { ReactNode } from "react";

export function SearchBarSuggestionSection<T>({
  items,
  header,
  startIndex,
  activeIndex,
  hasDivider,
  getKey,
  getTestId,
  renderIcon,
  renderLabel,
  renderTrailing,
  onActiveIndex,
  onSelect,
}: {
  items: readonly T[];
  header: string;
  startIndex: number;
  activeIndex: number;
  hasDivider: boolean;
  getKey: (item: T) => string;
  getTestId?: (item: T) => string | undefined;
  renderIcon: (item: T) => ReactNode;
  renderLabel: (item: T) => ReactNode;
  renderTrailing?: (item: T) => ReactNode;
  onActiveIndex: (index: number) => void;
  onSelect: (item: T) => void;
}) {
  if (items.length === 0) return null;

  return (
    <>
      <div
        className={`px-3 pt-2 pb-1 text-[10px] font-semibold uppercase tracking-wider text-muted ${
          hasDivider ? "border-t border-border-soft" : ""
        }`}
      >
        {header}
      </div>
      {items.map((item, i) => {
        const flatIndex = startIndex + i;
        return (
          <div
            key={getKey(item)}
            id={`search-option-${flatIndex}`}
            role="option"
            aria-selected={flatIndex === activeIndex}
            data-suggestion
            data-testid={getTestId?.(item)}
            onMouseDown={(e) => {
              e.preventDefault();
              onSelect(item);
            }}
            onMouseEnter={() => onActiveIndex(flatIndex)}
            className={`flex cursor-pointer items-center gap-2 px-3 py-2 text-sm ${
              flatIndex === activeIndex ? "bg-primary/10" : "hover:bg-primary/5"
            }`}
          >
            {renderIcon(item)}
            {renderLabel(item)}
            {renderTrailing?.(item)}
          </div>
        );
      })}
    </>
  );
}
