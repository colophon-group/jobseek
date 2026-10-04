import { afterEach, beforeEach, expect, it, vi } from "vitest";

const factory = vi.hoisted(() => vi.fn(() => ({})));
vi.mock("better-auth/react", () => ({ createAuthClient: factory }));
vi.mock("better-auth/client/plugins", () => ({ usernameClient: () => ({}) }));
import { listenForAccountIdentityChanges, notifyAccountIdentityChanged } from "../auth-client";

const ports: FakeChannel[] = [];
const posted = vi.fn();
class FakeChannel {
  onmessage: ((event: MessageEvent) => void) | null = null;
  closed = false;
  constructor(public name: string) { ports.push(this); }
  postMessage(data: unknown) {
    posted(data);
    for (const port of ports) if (port !== this && !port.closed && port.name === this.name) {
      port.onmessage?.(new MessageEvent("message", { data }));
    }
  }
  close() { this.closed = true; }
}

beforeEach(() => {
  ports.length = 0;
  posted.mockClear();
  vi.stubGlobal("BroadcastChannel", FakeChannel);
});
afterEach(() => vi.unstubAllGlobals());

type ClientOptions = { fetchOptions: { onSuccess: (context: { request: { url: string }; data?: unknown }) => void } };
const onSuccess = (factory.mock.calls[0] as unknown as [ClientOptions])[0].fetchOptions.onSuccess;

it.each(["sign-in/email", "sign-in/username", "sign-in/social", "sign-up/email", "sign-out", "delete-user", "update-user"])(
  "broadcasts only an invalidation after successful %s", (path) => {
    onSuccess({ request: { url: `https://example.test/api/auth/${path}` }, data: { user: { id: "must-not-be-broadcast" } } });
    expect(posted.mock.calls).toEqual([["changed"]]);
    expect(ports[0].closed).toBe(true);
  },
);

it("does not broadcast successful session reads or initial account lookup", () => {
  onSuccess({ request: { url: "https://example.test/api/auth/get-session" } });
  expect(posted).not.toHaveBeenCalled();
});

it("receives other-tab invalidation without rebroadcast and unsubscribes on cleanup", () => {
  const invalidated = vi.fn();
  const stop = listenForAccountIdentityChanges(invalidated);
  const otherTab = new FakeChannel("jobseek-account-identity");
  otherTab.postMessage("changed");
  expect(invalidated).toHaveBeenCalledTimes(1);
  expect(posted.mock.calls).toEqual([["changed"]]);
  otherTab.postMessage({ user: { id: "forged" } });
  expect(invalidated).toHaveBeenCalledTimes(1);
  stop();
  expect(ports[0].closed).toBe(true);
  notifyAccountIdentityChanged();
  expect(invalidated).toHaveBeenCalledTimes(1);
  expect(ports.at(-1)?.closed).toBe(true);
  otherTab.close();
});

it("keeps local invalidation available when cross-tab messaging is blocked", () => {
  vi.stubGlobal("BroadcastChannel", class { constructor() { throw new Error("blocked"); } });
  const invalidated = vi.fn();
  const stop = listenForAccountIdentityChanges(invalidated);
  notifyAccountIdentityChanged();
  expect(invalidated).toHaveBeenCalledTimes(1);
  stop();
});
