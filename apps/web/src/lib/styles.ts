export const eyebrowClass = "text-xs font-semibold uppercase tracking-wider text-muted";
export const sectionHeadingClass = "text-2xl font-bold md:text-3xl";

/**
 * Anchor scroll-margin for sections that may be targeted by `#hash` navigation.
 * The sticky `<Header>` is `h-12` (48px) + 1px border = 49px tall, so we use
 * `scroll-mt-24` (~96px on mobile) / `md:scroll-mt-32` (~128px on desktop) to
 * give the anchored element breathing room below the header.
 */
export const sectionScrollMarginClass = "scroll-mt-24 md:scroll-mt-32";
