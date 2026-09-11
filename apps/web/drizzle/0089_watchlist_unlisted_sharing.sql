-- Explicit unlisted sharing is separate from the retired public-watchlist
-- flag. Existing rows stay private-by-link regardless of legacy is_public.
ALTER TABLE public.watchlist
  ADD COLUMN share_enabled boolean DEFAULT false NOT NULL;
