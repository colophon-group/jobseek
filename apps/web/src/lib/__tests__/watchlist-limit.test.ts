import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

const mocks = vi.hoisted(() => ({ countOwned: vi.fn() }));
vi.mock("@/db", () => ({
  db: {
    select: () => ({ from: () => ({ where: mocks.countOwned }) }),
  },
}));

import { PLAN_LIMITS } from "@/lib/plans";
import {
  createWithinWatchlistLimit,
  canCreateWatchlist,
  MAX_WATCHLISTS_PER_ACCOUNT,
  WatchlistLimitReachedError,
} from "@/lib/watchlist-limit";

class Mutex {
  private tail = Promise.resolve();

  async acquire(): Promise<() => void> {
    const previous = this.tail;
    let release!: () => void;
    this.tail = new Promise<void>((resolve) => {
      release = resolve;
    });
    await previous;
    return release;
  }
}

function accountHarness(initialOwned: number, userId = "user-1") {
  const mutex = new Mutex();
  let owned = initialOwned;

  const transaction = async <T>(
    callback: (tx: unknown) => Promise<T>,
  ): Promise<T> => {
    let release: (() => void) | undefined;
    const tx = {
      execute: async () => {
        release = await mutex.acquire();
        return [];
      },
      select: () => ({
        from: () => ({
          where: async () => [{ value: owned }],
        }),
      }),
    };

    try {
      return await callback(tx);
    } finally {
      release?.();
    }
  };

  const create = () => transaction((tx) =>
    createWithinWatchlistLimit(tx as never, userId, async () => {
      await Promise.resolve();
      owned += 1;
      return owned;
    })
  );

  return {
    create,
    deleteOne: () => {
      owned -= 1;
    },
    owned: () => owned,
  };
}

beforeEach(() => {
  vi.stubEnv("WATCHLIST_LIMIT_EXEMPT_USER_IDS", "");
  mocks.countOwned.mockReset();
});
afterEach(() => vi.unstubAllEnvs());

describe("watchlist ownership policy (#8316)", () => {
  it("uses the same 10-watchlist ceiling for Free and paid plans", () => {
    expect(MAX_WATCHLISTS_PER_ACCOUNT).toBe(10);
    expect(PLAN_LIMITS.free.maxWatchlists).toBe(10);
    expect(PLAN_LIMITS.unlimited.maxWatchlists).toBe(10);
  });

  it("allows the tenth watchlist", async () => {
    const account = accountHarness(9);

    await expect(account.create()).resolves.toBe(10);
    expect(account.owned()).toBe(10);
  });

  it("rejects a create when the account already owns exactly 10", async () => {
    const account = accountHarness(10);

    await expect(account.create()).rejects.toBeInstanceOf(
      WatchlistLimitReachedError,
    );
    expect(account.owned()).toBe(10);
  });

  it("allows creation again after deletion takes the account below 10", async () => {
    const account = accountHarness(10);
    account.deleteOne();

    await expect(account.create()).resolves.toBe(10);
    expect(account.owned()).toBe(10);
  });

  it("serializes concurrent attempts so only one can claim the tenth slot", async () => {
    const account = accountHarness(9);

    const results = await Promise.allSettled([
      account.create(),
      account.create(),
    ]);

    expect(results.filter((result) => result.status === "fulfilled")).toHaveLength(1);
    const rejection = results.find((result) => result.status === "rejected");
    expect(rejection).toMatchObject({
      status: "rejected",
      reason: expect.any(WatchlistLimitReachedError),
    });
    expect(account.owned()).toBe(10);
  });

  it("does not mutate a grandfathered 16-watchlist account", async () => {
    const account = accountHarness(16);

    await expect(account.create()).rejects.toBeInstanceOf(
      WatchlistLimitReachedError,
    );
    expect(account.owned()).toBe(16);
  });

  it("restores creation after a grandfathered account deletes below the cap", async () => {
    const account = accountHarness(16);
    for (let index = 0; index < 7; index += 1) account.deleteOne();

    expect(account.owned()).toBe(9);
    await expect(account.create()).resolves.toBe(10);
  });

  it("allows an explicitly exempt account to keep creating above the cap", async () => {
    vi.stubEnv("WATCHLIST_LIMIT_EXEMPT_USER_IDS", " other-user, user-1, ");
    const account = accountHarness(16);

    await expect(account.create()).resolves.toBe(17);
    await expect(account.create()).resolves.toBe(18);
    mocks.countOwned.mockResolvedValue([{ value: 18 }]);
    await expect(canCreateWatchlist("user-1")).resolves.toEqual({
      allowed: true, current: 18, max: null,
    });
  });

  it.each(["", " , , ", "user-10", "prefix-user-1", "USER-1", "*"])(
    "retains the cap unless the exact account ID is listed (%s)",
    async (exemptIds) => {
      vi.stubEnv("WATCHLIST_LIMIT_EXEMPT_USER_IDS", exemptIds);
      await expect(accountHarness(10).create()).rejects.toBeInstanceOf(
        WatchlistLimitReachedError,
      );
      mocks.countOwned.mockResolvedValue([{ value: 10 }]);
      await expect(canCreateWatchlist("user-1")).resolves.toEqual({
        allowed: false, current: 10, max: 10,
      });
    },
  );

  it("revoking an exemption blocks further creation without deleting lists", async () => {
    vi.stubEnv("WATCHLIST_LIMIT_EXEMPT_USER_IDS", "user-1");
    const account = accountHarness(10);
    await account.create();
    vi.stubEnv("WATCHLIST_LIMIT_EXEMPT_USER_IDS", "other-user");
    await expect(account.create()).rejects.toBeInstanceOf(WatchlistLimitReachedError);
    expect(account.owned()).toBe(11);
  });
});
