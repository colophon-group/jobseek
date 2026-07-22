import type { ReactNode } from "react";
import { Button } from "@/components/ui/Button";

type WatchlistNotFoundStateProps = {
  lang: string;
  title: ReactNode;
  message: ReactNode;
  browseLabel: ReactNode;
};

export function WatchlistNotFoundState({
  lang,
  title,
  message,
  browseLabel,
}: WatchlistNotFoundStateProps) {
  return (
    <div className="flex flex-col items-center justify-center py-24 text-center">
      <h1 className="text-2xl font-bold">{title}</h1>
      <p className="mt-2 text-muted">{message}</p>
      <Button href={`/${lang}/watchlists`} className="mt-6">
        {browseLabel}
      </Button>
    </div>
  );
}
