import { readFileSync } from "node:fs";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import "@/test-utils/lingui-mock";
import type { InterviewEntry } from "@/lib/actions/my-jobs-types";
import { InterviewList } from "../interview-list";

const interview: InterviewEntry = {
  id: "interview-1",
  round: 1,
  type: "interview",
  scheduledAt: null,
  createdAt: "2026-07-22T00:00:00.000Z",
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function renderList(overrides: Partial<{
  onAdd: (type: InterviewEntry["type"]) => Promise<boolean>;
  onUpdate: (id: string, updates: { type?: InterviewEntry["type"]; scheduledAt?: string | null }) => Promise<boolean>;
  onDelete: (id: string) => Promise<boolean>;
}> = {}) {
  const props = {
    onAdd: vi.fn(async () => true),
    onUpdate: vi.fn(async () => true),
    onDelete: vi.fn(async () => true),
    ...overrides,
  };
  render(<InterviewList interviews={[interview]} {...props} />);
  return props;
}

async function openEditor(user: ReturnType<typeof userEvent.setup>) {
  const trigger = screen.getByRole("button", { name: "#1 Interview" });
  await user.click(trigger);
  const menu = await screen.findByRole("menu");
  return { trigger, menu };
}

async function openDeleteDialog(user: ReturnType<typeof userEvent.setup>) {
  const { trigger, menu } = await openEditor(user);
  await user.click(within(menu).getByRole("menuitem", { name: "Delete" }));
  const dialog = await screen.findByRole("alertdialog", { name: "Delete interview?" });
  return { trigger, dialog };
}

describe("InterviewList", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("uses a collision-aware semantic menu that closes on Escape and restores trigger focus", async () => {
    const user = userEvent.setup();
    renderList();

    const { trigger, menu } = await openEditor(user);
    expect(trigger.getAttribute("aria-expanded")).toBe("true");
    expect(within(menu).getByRole("menuitemradio", { name: "Other" })).toBeTruthy();
    expect(menu.className).toContain("--radix-dropdown-menu-content-available-height");

    await user.keyboard("{Escape}");

    await waitFor(() => {
      expect(screen.queryByRole("menu")).toBeNull();
      expect(document.activeElement).toBe(trigger);
    });
  });

  it("does not retain the hand-rolled document mouse listener", () => {
    const source = readFileSync("src/components/my-jobs/interview-list.tsx", "utf8");
    expect(source).toContain("<DropdownMenu.Portal>");
    expect(source).toContain("collisionPadding={12}");
    expect(source).not.toContain('document.addEventListener("mousedown"');
  });

  it("guards Add interview while pending and announces success", async () => {
    const user = userEvent.setup();
    const pending = deferred<boolean>();
    const onAdd = vi.fn(() => pending.promise);
    renderList({ onAdd });

    await user.click(screen.getByRole("button", { name: "Add interview" }));
    const adding = screen.getByRole("button", { name: "Adding…" });
    expect(adding).toHaveProperty("disabled", true);
    await user.click(adding);
    expect(onAdd).toHaveBeenCalledTimes(1);

    pending.resolve(true);
    await waitFor(() => {
      expect(screen.getByRole("status").textContent).toBe("Interview added.");
      expect(screen.getByRole("button", { name: "Add interview" })).toHaveProperty("disabled", false);
    });
  });

  it("surfaces an actionable Add interview failure", async () => {
    const user = userEvent.setup();
    renderList({ onAdd: vi.fn(async () => false) });

    await user.click(screen.getByRole("button", { name: "Add interview" }));

    expect((await screen.findByRole("alert")).textContent).toBe("Couldn't add the interview. Try again.");
  });

  it("updates an interview type through the semantic radio menu", async () => {
    const user = userEvent.setup();
    const onUpdate = vi.fn(async () => true);
    renderList({ onUpdate });

    const { menu } = await openEditor(user);
    await user.click(within(menu).getByRole("menuitemradio", { name: "Video Call" }));

    await waitFor(() => {
      expect(onUpdate).toHaveBeenCalledWith("interview-1", { type: "video_call" });
      expect(screen.getByRole("status").textContent).toBe("Interview updated.");
    });
  });

  it("opens a labelled delete confirmation with safe focus and restores the row on Cancel", async () => {
    const user = userEvent.setup();
    const props = renderList();

    const { trigger, dialog } = await openDeleteDialog(user);
    const cancel = within(dialog).getByRole("button", { name: "Cancel" });
    await waitFor(() => expect(document.activeElement).toBe(cancel));

    await user.click(cancel);

    await waitFor(() => {
      expect(screen.queryByRole("alertdialog")).toBeNull();
      expect(document.activeElement).toBe(trigger);
    });
    expect(props.onDelete).not.toHaveBeenCalled();
  });

  it("restores the interview-row trigger after closing the confirmation with Escape", async () => {
    const user = userEvent.setup();
    renderList();

    const { trigger } = await openDeleteDialog(user);
    await user.keyboard("{Escape}");

    await waitFor(() => {
      expect(screen.queryByRole("alertdialog")).toBeNull();
      expect(document.activeElement).toBe(trigger);
    });
  });

  it("deletes only after explicit confirmation and announces success", async () => {
    const user = userEvent.setup();
    const onDelete = vi.fn(async () => true);
    renderList({ onDelete });

    const { dialog } = await openDeleteDialog(user);
    await user.click(within(dialog).getByRole("button", { name: "Delete" }));

    await waitFor(() => {
      expect(onDelete).toHaveBeenCalledWith("interview-1");
      expect(screen.queryByRole("alertdialog")).toBeNull();
      expect(screen.getByRole("status").textContent).toBe("Interview deleted.");
    });
  });

  it("deletes only after confirmation and keeps failures inside the dialog", async () => {
    const user = userEvent.setup();
    const onDelete = vi.fn(async () => false);
    renderList({ onDelete });

    const { dialog } = await openDeleteDialog(user);
    await user.click(within(dialog).getByRole("button", { name: "Delete" }));

    await waitFor(() => {
      expect(onDelete).toHaveBeenCalledWith("interview-1");
      expect(within(dialog).getByRole("alert").textContent).toBe("Couldn't delete the interview. Try again.");
      expect(screen.getByRole("alertdialog", { name: "Delete interview?" })).toBeTruthy();
    });
  });
});
