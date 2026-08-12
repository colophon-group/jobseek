"use client";

import { Trans } from "@lingui/react/macro";
import { type MouseEvent, useEffect } from "react";

const SKIP_LINK_CLASS =
  "fixed top-2 left-2 z-[100] -translate-y-16 rounded-md bg-primary px-4 py-2 text-sm font-semibold text-primary-contrast focus:translate-y-0 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary";
const MAIN_CONTENT_ID = "main-content";
const MAIN_CONTENT_CANDIDATE_ATTRIBUTE = "data-main-content-candidate";
const MAIN_CONTENT_CANDIDATE_SELECTOR =
  `#${MAIN_CONTENT_ID},[${MAIN_CONTENT_CANDIDATE_ATTRIBUTE}]`;

function isAccessibilityHidden(candidate: HTMLElement) {
  let current: Element | null = candidate;

  while (current) {
    if (
      current.hasAttribute("hidden") ||
      current.hasAttribute("inert") ||
      current.getAttribute("aria-hidden")?.toLowerCase() === "true"
    ) {
      return true;
    }
    current = current.parentElement;
  }

  return false;
}

function reconcileMainContentTarget() {
  const candidates = Array.from(
    document.querySelectorAll<HTMLElement>(MAIN_CONTENT_CANDIDATE_SELECTOR),
  );

  // Remember every copy independently of the canonical id. If Next later
  // promotes a hidden PPR tree, React may not restore an id removed outside
  // its render cycle because the JSX prop itself did not change.
  for (const candidate of candidates) {
    candidate.setAttribute(MAIN_CONTENT_CANDIDATE_ATTRIBUTE, "");
  }

  const target = candidates.find(
    (candidate) =>
      !isAccessibilityHidden(candidate) &&
      candidate.getClientRects().length > 0,
  );

  if (!target) return null;

  // A streamed Next.js route can retain its prerendered copy inside a hidden
  // Suspense container after the visible copy has hydrated. React renders the
  // same fragment id into both copies, so native `#main-content` lookup would
  // otherwise resolve the hidden one. Keep the visible landmark authoritative
  // without removing the fallback before a rendered target exists.
  for (const candidate of candidates) {
    if (candidate === target) {
      if (candidate.id !== MAIN_CONTENT_ID) candidate.id = MAIN_CONTENT_ID;
    } else if (candidate.id === MAIN_CONTENT_ID) {
      candidate.removeAttribute("id");
    }
  }

  return target;
}

function containsMainContentTarget(node: Node) {
  return (
    node instanceof Element &&
    (node.matches(MAIN_CONTENT_CANDIDATE_SELECTOR) ||
      node.querySelector(MAIN_CONTENT_CANDIDATE_SELECTOR) !== null)
  );
}

function mainContentTargetMayHaveChanged(mutations: MutationRecord[]) {
  return mutations.some((mutation) => {
    if (mutation.type === "attributes") {
      if (!(mutation.target instanceof Element)) return false;

      if (mutation.attributeName === "id") {
        return mutation.target.matches(MAIN_CONTENT_CANDIDATE_SELECTOR);
      }

      return containsMainContentTarget(mutation.target);
    }

    return [...mutation.addedNodes, ...mutation.removedNodes].some(
      containsMainContentTarget,
    );
  });
}

function focusVisibleMain(event: MouseEvent<HTMLAnchorElement>) {
  const target = reconcileMainContentTarget();

  // Keep the native fragment fallback when the streamed page has not exposed
  // a visible target yet. Once hydrated, select the rendered copy explicitly:
  // React/Next may retain an earlier duplicate inside a display:none Suspense
  // container, which native fragment navigation otherwise resolves first.
  if (!target) return;

  event.preventDefault();
  if (!target.hasAttribute("tabindex")) target.tabIndex = -1;
  target.focus({ preventScroll: true });
  target.scrollIntoView({ block: "start" });

  const url = new URL(window.location.href);
  url.hash = MAIN_CONTENT_ID;
  if (window.location.hash === url.hash) {
    window.history.replaceState(null, "", url);
  } else {
    window.history.pushState(null, "", url);
  }
}

export function SkipToContentLink() {
  useEffect(() => {
    reconcileMainContentTarget();

    // Public/app layouts persist across client navigation while Next streams
    // replacement route segments underneath them. Reconcile additions and
    // hidden-state transitions so a later route cannot reintroduce a hidden
    // duplicate fragment target.
    const observer = new MutationObserver((mutations) => {
      // Search results and other dynamic app surfaces mutate frequently. Only
      // run the geometry check when a mutation can actually affect the skip
      // target instead of forcing layout for every subtree update.
      if (mainContentTargetMayHaveChanged(mutations)) {
        reconcileMainContentTarget();
      }
    });
    observer.observe(document.body, {
      attributes: true,
      attributeFilter: ["aria-hidden", "hidden", "id", "inert"],
      childList: true,
      subtree: true,
    });

    return () => observer.disconnect();
  }, []);

  return (
    <a
      href="#main-content"
      className={SKIP_LINK_CLASS}
      onClick={focusVisibleMain}
    >
      <Trans id="common.a11y.skipToContent" comment="Skip to main content link for keyboard users">
        Skip to content
      </Trans>
    </a>
  );
}
