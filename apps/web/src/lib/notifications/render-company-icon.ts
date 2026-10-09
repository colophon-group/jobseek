import sharp from "sharp";

/** Three pixels per CSS pixel, with a two-CSS-pixel inset around the artwork. */
export async function renderEmailCompanyIcon(source: Uint8Array): Promise<Buffer> {
  return sharp(source, { limitInputPixels: 16_000_000, density: 144 })
    .rotate()
    .resize(84, 84, { fit: "contain", background: "#f5f6f3" })
    .flatten({ background: "#f5f6f3" })
    .extend({ top: 6, bottom: 6, left: 6, right: 6, background: "#f5f6f3" })
    .png()
    .toBuffer();
}
