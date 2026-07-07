"use client";

import {
  useCallback,
  useRef,
  useState,
  type Dispatch,
  type MutableRefObject,
  type SetStateAction,
} from "react";

export function useLatest<T>(value: T): MutableRefObject<T> {
  const ref = useRef(value);
  ref.current = value;
  return ref;
}

export function useLatestState<T>(
  initialState: T | (() => T),
): readonly [T, Dispatch<SetStateAction<T>>, MutableRefObject<T>] {
  const [state, setReactState] = useState<T>(initialState);
  const ref = useLatest(state);

  const setState = useCallback((next: SetStateAction<T>) => {
    const value =
      typeof next === "function"
        ? (next as (previous: T) => T)(ref.current)
        : next;
    ref.current = value;
    setReactState(value);
  }, [ref]);

  return [state, setState, ref] as const;
}
