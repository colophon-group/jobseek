import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { useLatest, useLatestState } from "../use-latest";

describe("useLatest", () => {
  it("tracks the latest rendered value", () => {
    const seen: number[] = [];

    function Harness({ value }: { value: number }) {
      const latest = useLatest(value);
      seen.push(latest.current);
      return null;
    }

    const { rerender } = render(<Harness value={1} />);
    rerender(<Harness value={2} />);

    expect(seen).toEqual([1, 2]);
  });
});

describe("useLatestState", () => {
  it("updates the latest ref synchronously when the setter runs", () => {
    const observed: number[] = [];

    function Harness() {
      const [count, setCount, countRef] = useLatestState(0);
      return (
        <button
          type="button"
          onClick={() => {
            setCount((current) => current + 1);
            observed.push(countRef.current);
          }}
        >
          {count}
        </button>
      );
    }

    render(<Harness />);

    fireEvent.click(screen.getByRole("button"));

    expect(observed).toEqual([1]);
    expect(screen.getByRole("button").textContent).toBe("1");
  });
});
