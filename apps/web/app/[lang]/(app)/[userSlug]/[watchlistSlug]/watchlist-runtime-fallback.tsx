import { Loader2 } from "lucide-react";
import type { Locale } from "@/lib/i18n";

const loadingLabels: Record<Locale, string> = {
  en: "Loading…",
  de: "Laden…",
  fr: "Chargement…",
  it: "Caricamento…",
};

export function WatchlistRuntimeFallback({ locale }: { locale: Locale }) {
  return (
    <div
      role="status"
      aria-busy="true"
      aria-live="polite"
      className="flex min-h-64 flex-col items-center justify-center gap-3 text-sm text-muted"
    >
      <Loader2 className="size-5 animate-spin" aria-hidden="true" />
      {loadingLabels[locale]}
    </div>
  );
}
