import { Trans } from "@lingui/react/macro";
import { siteConfig } from "@/content/config";
import styles from "./Footer.module.css";

type FooterProps = {
  lang?: string;
};

export function Footer({ lang }: FooterProps) {
  const year = new Date().getFullYear();
  const links = siteConfig.footer.links;
  const prefix = lang ? `/${lang}` : "";

  return (
    <footer className={styles.footer}>
      <div className={styles.inner}>
        <p className={styles.copy}>
          &copy; {year} {siteConfig.creator}.{" "}
          <Trans
            id="common.footer.text"
            comment="Footer license summary text"
          >
            Released under the MIT License. Job data is CC BY-NC 4.0.
          </Trans>
        </p>
        <ul className={styles.links}>
          <li>
            <a
              className={styles.link}
              href={links[0].href}
              target="_blank"
              rel="noreferrer"
            >
              <Trans id="common.footer.github" comment="Footer link to GitHub repo">GitHub</Trans>
            </a>
          </li>
          <li>
            <a
              className={styles.link}
              href={links[1].href}
              target="_blank"
              rel="noreferrer"
            >
              <Trans id="common.footer.contact" comment="Footer link to contact email">Contact</Trans>
            </a>
          </li>
          <li>
            <a className={styles.link} href={`${prefix}${links[2].href}`}>
              <Trans id="common.footer.licenseLink" comment="Footer link to license page">License</Trans>
            </a>
          </li>
          <li>
            <a className={styles.link} href={`${prefix}${links[3].href}`}>
              <Trans id="common.footer.privacyLink" comment="Footer link to privacy policy page">Privacy</Trans>
            </a>
          </li>
          <li>
            <a className={styles.link} href={`${prefix}${links[4].href}`}>
              <Trans id="common.footer.termsLink" comment="Footer link to terms of service page">Terms</Trans>
            </a>
          </li>
        </ul>
      </div>
    </footer>
  );
}
