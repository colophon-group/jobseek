"use client";

import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { useRouter } from "next/navigation";
import { Trans, useLingui } from "@lingui/react/macro";
import { useSession } from "@/components/providers/SessionProvider";
import { Button } from "@/components/ui/Button";
import { ErrorAlert } from "@/components/ui/ErrorAlert";
import { FormField } from "@/components/ui/FormField";
import { SuccessAlert } from "@/components/ui/SuccessAlert";
import { authClient } from "@/lib/auth-client";
import { renameUsername } from "@/lib/actions/preferences";
import { translateActionError } from "@/lib/action-error-messages";
import { isReservedUsername } from "@/lib/username";

const USERNAME_RE = /^[a-z0-9][a-z0-9-]*[a-z0-9]$/;

export function UsernameSection({ currentUsername }: { currentUsername: string }) {
  const { t } = useLingui();
  const router = useRouter();
  const { refresh: refreshSession } = useSession();
  const [savedUsername, setSavedUsername] = useState(currentUsername);
  const [value, setValue] = useState(currentUsername);
  const [loading, setLoading] = useState(false);
  const [checking, setChecking] = useState(false);
  const [available, setAvailable] = useState<boolean | null>(null);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, []);

  const normalized = value.toLowerCase().trim();
  const unchanged = normalized === savedUsername;
  const tooShort = normalized.length < 3;
  const tooLong = normalized.length > 30;
  const invalidChars = normalized.length >= 3 && !USERNAME_RE.test(normalized);
  const reserved = !invalidChars && normalized.length >= 3 && isReservedUsername(normalized);

  function handleChange(raw: string) {
    const v = raw.toLowerCase().replace(/[^a-z0-9-]/g, "");
    setValue(v);
    setError("");
    setSuccess("");
    setAvailable(null);

    if (debounceRef.current) clearTimeout(debounceRef.current);

    const norm = v.trim();
    if (norm === savedUsername || norm.length < 3 || norm.length > 30 || !USERNAME_RE.test(norm) || isReservedUsername(norm)) return;

    setChecking(true);
    debounceRef.current = setTimeout(async () => {
      try {
        const res = await authClient.isUsernameAvailable({ username: norm });
        setAvailable(!!res.data?.available);
      } catch {
        setAvailable(null);
      }
      setChecking(false);
    }, 400);
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (unchanged || tooShort || tooLong || invalidChars || reserved || available === false) return;

    setError("");
    setSuccess("");
    setLoading(true);

    // Server action wraps `auth.api.updateUser` AND fans out every
    // cache layer the rename invalidates (Redis session, watchlist
    // cache tags, Typesense `owner_username`, sitemap). See #3022 +
    // the action's docstring.
    try {
      const { error } = await renameUsername(normalized);
      if (error) {
        setLoading(false);
        setError(translateActionError(t, error));
        return;
      }
    } catch {
      setLoading(false);
      setError(t({ id: "settings.account.username.error", comment: "Generic username update error", message: "Failed to update username" }));
      return;
    }

    // Re-pull the bootstrap payload so SessionProvider stops handing
    // the stale `user.username` to URL-building components like
    // WatchlistCard / save-search / mirror. router.refresh() rebuilds
    // any RSC tree currently rendered with the old slug.
    await refreshSession();
    router.refresh();
    setLoading(false);
    setSavedUsername(normalized);
    setAvailable(null);
    setSuccess(t({ id: "settings.account.username.success", comment: "Username updated success message", message: "Username updated." }));
  }

  function getHint() {
    if (unchanged) return null;
    if (tooShort) return t({ id: "settings.account.username.tooShort", comment: "Username too short hint", message: "At least 3 characters" });
    if (tooLong) return t({ id: "settings.account.username.tooLong", comment: "Username too long hint", message: "At most 30 characters" });
    if (invalidChars) return t({ id: "settings.account.username.invalidChars", comment: "Username invalid characters hint", message: "Only lowercase letters, numbers, and hyphens (cannot start/end with hyphen)" });
    if (reserved) return t({ id: "settings.account.username.reserved", comment: "Username is reserved hint", message: "This username is reserved" });
    if (checking) return t({ id: "settings.account.username.checking", comment: "Checking username availability", message: "Checking availability..." });
    if (available === true) return t({ id: "settings.account.username.available", comment: "Username is available", message: "Available" });
    if (available === false) return t({ id: "settings.account.username.taken", comment: "Username is taken", message: "Already taken" });
    return null;
  }

  const hint = getHint();
  const usernameValidationInvalid = !unchanged && (tooShort || tooLong || invalidChars || reserved || available === false);
  const usernameInvalid = usernameValidationInvalid || !!error;
  const hintClassName = available === true
    ? "mt-1 text-xs text-green-600 dark:text-green-400"
    : usernameInvalid
      ? "mt-1 text-xs text-error"
      : "mt-1 text-xs text-muted";

  return (
    <section>
      <h2 className="mb-1 text-base font-semibold">
        <Trans id="settings.account.username.title" comment="Username section heading">Username</Trans>
      </h2>
      <p className="mb-4 text-sm text-muted">
        <Trans id="settings.account.username.description" comment="Username section description">
          Your unique handle used in your public profile URL.
        </Trans>
      </p>
      <ErrorAlert message={error} focusOnRender />
      <form onSubmit={handleSubmit}>
        <div className="flex flex-col gap-4 min-[480px]:flex-row min-[480px]:items-end">
          <div className="flex-1">
            <FormField
              label={t({ id: "settings.account.username.label", comment: "Username input label", message: "Username" })}
              required
              autoComplete="username"
              value={value}
              onChange={(e) => handleChange(e.target.value)}
              maxLength={30}
              hint={hint}
              hintClassName={hintClassName}
              error={error}
              aria-invalid={usernameInvalid || undefined}
            />
          </div>
          <Button type="submit" disabled={loading || unchanged || tooShort || tooLong || invalidChars || reserved || available === false || checking} size="sm">
            {loading
              ? t({ id: "settings.account.username.saving", comment: "Username save button while loading", message: "Saving..." })
              : t({ id: "settings.account.username.save", comment: "Username save button", message: "Update username" })}
          </Button>
        </div>
        {success && <div className="mt-3"><SuccessAlert message={success} /></div>}
      </form>
    </section>
  );
}
