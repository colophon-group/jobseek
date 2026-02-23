"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Trans } from "@lingui/react/macro";
import { useLingui } from "@lingui/react/macro";
import { useAuth } from "@/lib/useAuth";
import { siteConfig } from "@/content/config";
import { ThemeToggleButton } from "@/components/ThemeToggleButton";
import { LocaleSwitcher } from "@/components/LocaleSwitcher";
import { ThemedImage } from "@/components/ThemedImage";
import { useLocalePath } from "@/lib/useLocalePath";
import { Button } from "@/components/ui/Button";
import * as Dialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";

type MobileMenuProps = {
  open: boolean;
  onCloseAction: () => void;
};

export function MobileMenu({ open, onCloseAction }: MobileMenuProps) {
  const { isLoggedIn } = useAuth();
  const { t } = useLingui();
  const lp = useLocalePath();
  const pathname = usePathname();

  function ariaCurrent(href: string) {
    return pathname === href || pathname.startsWith(href + "/") ? ("page" as const) : undefined;
  }

  const authHref = isLoggedIn ? lp(siteConfig.nav.dashboard.href) : lp(siteConfig.nav.login.href);
  const authLabel = isLoggedIn
    ? t({ id: "common.dashboard.action", comment: "Dashboard nav button label", message: "To dashboard" })
    : t({ id: "common.auth.login", comment: "Login button label", message: "Log in" });

  return (
    <Dialog.Root open={open} onOpenChange={(v) => { if (!v) onCloseAction(); }}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/40 data-[state=open]:animate-in data-[state=open]:fade-in-0" />
        <Dialog.Content
          className="fixed inset-y-0 right-0 z-50 w-80 bg-surface shadow-xl data-[state=open]:animate-in data-[state=open]:slide-in-from-right"
          aria-describedby={undefined}
        >
          <Dialog.Title className="sr-only">Menu</Dialog.Title>
          <div className="px-5 py-6">
            <div className="flex items-center justify-between gap-2">
              <Link
                href={lp("/")}
                onClick={onCloseAction}
                className="inline-flex items-center gap-2 no-underline"
              >
                <ThemedImage
                  darkSrc={siteConfig.logoWide.dark}
                  lightSrc={siteConfig.logoWide.light}
                  alt="Job Seek"
                  width={siteConfig.logoWide.width}
                  height={siteConfig.logoWide.height}
                  style={{ height: 32, width: "auto" }}
                />
              </Link>
              <div className="flex items-center gap-2">
                <LocaleSwitcher />
                <ThemeToggleButton />
                <Dialog.Close asChild>
                  <button
                    className="inline-flex items-center justify-center rounded-md p-1.5 text-foreground hover:bg-border-soft"
                    aria-label={t({ id: "common.mobileMenu.close", comment: "Aria label for close mobile menu button", message: "Close menu" })}
                  >
                    <X size={18} />
                  </button>
                </Dialog.Close>
              </div>
            </div>

            <nav className="mt-6">
              <ul className="flex flex-col">
                <li>
                  <Link href={lp(siteConfig.nav.product.href)} prefetch={false} onClick={onCloseAction} className="block rounded-md px-3 py-2.5 transition-colors hover:bg-border-soft" aria-current={ariaCurrent(lp(siteConfig.nav.product.href))}>
                    <Trans id="common.nav.product" comment="Nav link: Product">Product</Trans>
                  </Link>
                </li>
                <li>
                  <Link href={lp(siteConfig.nav.features.href)} prefetch={false} onClick={onCloseAction} className="block rounded-md px-3 py-2.5 transition-colors hover:bg-border-soft" aria-current={ariaCurrent(lp(siteConfig.nav.features.href))}>
                    <Trans id="common.nav.features" comment="Nav link: Features">Features</Trans>
                  </Link>
                </li>
                <li>
                  <Link href={lp(siteConfig.nav.pricing.href)} prefetch={false} onClick={onCloseAction} className="block rounded-md px-3 py-2.5 transition-colors hover:bg-border-soft" aria-current={ariaCurrent(lp(siteConfig.nav.pricing.href))}>
                    <Trans id="common.nav.pricing" comment="Nav link: Pricing">Pricing</Trans>
                  </Link>
                </li>
                <li>
                  <Link href={lp(siteConfig.nav.company.href)} onClick={onCloseAction} className="block rounded-md px-3 py-2.5 transition-colors hover:bg-border-soft" aria-current={ariaCurrent(lp(siteConfig.nav.company.href))}>
                    <Trans id="common.nav.company" comment="Nav link: How do we index jobs?">How do we index jobs?</Trans>
                  </Link>
                </li>
              </ul>
            </nav>

            <hr className="my-4 border-divider" />

            <Button href={authHref} variant="outline" onClick={onCloseAction} className="w-full text-center">
              {authLabel}
            </Button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
