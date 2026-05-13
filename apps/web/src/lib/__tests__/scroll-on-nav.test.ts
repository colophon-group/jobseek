import { describe, it, expect, beforeEach, vi } from "vitest";
import { scrollToTopOnNav } from "../scroll-on-nav";

describe("scrollToTopOnNav", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("scrolls to top when href has no hash", () => {
    const spy = vi.spyOn(window, "scrollTo").mockImplementation(() => {});
    scrollToTopOnNav("/blog");
    expect(spy).toHaveBeenCalledTimes(1);
    expect(spy).toHaveBeenCalledWith({ top: 0, left: 0, behavior: "instant" });
  });

  it("scrolls when href is just a locale-prefixed path", () => {
    const spy = vi.spyOn(window, "scrollTo").mockImplementation(() => {});
    scrollToTopOnNav("/en/blog");
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("does NOT scroll when href contains a hash anchor", () => {
    const spy = vi.spyOn(window, "scrollTo").mockImplementation(() => {});
    scrollToTopOnNav("/#features");
    expect(spy).not.toHaveBeenCalled();
  });

  it("does NOT scroll when href is a bare hash", () => {
    const spy = vi.spyOn(window, "scrollTo").mockImplementation(() => {});
    scrollToTopOnNav("#section");
    expect(spy).not.toHaveBeenCalled();
  });

  it("does NOT scroll when href has hash with query", () => {
    const spy = vi.spyOn(window, "scrollTo").mockImplementation(() => {});
    scrollToTopOnNav("/page?q=x#anchor");
    expect(spy).not.toHaveBeenCalled();
  });
});
