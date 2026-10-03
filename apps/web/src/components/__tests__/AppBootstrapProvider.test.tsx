import { useState } from "react";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, act, fireEvent } from "@testing-library/react";
// SalaryDisplayProvider (mounted via AppBootstrapProvider) calls `useLingui()`
// for the period-suffix label (#3144). Stub the Lingui surface so the provider
// hierarchy can render without an I18n setup.
import "@/test-utils/lingui-mock";
import { useSession } from "../providers/SessionProvider";

// The real `@/lib/actions/bootstrap` is a server action that transitively
// imports `server-only`, which throws when loaded in a non-Next runtime.
// Neutralize it, then swap the action itself for a spy.
vi.mock("server-only", () => ({}));
const mockBootstrap = vi.fn();
const mockCreateWatchlist = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/lib/useLocalePath", () => ({ useLocalePath: () => (path: string) => `/en${path}` }));
vi.mock("@/lib/actions/watchlists", () => ({
  createWatchlist: (...args: unknown[]) => mockCreateWatchlist(...args),
  copySharedWatchlist: vi.fn(), createWatchlistFromHandoff: vi.fn(),
  shareWatchlist: vi.fn(), deleteWatchlist: vi.fn(),
}));
vi.mock("@/lib/actions/session-watchlists", () => ({ getSessionWatchlistActivityPreviews: vi.fn() }));
vi.mock("@/lib/actions/bootstrap", () => ({
  fetchAppBootstrap: (...args: unknown[]) => mockBootstrap(...args),
}));
vi.mock("../providers/PreferencesInitializer", () => ({
  PreferencesInitializer: () => null,
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
import { AppBootstrapProvider } from "../providers/AppBootstrapProvider";
import { stagePendingWatchlist, readPendingWatchlists } from "@/lib/pending-watchlist";
import { WatchlistsPage } from "../../../app/[lang]/(app)/watchlists/watchlists-page";
import { authClient } from "@/lib/auth-client";
import { localPrefs } from "@/lib/preference-timestamps";
import { useSalaryDisplay } from "../providers/SalaryDisplayProvider";
import { useSalaryRates } from "../providers/SalaryDisplayProvider";

const initialCurrencyRates = [
  { currency: "EUR", toEur: 1 },
  { currency: "CHF", toEur: 1.04 },
];

function SessionProbe() {
  const { user, plan, preferences, isLoggedIn, isPending, accountStatus, canRetry, retry, refresh, invalidate } = useSession();
  return (
    <>
      <button onClick={() => { void retry(); }}>Retry</button>
      <button onClick={() => { void refresh(); }}>Refresh</button>
      <button onClick={invalidate}>Invalidate</button>
      <span data-testid="account-status">{accountStatus}</span>
      <span data-testid="can-retry">{String(canRetry)}</span>
      <span data-testid="pending">{String(isPending)}</span>
      <span data-testid="logged-in">{String(isLoggedIn)}</span>
      <span data-testid="user-name">{user?.name ?? "none"}</span>
      <span data-testid="plan">{plan}</span>
      <span data-testid="job-languages">
        {preferences?.jobLanguages?.join(",") ?? "none"}
      </span>
    </>
  );
}

function RatesProbe() {
  const rates = useSalaryRates();
  return (
    <span data-testid="rates">
      {rates.map((rate) => rate.currency).join(",")}
    </span>
  );
}

let cookieSetterSpy: ReturnType<typeof vi.spyOn> | undefined;
let cookieValue = "";
let cookieSpy: ReturnType<typeof vi.spyOn> | undefined;
function setDocumentCookie(value: string) {
  cookieSpy?.mockRestore();
  cookieSetterSpy?.mockRestore();
  cookieValue = value;
  cookieSpy = vi.spyOn(document, "cookie", "get").mockImplementation(() => cookieValue);
  cookieSetterSpy = vi.spyOn(document, "cookie", "set").mockImplementation((next) => {
    if (next.startsWith("logged_in=;") && next.includes("Max-Age=0")) {
      cookieValue = cookieValue.split(";").filter((part) => !part.trim().startsWith("logged_in=")).join(";");
    }
  });
}

beforeEach(() => {
  mockBootstrap.mockReset();
  mockCreateWatchlist.mockReset();
  window.sessionStorage.clear();
});

afterEach(() => {
  cookieSpy?.mockRestore();
  cookieSetterSpy?.mockRestore();
  cookieSpy = undefined;
  cookieSetterSpy = undefined;
  vi.useRealTimers();
});

describe("AppBootstrapProvider", () => {
  it("does not call fetchAppBootstrap when the `logged_in` hint cookie is absent", async () => {
    setDocumentCookie("utm_source=google; NEXT_LOCALE=en");
    mockBootstrap.mockResolvedValue({
      user: { id: "ghost", email: "x@x", name: "Ghost", emailVerified: true },
      prefs: { jobLanguages: ["de", "en"] },
      savedStatuses: [],
      starredIds: [],
    });

    render(
      <AppBootstrapProvider initialCurrencyRates={initialCurrencyRates}>
        <SessionProbe />
        <RatesProbe />
      </AppBootstrapProvider>,
    );

    // Give any queued effects a chance to run.
    await waitFor(() => {
      expect(screen.getByTestId("pending").textContent).toBe("false");
    });

    expect(mockBootstrap).not.toHaveBeenCalled();
    expect(screen.getByTestId("logged-in").textContent).toBe("false");
    expect(screen.getByTestId("user-name").textContent).toBe("none");
    expect(screen.getByTestId("plan").textContent).toBe("free");
    expect(screen.getByTestId("rates").textContent).toBe("EUR,CHF");
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
      plan: "unlimited",
      prefs: { jobLanguages: ["de", "en"] },
      savedStatuses: [],
      starredIds: [],
    });

    render(
      <AppBootstrapProvider initialCurrencyRates={initialCurrencyRates}>
        <SessionProbe />
      </AppBootstrapProvider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("logged-in").textContent).toBe("true");
    });
    expect(mockBootstrap).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("user-name").textContent).toBe("Alice");
    expect(screen.getByTestId("plan").textContent).toBe("unlimited");
    expect(screen.getByTestId("job-languages").textContent).toBe("de,en");
  });

  it("keeps failed lookup unresolved without erasing the hint or retrying automatically", async () => {
    setDocumentCookie("logged_in=1; NEXT_LOCALE=en");
    mockBootstrap.mockRejectedValue(new Error("server action unavailable"));

    render(
      <AppBootstrapProvider initialCurrencyRates={initialCurrencyRates}>
        <SessionProbe />
      </AppBootstrapProvider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("account-status").textContent).toBe("unavailable");
    });
    expect(screen.getByTestId("pending").textContent).toBe("true");
    expect(screen.getByTestId("logged-in").textContent).toBe("false");
    expect(screen.getByTestId("plan").textContent).toBe("free");
    expect(document.cookie).toContain("logged_in=1");
    expect(mockBootstrap).toHaveBeenCalledTimes(1);
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
      <AppBootstrapProvider initialCurrencyRates={initialCurrencyRates}>
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
    // null it out and flash the spinner on every `useSession()` consumer.
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
      <AppBootstrapProvider initialCurrencyRates={initialCurrencyRates}>
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
      <AppBootstrapProvider initialCurrencyRates={initialCurrencyRates}>
        <SessionProbe />
      </AppBootstrapProvider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("pending").textContent).toBe("false");
    });
    expect(mockBootstrap).not.toHaveBeenCalled();
  });
});

const alice = {
  user: { id: "u1", email: "alice@example.com", name: "Alice", emailVerified: true },
  plan: "unlimited", prefs: null, savedStatuses: [], starredIds: [],
};
const bob = { ...alice, user: { ...alice.user, id: "u2", name: "Bob" } };
function mountRecovery() {
  return render(<AppBootstrapProvider initialCurrencyRates={initialCurrencyRates}><SessionProbe /></AppBootstrapProvider>);
}
it.each(["rejection", "timeout"])("recovers from %s only through bounded manual retry", async (failure) => {
  vi.useFakeTimers();
  setDocumentCookie("logged_in=1");
  let resolveOld!: (value: unknown) => void;
  mockBootstrap.mockImplementationOnce(() => failure === "rejection"
    ? Promise.reject(new Error("unavailable"))
    : new Promise((resolve) => { resolveOld = resolve; }));
  mockBootstrap.mockResolvedValueOnce(bob);
  mountRecovery();
  await act(async () => { await vi.advanceTimersByTimeAsync(4_000); });
  expect(screen.getByTestId("account-status").textContent).toBe("unavailable");
  expect(screen.getByTestId("pending").textContent).toBe("true");
  fireEvent.click(screen.getByText("Retry"));
  expect(mockBootstrap).toHaveBeenCalledTimes(1);
  await act(async () => { await vi.advanceTimersByTimeAsync(1_000); });
  expect(screen.getByTestId("can-retry").textContent).toBe("true");
  expect(mockBootstrap).toHaveBeenCalledTimes(1);
  await act(async () => { fireEvent.click(screen.getByText("Retry")); });
  expect(mockBootstrap).toHaveBeenCalledTimes(2);
  expect(screen.getByTestId("user-name").textContent).toBe("Bob");
  expect(screen.getByTestId("account-status").textContent).toBe("ready");
  if (failure === "timeout") {
    await act(async () => { resolveOld(alice); });
    expect(screen.getByTestId("user-name").textContent).toBe("Bob");
  }
});
it("retains verified identity on failed refresh only until its context is invalidated", async () => {
  setDocumentCookie("logged_in=1");
  mockBootstrap.mockResolvedValueOnce(alice).mockRejectedValueOnce(new Error("down"));
  mountRecovery();
  await screen.findByText("Alice");
  await act(async () => { fireEvent.click(screen.getByText("Refresh")); });
  expect(screen.getByTestId("user-name").textContent).toBe("Alice");
  expect(screen.getByTestId("pending").textContent).toBe("false");
  expect(screen.getByTestId("account-status").textContent).toBe("unavailable");
  await act(async () => { authClient.$store.notify("$sessionSignal"); });
  expect(screen.getByTestId("user-name").textContent).toBe("none");
  expect(screen.getByTestId("pending").textContent).toBe("true");
  expect(mockBootstrap).toHaveBeenCalledTimes(2);
});
it.each(["logout", "auth signal", "changed hint"])("prevents old response restoring identity after %s", async (change) => {
  setDocumentCookie("logged_in=1");
  let resolveOld!: (value: unknown) => void;
  mockBootstrap.mockReturnValueOnce(new Promise((resolve) => { resolveOld = resolve; }));
  mockBootstrap.mockResolvedValueOnce(bob);
  mountRecovery();
  await act(async () => {
    if (change === "logout") fireEvent.click(screen.getByText("Invalidate"));
    if (change === "auth signal") authClient.$store.notify("$sessionSignal");
    if (change === "changed hint") cookieValue = "logged_in=2";
    resolveOld(alice);
  });
  expect(screen.getByTestId("user-name").textContent).toBe("none");
  expect(screen.getByTestId("account-status").textContent).toBe("unavailable");
  expect(mockBootstrap).toHaveBeenCalledTimes(1);
  await act(async () => { fireEvent.click(screen.getByText("Retry")); });
  expect(screen.getByTestId("user-name").textContent).toBe("Bob");
});
it("invalidates verified identity when a hint disappears on focus, without a request", async () => {
  setDocumentCookie("logged_in=1");
  mockBootstrap.mockResolvedValueOnce(alice);
  mountRecovery();
  await screen.findByText("Alice");
  await act(async () => { cookieValue = ""; window.dispatchEvent(new Event("focus")); });
  expect(screen.getByTestId("user-name").textContent).toBe("none");
  expect(screen.getByTestId("pending").textContent).toBe("false");
  expect(screen.getByTestId("account-status").textContent).toBe("ready");
  expect(mockBootstrap).toHaveBeenCalledTimes(1);
});
it("does not hydrate or persist anonymous salary choices while identity is unresolved", async () => {
  setDocumentCookie("logged_in=1");
  mockBootstrap.mockRejectedValueOnce(new Error("down"));
  localPrefs.displayCurrency.set("CHF");
  const persist = vi.spyOn(localPrefs.displayCurrency, "set");
  function SalaryProbe() {
    const { displayCurrency, update } = useSalaryDisplay();
    return <button onClick={() => update({ displayCurrency: "USD" })}>{displayCurrency ?? "No currency"}</button>;
  }
  render(<AppBootstrapProvider initialCurrencyRates={initialCurrencyRates}><SessionProbe /><SalaryProbe /></AppBootstrapProvider>);
  await waitFor(() => expect(screen.getByTestId("account-status").textContent).toBe("unavailable"));
  fireEvent.click(screen.getByText("No currency"));
  expect(persist).not.toHaveBeenCalled();
  persist.mockRestore();
});

it.each(["logout", "account switch"])("invalidates across tabs on %s and ignores an older same-hint response", async (change) => {
  const ports: FakeChannel[] = [];
  const posted: unknown[] = [];
  class FakeChannel {
    onmessage: ((event: MessageEvent) => void) | null = null;
    closed = false;
    constructor(public name: string) { ports.push(this); }
    postMessage(data: unknown) {
      posted.push(data);
      for (const port of ports) if (port !== this && port.name === this.name && !port.closed) {
        port.onmessage?.(new MessageEvent("message", { data }));
      }
    }
    close() { this.closed = true; }
  }
  vi.stubGlobal("BroadcastChannel", FakeChannel);
  setDocumentCookie("logged_in=1");
  let resolveOld!: (value: unknown) => void;
  mockBootstrap.mockResolvedValueOnce(alice);
  mockBootstrap.mockReturnValueOnce(new Promise((resolve) => { resolveOld = resolve; }));
  mockBootstrap.mockResolvedValueOnce(change === "logout" ? { ...alice, user: null } : bob);
  const view = mountRecovery();
  await screen.findByText("Alice");
  await act(async () => { fireEvent.click(screen.getByText("Refresh")); });
  const otherTab = new FakeChannel("jobseek-account-identity");
  await act(async () => {
    otherTab.postMessage("changed");
    if (change === "logout") cookieValue = "";
    resolveOld(alice);
  });
  expect(screen.getByTestId("user-name").textContent).toBe("none");
  expect(screen.getByTestId("account-status").textContent).toBe("unavailable");
  expect(posted).toEqual(["changed"]);
  expect(mockBootstrap).toHaveBeenCalledTimes(2);
  await act(async () => { fireEvent.click(screen.getByText("Retry")); });
  expect(screen.getByTestId("user-name").textContent).toBe(change === "logout" ? "none" : "Bob");
  expect(screen.getByTestId("account-status").textContent).toBe("ready");
  expect(posted).toEqual(["changed"]);
  view.unmount();
  expect(ports[0].closed).toBe(true);
  otherTab.close();
  vi.unstubAllGlobals();
});

it("coalesces manual retry clicks into one normal request", async () => {
  vi.useFakeTimers();
  setDocumentCookie("logged_in=1");
  let resolveRetry!: (value: unknown) => void;
  mockBootstrap.mockRejectedValueOnce(new Error("down"));
  mockBootstrap.mockReturnValueOnce(new Promise((resolve) => { resolveRetry = resolve; }));
  mountRecovery();
  await act(async () => { await vi.advanceTimersByTimeAsync(5_000); });
  await act(async () => {
    fireEvent.click(screen.getByText("Retry"));
    fireEvent.click(screen.getByText("Retry"));
    fireEvent.click(screen.getByText("Retry"));
  });
  expect(mockBootstrap).toHaveBeenCalledTimes(2);
  await act(async () => { resolveRetry(bob); });
  expect(screen.getByTestId("user-name").textContent).toBe("Bob");
});

it("preserves initial child state and imports pending watchlist intent once after account confirmation", async () => {
  setDocumentCookie("logged_in=1");
  const draft = { title: "Pending engineering", companyIds: [], filters: { anyCompany: true }, isPublic: false as const };
  stagePendingWatchlist({ kind: "create", draft });
  let resolveBootstrap!: (value: unknown) => void;
  let resolveImport!: (value: unknown) => void;
  mockBootstrap.mockReturnValueOnce(new Promise((resolve) => { resolveBootstrap = resolve; }));
  mockCreateWatchlist.mockReturnValueOnce(new Promise((resolve) => { resolveImport = resolve; }));
  function DraftInput() {
    const [draft, setDraft] = useState("");
    return <input aria-label="Draft title" value={draft} onChange={(event) => setDraft(event.target.value)} />;
  }
  render(<AppBootstrapProvider initialCurrencyRates={initialCurrencyRates}>
    <SessionProbe /><DraftInput />
    <WatchlistsPage initialWatchlists={[]} limitReached={false} locale="en" />
  </AppBootstrapProvider>);
  fireEvent.change(screen.getByLabelText("Draft title"), { target: { value: "Unsaved local edit" } });
  expect(readPendingWatchlists()).toHaveLength(1);
  expect(mockCreateWatchlist).not.toHaveBeenCalled();
  await act(async () => { resolveBootstrap(alice); });
  await waitFor(() => expect(mockCreateWatchlist).toHaveBeenCalledTimes(1));
  expect(mockCreateWatchlist).toHaveBeenCalledWith(draft);
  expect((screen.getByLabelText("Draft title") as HTMLInputElement).value).toBe("Unsaved local edit");
  await act(async () => { resolveImport({ id: "11111111-1111-4111-8111-111111111111" }); });
  expect(readPendingWatchlists()).toHaveLength(0);
  mockBootstrap.mockResolvedValueOnce(alice);
  await act(async () => { fireEvent.click(screen.getByText("Refresh")); });
  expect(mockCreateWatchlist).toHaveBeenCalledTimes(1);
  expect((screen.getByLabelText("Draft title") as HTMLInputElement).value).toBe("Unsaved local edit");
});
