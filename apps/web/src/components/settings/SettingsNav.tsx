"use client";

import { useEffect } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useLingui } from "@lingui/react/macro";
import { useLocalePath } from "@/lib/useLocalePath";

export function SettingsNav() {
  const { t } = useLingui();
  const lp = useLocalePath();
  const pathname = usePathname();
  const router = useRouter();
  useEffect(() => {
    const redirectNotifications = () => {
      if (
        pathname === lp("/settings") &&
        window.location.hash === "#notifications"
      )
        router.replace(`${lp("/settings/email")}#notifications`);
    };
    redirectNotifications();
    window.addEventListener("hashchange", redirectNotifications);
    return () =>
      window.removeEventListener("hashchange", redirectNotifications);
  }, [pathname, lp, router]);

  const links = [
    {
      href: lp("/settings"),
      label: t({
        id: "settings.nav.preferences",
        comment: "Desktop preferences heading and navigation label",
        message: "Preferences",
      }),
      mobileLabel: t({
        id: "settings.nav.general",
        comment: "Short mobile preferences navigation label",
        message: "General",
      }),
      exact: true,
    },
    {
      href: lp("/settings/email"),
      label: t({
        id: "settings.nav.email",
        comment: "Email notification settings navigation label",
        message: "Email",
      }),
      exact: false,
    },
    {
      href: lp("/settings/account"),
      label: t({
        id: "settings.nav.account",
        comment: "Account settings nav link",
        message: "Account",
      }),
      exact: false,
    },
    {
      href: lp("/settings/billing"),
      label: t({
        id: "settings.nav.billing",
        comment: "Subscription settings nav link",
        message: "Subscription",
      }),
      mobileLabel: t({
        id: "settings.nav.plan",
        comment: "Short mobile subscription navigation label",
        message: "Plan",
      }),
      exact: false,
    },
  ];

  function isActive(href: string, exact: boolean) {
    if (exact) return pathname === href;
    return pathname.startsWith(href);
  }

  return (
    <nav
      aria-label={t({
        id: "settings.title",
        comment: "Settings page heading",
        message: "Settings",
      })}
      className="grid grid-cols-4 md:grid-cols-1 md:gap-1"
    >
      {links.map((link) => (
        <Link
          key={link.href}
          href={link.href}
          prefetch={false}
          aria-current={isActive(link.href, link.exact) ? "page" : undefined}
          className={`min-h-11 border-b-2 px-1 py-3 text-center text-xs transition-colors hover:text-foreground md:rounded-lg md:border-b-0 md:border-l-2 md:px-3 md:py-2.5 md:text-left ${isActive(link.href, link.exact) ? "border-primary font-semibold text-foreground md:bg-border-soft" : "border-transparent text-muted"}`}
        >
          <span className="md:hidden">{link.mobileLabel ?? link.label}</span>
          <span className="hidden md:inline">{link.label}</span>
        </Link>
      ))}
    </nav>
  );
}
