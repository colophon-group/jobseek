import { beforeEach, describe, expect, it, vi } from "vitest";

type Listener = (event: MessageEvent) => void;

class MockBroadcastChannel {
  static instances: MockBroadcastChannel[] = [];
  listeners = new Set<Listener>();
  closed = false;

  constructor(public name: string) {
    MockBroadcastChannel.instances.push(this);
  }

  addEventListener(_type: "message", listener: Listener) {
    this.listeners.add(listener);
  }

  removeEventListener(_type: "message", listener: Listener) {
    this.listeners.delete(listener);
  }

  postMessage(data: unknown) {
    for (const instance of MockBroadcastChannel.instances) {
      if (instance === this || instance.closed || instance.name !== this.name) continue;
      for (const listener of instance.listeners) {
        listener({ data } as MessageEvent);
      }
    }
  }

  close() {
    this.closed = true;
  }
}

describe("watchlist selection cross-tab channel", () => {
  beforeEach(() => {
    vi.resetModules();
    MockBroadcastChannel.instances = [];
    vi.stubGlobal("BroadcastChannel", MockBroadcastChannel);
  });

  it("notifies other tabs without refreshing the publishing tab", async () => {
    const selection = await import("@/lib/watchlist-selection-client");
    const localRefresh = vi.fn();
    const unsubscribe = selection.subscribeToWatchlistSelection(localRefresh);

    selection.broadcastWatchlistSelectionChanged();
    expect(localRefresh).not.toHaveBeenCalled();

    const localSubscriber = MockBroadcastChannel.instances[0];
    for (const listener of localSubscriber.listeners) {
      listener({
        data: { type: "selection-changed", sender: "another-tab" },
      } as MessageEvent);
    }
    expect(localRefresh).toHaveBeenCalledOnce();

    unsubscribe();
    expect(localSubscriber.closed).toBe(true);
  });
});
