/** @vitest-environment happy-dom */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook } from "@testing-library/react";
import { useClearTypesenseOnAuthChange } from "../use-clear-typesense-on-auth-change";
import * as keyModule from "../typesense-browser-key";

describe("useClearTypesenseOnAuthChange", () => {
  let clearSpy: ReturnType<typeof vi.spyOn>;
  const ORIGINAL_FLAG = process.env.NEXT_PUBLIC_TYPESENSE_DIRECT;

  beforeEach(() => {
    process.env.NEXT_PUBLIC_TYPESENSE_DIRECT = "1";
    clearSpy = vi.spyOn(keyModule, "clearTypesenseBrowserConfig").mockImplementation(() => {});
  });

  afterEach(() => {
    clearSpy.mockRestore();
    if (ORIGINAL_FLAG === undefined) {
      delete process.env.NEXT_PUBLIC_TYPESENSE_DIRECT;
    } else {
      process.env.NEXT_PUBLIC_TYPESENSE_DIRECT = ORIGINAL_FLAG;
    }
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
    process.env.NEXT_PUBLIC_TYPESENSE_DIRECT = "0";
    const { rerender } = renderHook(
      (p: boolean) => useClearTypesenseOnAuthChange(p),
      { initialProps: false },
    );
    rerender(true);
    expect(clearSpy).not.toHaveBeenCalled();
  });
});
