import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { cleanup, render, screen } from "@testing-library/react";
import sharp from "sharp";
import { afterEach, describe, expect, it, vi } from "vitest";
import { publicDomainAssets } from "@/content/config";
import { PublicDomainArt } from "@/components/PublicDomainArt";

const themeState = vi.hoisted(() => ({ resolvedTheme: "dark" }));

vi.mock("next-themes", () => ({
  useTheme: () => ({ resolvedTheme: themeState.resolvedTheme }),
}));

vi.mock("next/image", () => ({
  default: ({
    fill: _fill,
    ...props
  }: React.ImgHTMLAttributes<HTMLImageElement> & {
    fill?: boolean;
  }) => (
    // eslint-disable-next-line @next/next/no-img-element
    <img {...props} />
  ),
}));

vi.mock("@lingui/react/macro", () => ({
  useLingui: () => ({
    t: ({ message }: { message?: string }) => message ?? "",
  }),
  Trans: ({ children }: { children: React.ReactNode }) => children,
}));

function publicDomainArtCall(source: string, marker: string): string {
  const markerIndex = source.indexOf(marker);
  expect(markerIndex).toBeGreaterThanOrEqual(0);

  const callStart = source.lastIndexOf("<PublicDomainArt", markerIndex);
  const callEnd = source.indexOf("/>", markerIndex);
  expect(callStart).toBeGreaterThanOrEqual(0);
  expect(callEnd).toBeGreaterThan(markerIndex);

  return source.slice(callStart, callEnd + 2);
}

afterEach(() => {
  cleanup();
  themeState.resolvedTheme = "dark";
});

describe("homepage image fetch priority", () => {
  it("gives high fetch priority only to the measured hero LCP candidate", () => {
    const heroSource = readFileSync("src/components/Hero.tsx", "utf8");
    const homepageSource = readFileSync(
      "app/[lang]/(public)/page.tsx",
      "utf8",
    );
    const globalStyles = readFileSync("app/globals.css", "utf8");

    const heroArt = publicDomainArtCall(heroSource, "asset={heroArt}");
    const afterPricingArt = publicDomainArtCall(
      homepageSource,
      "asset={afterPricingArt}",
    );

    expect(heroArt).toContain('loading="eager"');
    expect(heroArt).toContain('fetchPriority="high"');
    expect(heroArt).toContain('themeRendering="css-invert"');
    expect(afterPricingArt).not.toContain("fetchPriority");
    expect(afterPricingArt).not.toContain("preload");
    expect(globalStyles).toMatch(
      /\.theme-art-invert-dark\s*{\s*filter:\s*none;/,
    );
    expect(globalStyles).toMatch(
      /\.dark \.theme-art-invert-dark\s*{\s*filter:\s*invert\(1\);/,
    );
  });

  it("forwards one invariant high-priority Astrologer across persisted themes", () => {
    const { rerender } = render(
      <PublicDomainArt
        asset={publicDomainAssets.the_astrologer}
        loading="eager"
        fetchPriority="high"
        themeRendering="css-invert"
        credit={false}
      />,
    );

    const darkThemeImage = screen.getByRole("img");
    const invariantSrc = publicDomainAssets.the_astrologer.light;
    expect(darkThemeImage.getAttribute("src")).toBe(invariantSrc);
    expect(darkThemeImage.getAttribute("loading")).toBe("eager");
    expect(darkThemeImage.getAttribute("fetchpriority")).toBe("high");
    expect(darkThemeImage.classList.contains("theme-art-invert-dark")).toBe(true);

    themeState.resolvedTheme = "light";
    rerender(
      <PublicDomainArt
        asset={publicDomainAssets.the_astrologer}
        loading="eager"
        fetchPriority="high"
        themeRendering="css-invert"
        credit={false}
      />,
    );

    expect(screen.getByRole("img").getAttribute("src")).toBe(invariantSrc);
    expect(screen.getByRole("img").getAttribute("fetchpriority")).toBe("high");
  });

  it("keeps the after-pricing Miser lazy and non-preloaded", () => {
    render(
      <PublicDomainArt
        asset={publicDomainAssets.the_miser}
        credit={false}
      />,
    );

    const image = screen.getByRole("img");
    expect(image.getAttribute("loading")).toBe("lazy");
    expect(image.getAttribute("fetchpriority")).toBeNull();
  });

  it("uses Astrologer variants that are exact visible-pixel inverses", async () => {
    const asset = publicDomainAssets.the_astrologer;
    const [lightVariant, darkVariant] = await Promise.all(
      [asset.light, asset.dark].map(async (src) => {
        expect(src).toBeDefined();
        return sharp(resolve("public", src!.slice(1)))
          .ensureAlpha()
          .raw()
          .toBuffer({ resolveWithObject: true });
      }),
    );

    expect(lightVariant.info.width).toBe(darkVariant.info.width);
    expect(lightVariant.info.height).toBe(darkVariant.info.height);

    let mismatchedChannels = 0;
    for (let offset = 0; offset < lightVariant.data.length; offset += 4) {
      if (lightVariant.data[offset + 3] !== darkVariant.data[offset + 3]) {
        mismatchedChannels += 1;
      }
      if (lightVariant.data[offset + 3] === 0) continue;

      for (let channel = 0; channel < 3; channel += 1) {
        if (
          lightVariant.data[offset + channel]
            + darkVariant.data[offset + channel]
          !== 255
        ) {
          mismatchedChannels += 1;
        }
      }
    }

    expect(mismatchedChannels).toBe(0);
  });
});
