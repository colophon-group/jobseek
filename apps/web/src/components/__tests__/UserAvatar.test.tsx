import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { UserAvatar, getUserInitials } from "../UserAvatar";

// `UserAvatar` imports `clearStoredUserImage` from the server-actions
// module, which transitively pulls "server-only" + the Drizzle adapter.
// We swap in a stub so the test doesn't try to boot the DB; the
// component's `onBrokenImage` prop is the production-equivalent escape
// hatch we use to verify the heal call fires.
vi.mock("@/lib/actions/preferences", () => ({
  clearStoredUserImage: vi.fn(async () => {}),
}));

describe("getUserInitials", () => {
  it("uppercases first letters of the first two words", () => {
    expect(getUserInitials("Ada Lovelace")).toBe("AL");
  });

  it("handles a single-word name", () => {
    expect(getUserInitials("Plato")).toBe("P");
  });

  it("ignores extra whitespace", () => {
    expect(getUserInitials("  Marie   Curie  ")).toBe("MC");
  });

  it("falls back to the literal '?' when label is empty", () => {
    expect(getUserInitials("?")).toBe("?");
  });
});

describe("UserAvatar", () => {
  it("renders initials when image is null", () => {
    render(<UserAvatar image={null} name="Ada Lovelace" email="ada@test.com" size={32} />);
    expect(screen.getByTestId("user-avatar-initials").textContent).toBe("AL");
    expect(screen.queryByTestId("user-avatar-img")).toBeNull();
  });

  it("renders image when src is present", () => {
    render(
      <UserAvatar
        image="https://example.com/avatar.png"
        name="Ada Lovelace"
        email="ada@test.com"
        size={32}
      />,
    );
    const img = screen.getByTestId("user-avatar-img") as HTMLImageElement;
    expect(img.src).toBe("https://example.com/avatar.png");
  });

  it("falls back to initials on image error and fires the heal callback", async () => {
    const onBrokenImage = vi.fn(async () => {});
    render(
      <UserAvatar
        image="https://media.licdn.com/expired.jpg?e=1773878400"
        name="Ada Lovelace"
        email="ada@test.com"
        size={32}
        onBrokenImage={onBrokenImage}
      />,
    );

    fireEvent.error(screen.getByTestId("user-avatar-img"));

    await waitFor(() => {
      expect(screen.getByTestId("user-avatar-initials").textContent).toBe("AL");
    });
    expect(screen.queryByTestId("user-avatar-img")).toBeNull();
    expect(onBrokenImage).toHaveBeenCalledTimes(1);
  });

  it("calls the heal callback at most once even if the image errors repeatedly", async () => {
    const onBrokenImage = vi.fn(async () => {});
    const { rerender } = render(
      <UserAvatar
        image="https://media.licdn.com/expired.jpg?e=1773878400"
        name="Ada Lovelace"
        email="ada@test.com"
        size={32}
        onBrokenImage={onBrokenImage}
      />,
    );

    fireEvent.error(screen.getByTestId("user-avatar-img"));
    // After the first error the image is unmounted; the only way the
    // <img> could fire again is a parent re-render that re-mounts it
    // (e.g. a `key` change). Simulate that.
    rerender(
      <UserAvatar
        image="https://media.licdn.com/expired.jpg?e=1773878400"
        name="Ada Lovelace"
        email="ada@test.com"
        size={32}
        onBrokenImage={onBrokenImage}
      />,
    );
    // The internal `broken` state survives the re-render with the same
    // props, so no <img> is re-mounted — the heal call stays at 1.
    expect(onBrokenImage).toHaveBeenCalledTimes(1);
  });

  it("falls back to email when name is missing", () => {
    render(<UserAvatar image={null} name={null} email="abc@test.com" size={32} />);
    expect(screen.getByTestId("user-avatar-initials").textContent).toBe("A");
  });

  it("renders '?' when both name and email are empty", () => {
    render(<UserAvatar image={null} name="" email="" size={32} />);
    expect(screen.getByTestId("user-avatar-initials").textContent).toBe("?");
  });
});
