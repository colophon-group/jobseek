import type { Metadata } from "next";
import { NotFoundContent } from "./not-found-content";

// Server wrapper so we can export `metadata` (client components can't).
// Without this, the title would cascade from `[lang]/layout.tsx`'s
// `default: "Job Seek"` and a 404 response would emit a misleading
// `<title>Job Seek</title>`. Robots is also explicitly noindex,follow
// so search engines don't surface the 404 surface itself.
export const metadata: Metadata = {
  title: "Page not found",
  robots: { index: false, follow: false },
};

export default function NotFound() {
  return <NotFoundContent />;
}
