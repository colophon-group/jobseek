import { ImageResponse } from "next/og";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { getBlogPost, listBlogSlugs } from "@/lib/blog";

export const alt = "Job Seek blog post";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";
// 30-day cache via explicit `Cache-Control` headers on the
// ImageResponse — `'use cache'` doesn't apply (ImageResponse is a
// class instance). Vercel purges the CDN on every deploy so
// `immutable` is safe; posts rarely change after publish anyway.
const CACHE_HEADERS = {
  "Cache-Control": "public, max-age=2592000, s-maxage=2592000, immutable",
};

export async function generateStaticParams(): Promise<{ lang: string; slug: string }[]> {
  const slugs = await listBlogSlugs();
  const locales = ["en", "de", "fr", "it"] as const;
  return slugs.flatMap((slug) => locales.map((lang) => ({ lang, slug })));
}

// Satori only supports TTF/OTF, not woff2.
const fontPromise = readFile(
  join(process.cwd(), "public/fonts/JetBrainsMono-Bold.ttf"),
);

export default async function OgImage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  const post = await getBlogPost(slug);
  const fontData = await fontPromise;

  if (!post) {
    return new ImageResponse(
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          backgroundColor: "#0a0a0a",
          color: "#fafafa",
          fontSize: 48,
          fontFamily: "JetBrains Mono",
        }}
      >
        Not Found
      </div>,
      {
        ...size,
        headers: CACHE_HEADERS,
        fonts: [{ name: "JetBrains Mono", data: fontData, weight: 700, style: "normal" }],
      },
    );
  }

  const datePublished = new Date(post.datePublished).toLocaleDateString("en", {
    year: "numeric",
    month: "long",
    day: "numeric",
  });

  return new ImageResponse(
    <div
      style={{
        width: "100%",
        height: "100%",
        display: "flex",
        flexDirection: "column",
        backgroundColor: "#0a0a0a",
        color: "#fafafa",
        fontFamily: "JetBrains Mono",
        padding: "60px 80px",
      }}
    >
      <span style={{ fontSize: 28, color: "#a1a1aa" }}>Blog</span>

      <span
        style={{
          fontSize: 56,
          fontWeight: 700,
          marginTop: 24,
          lineHeight: 1.15,
          maxHeight: 56 * 1.15 * 4,
          overflow: "hidden",
          display: "flex",
        }}
      >
        {post.title}
      </span>

      <div
        style={{
          display: "flex",
          gap: "16px",
          marginTop: "auto",
          fontSize: 22,
          color: "#71717a",
        }}
      >
        <span>{datePublished}</span>
        <span>·</span>
        <span>{post.author}</span>
      </div>

      <div
        style={{
          position: "absolute",
          bottom: 40,
          right: 80,
          fontSize: 20,
          color: "#52525b",
          display: "flex",
        }}
      >
        jseek.co
      </div>
    </div>,
    {
      ...size,
      headers: CACHE_HEADERS,
      fonts: [
        {
          name: "JetBrains Mono",
          data: fontData,
          weight: 700,
          style: "normal",
        },
      ],
    },
  );
}
