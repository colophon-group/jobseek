"use client";

import { useEffect, useRef, useState } from "react";
import { clearStoredUserImage } from "@/lib/actions/preferences";

/**
 * Compute the 1-2 char initials placeholder displayed when no avatar
 * image is available (or when the stored OAuth image URL has expired
 * and is being healed — see `clearStoredUserImage`).
 *
 * Exported for tests and any standalone consumer; the main render path
 * lives inside `<UserAvatar>` below.
 */
export function getUserInitials(label: string): string {
  return label
    .split(" ")
    .map((w) => w[0])
    .filter(Boolean)
    .join("")
    .toUpperCase()
    .slice(0, 2);
}

/**
 * Window for the transient-5xx guard: a single image error is treated
 * as a possibly-transient failure (Google/GitHub 502, ECONNRESET, etc.)
 * and we only commit the heal once we've seen a second error from the
 * same URL within this many milliseconds. After the window expires the
 * counter resets so a legitimately-broken URL still trips eventually.
 *
 * Exported for tests so they can assert behaviour around the boundary
 * without hard-coding the magic number.
 */
export const TRANSIENT_ERROR_WINDOW_MS = 60_000;

/**
 * Session-scoped memo of URLs the heal action has already been invoked
 * for. SPA navs between routes remount `UserAvatar` (it lives inside
 * `AppHeader` and the header is part of the per-page layout tree), so
 * without this guard a stale `image` URL that we already nulled
 * server-side would re-trigger `clearStoredUserImage` on every route
 * change until the bootstrap refresh catches up. The action is
 * idempotent server-side but the wasted RPCs add up across nav-heavy
 * sessions.
 *
 * Module-level (not React state) on purpose — survives remounts, dies
 * with the JS context (full reload / new tab), which mirrors the "until
 * the next bootstrap refresh" expiry we actually want.
 *
 * Exported for tests so they can clear it between cases.
 */
export const __healedImageUrls: Set<string> = new Set();

type UserAvatarProps = {
  /** Source URL — typically `user.image` from `useAuth()`. */
  image: string | null | undefined;
  /** Display name fed to `getUserInitials` for the fallback. */
  name: string | null | undefined;
  /** Used as a secondary `getUserInitials` source if `name` is missing. */
  email?: string | null | undefined;
  /** Pixel size of the circle (width + height). */
  size: number;
  /**
   * Tailwind font-size class applied to the initials fallback. Both
   * existing render sites need a different value (desktop `text-xs`,
   * mobile bottom-bar `text-[10px]`), so it has to be a prop rather
   * than derived from `size`.
   */
  initialsTextClass?: string;
  /** Extra Tailwind classes appended to the wrapper. */
  className?: string;
  /**
   * Override the default `clearStoredUserImage` server action — exists
   * for tests, never set in production.
   */
  onBrokenImage?: () => Promise<void> | void;
};

/**
 * Renders the viewer's avatar with a self-healing fallback to initials.
 *
 * - `image` null/empty → initials immediately.
 * - `image` loads successfully → image.
 * - `image` non-null but the request fails (404/410/invalid bytes/etc.)
 *   → swap to initials in the current render AND fire the
 *   `clearStoredUserImage` server action so the next session refresh
 *   returns `image: null` and future loads skip the doomed network
 *   call entirely. See issue #3035 — the symptom is an expired
 *   LinkedIn signed-photo URL (`media.licdn.com/...?e=<past-epoch>`)
 *   that the OAuth callback persisted at signup and the row never
 *   updates back to.
 *
 * Three guards keep the heal call safe and cheap (issue #3048):
 *
 * 1. Prop-change reset — if a caller flips `image` from broken→valid
 *    mid-mount (no current path does, but cheap insurance), the
 *    `broken`/`healed` state resets so the fresh URL gets a chance.
 * 2. Transient-5xx debounce — a single `onError` could be a Google
 *    502 or GitHub ECONNRESET that resolves on retry. We only commit
 *    the heal after a second error from the same URL within
 *    `TRANSIENT_ERROR_WINDOW_MS`. LinkedIn's permanent 410 trips on
 *    the second nav/render; transients self-heal.
 * 3. Session memo of healed URLs — SPA nav remounts the avatar, which
 *    re-emits `<img>` and re-fires `onError`. The first remount past a
 *    successful heal would re-RPC `clearStoredUserImage` for no UX
 *    gain. The `__healedImageUrls` set short-circuits that.
 */
export function UserAvatar({
  image,
  name,
  email,
  size,
  initialsTextClass = "text-xs",
  className,
  onBrokenImage,
}: UserAvatarProps) {
  const [broken, setBroken] = useState(false);
  const [healed, setHealed] = useState(false);
  // First-error timestamp keyed by URL — survives the rerender that
  // sets `broken`, dies on remount (which is the right scope: a fresh
  // mount with the same URL gets a fresh two-strike budget).
  const firstErrorAtRef = useRef<{ url: string; at: number } | null>(null);

  // Reset state when the `image` prop changes — covers the unlikely
  // but cheap case of a caller swapping a broken URL for a valid one
  // without remounting the component (e.g., session refresh updating
  // the auth context in place).
  useEffect(() => {
    setBroken(false);
    setHealed(false);
    firstErrorAtRef.current = null;
  }, [image]);

  const showImage = Boolean(image) && !broken;
  const initialsSource = name?.trim() || email?.trim() || "?";
  const initials = getUserInitials(initialsSource);

  function handleError() {
    const url = typeof image === "string" ? image : "";

    // Session-memo short-circuit: this URL was already healed in this
    // JS context, no need to RPC again. Still swap to initials.
    if (url && __healedImageUrls.has(url)) {
      setBroken(true);
      return;
    }

    // Transient-5xx debounce: the first error from this URL just
    // primes the counter; only the second error within the window
    // commits the heal. This means the placeholder also stays
    // optimistic on first failure — the browser's broken-image icon
    // is briefly visible, but if the retry succeeds we keep the
    // image. Permanently-broken URLs (410, 404) trip the second
    // error on the next render/remount anyway.
    const now = Date.now();
    const prior = firstErrorAtRef.current;
    if (!prior || prior.url !== url || now - prior.at > TRANSIENT_ERROR_WINDOW_MS) {
      firstErrorAtRef.current = { url, at: now };
      return;
    }
    firstErrorAtRef.current = null;

    setBroken(true);
    if (healed) return;
    setHealed(true);
    if (url) {
      __healedImageUrls.add(url);
    }
    // Fire-and-forget — the server action invalidates the session
    // cache and the next bootstrap refresh returns `image: null`. Any
    // failure is logged; nothing the UI can do about it.
    Promise.resolve((onBrokenImage ?? clearStoredUserImage)()).catch(
      (err) => {
        console.warn("[UserAvatar] broken-image heal failed", err);
      },
    );
  }

  const wrapperClass = `flex items-center justify-center rounded-full bg-primary font-semibold text-primary-contrast overflow-hidden ${initialsTextClass} ${className ?? ""}`;

  return (
    <span
      aria-hidden="true"
      style={{ width: size, height: size }}
      className={wrapperClass}
    >
      {showImage ? (
        // User avatars come from arbitrary OAuth providers (Google,
        // LinkedIn, GitHub, …). next/image remote-host allowlist would
        // block many of them, so we render raw <img> and rely on the
        // browser cache + onError fallback below.
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={image as string}
          alt=""
          width={size}
          height={size}
          style={{ width: size, height: size }}
          className="rounded-full object-cover"
          onError={handleError}
          referrerPolicy="no-referrer"
          data-testid="user-avatar-img"
        />
      ) : (
        <span data-testid="user-avatar-initials">{initials}</span>
      )}
    </span>
  );
}
