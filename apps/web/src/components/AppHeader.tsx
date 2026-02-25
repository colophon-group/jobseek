"use client";

import Link from "next/link";
import { Trans } from "@lingui/react/macro";
import { useLingui } from "@lingui/react/macro";
import * as Tooltip from "@radix-ui/react-tooltip";
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { Home, Bookmark, Settings, Search, LogIn, LogOut } from "lucide-react";
import { siteConfig } from "@/content/config";
import { ThemedImage } from "@/components/ThemedImage";
import { useLocalePath } from "@/lib/useLocalePath";
import { useAuth } from "@/lib/useAuth";
import { authClient } from "@/lib/auth-client";
import { Button } from "@/components/ui/Button";

const tooltipContentClass =
  "z-50 rounded-md bg-tooltip-bg px-2.5 py-1 text-xs text-white data-[state=delayed-open]:animate-[tooltip-in_150ms_ease] data-[state=instant-open]:animate-[tooltip-in_150ms_ease] data-[state=closed]:animate-[tooltip-out_100ms_ease_forwards]";

const iconBtnClass =
  "inline-flex items-center justify-center rounded-md p-1.5 text-foreground hover:bg-border-soft transition-colors cursor-pointer";

function NavIcon({ href, label, children }: { href: string; label: string; children: React.ReactNode }) {
  return (
    <Tooltip.Root>
      <Tooltip.Trigger asChild>
        <Link href={href} prefetch={false} className={iconBtnClass} aria-label={label}>
          {children}
        </Link>
      </Tooltip.Trigger>
      <Tooltip.Portal>
        <Tooltip.Content className={tooltipContentClass} sideOffset={6}>
          {label}
        </Tooltip.Content>
      </Tooltip.Portal>
    </Tooltip.Root>
  );
}

function BottomBarLink({ href, label, children }: { href: string; label: string; children: React.ReactNode }) {
  return (
    <Link href={href} prefetch={false} className="flex flex-1 flex-col items-center gap-0.5 py-1.5 text-foreground transition-colors hover:text-muted">
      {children}
      <span className="text-[10px] leading-tight">{label}</span>
    </Link>
  );
}

export function AppHeader() {
  const { t } = useLingui();
  const lp = useLocalePath();
  const { isLoggedIn, user, isPending } = useAuth();

  const appHref = lp(siteConfig.nav.app.href);

  const homeLabel = t({ id: "app.header.nav.home", comment: "Home nav icon tooltip", message: "Home" });
  const savedLabel = t({ id: "app.header.nav.saved", comment: "Saved items nav icon tooltip", message: "Saved" });
  const settingsLabel = t({ id: "app.header.nav.settings", comment: "Settings nav icon tooltip", message: "Settings" });
  const searchLabel = t({ id: "app.header.nav.search", comment: "Search nav icon tooltip", message: "Search" });

  function getInitials(name: string) {
    return name
      .split(" ")
      .map((w) => w[0])
      .join("")
      .toUpperCase()
      .slice(0, 2);
  }

  async function handleSignOut() {
    await authClient.signOut();
    window.location.href = lp("/app");
  }

  const avatarButton = (
    <button
      className="flex size-8 items-center justify-center rounded-full bg-primary text-xs font-semibold text-primary-contrast transition-opacity hover:opacity-90 cursor-pointer"
      aria-label={t({
        id: "app.header.avatar.label",
        comment: "Aria label for user avatar menu",
        message: "Account menu",
      })}
    >
      {user?.image ? (
        <img src={user.image} alt="" className="size-8 rounded-full object-cover" />
      ) : (
        getInitials(user?.name ?? user?.email ?? "?")
      )}
    </button>
  );

  const avatarDropdownContent = (
    <DropdownMenu.Content
      className="z-50 min-w-[200px] rounded-md border border-border-soft bg-surface p-1 shadow-lg"
      sideOffset={5}
      align="end"
    >
      <div className="px-2 py-1.5">
        {user?.name && <p className="text-sm font-semibold">{user.name}</p>}
        <p className="text-xs text-muted">{user?.email}</p>
      </div>
      <DropdownMenu.Separator className="my-1 h-px bg-border-soft" />
      <DropdownMenu.Item
        className="flex cursor-pointer items-center gap-2 rounded-sm px-2 py-1.5 text-sm outline-none hover:bg-border-soft"
        onSelect={handleSignOut}
      >
        <LogOut size={16} />
        <Trans id="app.header.signOut" comment="Sign out dropdown menu item">Sign out</Trans>
      </DropdownMenu.Item>
    </DropdownMenu.Content>
  );

  return (
    <Tooltip.Provider delayDuration={0} skipDelayDuration={300}>
      {/* ── Desktop top header ── */}
      <header className="fixed top-0 right-0 left-0 z-50 hidden border-b border-divider backdrop-blur-md md:block">
        <div className="mx-auto flex h-12 max-w-[1200px] items-center gap-4 px-4">
          {/* Logo */}
          <Link href={appHref} prefetch={false} className="inline-flex shrink-0 items-center gap-2">
            <ThemedImage
              lightSrc={siteConfig.logoWide.light}
              darkSrc={siteConfig.logoWide.dark}
              alt="Job Seek"
              width={siteConfig.logoWide.width}
              height={siteConfig.logoWide.height}
              style={{ height: 36, width: "auto" }}
            />
          </Link>

          {/* Search bar (desktop only) */}
          <div className="hidden flex-1 items-center gap-2 rounded-md border border-border-soft bg-surface px-3 py-1.5 md:flex md:max-w-md">
            <Search size={16} className="shrink-0 text-muted" />
            <input
              type="text"
              readOnly
              placeholder={t({
                id: "app.header.searchPlaceholder",
                comment: "Placeholder text in app header search bar",
                message: "Search...",
              })}
              className="w-full bg-transparent text-sm outline-none placeholder:text-muted"
            />
          </div>

          {/* Spacer pushes right-side items to the edge */}
          <div className="flex-1" />

          {/* Nav icons (desktop only) */}
          <nav className="hidden items-center gap-1 md:flex">
            <NavIcon href={appHref} label={homeLabel}>
              <Home size={18} />
            </NavIcon>
            <NavIcon href={appHref} label={savedLabel}>
              <Bookmark size={18} />
            </NavIcon>
            <NavIcon href={lp(siteConfig.nav.settings.href)} label={settingsLabel}>
              <Settings size={18} />
            </NavIcon>
          </nav>

          {/* Auth area (desktop only) */}
          <div className="hidden items-center md:flex">
            {isLoggedIn && user ? (
              <DropdownMenu.Root>
                <DropdownMenu.Trigger asChild>
                  {avatarButton}
                </DropdownMenu.Trigger>
                <DropdownMenu.Portal>
                  {avatarDropdownContent}
                </DropdownMenu.Portal>
              </DropdownMenu.Root>
            ) : (
              <Button href={lp(siteConfig.nav.login.href)} variant="primary" size="sm" className="gap-2">
                <LogIn size={16} />
                {t({ id: "common.auth.login", comment: "Login button label", message: "Log in" })}
              </Button>
            )}
          </div>
        </div>
      </header>

      {/* ── Mobile bottom bar ── */}
      <nav className="fixed bottom-0 left-0 right-0 z-50 flex items-center border-t border-divider backdrop-blur-md md:hidden">
        <BottomBarLink href={appHref} label={homeLabel}>
          <Home size={20} />
        </BottomBarLink>
        <BottomBarLink href={appHref} label={searchLabel}>
          <Search size={20} />
        </BottomBarLink>
        <BottomBarLink href={appHref} label={savedLabel}>
          <Bookmark size={20} />
        </BottomBarLink>
        <BottomBarLink href={lp(siteConfig.nav.settings.href)} label={settingsLabel}>
          <Settings size={20} />
        </BottomBarLink>
        <span className="flex flex-1">
          {isLoggedIn && user ? (
            <DropdownMenu.Root>
              <DropdownMenu.Trigger asChild>
                <button className="flex flex-1 flex-col items-center gap-0.5 py-1.5 transition-colors cursor-pointer">
                  <span className="flex size-6 items-center justify-center rounded-full bg-primary text-[10px] font-semibold text-primary-contrast">
                    {user.image ? (
                      <img src={user.image} alt="" className="size-6 rounded-full object-cover" />
                    ) : (
                      getInitials(user.name ?? user.email ?? "?")
                    )}
                  </span>
                  <span className="text-[10px] leading-tight text-foreground">
                    {t({ id: "app.header.nav.account", comment: "Account bottom bar label", message: "Account" })}
                  </span>
                </button>
              </DropdownMenu.Trigger>
              <DropdownMenu.Portal>
                {avatarDropdownContent}
              </DropdownMenu.Portal>
            </DropdownMenu.Root>
          ) : (
            <Link href={lp(siteConfig.nav.login.href)} className="flex flex-1 flex-col items-center gap-0.5 py-1.5 text-foreground transition-colors hover:text-muted">
              <LogIn size={20} />
              <span className="text-[10px] leading-tight">
                {t({ id: "common.auth.login", comment: "Login button label", message: "Log in" })}
              </span>
            </Link>
          )}
        </span>
      </nav>
    </Tooltip.Provider>
  );
}
