import { describe, it, expect, vi } from "vitest";
import { renderHook } from "@testing-library/react";

vi.mock("next/navigation", () => ({
  useParams: () => ({ lang: "de" }),
}));

import { useLocalePath } from "../useLocalePath";

describe("useLocalePath", () => {
  it("prefixes absolute paths with locale", () => {
    const { result } = renderHook(() => useLocalePath());
    expect(result.current("/features")).toBe("/de/features");
  });

  it("leaves external http URLs unchanged", () => {
    const { result } = renderHook(() => useLocalePath());
    expect(result.current("http://external.com")).toBe("http://external.com");
  });

  it("leaves https URLs unchanged", () => {
    const { result } = renderHook(() => useLocalePath());
    expect(result.current("https://example.com")).toBe("https://example.com");
  });

  it("leaves mailto: URLs unchanged", () => {
    const { result } = renderHook(() => useLocalePath());
    expect(result.current("mailto:x@test.com")).toBe("mailto:x@test.com");
  });

  it("does not double-prefix already-prefixed paths", () => {
    const { result } = renderHook(() => useLocalePath());
    expect(result.current("/de/features")).toBe("/de/features");
  });

  it("handles bare locale path", () => {
    const { result } = renderHook(() => useLocalePath());
    expect(result.current("/de")).toBe("/de");
  });

  it("prefixes relative paths with locale", () => {
    const { result } = renderHook(() => useLocalePath());
    expect(result.current("relative")).toBe("/de/relative");
  });

  it("handles hash-only paths", () => {
    const { result } = renderHook(() => useLocalePath());
    expect(result.current("/#features")).toBe("/de/#features");
  });
});
