import Link from "next/link";
import { Trans } from "@lingui/react/macro";
import { siteConfig } from "@/content/config";

type FooterProps = {
  lang?: string;
};

export function Footer({ lang }: FooterProps) {
  const year = new Date().getFullYear();
  const links = siteConfig.footer.links;
  const prefix = lang ? `/${lang}` : "";

  const linkClass = "text-sm font-medium tracking-wide hover:text-muted transition-colors";

  return (
    <footer className="mt-8 border-t border-divider md:mt-16">
      <div className="mx-auto flex max-w-[1200px] flex-col gap-4 px-6 py-6 sm:flex-row sm:items-center sm:justify-between">
        <p className="order-2 m-0 text-sm text-muted sm:order-1">
          &copy; {year} {siteConfig.creator}.{" "}
          <Trans
            id="common.footer.text"
            comment="Footer license summary text"
          >
            Released under the MIT License. Job data is CC BY-NC 4.0.
          </Trans>
        </p>
        <nav aria-label="Footer" className="order-1 sm:order-2">
          <ul className="flex list-none gap-4 p-0">
            <li>
              <a className={linkClass} href={links[0].href} target="_blank" rel="noreferrer">
                <Trans id="common.footer.github" comment="Footer link to GitHub repo">GitHub</Trans>
                <span className="sr-only"><Trans id="common.a11y.opensInNewTab" comment="Screen reader text for external links">(opens in new tab)</Trans></span>
              </a>
            </li>
            <li>
              <a className={linkClass} href={links[1].href} target="_blank" rel="noreferrer">
                <Trans id="common.footer.contact" comment="Footer link to contact email">Contact</Trans>
                <span className="sr-only"><Trans id="common.a11y.opensInNewTab" comment="Screen reader text for external links">(opens in new tab)</Trans></span>
              </a>
            </li>
            <li>
              <Link className={linkClass} href={`${prefix}${links[2].href}`}>
                <Trans id="common.footer.licenseLink" comment="Footer link to license page">License</Trans>
              </Link>
            </li>
            <li>
              <Link className={linkClass} href={`${prefix}${links[3].href}`}>
                <Trans id="common.footer.privacyLink" comment="Footer link to privacy policy page">Privacy</Trans>
              </Link>
            </li>
            <li>
              <Link className={linkClass} href={`${prefix}${links[4].href}`}>
                <Trans id="common.footer.termsLink" comment="Footer link to terms of service page">Terms</Trans>
              </Link>
            </li>
          </ul>
        </nav>
      </div>
    </footer>
  );
}
