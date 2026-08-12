const SITE_OG_ASSET_ORIGIN = "https://jobseek-assets.colophon-group.org";

/**
 * Bump when the deterministic site-wide card changes. The version is part of
 * the object path so social/CDN caches may keep every published card
 * immutable without serving stale pixels after a redesign.
 */
export const SITE_OG_VERSION = "jobseek-v1";

export const SITE_OG_KEY = `og/site/${SITE_OG_VERSION}.png`;

export const SITE_OG_PUBLIC_URL = `${SITE_OG_ASSET_ORIGIN}/${SITE_OG_KEY}`;
