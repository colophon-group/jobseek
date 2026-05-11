import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, act } from "@testing-library/react";
import { useSession } from "../SessionProvider";

// The real `@/lib/actions/bootstrap` is a server action that transitively
// imports `server-only`, which throws when loaded in a non-Next runtime.
// Neutralize it, then swap the action itself for a spy.
vi.mock("server-only", () => ({}));
const mockBootstrap = vi.fn();
vi.mock("@/lib/actions/bootstrap", () => ({
  fetchAppBootstrap: (...args: unknown[]) => mockBootstrap(...args),
}));

// BannerProvider reads window.localStorage during render. happy-dom's
// localStorage implementation doesn't always expose getItem as a plain
// function on the prototype, so stub it here — this test isn't
// exercising that code path.
if (typeof window !== "undefined") {
  const memory = new Map<string, string>();
  const stub: Storage = {
    get length() {
      return memory.size;
    },
    clear: () => memory.clear(),
    getItem: (k: string) => (memory.has(k) ? (memory.get(k) as string) : null),
    key: (i: number) => Array.from(memory.keys())[i] ?? null,
    removeItem: (k: string) => {
      memory.delete(k);
    },
    setItem: (k: string, v: string) => {
      memory.set(k, v);
    },
  };
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: stub,
  });
}

// Import after the mock is installed.
import { AppBootstrapProvider } from "../AppBootstrapProvider";

function SessionProbe() {
  const { user, isLoggedIn, isPending } = useSession();
  return (
    <>
      <span data-testid="pending">{String(isPending)}</span>
      <span data-testid="logged-in">{String(isLoggedIn)}</span>
      <span data-testid="user-name">{user?.name ?? "none"}</span>
    </>
  );
}

let cookieSpy: ReturnType<typeof vi.spyOn> | undefined;
function setDocumentCookie(value: string) {
  cookieSpy?.mockRestore();
  cookieSpy = vi.spyOn(document, "cookie", "get").mockReturnValue(value);
}

beforeEach(() => {
  mockBootstrap.mockReset();
});

afterEach(() => {
  cookieSpy?.mockRestore();
  cookieSpy = undefined;
});

describe("AppBootstrapProvider", () => {
  it("does not call fetchAppBootstrap when the `logged_in` hint cookie is absent", async () => {
    setDocumentCookie("utm_source=google; NEXT_LOCALE=en");
    mockBootstrap.mockResolvedValue({
      user: { id: "ghost", email: "x@x", name: "Ghost", emailVerified: true },
      prefs: null,
      savedStatuses: [],
      starredIds: [],
    });

    render(
      <AppBootstrapProvider>
        <SessionProbe />
      </AppBootstrapProvider>,
    );

    // Give any queued effects a chance to run.
    await waitFor(() => {
      expect(screen.getByTestId("pending").textContent).toBe("false");
    });

    expect(mockBootstrap).not.toHaveBeenCalled();
    expect(screen.getByTestId("logged-in").textContent).toBe("false");
    expect(screen.getByTestId("user-name").textContent).toBe("none");
  });

  it("calls fetchAppBootstrap and propagates user state when the hint cookie is present", async () => {
    setDocumentCookie("logged_in=1; NEXT_LOCALE=en");
    mockBootstrap.mockResolvedValue({
      user: {
        id: "u1",
        email: "alice@example.com",
        name: "Alice",
        emailVerified: true,
      },
      prefs: null,
      savedStatuses: [],
      starredIds: [],
    });

    render(
      <AppBootstrapProvider>
        <SessionProbe />
      </AppBootstrapProvider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("logged-in").textContent).toBe("true");
    });
    expect(mockBootstrap).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("user-name").textContent).toBe("Alice");
  });

  it("is pending at first render when bootstrap is required", async () => {
    setDocumentCookie("logged_in=1");
    let resolveBootstrap!: (v: unknown) => void;
    mockBootstrap.mockReturnValue(
      new Promise((resolve) => {
        resolveBootstrap = resolve;
      }),
    );

    render(
      <AppBootstrapProvider>
        <SessionProbe />
      </AppBootstrapProvider>,
    );

    // Pre-resolution: waiting on the server action.
    expect(screen.getByTestId("pending").textContent).toBe("true");
    expect(mockBootstrap).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolveBootstrap({
        user: null,
        prefs: null,
        savedStatuses: [],
        starredIds: [],
      });
    });

    expect(screen.getByTestId("pending").textContent).toBe("false");
  });

  it("exposes refresh() that re-fetches bootstrap and replaces state without an isPending flicker (#3022)", async () => {
    setDocumentCookie("logged_in=1; NEXT_LOCALE=en");

    // First mount → returns the OLD identity. Subsequent refresh() →
    // returns the NEW identity. We assert (a) the user name flips
    // after refresh() resolves, and (b) `isPending` stays `false`
    // throughout the refresh — replacing `data` in place must not
    // null it out and flash the spinner on every `useAuth()` consumer.
    mockBootstrap.mockResolvedValueOnce({
      user: {
        id: "u1",
        email: "x@x",
        name: "Alice",
        emailVerified: true,
        username: "oldname",
      },
      prefs: null,
      savedStatuses: [],
      starredIds: [],
    });

    let resolveRefresh!: (v: unknown) => void;
    mockBootstrap.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveRefresh = resolve;
      }),
    );

    let triggerRefresh!: () => Promise<void>;
    function RefreshProbe() {
      const { refresh } = useSession();
      triggerRefresh = refresh;
      return null;
    }

    render(
      <AppBootstrapProvider>
        <SessionProbe />
        <RefreshProbe />
      </AppBootstrapProvider>,
    );

    // Initial mount finishes — OLD identity visible.
    await waitFor(() => {
      expect(screen.getByTestId("user-name").textContent).toBe("Alice");
    });
    expect(screen.getByTestId("pending").textContent).toBe("false");

    // Kick off refresh; do NOT resolve the inner promise yet.
    let refreshDone = false;
    await act(async () => {
      triggerRefresh().then(() => {
        refreshDone = true;
      });
    });

    // While refresh is in-flight, isPending must STILL be false (no
    // flicker for consumers) and the old identity is still shown.
    expect(screen.getByTestId("pending").textContent).toBe("false");
    expect(screen.getByTestId("user-name").textContent).toBe("Alice");

    // Resolve the refresh with the new identity.
    await act(async () => {
      resolveRefresh({
        user: {
          id: "u1",
          email: "x@x",
          name: "Alice Renamed",
          emailVerified: true,
          username: "newname",
        },
        prefs: null,
        savedStatuses: [],
        starredIds: [],
      });
    });

    await waitFor(() => {
      expect(screen.getByTestId("user-name").textContent).toBe("Alice Renamed");
    });
    expect(refreshDone).toBe(true);
    expect(screen.getByTestId("pending").textContent).toBe("false");
    expect(mockBootstrap).toHaveBeenCalledTimes(2);
  });

  it("does not substring-match `logged_in` against other cookie names", async () => {
    // Regression guard for a prior plan using bare `.includes`. A cookie
    // named `x_logged_in_ago` should NOT be treated as the hint.
    setDocumentCookie("x_logged_in_ago=1; NEXT_LOCALE=en");
    mockBootstrap.mockResolvedValue({
      user: null,
      prefs: null,
      savedStatuses: [],
      starredIds: [],
    });

    render(
      <AppBootstrapProvider>
        <SessionProbe />
      </AppBootstrapProvider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("pending").textContent).toBe("false");
    });
    expect(mockBootstrap).not.toHaveBeenCalled();
  });
});
