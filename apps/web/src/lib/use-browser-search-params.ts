"use client";

import { useMemo, useSyncExternalStore } from "react";

const URL_CHANGE_EVENT = "jseek:urlchange";
const HISTORY_PATCH_MARKER = Symbol.for("jseek.history-urlchange-patched");

type PatchedWindow = Window & {
  [HISTORY_PATCH_MARKER]?: boolean;
};

function notifyUrlChange() {
  window.dispatchEvent(new Event(URL_CHANGE_EVENT));
}

/**
 * Make browser-native History API writes observable without importing
 * `useSearchParams`. Next.js calls `pushState`/`replaceState` for client
 * navigation, while Explore also writes filters through `replaceState`
 * directly. Wrapping the installed methods once lets both paths share the
 * same subscription and preserves Next's own patched implementations.
 */
function installHistoryObserver() {
  const target = window as PatchedWindow;
  if (target[HISTORY_PATCH_MARKER]) return;
  target[HISTORY_PATCH_MARKER] = true;

  const pushState = window.history.pushState;
  const replaceState = window.history.replaceState;

  window.history.pushState = function pushStateAndNotify(...args) {
    pushState.apply(this, args);
    notifyUrlChange();
  };
  window.history.replaceState = function replaceStateAndNotify(...args) {
    replaceState.apply(this, args);
    notifyUrlChange();
  };
}

function subscribe(onStoreChange: () => void) {
  installHistoryObserver();
  window.addEventListener("popstate", onStoreChange);
  window.addEventListener(URL_CHANGE_EVENT, onStoreChange);
  return () => {
    window.removeEventListener("popstate", onStoreChange);
    window.removeEventListener(URL_CHANGE_EVENT, onStoreChange);
  };
}

function getSnapshot() {
  return window.location.search;
}

function getServerSnapshot() {
  return "";
}

/**
 * Hydration-safe browser query state for cached client trees.
 *
 * `next/navigation::useSearchParams` intentionally suspends static output.
 * That is correct for request-specific server rendering, but Explore's HTML
 * shell is deliberately query-agnostic and restores filters after hydration.
 * Returning an empty server snapshot keeps the cached result markup in raw
 * HTML; `useSyncExternalStore` then publishes the real browser query after
 * hydration and on push/replace/back/forward navigation.
 */
export function useBrowserSearchParams(): URLSearchParams {
  const search = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
  return useMemo(() => new URLSearchParams(search), [search]);
}
