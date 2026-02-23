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
import { Menu, LogIn } from "lucide-react";

type HeaderProps = {
  onOpenMobileAction: () => void;
};

const navLinkClass =
  "whitespace-nowrap text-sm font-medium px-3 py-1 transition-colors hover:text-muted";

export function Header({ onOpenMobileAction }: HeaderProps) {
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
    <header className="sticky top-0 z-50 border-b border-divider backdrop-blur-md">
      <div className="mx-auto flex h-12 max-w-[1200px] items-center gap-4 px-4">
        <Link href={lp("/")} className="inline-flex shrink-0 items-center gap-2">
          <ThemedImage
            lightSrc={siteConfig.logoWide.light}
            darkSrc={siteConfig.logoWide.dark}
            alt="Job Seek"
            width={siteConfig.logoWide.width}
            height={siteConfig.logoWide.height}
            style={{ height: 36, width: "auto" }}
          />
        </Link>

        <div className="flex-1" />

        {/* prefetch={false} on same-page anchor links (/, /#features, /#pricing)
            to avoid wasted edge requests. "How do we index" and CTA buttons
            keep prefetch enabled — those are cross-page hot paths. */}
        <nav className="hidden items-center gap-5 lg:flex">
          <Link href={lp(siteConfig.nav.product.href)} prefetch={false} className={navLinkClass} aria-current={ariaCurrent(lp(siteConfig.nav.product.href))}>
            <Trans id="common.nav.product" comment="Nav link: Product">Product</Trans>
          </Link>
          <Link href={lp(siteConfig.nav.features.href)} prefetch={false} className={navLinkClass} aria-current={ariaCurrent(lp(siteConfig.nav.features.href))}>
            <Trans id="common.nav.features" comment="Nav link: Features">Features</Trans>
          </Link>
          <Link href={lp(siteConfig.nav.pricing.href)} prefetch={false} className={navLinkClass} aria-current={ariaCurrent(lp(siteConfig.nav.pricing.href))}>
            <Trans id="common.nav.pricing" comment="Nav link: Pricing">Pricing</Trans>
          </Link>
          <Link href={lp(siteConfig.nav.company.href)} className={navLinkClass} aria-current={ariaCurrent(lp(siteConfig.nav.company.href))}>
            <Trans id="common.nav.company" comment="Nav link: How do we index jobs?">How do we index jobs?</Trans>
          </Link>
        </nav>

        <div className="hidden items-center gap-3 lg:flex">
          <LocaleSwitcher />
          <ThemeToggleButton />
          <Button href={authHref} variant="primary" size="sm" className="gap-2">
            <LogIn size={16} />
            {authLabel}
          </Button>
        </div>

        <button
          onClick={onOpenMobileAction}
          className="inline-flex items-center justify-center rounded-md p-1.5 text-foreground hover:bg-border-soft lg:hidden"
          aria-label={t({ id: "common.header.openMenu", comment: "Aria label for mobile menu button", message: "Open main menu" })}
        >
          <Menu size={20} />
        </button>
      </div>
    </header>
  );
}
