import type { Metadata } from "next";
import { NotFoundContent } from "./not-found-content";

// Server wrapper so we can export `metadata` (client components can't).
// Without this, the title would cascade from `[lang]/layout.tsx`'s
// `default: "Job Seek"` and a 404 response would emit a misleading
// `<title>Job Seek</title>`. Robots is also explicitly `noindex, nofollow`
// since the 404 surface itself shouldn't be indexed AND its only
// outgoing link goes to "/" which crawlers already know.
export const metadata: Metadata = {
  title: "Page not found",
  robots: { index: false, follow: false },
};

export default function NotFound() {
  return <NotFoundContent />;
}
