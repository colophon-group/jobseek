import { describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

const mocks = vi.hoisted(() => ({ transaction: vi.fn() }));

vi.mock("@/db", () => ({ db: { transaction: mocks.transaction } }));

import { PostgresAiFilterExecutionRepository } from "./postgres-repository";

describe("AI filter paid reservation authorization", () => {
  it("checks authorization in the database for every owner", async () => {
    const repository = new PostgresAiFilterExecutionRepository({
      user: null,
      project: null,
    });
    const input = {
      context: { ownerId: "otherOwner12345678" },
    } as Parameters<typeof repository.reserveBudget>[0];

    await repository.reserveBudget(input);
    expect(mocks.transaction).toHaveBeenCalledOnce();
  });
});
