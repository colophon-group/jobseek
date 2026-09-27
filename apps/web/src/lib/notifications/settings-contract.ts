import type { WatchlistFilters } from "@/lib/watchlist-matcher-contract";
import type { SelectedLocation } from "@/lib/search/types";
type TaxonomyLabel = { id: number; slug: string; name: string };
export type NotificationFilterPreview = {
  filters: WatchlistFilters;
  locations?: SelectedLocation[];
  occupations?: TaxonomyLabel[];
  seniorities?: TaxonomyLabel[];
  technologies?: TaxonomyLabel[];
};
export type WatchlistNotificationMode = "off" | "all" | "narrowed";
export type NotificationWatchlistSetting = {
  id: string;
  title: string;
  mode: WatchlistNotificationMode;
  filterPreview?: NotificationFilterPreview;
  prompt: string | null;
  narrowingAvailable: boolean;
};
