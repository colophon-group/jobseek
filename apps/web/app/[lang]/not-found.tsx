"use client";

import { Trans } from "@lingui/react/macro";
import { useLocalePath } from "@/lib/useLocalePath";
import styles from "../error.module.css";

export default function NotFound() {
  const localePath = useLocalePath();

  return (
    <main className={styles.container}>
      <h1 className={styles.title}>
        <Trans id="notFound.title" comment="Heading shown on the 404 page">
          Page not found
        </Trans>
      </h1>
      <p className={styles.message}>
        <Trans id="notFound.body" comment="Body text on the 404 page">
          The page you are looking for does not exist or has been moved.
        </Trans>
      </p>
      <a className={styles.button} href={localePath("/")}>
        <Trans id="notFound.goHome" comment="Link to return to the homepage from a 404 page">
          Go home
        </Trans>
      </a>
    </main>
  );
}
