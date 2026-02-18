"use client";

// No i18n here — this renders when the root layout crashes,
// so there is no LinguiClientProvider in the tree.

import "./globals.css";
import styles from "./error.module.css";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <html lang="en">
      <body>
        <main className={styles.container}>
          <h1 className={styles.title}>Something went wrong</h1>
          <p className={styles.message}>
            {error.digest ? `Error ID: ${error.digest}` : error.message}
          </p>
          <button className={styles.button} onClick={reset}>
            Try again
          </button>
        </main>
      </body>
    </html>
  );
}
