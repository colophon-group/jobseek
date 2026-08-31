const WATCHLIST_SELECTION_CHANNEL = "jobseek-watchlist-selection";
const WATCHLIST_SELECTION_EVENT = "selection-changed";

const tabId = typeof crypto !== "undefined" && "randomUUID" in crypto
  ? crypto.randomUUID()
  : Math.random().toString(36).slice(2);

type SelectionMessage = {
  type: typeof WATCHLIST_SELECTION_EVENT;
  sender: string;
};

export function broadcastWatchlistSelectionChanged(): void {
  if (typeof BroadcastChannel === "undefined") return;
  const channel = new BroadcastChannel(WATCHLIST_SELECTION_CHANNEL);
  channel.postMessage({ type: WATCHLIST_SELECTION_EVENT, sender: tabId });
  channel.close();
}

export function subscribeToWatchlistSelection(
  onExternalChange: () => void,
): () => void {
  if (typeof BroadcastChannel === "undefined") return () => {};
  const channel = new BroadcastChannel(WATCHLIST_SELECTION_CHANNEL);
  const listener = (event: MessageEvent<SelectionMessage>) => {
    if (
      event.data?.type === WATCHLIST_SELECTION_EVENT &&
      event.data.sender !== tabId
    ) {
      onExternalChange();
    }
  };
  channel.addEventListener("message", listener);
  return () => {
    channel.removeEventListener("message", listener);
    channel.close();
  };
}
