import { describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

const mocks = vi.hoisted(() => ({ transaction: vi.fn() }));

vi.mock("@/db", () => ({ db: { transaction: mocks.transaction } }));

import {
  AiFilterAuthorizationError,
  PostgresAiFilterExecutionRepository,
} from "./postgres-repository";

describe("AI filter paid reservation authorization", () => {
  it("rejects owners outside the pilot before touching the database", async () => {
    const repository = new PostgresAiFilterExecutionRepository({
      user: null,
      project: null,
      pilotUserIds: ["pilotOwner12345678"],
    });
    const input = {
      context: { ownerId: "otherOwner12345678" },
    } as Parameters<typeof repository.reserveBudget>[0];

    await expect(repository.reserveBudget(input)).rejects.toBeInstanceOf(
      AiFilterAuthorizationError,
    );
    expect(mocks.transaction).not.toHaveBeenCalled();
  });
});
