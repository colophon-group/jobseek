import type { Metadata } from "next";
import Link from "next/link";
import { siteConfig } from "@/content/config";
import "./globals.css";

// Top-level not-found handler for paths that don't match any `/[lang]/...`
// route — typically URLs with dots that the locale-redirect middleware
// excludes (e.g. `/random.html`, `/foo.txt`). Without a root `app/layout.tsx`,
// Next.js requires this file to render its own `<html>` + `<body>`. The
// localized `[lang]/not-found.tsx` (with translated copy) handles
// `/<locale>/<missing>` paths and is the more common 404 surface.

export const metadata: Metadata = {
  title: "Page not found",
  // Prevent search engines from indexing the bare 404 page or shipping
  // its OG metadata in unfurl previews — the upstream link is broken,
  // not the site.
  robots: { index: false, follow: false },
};

export default function NotFound() {
  return (
    <html lang="en" className="dark">
      <body className="bg-background text-foreground antialiased">
        <main className="mx-auto flex min-h-dvh max-w-md flex-col items-center justify-center px-6 py-12 text-center">
          <h1 className="mb-2 text-2xl font-bold">Page not found</h1>
          <p className="mb-6 text-sm text-muted">
            The page you are looking for does not exist or has been moved.
          </p>
          <Link
            href={siteConfig.url}
            className="inline-block rounded-full bg-primary px-6 py-2.5 text-sm font-semibold text-primary-contrast no-underline hover:opacity-85"
          >
            Go home
          </Link>
        </main>
      </body>
    </html>
  );
}
