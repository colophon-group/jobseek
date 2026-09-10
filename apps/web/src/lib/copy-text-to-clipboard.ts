/** Copy text with a selection fallback for embedded browsers and Safari. */
export async function copyTextToClipboard(value: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(value);
      return;
    } catch {
      // Some embedded browsers expose Clipboard.writeText but reject it.
    }
  }

  const textarea = document.createElement("textarea");
  textarea.value = value;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  const previouslyFocused = document.activeElement instanceof HTMLElement
    ? document.activeElement
    : null;
  document.body.appendChild(textarea);
  try {
    textarea.select();
    const copied = document.execCommand("copy");
    if (!copied) throw new Error("Clipboard unavailable");
  } finally {
    textarea.remove();
    if (previouslyFocused?.isConnected) {
      try {
        previouslyFocused.focus({ preventScroll: true });
      } catch {
        try {
          previouslyFocused.focus();
        } catch {
          // Focus restoration is best effort on legacy embedded browsers.
        }
      }
    }
  }
}
