import Link from "next/link";
import { siteConfig } from "@/content/config";
import "./globals.css";

// Top-level not-found handler for paths that don't match any `/[lang]/...`
// route — typically URLs with dots that the locale-redirect middleware
// excludes (e.g. `/random.html`, `/foo.txt`). Without a root `app/layout.tsx`,
// Next.js requires this file to render its own `<html>` + `<body>`. The
// localized `[lang]/not-found.tsx` (with translated copy) handles
// `/<locale>/<missing>` paths and is the more common 404 surface.

export default function NotFound() {
  return (
    <html lang="en">
      <body
        style={{
          margin: 0,
          minHeight: "100vh",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          backgroundColor: "#0a0a0a",
          color: "#fafafa",
          fontFamily:
            "ui-monospace, SFMono-Regular, 'JetBrains Mono', Menlo, monospace",
          textAlign: "center",
          padding: "2rem",
        }}
      >
        <main style={{ maxWidth: "32rem" }}>
          <h1 style={{ fontSize: "1.5rem", fontWeight: 700, margin: "0 0 0.5rem 0" }}>
            Page not found
          </h1>
          <p style={{ fontSize: "0.875rem", color: "#a1a1aa", margin: "0 0 1.5rem 0" }}>
            The page you are looking for does not exist or has been moved.
          </p>
          <Link
            href={siteConfig.url}
            style={{
              display: "inline-block",
              padding: "0.625rem 1.5rem",
              borderRadius: "9999px",
              background: "#fafafa",
              color: "#0a0a0a",
              fontSize: "0.875rem",
              fontWeight: 600,
              textDecoration: "none",
            }}
          >
            Go home
          </Link>
        </main>
      </body>
    </html>
  );
}
