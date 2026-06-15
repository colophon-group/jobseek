import type { Metadata } from "next";
import { defaultLocale, isLocale, loadCatalog } from "@/lib/i18n";
import { NotFoundContent } from "./not-found-content";

type Props = {
  params: Promise<{ lang: string }>;
};

// Server wrapper so we can export `metadata` (client components can't).
// Without this, the title would cascade from `[lang]/layout.tsx`'s
// `default: "Job Seek"` and a 404 response would emit a misleading
// `<title>Job Seek</title>`. Robots is also explicitly `noindex, nofollow`
// since the 404 surface itself shouldn't be indexed AND its only
// outgoing link goes to "/" which crawlers already know.
export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { lang } = await params;
  const locale = isLocale(lang) ? lang : defaultLocale;
  const { i18n } = await loadCatalog(locale);

  return {
    title: i18n._({
      id: "notFound.title",
      comment: "Heading shown on the 404 page",
      message: "Page not found",
    }),
    robots: { index: false, follow: false },
  };
}

export default function NotFound() {
  return <NotFoundContent />;
}
