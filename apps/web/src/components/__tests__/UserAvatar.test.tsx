import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import {
  UserAvatar,
  getUserInitials,
  __healedImageUrls,
  TRANSIENT_ERROR_WINDOW_MS,
} from "../UserAvatar";

// `UserAvatar` imports `clearStoredUserImage` from the server-actions
// module, which transitively pulls "server-only" + the Drizzle adapter.
// We swap in a stub so the test doesn't try to boot the DB; the
// component's `onBrokenImage` prop is the production-equivalent escape
// hatch we use to verify the heal call fires.
vi.mock("@/lib/actions/preferences", () => ({
  clearStoredUserImage: vi.fn(async () => {}),
}));

// The per-session memo of healed URLs (see UserAvatar.tsx) is module-
// level on purpose. Tests need a clean slate between cases or fix-3
// behaviour would bleed across them.
beforeEach(() => {
  __healedImageUrls.clear();
});

describe("getUserInitials", () => {
  it("uppercases first letters of the first two words", () => {
    expect(getUserInitials("Ada Lovelace")).toBe("AL");
  });

  it("handles a single-word name", () => {
    expect(getUserInitials("Plato")).toBe("P");
  });

  it("ignores extra whitespace", () => {
    expect(getUserInitials("  Marie   Curie  ")).toBe("MC");
  });

  it("falls back to the literal '?' when label is empty", () => {
    expect(getUserInitials("?")).toBe("?");
  });
});

describe("UserAvatar", () => {
  it("renders initials when image is null", () => {
    render(<UserAvatar image={null} name="Ada Lovelace" email="ada@test.com" size={32} />);
    expect(screen.getByTestId("user-avatar-initials").textContent).toBe("AL");
    expect(screen.queryByTestId("user-avatar-img")).toBeNull();
  });

  it("renders image when src is present", () => {
    render(
      <UserAvatar
        image="https://example.com/avatar.png"
        name="Ada Lovelace"
        email="ada@test.com"
        size={32}
      />,
    );
    const img = screen.getByTestId("user-avatar-img") as HTMLImageElement;
    expect(img.src).toBe("https://example.com/avatar.png");
  });

  it("falls back to initials after two errors and fires the heal callback", async () => {
    const onBrokenImage = vi.fn(async () => {});
    render(
      <UserAvatar
        image="https://media.licdn.com/expired-1.jpg?e=1773878400"
        name="Ada Lovelace"
        email="ada@test.com"
        size={32}
        onBrokenImage={onBrokenImage}
      />,
    );

    // First error primes the transient-5xx counter — must not heal yet.
    fireEvent.error(screen.getByTestId("user-avatar-img"));
    expect(onBrokenImage).not.toHaveBeenCalled();
    // Second error within the window commits the heal.
    fireEvent.error(screen.getByTestId("user-avatar-img"));

    await waitFor(() => {
      expect(screen.getByTestId("user-avatar-initials").textContent).toBe("AL");
    });
    expect(screen.queryByTestId("user-avatar-img")).toBeNull();
    expect(onBrokenImage).toHaveBeenCalledTimes(1);
  });

  it("calls the heal callback at most once even if the image errors many times", async () => {
    const onBrokenImage = vi.fn(async () => {});
    const { rerender } = render(
      <UserAvatar
        image="https://media.licdn.com/expired-2.jpg?e=1773878400"
        name="Ada Lovelace"
        email="ada@test.com"
        size={32}
        onBrokenImage={onBrokenImage}
      />,
    );

    // Two errors to trip the transient guard and commit the heal.
    fireEvent.error(screen.getByTestId("user-avatar-img"));
    fireEvent.error(screen.getByTestId("user-avatar-img"));
    // After the heal the image is unmounted; the only way the
    // <img> could fire again is a parent re-render that re-mounts it
    // (e.g. a `key` change). Simulate that.
    rerender(
      <UserAvatar
        image="https://media.licdn.com/expired-2.jpg?e=1773878400"
        name="Ada Lovelace"
        email="ada@test.com"
        size={32}
        onBrokenImage={onBrokenImage}
      />,
    );
    // The internal `broken` state survives the re-render with the same
    // props, so no <img> is re-mounted — the heal call stays at 1.
    expect(onBrokenImage).toHaveBeenCalledTimes(1);
  });

  it("falls back to email when name is missing", () => {
    render(<UserAvatar image={null} name={null} email="abc@test.com" size={32} />);
    expect(screen.getByTestId("user-avatar-initials").textContent).toBe("A");
  });

  it("renders '?' when both name and email are empty", () => {
    render(<UserAvatar image={null} name="" email="" size={32} />);
    expect(screen.getByTestId("user-avatar-initials").textContent).toBe("?");
  });

  // ── Issue #3048 follow-ups ──

  // Fix 1 — prop-change reset.
  // On `origin/main`, `broken`/`healed` state survives an `image` prop
  // swap, so a broken→valid URL change leaves the user staring at the
  // initials placeholder. After the fix the state resets and the new
  // URL gets a fresh chance to load. Latent today (no caller flips
  // `image` mid-mount) but cheap insurance.
  it("(fix #3048-1) resets broken state when image prop changes", async () => {
    const onBrokenImage = vi.fn(async () => {});
    const { rerender } = render(
      <UserAvatar
        image="https://media.licdn.com/broken.jpg?e=1"
        name="Ada Lovelace"
        size={32}
        onBrokenImage={onBrokenImage}
      />,
    );

    // Force broken=true. On origin/main this only takes one error; on
    // the fix branch the transient guard requires two. Firing twice
    // works on both code paths because the second error after broken
    // is set is a no-op on main (img is already unmounted, so this
    // second call targets the already-removed node — see below) and
    // commits on fix.
    const img = screen.getByTestId("user-avatar-img");
    fireEvent.error(img);
    // On the fix branch the img is still mounted after the first
    // error (transient guard); fire again to actually commit broken.
    const stillThere = screen.queryByTestId("user-avatar-img");
    if (stillThere) fireEvent.error(stillThere);

    await waitFor(() => {
      expect(screen.queryByTestId("user-avatar-initials")).not.toBeNull();
    });
    expect(screen.queryByTestId("user-avatar-img")).toBeNull();

    // Now flip the image prop to a fresh URL. On origin/main the
    // `broken` state persists and we still see initials; on the fix
    // the `useEffect([image])` resets state and the new img mounts.
    rerender(
      <UserAvatar
        image="https://example.com/fresh.png"
        name="Ada Lovelace"
        size={32}
        onBrokenImage={onBrokenImage}
      />,
    );

    expect(screen.queryByTestId("user-avatar-img")).not.toBeNull();
    expect((screen.getByTestId("user-avatar-img") as HTMLImageElement).src).toBe(
      "https://example.com/fresh.png",
    );
  });

  // Fix 2 — transient-5xx guard.
  // On `origin/main`, the very first `onError` fires
  // `clearStoredUserImage` and permanently nulls the row. A Google
  // signed URL or GitHub avatar that 502s once on a slow network would
  // be wiped forever, only recovering via OAuth re-link. After the fix
  // a single error is treated as transient: the img stays mounted, no
  // RPC fires, and only a second error within ~60s commits the heal.
  it("(fix #3048-2) does not fire the heal action on a single transient error", () => {
    const onBrokenImage = vi.fn(async () => {});
    render(
      <UserAvatar
        image="https://lh3.googleusercontent.com/a/transient.jpg"
        name="Ada Lovelace"
        size={32}
        onBrokenImage={onBrokenImage}
      />,
    );

    fireEvent.error(screen.getByTestId("user-avatar-img"));

    // Single error must not RPC.
    expect(onBrokenImage).not.toHaveBeenCalled();
    // And the img stays mounted — initials placeholder doesn't take
    // over until the second strike commits.
    expect(screen.queryByTestId("user-avatar-img")).not.toBeNull();
  });

  // Fix 3 — per-nav remount memo.
  // On `origin/main`, every SPA navigation remounts `UserAvatar` (it
  // lives inside `AppHeader` in the per-page layout), and the broken
  // URL re-fires `onError`, which re-fires `clearStoredUserImage`.
  // The server action is idempotent but each nav burns a roundtrip
  // until the bootstrap refresh catches up. After the fix a module-
  // level set remembers URLs that have already been healed in this
  // JS context and short-circuits subsequent mounts.
  it("(fix #3048-3) does not re-fire the heal action across remounts on the same broken URL", async () => {
    const onBrokenImage = vi.fn(async () => {});
    const brokenUrl = "https://media.licdn.com/expired-3.jpg?e=1";

    // First mount: two errors trigger the heal.
    const first = render(
      <UserAvatar
        image={brokenUrl}
        name="Ada Lovelace"
        size={32}
        onBrokenImage={onBrokenImage}
      />,
    );
    fireEvent.error(first.getByTestId("user-avatar-img"));
    // The first render's <img> may have been unmounted by now if the
    // transient guard ever changes; defensively re-query.
    const stillThere1 = first.queryByTestId("user-avatar-img");
    if (stillThere1) fireEvent.error(stillThere1);
    await waitFor(() => expect(onBrokenImage).toHaveBeenCalledTimes(1));

    // SPA nav: unmount + fresh mount with the same `image` value, as
    // happens when AppHeader re-renders inside a route transition
    // before the auth-context refresh has cleared `user.image`.
    first.unmount();
    const second = render(
      <UserAvatar
        image={brokenUrl}
        name="Ada Lovelace"
        size={32}
        onBrokenImage={onBrokenImage}
      />,
    );
    // Fire errors again from the new mount. Without the memo, two
    // errors would commit a second heal call.
    fireEvent.error(second.getByTestId("user-avatar-img"));
    const stillThere2 = second.queryByTestId("user-avatar-img");
    if (stillThere2) fireEvent.error(stillThere2);

    // Memo short-circuit: heal stays at 1 even after the remount.
    expect(onBrokenImage).toHaveBeenCalledTimes(1);
    // And the second mount still falls back to initials — the user-
    // facing behaviour stays correct, we just skip the RPC.
    await waitFor(() => {
      expect(second.queryByTestId("user-avatar-initials")).not.toBeNull();
    });
  });

  // Sanity: TRANSIENT_ERROR_WINDOW_MS is exported and a positive
  // number. Cheap guard against a typo'd constant zeroing the window.
  it("transient-5xx window is a positive duration", () => {
    expect(TRANSIENT_ERROR_WINDOW_MS).toBeGreaterThan(0);
  });

  // Bonus: errors more than TRANSIENT_ERROR_WINDOW_MS apart do not
  // commit. This is the second half of the transient-5xx contract —
  // a one-off failure today + a one-off failure tomorrow stays
  // transient on both occasions, never permanently nulling a row
  // that's actually fine.
  it("(fix #3048-2) does not heal when two errors straddle the transient window", async () => {
    vi.useFakeTimers();
    try {
      const onBrokenImage = vi.fn(async () => {});
      render(
        <UserAvatar
          image="https://lh3.googleusercontent.com/a/window-straddle.jpg"
          name="Ada Lovelace"
          size={32}
          onBrokenImage={onBrokenImage}
        />,
      );

      fireEvent.error(screen.getByTestId("user-avatar-img"));
      // Advance the clock past the window.
      vi.setSystemTime(Date.now() + TRANSIENT_ERROR_WINDOW_MS + 1_000);
      fireEvent.error(screen.getByTestId("user-avatar-img"));

      expect(onBrokenImage).not.toHaveBeenCalled();
      expect(screen.queryByTestId("user-avatar-img")).not.toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });
});

afterEach(() => {
  __healedImageUrls.clear();
});
