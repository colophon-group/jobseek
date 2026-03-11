"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { X, MapPin } from "lucide-react";
import { useLingui } from "@lingui/react/macro";
import { suggestLocations } from "@/lib/actions/locations";
import type { LocationSuggestion } from "@/lib/actions/locations";

export interface SelectedLocation {
  id: number;
  name: string;
  type: LocationSuggestion["type"];
  parentName: string | null;
}

interface LocationPillsProps {
  locations: SelectedLocation[];
  onAdd: (location: SelectedLocation) => void;
  onRemove: (locationId: number) => void;
  locale: string;
  userLat?: number;
  userLng?: number;
}

const TYPE_LABELS: Record<string, string> = {
  macro: "Region",
  country: "Country",
  region: "Region",
  city: "City",
};

export function LocationPills({
  locations,
  onAdd,
  onRemove,
  locale,
  userLat: serverLat,
  userLng: serverLng,
}: LocationPillsProps) {
  const { t } = useLingui();
  const [inputValue, setInputValue] = useState("");
  const [suggestions, setSuggestions] = useState<LocationSuggestion[]>([]);
  const [isOpen, setIsOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);
  const [browserGeo, setBrowserGeo] = useState<{ lat: number; lng: number } | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(null);
  const isKeyboardNav = useRef(false);

  // Resolve effective coordinates: server (Vercel IP) → browser geolocation
  const userLat = serverLat ?? browserGeo?.lat;
  const userLng = serverLng ?? browserGeo?.lng;

  // Request browser geolocation once if server didn't provide coords
  useEffect(() => {
    if (serverLat != null || !navigator.geolocation) return;
    navigator.geolocation.getCurrentPosition(
      (pos) => setBrowserGeo({ lat: pos.coords.latitude, lng: pos.coords.longitude }),
      () => {},  // silently ignore denial
      { maximumAge: 600_000, timeout: 5_000 },
    );
  }, [serverLat]);

  const fetchSuggestions = useCallback(
    (query: string) => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
      if (query.trim().length < 2) {
        setSuggestions([]);
        setIsOpen(false);
        return;
      }
      debounceRef.current = setTimeout(async () => {
        const results = await suggestLocations({
          query,
          locale,
          userLat,
          userLng,
        });
        // Filter out already-selected locations
        const selectedIds = new Set(locations.map((l) => l.id));
        const filtered = results.filter((r) => !selectedIds.has(r.id));
        setSuggestions(filtered);
        setIsOpen(filtered.length > 0);
        setActiveIndex(-1);
      }, 200);
    },
    [locale, userLat, userLng, locations],
  );

  const selectSuggestion = useCallback(
    (suggestion: LocationSuggestion) => {
      onAdd({
        id: suggestion.id,
        name: suggestion.name,
        type: suggestion.type,
        parentName: suggestion.parentName,
      });
      setInputValue("");
      setSuggestions([]);
      setIsOpen(false);
      setActiveIndex(-1);
      inputRef.current?.focus();
    },
    [onAdd],
  );

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (!isOpen || suggestions.length === 0) return;

    if (e.key === "ArrowDown") {
      e.preventDefault();
      isKeyboardNav.current = true;
      setActiveIndex((prev) =>
        prev < suggestions.length - 1 ? prev + 1 : 0,
      );
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      isKeyboardNav.current = true;
      setActiveIndex((prev) =>
        prev > 0 ? prev - 1 : suggestions.length - 1,
      );
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (activeIndex >= 0 && activeIndex < suggestions.length) {
        selectSuggestion(suggestions[activeIndex]);
      }
    } else if (e.key === "Escape") {
      setIsOpen(false);
      setActiveIndex(-1);
    }
  };

  // Close dropdown on outside click
  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (
        containerRef.current &&
        !containerRef.current.contains(e.target as Node)
      ) {
        setIsOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  // Scroll active item into view (keyboard nav only)
  useEffect(() => {
    if (isKeyboardNav.current && activeIndex >= 0 && listRef.current) {
      const item = listRef.current.children[activeIndex] as HTMLElement;
      item?.scrollIntoView({ block: "nearest" });
    }
    isKeyboardNav.current = false;
  }, [activeIndex]);

  const placeholder = t({
    id: "search.locations.addPlaceholder",
    comment: "Placeholder in the add location input",
    message: "Add location...",
  });

  function pillLabel(loc: SelectedLocation) {
    if (loc.parentName && loc.type !== "country" && loc.type !== "macro") {
      return `${loc.name}, ${loc.parentName}`;
    }
    return loc.name;
  }

  return (
    <div className="flex flex-wrap items-center gap-2" ref={containerRef}>
      {locations.map((loc) => (
        <span
          key={loc.id}
          className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-3 py-1 text-sm text-primary"
        >
          <MapPin size={12} className="shrink-0" />
          {pillLabel(loc)}
          <button
            onClick={() => onRemove(loc.id)}
            className="ml-0.5 rounded-full p-0.5 transition-colors hover:bg-primary/20 cursor-pointer"
            aria-label={t({
              id: "search.locations.remove",
              comment: "Aria label for removing a location pill",
              message: "Remove location",
            })}
          >
            <X size={12} />
          </button>
        </span>
      ))}
      <div className="relative">
        <div className="inline-flex items-center gap-1 rounded-full border border-dashed border-border-soft px-3 py-1">
          <MapPin size={14} className="shrink-0 text-muted" />
          <div className="relative inline-grid items-center">
            <span className="invisible col-start-1 row-start-1 whitespace-pre text-sm">
              {inputValue || placeholder}
            </span>
            <input
              ref={inputRef}
              type="text"
              size={1}
              value={inputValue}
              onChange={(e) => {
                setInputValue(e.target.value);
                fetchSuggestions(e.target.value);
              }}
              onFocus={() => {
                if (suggestions.length > 0) setIsOpen(true);
              }}
              onKeyDown={handleKeyDown}
              placeholder={placeholder}
              className="col-start-1 row-start-1 w-full min-w-0 bg-transparent text-sm outline-none placeholder:text-muted"
              role="combobox"
              aria-expanded={isOpen}
              aria-autocomplete="list"
              aria-activedescendant={
                activeIndex >= 0 ? `loc-option-${activeIndex}` : undefined
              }
            />
          </div>
        </div>
        {isOpen && suggestions.length > 0 && (
          <ul
            ref={listRef}
            role="listbox"
            className="absolute left-0 top-full z-50 mt-1 max-h-60 w-64 overflow-auto rounded-lg border border-border-soft bg-surface shadow-lg"
          >
            {suggestions.map((s, i) => (
              <li
                key={s.id}
                id={`loc-option-${i}`}
                role="option"
                aria-selected={i === activeIndex}
                onMouseDown={(e) => {
                  e.preventDefault();
                  selectSuggestion(s);
                }}
                onMouseEnter={() => setActiveIndex(i)}
                className={`flex cursor-pointer items-center gap-2 px-3 py-2 text-sm ${
                  i === activeIndex ? "bg-primary/10" : "hover:bg-primary/5"
                }`}
              >
                <MapPin size={14} className="shrink-0 text-muted" />
                <div className="min-w-0 flex-1">
                  <span className="font-medium">{s.name}</span>
                  {s.parentName && (
                    <span className="text-muted">, {s.parentName}</span>
                  )}
                  <span className="ml-1.5 text-xs text-muted">
                    {TYPE_LABELS[s.type]}
                  </span>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
