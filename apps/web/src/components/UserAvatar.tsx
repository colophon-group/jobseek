"use client";

import { useState } from "react";
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
 * The healing call fires at most once per component mount per session
 * (guarded by `healed` state); subsequent re-renders with the same
 * broken URL won't re-fire even if React re-renders mid-flight.
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

  const showImage = Boolean(image) && !broken;
  const initialsSource = name?.trim() || email?.trim() || "?";
  const initials = getUserInitials(initialsSource);

  function handleError() {
    setBroken(true);
    if (healed) return;
    setHealed(true);
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
