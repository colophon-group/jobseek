import { beforeEach, expect, it, vi } from "vitest";
vi.mock("server-only", () => ({}));
const mocks = vi.hoisted(() => ({ session: vi.fn(), setMode: vi.fn(), preferences: vi.fn(), lists: vi.fn() }));
vi.mock("@/lib/sessionCache", () => ({ getSessionUserId: mocks.session }));
vi.mock("@/lib/services/notification-watchlist-settings", () => ({ setWatchlistNotificationModeForUser: mocks.setMode, getNotificationWatchlistsForUser: mocks.lists }));
vi.mock("@/lib/services/notification-preferences", () => ({ getNotificationPreferencesForUser: mocks.preferences, setNotificationsPausedForUser: vi.fn() }));
import { getNotificationPreferences, setWatchlistNotificationMode } from "../notifications";
const id = "11111111-1111-4111-8111-111111111111";
beforeEach(() => vi.resetAllMocks());
it("requires a session and rejects malformed scope requests before any writes", async () => {
  expect(await setWatchlistNotificationMode(id, "narrowed")).toEqual({ error: "not_authenticated" });
  mocks.session.mockResolvedValue("owner");
  expect(await setWatchlistNotificationMode("not-a-uuid", "all")).toEqual({ error: "invalid_request" });
  expect(await setWatchlistNotificationMode(id, "surprise" as "all")).toEqual({ error: "invalid_request" });
  expect(mocks.setMode).not.toHaveBeenCalled();
});
it("binds prompt reads and preference writes to the session owner", async () => {
  mocks.session.mockResolvedValue("owner");
  mocks.preferences.mockResolvedValue({ notificationsPaused: false });
  mocks.lists.mockResolvedValue([{ id, prompt: "Backend roles" }]);
  mocks.setMode.mockResolvedValue({ mode: "narrowed" });
  expect(await getNotificationPreferences()).toEqual({ notificationsPaused: false, watchlists: [{ id, prompt: "Backend roles" }] });
  expect(mocks.lists).toHaveBeenCalledWith("owner", "en");
  await setWatchlistNotificationMode(id, "narrowed");
  expect(mocks.setMode).toHaveBeenCalledWith("owner", id, "narrowed");
});
