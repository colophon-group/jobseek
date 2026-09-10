import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { copyTextToClipboard } from "../copy-text-to-clipboard";

const originalClipboard = Object.getOwnPropertyDescriptor(navigator, "clipboard");
const originalExecCommand = Object.getOwnPropertyDescriptor(document, "execCommand");

function setClipboard(writeText?: (value: string) => Promise<void>) {
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: writeText ? { writeText } : undefined,
  });
}

function setExecCommand(copy: () => boolean) {
  Object.defineProperty(document, "execCommand", {
    configurable: true,
    value: copy,
  });
}

describe("copyTextToClipboard", () => {
  beforeEach(() => {
    document.body.replaceChildren();
  });

  afterEach(() => {
    document.body.replaceChildren();
    if (originalClipboard) {
      Object.defineProperty(navigator, "clipboard", originalClipboard);
    } else {
      Reflect.deleteProperty(navigator, "clipboard");
    }
    if (originalExecCommand) {
      Object.defineProperty(document, "execCommand", originalExecCommand);
    } else {
      Reflect.deleteProperty(document, "execCommand");
    }
    vi.restoreAllMocks();
  });

  it("uses the native clipboard without creating a fallback textarea", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    setClipboard(writeText);

    await copyTextToClipboard("https://example.test/watchlists/one");

    expect(writeText).toHaveBeenCalledWith("https://example.test/watchlists/one");
    expect(document.querySelector("textarea")).toBeNull();
  });

  it("cleans up the successful fallback and restores focus", async () => {
    setClipboard(vi.fn().mockRejectedValue(new Error("denied")));
    const execCommand = vi.fn(() => true);
    setExecCommand(execCommand);
    const trigger = document.createElement("button");
    document.body.appendChild(trigger);
    trigger.focus();

    await copyTextToClipboard("https://example.test/watchlists/two");

    expect(execCommand).toHaveBeenCalledWith("copy");
    expect(document.querySelector("textarea")).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  it("cleans up and restores focus when the fallback throws", async () => {
    setClipboard();
    setExecCommand(() => {
      throw new Error("legacy copy failed");
    });
    const trigger = document.createElement("button");
    document.body.appendChild(trigger);
    trigger.focus();

    await expect(copyTextToClipboard("https://example.test/watchlists/three"))
      .rejects.toThrow("legacy copy failed");

    expect(document.querySelector("textarea")).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  it("cleans up when the fallback reports that it could not copy", async () => {
    setClipboard();
    setExecCommand(() => false);

    await expect(copyTextToClipboard("https://example.test/watchlists/four"))
      .rejects.toThrow("Clipboard unavailable");

    expect(document.querySelector("textarea")).toBeNull();
  });
});
