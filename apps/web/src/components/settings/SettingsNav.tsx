"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useLingui } from "@lingui/react/macro";
import { useLocalePath } from "@/lib/useLocalePath";

const linkBase =
  "block border-b-2 px-3 py-2 text-sm transition-colors hover:text-foreground";
const linkActive = "border-primary font-semibold text-foreground";
const linkInactive = "border-transparent text-muted";

export function SettingsNav() {
  const { t } = useLingui();
  const lp = useLocalePath();
  const pathname = usePathname();

  const links = [
    {
      href: lp("/app/settings"),
      label: t({ id: "settings.nav.general", comment: "General settings nav link", message: "General" }),
      exact: true,
    },
    {
      href: lp("/app/settings/account"),
      label: t({ id: "settings.nav.account", comment: "Account settings nav link", message: "Account" }),
      exact: false,
    },
  ];

  function isActive(href: string, exact: boolean) {
    if (exact) return pathname === href;
    return pathname.startsWith(href);
  }

  return (
    <nav className="flex gap-1">
      {links.map((link) => (
        <Link
          key={link.href}
          href={link.href}
          className={`${linkBase} ${isActive(link.href, link.exact) ? linkActive : linkInactive}`}
        >
          {link.label}
        </Link>
      ))}
    </nav>
  );
}
