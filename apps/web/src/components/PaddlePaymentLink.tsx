"use client";

import { useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Trans, useLingui } from "@lingui/react/macro";
import { loadPaddle } from "@/lib/paddle/browser";
import { ErrorAlert } from "@/components/ui/ErrorAlert";

/** Public landing page for Paddle payment/update-method links, including
 * customers who aren't currently signed into Job Seek. */
export function PaddlePaymentLink() {
  const { t, i18n } = useLingui();
  const params = useSearchParams();
  const transactionId = params.get("_ptxn");
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (!transactionId || !/^txn_[a-z0-9]{26}$/.test(transactionId)) return;
    // Paddle.js automatically opens the transaction identified by _ptxn.
    // Do not also call Checkout.open(), which would open it twice.
    void loadPaddle(i18n.locale).catch(() => setFailed(true));
  }, [transactionId, i18n.locale]);
  return (
    <main id="main-content" className="mx-auto max-w-xl px-4 py-20">
      <h1 className="mb-4 text-2xl font-bold"><Trans id="checkout.title" comment="Paddle payment link page title">Secure checkout</Trans></h1>
      <p className="text-muted"><Trans id="checkout.description" comment="Paddle payment link page instructions">Use your payment link to open Paddle’s secure checkout or update your payment method.</Trans></p>
      {failed && <ErrorAlert message={t({ id: "checkout.loadError", comment: "Paddle script failed to load", message: "Checkout could not load. Please refresh and try again." })} />}
    </main>
  );
}
