// Reset scroll to top synchronously on click, so users see new pages
// at the top of the viewport even when the route transition takes
// >100ms (e.g. a Cache Components prerender hit that streams in
// dynamic data). Without this, the URL flip + new-page commit happens
// after the network round-trip, and the user briefly sees the new
// content rendered at the old scroll position before Next's built-in
// scroll-to-top fires. See #3030.
//
// Hash-anchor links (e.g. `/#features`) are skipped — the browser will
// scroll to the anchor on arrival, and pre-scrolling to top would
// cause a visible jump-to-top then jump-to-anchor flash.
//
// Same-URL clicks (e.g. user is on /blog and clicks the "Blog" header
// link) also benefit: Next treats those as no-ops, so without this
// handler the scroll stays wherever the user was.

export function scrollToTopOnNav(href: string): void {
  if (href.includes("#")) return;
  if (typeof window === "undefined") return;
  window.scrollTo({ top: 0, left: 0, behavior: "instant" });
}
