"use client";

import Link from "next/link";
import type { ComponentProps } from "react";
import { scrollToTopOnNav } from "@/lib/scroll-on-nav";

/**
 * Client-side wrapper around `next/link::Link` that auto-wires
 * `scrollToTopOnNav(href)` on click (#3046).
 *
 * Use this anywhere a `<Link>` participates in cross-page navigation
 * and should land the new page at scroll-top — Footer links, blog
 * cross-links, banner CTAs, MDX mention pills/cards.
 *
 * Why a wrapper instead of sprinkling `onClick={() => scrollToTopOnNav(href)}`:
 *
 *  - Three of the four target surfaces in #3046 (`Footer`, `RelatedPosts`,
 *    `MdxMentions`) are server components. RSC parents cannot serialise
 *    inline event-handler closures into Client Components, so each call
 *    site would otherwise need to flip its file to `"use client"` —
 *    losing async data fetching (`await listBlogPosts()`,
 *    `await cachedCompany(...)`).
 *  - One thin client boundary per surface is cheaper than seven, both
 *    in bundle size and in mental overhead.
 *  - Header / MobileMenu stay on the sprinkle pattern (#3030, #3042)
 *    because they are already `"use client"` and use `usePathname()` for
 *    `aria-current`; converting them to NavLink would require either
 *    forwarding the `aria-current` prop or wiring two components, which
 *    is more code than the existing call sites.
 *
 * `href` is required as a string so we can pass it to `scrollToTopOnNav`.
 * `next/link::Link` accepts `UrlObject` too, but every internal route in
 * this app is rendered as a string template (see grep for `<Link href=`).
 * Hash-anchor hrefs are auto-skipped inside `scrollToTopOnNav`.
 */
type NavLinkProps = Omit<ComponentProps<typeof Link>, "href" | "onClick"> & {
  href: string;
};

export function NavLink({ href, ...rest }: NavLinkProps) {
  return (
    <Link
      href={href}
      onClick={() => scrollToTopOnNav(href)}
      {...rest}
    />
  );
}
