import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ExcludeTitlePills } from "@/components/search/exclude-title-pills";

vi.mock("@lingui/react/macro", () => ({
  useLingui: () => ({ t: (opts: { message: string }) => opts.message }),
}));

describe("ExcludeTitlePills", () => {
  it("renders existing keywords as dismissable chips", () => {
    render(
      <ExcludeTitlePills
        keywords={["senior", "staff"]}
        onAdd={() => {}}
        onRemove={() => {}}
      />,
    );
    expect(screen.getByText("senior")).toBeDefined();
    expect(screen.getByText("staff")).toBeDefined();
  });

  it("calls onRemove with the keyword when its × button is clicked", () => {
    const onRemove = vi.fn();
    render(
      <ExcludeTitlePills
        keywords={["senior"]}
        onAdd={() => {}}
        onRemove={onRemove}
      />,
    );
    fireEvent.click(screen.getByLabelText(/remove/i));
    expect(onRemove).toHaveBeenCalledWith("senior");
  });

  it("calls onAdd with trimmed input on form submit", () => {
    const onAdd = vi.fn();
    render(
      <ExcludeTitlePills keywords={[]} onAdd={onAdd} onRemove={() => {}} />,
    );
    const input = screen.getByRole("textbox") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "  principal  " } });
    fireEvent.submit(input.closest("form")!);
    expect(onAdd).toHaveBeenCalledWith("principal");
    expect(input.value).toBe("");
  });

  it("does not call onAdd for empty input", () => {
    const onAdd = vi.fn();
    render(
      <ExcludeTitlePills keywords={[]} onAdd={onAdd} onRemove={() => {}} />,
    );
    const input = screen.getByRole("textbox") as HTMLInputElement;
    fireEvent.submit(input.closest("form")!);
    expect(onAdd).not.toHaveBeenCalled();
  });

  it("does not call onAdd for case-insensitive duplicates", () => {
    const onAdd = vi.fn();
    render(
      <ExcludeTitlePills
        keywords={["Senior"]}
        onAdd={onAdd}
        onRemove={() => {}}
      />,
    );
    const input = screen.getByRole("textbox") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "SENIOR" } });
    fireEvent.submit(input.closest("form")!);
    expect(onAdd).not.toHaveBeenCalled();
  });
});
