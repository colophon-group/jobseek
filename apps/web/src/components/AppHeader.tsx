"use client";

import Link from "next/link";
import { Trans } from "@lingui/react/macro";
import { useLingui } from "@lingui/react/macro";
import * as Tooltip from "@radix-ui/react-tooltip";
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { Compass, Briefcase, Eye, Inbox, Settings, LogIn, LogOut } from "lucide-react";
import { siteConfig } from "@/content/config";
import { ThemedImage } from "@/components/ThemedImage";
import { useLocalePath } from "@/lib/useLocalePath";
import { useAuth } from "@/lib/useAuth";
import { authClient } from "@/lib/auth-client";
import { Button } from "@/components/ui/Button";
import { SearchBar } from "@/components/search/search-bar";
import { tooltipClass } from "@/components/ui/tooltip-styles";

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
        <Tooltip.Content className={tooltipClass} sideOffset={6}>
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

  const exploreLabel = t({ id: "app.header.nav.explore", comment: "Explore nav icon tooltip", message: "Explore" });
  const watchlistsLabel = t({ id: "app.header.nav.watchlists", comment: "Watchlists nav icon tooltip", message: "Watchlists" });
  const myJobsLabel = t({ id: "app.header.nav.myJobs", comment: "My Jobs nav icon tooltip", message: "My Jobs" });
  const queueLabel = t({ id: "app.header.nav.queue", comment: "Queue nav icon tooltip", message: "Queue" });
  const settingsLabel = t({ id: "app.header.nav.settings", comment: "Settings nav icon tooltip", message: "Settings" });


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
    // Manually clear session cookies — better-auth's nextCookies() plugin
    // uses cookies().set(name, "", {maxAge:0}) which can silently fail in
    // route handlers, leaving the browser cookie intact.
    document.cookie = "better-auth.session_token=; Max-Age=0; Path=/";
    document.cookie = "__Secure-better-auth.session_token=; Max-Age=0; Path=/; Secure";
    document.cookie = "better-auth.session_data=; Max-Age=0; Path=/";
    document.cookie = "__Secure-better-auth.session_data=; Max-Age=0; Path=/; Secure";
    window.location.href = lp("/explore");
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
        // User avatars come from arbitrary OAuth providers.
        // next/image remote host allowlist would block many of them.
        // eslint-disable-next-line @next/next/no-img-element
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
      <header className="fixed top-0 right-0 left-0 z-50 hidden border-b border-divider bg-surface-alpha backdrop-blur-md md:block">
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
          <SearchBar className="hidden flex-1 md:block md:max-w-md" />

          {/* Spacer pushes right-side items to the edge */}
          <div className="flex-1" />

          {/* Nav icons (desktop only) */}
          <nav className="hidden items-center gap-1 md:flex">
            <NavIcon href={appHref} label={exploreLabel}>
              <Compass size={18} />
            </NavIcon>
            <NavIcon href={lp("/watchlists")} label={watchlistsLabel}>
              <Eye size={18} />
            </NavIcon>
            <NavIcon href={lp("/my-jobs")} label={myJobsLabel}>
              <Briefcase size={18} />
            </NavIcon>
            <NavIcon href={lp("/queue")} label={queueLabel}>
              <Inbox size={18} />
            </NavIcon>
            <NavIcon href={lp(siteConfig.nav.settings.href)} label={settingsLabel}>
              <Settings size={18} />
            </NavIcon>
          </nav>

          {/* Auth area (desktop only) */}
          <div className="hidden items-center md:flex">
            {isPending ? (
              <div className="h-8 w-8 rounded-full bg-muted/30 animate-pulse" />
            ) : isLoggedIn && user ? (
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
      <nav className="fixed bottom-0 left-0 right-0 z-50 flex items-center border-t border-divider bg-surface-alpha backdrop-blur-md md:hidden">
        <BottomBarLink href={appHref} label={exploreLabel}>
          <Compass size={20} />
        </BottomBarLink>
        <BottomBarLink href={lp("/watchlists")} label={watchlistsLabel}>
          <Eye size={20} />
        </BottomBarLink>
        <BottomBarLink href={lp("/my-jobs")} label={myJobsLabel}>
          <Briefcase size={20} />
        </BottomBarLink>
        <BottomBarLink href={lp("/queue")} label={queueLabel}>
          <Inbox size={20} />
        </BottomBarLink>
        <BottomBarLink href={lp(siteConfig.nav.settings.href)} label={settingsLabel}>
          <Settings size={20} />
        </BottomBarLink>
        <span className="flex flex-1">
          {isPending ? (
            <span className="flex flex-1 flex-col items-center gap-0.5 py-1.5">
              <span className="size-6 rounded-full bg-muted/30 animate-pulse" />
              <span className="h-3 w-10 rounded bg-muted/30 animate-pulse" />
            </span>
          ) : isLoggedIn && user ? (
            <DropdownMenu.Root>
              <DropdownMenu.Trigger asChild>
                <button className="flex flex-1 flex-col items-center gap-0.5 py-1.5 transition-colors cursor-pointer">
                  <span className="flex size-6 items-center justify-center rounded-full bg-primary text-[10px] font-semibold text-primary-contrast">
                    {user.image ? (
                      // User avatars come from arbitrary OAuth providers.
                      // eslint-disable-next-line @next/next/no-img-element
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
            <Link href={lp(siteConfig.nav.login.href)} prefetch={false} className="flex flex-1 flex-col items-center gap-0.5 py-1.5 text-foreground transition-colors hover:text-muted">
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
