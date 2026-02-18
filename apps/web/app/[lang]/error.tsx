"use client";

import { Trans } from "@lingui/react/macro";
import styles from "../error.module.css";

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <main className={styles.container}>
      <h1 className={styles.title}>
        <Trans id="error.title" comment="Heading shown when a page crashes unexpectedly">
          Something went wrong
        </Trans>
      </h1>
      <p className={styles.message}>
        {error.digest ? `Error ID: ${error.digest}` : error.message}
      </p>
      <button className={styles.button} onClick={reset}>
        <Trans id="error.retryButton" comment="Button to retry loading the page after an error">
          Try again
        </Trans>
      </button>
    </main>
  );
}
