import { describe, expect, it, vi } from "vitest";

vi.mock("next/cache", () => ({ cacheLife: vi.fn(), cacheTag: vi.fn() }));
vi.mock("next/navigation", () => ({
  notFound: () => { throw new Error("not found"); },
  permanentRedirect: (url: string) => { throw new Error(`redirect:${url}`); },
}));
vi.mock("next-mdx-remote/rsc", () => ({ compileMDX: async () => ({ content: "Article body" }) }));
vi.mock("@/components/blog/MdxMentions", () => ({ buildMdxComponents: () => ({}) }));
vi.mock("@/components/blog/RelatedPosts", () => ({ RelatedPosts: () => null }));
vi.mock("@lingui/react/server", () => ({ getI18n: () => ({ _: ({ message }: { message: string }) => message }) }));
vi.mock("@/lib/i18n", async (original) => ({
  ...await original<typeof import("@/lib/i18n")>(),
  initI18nForPage: async (params: Promise<{ lang: string }>) => (await params).lang,
}));
vi.mock("../blog", () => ({
  getBlogPost: async () => ({
    slug: "english-only", title: "Research", description: "Original research",
    datePublished: "2026-05-07", dateModified: "2026-09-28",
    author: "Viktor Shcherbakov", tags: [], body: "Article body",
  }),
  listBlogSlugs: async () => ["english-only"],
  getBlogPostLocales: async () => ["en"],
  readingTimeMinutes: () => 1,
}));

import BlogPage, { generateMetadata, generateStaticParams } from "../../../app/[lang]/(public)/blog/[slug]/page";

describe("blog metadata and untranslated URLs", () => {
  it("prerenders only existing locales and points fallback metadata at the real content", async () => {
    expect(await generateStaticParams()).toEqual([{ lang: "en", slug: "english-only" }]);
    const metadata = await generateMetadata({ params: Promise.resolve({ lang: "fr", slug: "english-only" }) });
    expect(metadata.alternates?.canonical).toBe("https://jseek.co/en/blog/english-only");
    expect(metadata.alternates?.languages).not.toHaveProperty("fr");
    expect(metadata).toMatchObject({
      twitter: { images: [{ url: "https://jseek.co/og/blog/en/english-only", alt: "Research" }] },
      openGraph: { images: [{ url: "https://jseek.co/og/blog/en/english-only", alt: "Research" }] },
    });
  });
  it("redirects an untranslated page instead of labeling English as French", async () => {
    await expect(BlogPage({ params: Promise.resolve({ lang: "fr", slug: "english-only" }) }))
      .rejects.toThrow("redirect:/en/blog/english-only");
  });
  it("includes an article image and the verified author identity in JSON-LD", async () => {
    const page = await BlogPage({ params: Promise.resolve({ lang: "en", slug: "english-only" }) });
    const schema = page.props.children[0].props.data;
    expect(schema.image).toEqual(["https://jseek.co/og/blog/en/english-only"]);
    expect(schema.author.url).toBe("https://github.com/viktor-shcherb");
    expect(schema.inLanguage).toBe("en");
  });
});
