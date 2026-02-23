"use client";

import { Trans } from "@lingui/react/macro";
import { useLingui } from "@lingui/react/macro";
import { siteConfig } from "@/content/config";
import { ContentPageHero } from "@/components/ContentPageHero";
import { TableOfContents } from "@/components/TableOfContents";

const sectionScroll = "scroll-mt-24 md:scroll-mt-32";

export function LicenseContent() {
  const { t } = useLingui();
  const anchors = siteConfig.license.anchors;
  const codeLink = `${siteConfig.repoUrl}/blob/main/LICENSE`;
  const dataLink = `${siteConfig.repoUrl}/blob/main/LICENSE-JOB-DATA`;
  const contactEmail = siteConfig.indexing.contactEmail;

  const tocTitle = t({ id: "license.toc.title", comment: "Table of contents heading", message: "Contents" });
  const tocAriaLabel = t({ id: "license.toc.ariaLabel", comment: "Table of contents aria label", message: "Page contents" });

  const tocItems = [
    { label: t({ id: "license.toc.overview", comment: "TOC: Overview", message: "Overview" }), href: `#${anchors.overview}` },
    { label: t({ id: "license.toc.code", comment: "TOC: Application code (MIT)", message: "Application code (MIT)" }), href: `#${anchors.code}` },
    { label: t({ id: "license.toc.data", comment: "TOC: Job data (CC BY-NC 4.0)", message: "Job data (CC BY-NC 4.0)" }), href: `#${anchors.data}` },
    { label: t({ id: "license.toc.contact", comment: "TOC: Contact", message: "Contact" }), href: `#${anchors.contact}` },
  ];

  return (
    <main className="py-12 md:py-20">
      <div className="mx-auto max-w-[1200px] px-4">
        <div className="flex flex-col items-start gap-12 lg:flex-row lg:gap-20">
          <div className="flex flex-1 flex-col gap-12 md:gap-16">
            {/* Overview */}
            <div className={`w-full max-w-[840px] ${sectionScroll}`} id={anchors.overview}>
              <ContentPageHero
                eyebrow={<Trans id="license.hero.eyebrow" comment="License page eyebrow">Licensing</Trans>}
                title={<Trans id="license.hero.title" comment="License page title">License of Job Seek</Trans>}
                description={<Trans id="license.hero.description" comment="License page description">
                  {"Job Seek's codebase is open source under MIT. The job data we collect and enrich is Creative Commons BY-NC 4.0. Below is the plain-language summary \u2014 please read the full licenses for exact terms."}
                </Trans>}
                artAssetKey={siteConfig.license.hero.art.assetKey}
                artFocus={siteConfig.license.hero.art.focus}
                artMaxWidth={380}
              />
            </div>

            {/* Code license */}
            <div className={`w-full max-w-[840px] rounded-lg border border-border-soft bg-surface p-6 md:p-8 ${sectionScroll}`} id={anchors.code}>
              <h2 className="text-lg font-bold">
                <Trans id="license.code.title" comment="MIT license section title">Application code (MIT License)</Trans>
              </h2>
              <p className="mt-2 text-muted">
                <Trans id="license.code.summary" comment="MIT license summary">
                  You can use, modify, and redistribute the code in personal or commercial products as long as you include the copyright and license notice.
                </Trans>
              </p>
              <ul className="mt-3 list-disc space-y-1 pl-6">
                <li><Trans id="license.code.r1" comment="MIT right 1">Copy and modify the code for any purpose, including commercial products.</Trans></li>
                <li><Trans id="license.code.r2" comment="MIT right 2">Redistribute your changes, as long as you include the MIT notice.</Trans></li>
                <li><Trans id="license.code.r3" comment="MIT right 3">{"No warranty \u2014 use at your own risk."}</Trans></li>
              </ul>
              <a href={codeLink} target="_blank" rel="noreferrer" className="mt-3 inline-block font-semibold text-primary underline">
                <Trans id="license.code.linkLabel" comment="Link to full MIT license">Read the full MIT License</Trans>
                <span className="sr-only"><Trans id="common.a11y.opensInNewTab" comment="Screen reader text for external links">(opens in new tab)</Trans></span>
              </a>
            </div>

            {/* Data license */}
            <div className={`w-full max-w-[840px] rounded-lg border border-border-soft bg-surface p-6 md:p-8 ${sectionScroll}`} id={anchors.data}>
              <h2 className="text-lg font-bold">
                <Trans id="license.data.title" comment="CC license section title">Collection of job postings (CC BY-NC 4.0)</Trans>
              </h2>
              <p className="mt-2 text-muted">
                <Trans id="license.data.summary" comment="CC license summary">
                  You may reuse the job dataset with attribution for non-commercial purposes. Commercial usage requires prior written consent.
                </Trans>
              </p>
              <ul className="mt-3 list-disc space-y-1 pl-6">
                <li>
                  <Trans id="license.data.r1" comment="CC right 1">
                    {"\u201CViktor Shcherbakov, Collection of Job Postings\u201D with a link to the source."}
                  </Trans>
                </li>
                <li><Trans id="license.data.r2" comment="CC right 2">No commercial redistribution or resale without permission.</Trans></li>
                <li><Trans id="license.data.r3" comment="CC right 3">You can remix/transform the data for research or personal dashboards.</Trans></li>
              </ul>
              <a href={dataLink} target="_blank" rel="noreferrer" className="mt-3 inline-block font-semibold text-primary underline">
                <Trans id="license.data.linkLabel" comment="Link to full CC license">Read the CC BY-NC 4.0 License</Trans>
                <span className="sr-only"><Trans id="common.a11y.opensInNewTab" comment="Screen reader text for external links">(opens in new tab)</Trans></span>
              </a>
            </div>

            {/* Contact */}
            <div className={`w-full max-w-[840px] ${sectionScroll}`} id={anchors.contact}>
              <h2 className="mb-3 text-lg font-bold">
                <Trans id="license.contact.title" comment="Contact section title">Contact</Trans>
              </h2>
              <p className="text-muted">
                <Trans id="license.contactCta" comment="Contact call to action">
                  Questions about licensing or commercial use? Email us.
                </Trans>
                {" "}
                <a href={`mailto:${contactEmail}`} className="text-primary underline">{contactEmail}</a>
              </p>
            </div>
          </div>

          <TableOfContents
            title={tocTitle}
            ariaLabel={tocAriaLabel}
            items={tocItems}
            className="hidden max-w-[260px] md:block"
          />
        </div>
      </div>
    </main>
  );
}
