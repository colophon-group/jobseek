import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const howWeIndex = readFileSync(
  "src/components/HowWeIndexContent.tsx",
  "utf8",
);
const faq = readFileSync("app/[lang]/(public)/faq/page.tsx", "utf8");
const about = readFileSync(
  "app/[lang]/(public)/about/about-content.tsx",
  "utf8",
);
const crawlerConfig = readFileSync("../crawler/src/config.py", "utf8");

describe("public indexing-policy copy", () => {
  it("matches the crawler's configured per-domain pacing", () => {
    expect(crawlerConfig).toContain("throttle_delay_default: float = 2.0");
    expect(crawlerConfig).toContain("throttle_delay_ats: float = 0.5");
    expect(howWeIndex).toContain("by 2 seconds by default and 0.5 seconds");
    expect(howWeIndex).not.toContain("one request per site per minute");
  });

  it("describes current robots and User-Agent behavior without false guarantees", () => {
    for (const source of [howWeIndex, faq, about]) {
      expect(source).not.toContain("identifies itself via");
      expect(source).not.toContain("All requests identify themselves");
      expect(source).not.toContain("We respect robots.txt");
    }

    expect(howWeIndex).toContain("Disallow enforcement is not yet active");
    expect(howWeIndex).toContain("stable browser-compatible");
    expect(faq).toContain("Disallow enforcement is not yet active");
  });

  it("removes the fictional identifying UA from every translated blog post", () => {
    for (const file of [
      "src/content/blog/how-we-index-job-postings.mdx",
      "src/content/blog/how-we-index-job-postings.de.mdx",
      "src/content/blog/how-we-index-job-postings.fr.mdx",
      "src/content/blog/how-we-index-job-postings.it.mdx",
    ]) {
      const post = readFileSync(file, "utf8");
      expect(post).not.toContain("Job-Seek-Crawler/X.Y");
      expect(post).toContain("issues/2841");
    }
  });
});
