/** @vitest-environment happy-dom */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook } from "@testing-library/react";
import { setTestEnv, withTestEnv } from "@/test-utils/env";
import { useClearTypesenseOnAuthChange } from "../use-clear-typesense-on-auth-change";
import * as keyModule from "../typesense-browser-key";

describe("useClearTypesenseOnAuthChange", () => {
  let clearSpy: ReturnType<typeof vi.spyOn>;

  withTestEnv({ NEXT_PUBLIC_TYPESENSE_DIRECT: "1" });

  beforeEach(() => {
    clearSpy = vi.spyOn(keyModule, "clearTypesenseBrowserConfig").mockImplementation(() => {});
  });

  afterEach(() => {
    clearSpy.mockRestore();
  });

  it("does NOT clear on first mount (would waste a key fetch on every soft nav)", () => {
    renderHook((p: boolean) => useClearTypesenseOnAuthChange(p), {
      initialProps: false,
    });
    expect(clearSpy).not.toHaveBeenCalled();
  });

  it("clears when isLoggedIn flips from false to true", () => {
    const { rerender } = renderHook(
      (p: boolean) => useClearTypesenseOnAuthChange(p),
      { initialProps: false },
    );
    expect(clearSpy).not.toHaveBeenCalled();
    rerender(true);
    expect(clearSpy).toHaveBeenCalledTimes(1);
  });

  it("clears when isLoggedIn flips from true to false (sign-out)", () => {
    const { rerender } = renderHook(
      (p: boolean) => useClearTypesenseOnAuthChange(p),
      { initialProps: true },
    );
    expect(clearSpy).not.toHaveBeenCalled();
    rerender(false);
    expect(clearSpy).toHaveBeenCalledTimes(1);
  });

  it("does not re-clear on identical re-renders", () => {
    const { rerender } = renderHook(
      (p: boolean) => useClearTypesenseOnAuthChange(p),
      { initialProps: true },
    );
    rerender(true);
    rerender(true);
    rerender(true);
    expect(clearSpy).not.toHaveBeenCalled();
  });

  it("no-ops when feature flag is off, even on auth flip", () => {
    setTestEnv({ NEXT_PUBLIC_TYPESENSE_DIRECT: "0" });
    const { rerender } = renderHook(
      (p: boolean) => useClearTypesenseOnAuthChange(p),
      { initialProps: false },
    );
    rerender(true);
    expect(clearSpy).not.toHaveBeenCalled();
  });
});
