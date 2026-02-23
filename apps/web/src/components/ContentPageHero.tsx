import type { ReactNode } from "react";
import { publicDomainAssets, type CropInsets } from "@/content/config";
import { PublicDomainArt } from "@/components/PublicDomainArt";
import { eyebrowClass, sectionHeadingClass } from "@/lib/styles";

type ContentPageHeroProps = {
  eyebrow: ReactNode;
  title: ReactNode;
  description: ReactNode;
  extra?: ReactNode;
  artAssetKey: string;
  artFocus?: { x: number; y: number };
  artMaxWidth?: number;
};

function computeArtDimensions(
  width: number,
  height: number,
  crop: CropInsets | undefined,
  maxWidth: number,
) {
  const effectiveW = width - (crop?.left ?? 0) - (crop?.right ?? 0);
  const effectiveH = height - (crop?.top ?? 0) - (crop?.bottom ?? 0);
  const displayWidth = Math.min(effectiveW, maxWidth);
  const aspectRatio = effectiveH > 0 ? effectiveW / effectiveH : 1;
  return { displayWidth, aspectRatio };
}

export function ContentPageHero({
  eyebrow,
  title,
  description,
  extra,
  artAssetKey,
  artFocus,
  artMaxWidth = 390,
}: ContentPageHeroProps) {
  const art = publicDomainAssets[artAssetKey];
  const dims = art ? computeArtDimensions(art.width, art.height, art.crop, artMaxWidth) : null;

  return (
    <div className="flex flex-col items-stretch justify-center gap-8 md:flex-row md:items-start md:gap-12">
      <div className="flex flex-1 flex-col gap-4">
        <span className={eyebrowClass}>{eyebrow}</span>
        <h1 className={sectionHeadingClass}>{title}</h1>
        <p className="text-muted">{description}</p>
        {extra}
      </div>
      {art && dims && (
        <div
          className="mx-auto flex w-full shrink-0 justify-center md:order-2"
          style={{
            flexBasis: dims.displayWidth,
            maxWidth: dims.displayWidth,
            aspectRatio: dims.aspectRatio,
            minHeight: 240,
          }}
        >
          <PublicDomainArt asset={art} focus={artFocus} credit className="h-full w-full" />
        </div>
      )}
    </div>
  );
}
