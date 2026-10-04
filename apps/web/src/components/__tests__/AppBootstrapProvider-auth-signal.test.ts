import { expect, it, vi } from "vitest";
import { createAuthClient } from "better-auth/react";

it("listening to the installed SDK mutation signal adds zero session requests", async () => {
  const request = vi.fn();
  const client = createAuthClient({ baseURL: "https://example.test", fetchOptions: { customFetchImpl: request } });
  const invalidated = vi.fn();
  const stop = client.$store.atoms.$sessionSignal.listen(invalidated);
  client.$store.notify("$sessionSignal");
  await new Promise((resolve) => setTimeout(resolve, 10));
  expect(invalidated).toHaveBeenCalledTimes(1);
  expect(request).not.toHaveBeenCalled();
  stop();
  client.$store.notify("$sessionSignal");
  expect(invalidated).toHaveBeenCalledTimes(1);
});
