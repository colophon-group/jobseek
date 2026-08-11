import { describe, expect, it, vi } from "vitest";
import {
  parseOptions,
  withRetry,
} from "../../../../script/prewarm-company-og-cache";

describe("company OG prewarm CLI", () => {
  it("parses bounded canary options passed through pnpm", () => {
    expect(parseOptions([
      "--",
      "--yes",
      "--concurrency",
      "3",
      "--max-companies",
      "25",
      "--locales",
      "en,de",
    ])).toEqual({
      concurrency: 3,
      force: false,
      locales: ["en", "de"],
      maxCompanies: 25,
      rendererVersion: null,
      yes: true,
    });
  });

  it("rejects invalid concurrency before touching external services", () => {
    expect(() => parseOptions(["--concurrency", "0"])).toThrow(
      "--concurrency must be a positive integer",
    );
  });

  it("retries an upload failure and surfaces the terminal error", async () => {
    const failure = new Error("simulated R2 failure");
    const operation = vi.fn().mockRejectedValue(failure);

    await expect(withRetry(operation, 0)).rejects.toBe(failure);
    expect(operation).toHaveBeenCalledTimes(3);
  });

  it("returns after the first successful upload attempt", async () => {
    const operation = vi.fn().mockResolvedValue("uploaded");

    await expect(withRetry(operation, 0)).resolves.toBe("uploaded");
    expect(operation).toHaveBeenCalledTimes(1);
  });
});
