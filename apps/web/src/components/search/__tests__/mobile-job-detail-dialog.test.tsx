import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { useState } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import "@/test-utils/lingui-mock";

vi.mock("@/components/search/job-detail-dialog", () => ({
  JobDetailPanel: ({ onClose, postingId }: { onClose: () => void; postingId: string | null }) => (
    <div data-testid="job-detail-panel" data-posting-id={postingId ?? ""}>
      <button type="button" onClick={onClose}>
        Close job details
      </button>
      <button type="button">Focusable detail action</button>
    </div>
  ),
}));

import { MobileJobDetailDialog } from "../mobile-job-detail-dialog";

const webRoot = resolve(__dirname, "../../../..");

const mobileOverlayCallsites = [
  "app/[lang]/(app)/explore/search-page.tsx",
  "app/[lang]/(app)/company/[slug]/company-page.tsx",
  "src/components/watchlist/watchlist-job-list.tsx",
  "app/[lang]/(app)/my-jobs/my-jobs-page.tsx",
] as const;

function installMatchMedia(matches: boolean) {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    configurable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
}

function DialogHarness() {
  const [postingId, setPostingId] = useState<string | null>("posting-1");

  return (
    <MobileJobDetailDialog
      postingId={postingId}
      onClose={() => setPostingId(null)}
    />
  );
}

function TriggerHarness() {
  const [postingId, setPostingId] = useState<string | null>(null);

  return (
    <>
      <button type="button" onClick={() => setPostingId("posting-1")}>
        Open posting
      </button>
      <button type="button">Outside action</button>
      <MobileJobDetailDialog
        postingId={postingId}
        onClose={() => setPostingId(null)}
      />
    </>
  );
}

describe("MobileJobDetailDialog", () => {
  beforeEach(() => {
    installMatchMedia(true);
  });

  it("renders the mobile job details as a named modal dialog", async () => {
    render(<MobileJobDetailDialog postingId="posting-1" onClose={() => {}} />);

    const dialog = await screen.findByRole("dialog", { name: /job details/i });
    expect(dialog.getAttribute("aria-modal")).toBe("true");
    expect(dialog.getAttribute("aria-labelledby")).toBeTruthy();
    expect(screen.getByTestId("job-detail-panel").getAttribute("data-posting-id")).toBe("posting-1");
  });

  it("does not mount the modal shell outside the mobile breakpoint", async () => {
    installMatchMedia(false);

    render(<MobileJobDetailDialog postingId="posting-1" onClose={() => {}} />);

    await waitFor(() => {
      expect(screen.queryByRole("dialog")).toBeNull();
    });
    expect(screen.queryByTestId("job-detail-panel")).toBeNull();
  });

  it("closes from Escape through Radix dialog behavior", async () => {
    render(<DialogHarness />);

    const dialog = await screen.findByRole("dialog", { name: /job details/i });
    fireEvent.keyDown(dialog, { key: "Escape", code: "Escape" });

    await waitFor(() => {
      expect(screen.queryByRole("dialog")).toBeNull();
    });
  });

  it("keeps tab focus inside the dialog and restores focus after Escape", async () => {
    const user = userEvent.setup();
    render(<TriggerHarness />);

    const opener = screen.getByRole("button", { name: "Open posting" });
    opener.focus();
    expect(document.activeElement).toBe(opener);
    await user.keyboard("{Enter}");

    const dialog = await screen.findByRole("dialog", { name: /job details/i });
    await waitFor(() => {
      expect(dialog.contains(document.activeElement)).toBe(true);
    });

    await user.tab();
    expect(dialog.contains(document.activeElement)).toBe(true);

    await user.keyboard("{Escape}");

    await waitFor(() => {
      expect(screen.queryByRole("dialog")).toBeNull();
    });
    expect(document.activeElement).toBe(opener);
  });
});

describe("mobile job-detail overlay source hygiene (#3161)", () => {
  it("keeps all mobile job-detail call sites on the shared Radix wrapper", () => {
    const sources = mobileOverlayCallsites.map((relPath) => readFileSync(join(webRoot, relPath), "utf8"));

    for (const source of sources) {
      expect(source).not.toContain("fixed inset-0 z-50 bg-black/40 lg:hidden");
      expect(source).toContain("MobileJobDetailDialog");
    }

    expect(
      sources.reduce((count, source) => count + (source.match(/<MobileJobDetailDialog\b/g)?.length ?? 0), 0),
    ).toBe(4);
  });
});
